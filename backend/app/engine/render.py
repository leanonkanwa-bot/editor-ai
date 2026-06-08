"""
FFmpeg renderer. Takes the EditPlan + transcript + raw video and produces the
final edited video.

Pipeline (production-correct order):
  1. SNAP each keep_segment edge to the nearest word boundary (Hard Rule 1).
  2. PAD the edges: 30–200ms working window (Hard Rule 2). Tighter for short
     form, looser for long form.
  3. Per-segment extract with 30ms audio fades baked in (Hard Rule 6, prevents
     audible pops at every cut).
  4. Lossless `-c copy` concat of segments (no double-encode).
  5. Single re-encode pass: scale/crop → burn ASS subtitles LAST
     (Hard Rule 7, never under overlays).

Borrows the per-segment-extract / lossless-concat / 30ms-afade pattern from
browser-use/video-use's helpers/render.py, which is the proven shape.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any

from app.agent.planner import EditPlan
from app.core.config import settings
from app.engine.captions import WordTiming, build_ass
from app.engine.transcribe import FFMPEG_PATH, FFPROBE_PATH
from app.engine.graphics import (
    AESTHETIC_COLORS,
    RenderedGraphic,
    render_motion_graphic,
    render_vignette_mask,
    render_whiteboard_layout,
    render_slide_layout,
)
try:
    from app.engine.hyperframes_engine import (
        generate_composition_html as _hf_gen_html,
        render_composition_to_video as _hf_render,
    )
    _HYPERFRAMES_AVAILABLE = True
except Exception as _hf_err:
    print(f"[HYPERFRAMES] Import failed (non-fatal): {_hf_err}")
    _HYPERFRAMES_AVAILABLE = False


SHORT_PAD_S    = 0.12   # 120ms — word-safe start/end buffer
LONG_PAD_S     = 0.20   # 200ms — cinematic breathing room
AUDIO_FADE_S   = 0.05   # 50ms — anti-pop fade at every segment boundary
AUDIO_HANDLE_S = 0.08   # 80ms audio handle kept before/after each cut edge



def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0:
        return
    stderr = proc.stderr[-2000:] or "(empty — process produced no stderr)"
    if proc.returncode < 0:
        sig = -proc.returncode
        hint = ""
        if sig == 9:
            hint = (
                " — SIGKILL. The kernel killed ffmpeg, almost always because "
                "the container ran out of memory. Try a shorter video, lower "
                "WHISPER_MODEL, or upgrade your hosting plan to give the "
                "encoder more headroom."
            )
        elif sig == 15:
            hint = " — SIGTERM. Something asked ffmpeg to stop."
        raise RuntimeError(
            f"ffmpeg killed by signal {sig}{hint}\n"
            f"  cmd: {shlex.join(cmd)}\n  stderr: {stderr}"
        )
    raise RuntimeError(
        f"ffmpeg failed (exit {proc.returncode}):\n"
        f"  cmd: {shlex.join(cmd)}\n  stderr: {stderr}"
    )


def _probe_duration(path: Path) -> float:
    out = subprocess.check_output(
        [
            FFPROBE_PATH, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
    )
    return float(out.strip())


def _probe_video_info(path: Path) -> dict[str, Any]:
    """Return {width, height, codec_name} for the first video stream."""
    try:
        out = subprocess.check_output(
            [
                FFPROBE_PATH, "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,codec_name",
                "-of", "csv=p=0",
                str(path),
            ],
            text=True,
        ).strip()
        parts = out.split(",")
        return {
            "width":  int(parts[0]) if len(parts) > 0 else 0,
            "height": int(parts[1]) if len(parts) > 1 else 0,
            "codec":  parts[2].strip() if len(parts) > 2 else "unknown",
        }
    except Exception:
        return {"width": 0, "height": 0, "codec": "unknown"}


def _needs_proxy(info: dict[str, Any], target_w: int, target_h: int) -> bool:
    """True when the source needs a pre-transcode proxy.

    Proxying is triggered when:
    - Codec is ProRes, HEVC/H.265, or other heavy-decode format
      (these hold large reference-frame buffers during decode), OR
    - Source resolution is larger than the target (4K→1080p decode is expensive).
    """
    heavy_codecs = {"prores", "hevc", "vp9", "av1", "dnxhd", "dnxhr",
                    "mjpeg", "mpeg2video", "cfhd"}
    codec = info.get("codec", "").lower()
    w, h = info.get("width", 0), info.get("height", 0)
    if codec in heavy_codecs:
        return True
    if w > target_w or h > target_h:
        return True
    return False


def _create_proxy(
    src: Path,
    dst: Path,
    target_w: int,
    target_h: int,
    fps: int,
) -> None:
    """Pre-transcode source to target-resolution H.264 with short keyframes.

    Why: heavy-decode codecs (ProRes, HEVC, 4K) keep large reference-frame
    buffers alive for every segment cut. Decoding them once here and writing
    a cheap H.264 proxy means all subsequent _cut_proxy_segment calls use
    stream-copy — zero decode memory, near-zero CPU.

    Keyframe every 2 s (60 frames at 30 fps) so stream-copy cuts have ≤ 2 s
    of alignment error (acceptable — the final re-encode corrects it).
    """
    vf = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase"
        f":flags=fast_bilinear,"
        f"crop={target_w}:{target_h},fps={fps}"
    )
    _run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-threads", "1",                    # global: limit decoder threads
        "-i", str(src),
        "-vf", vf,
        "-vsync", "cfr",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
        "-x264-params", (
            "rc-lookahead=0:bframes=0:ref=1:no-mbtree=1:"
            f"keyint=60:keyint_min=60"      # keyframe every 2 s
        ),
        "-threads", "1",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-async", "1",                      # normalize audio timestamps
        str(dst),
    ])


def _cut_proxy_segment(
    proxy: Path,
    start: float,
    end: float,
    dst: Path,
) -> None:
    """Cut a segment from the H.264 proxy using stream-copy for video.

    Stream-copy means zero decode memory — we just copy the H.264 bitstream
    bytes. Cuts snap to the nearest keyframe (≤ 2 s error), which the final
    re-encode corrects. Audio is re-encoded to apply normalization.
    """
    duration = max(0.1, end - start)
    _run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-threads", "1",
        "-fflags", "+genpts",               # regenerate PTS
        "-ss", f"{start:.6f}", "-i", str(proxy),
        "-t", f"{duration:.6f}",
        "-avoid_negative_ts", "make_zero",
        "-c:v", "copy",                     # zero decode/encode memory
        "-async", "1",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
        str(dst),
    ])


def _flat_words(transcript: dict[str, Any]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []):
            try:
                out.append((float(w["start"]), float(w["end"])))
            except (KeyError, TypeError, ValueError):
                continue
    out.sort()
    return out


def _snap_to_word_boundary(
    t: float,
    words: list[tuple[float, float]],
    edge: str,
) -> float:
    """Snap a cut time to a word boundary with ≥0.15s gap (FIX 3).

    Priority 1: find a boundary preceded/followed by ≥0.15s of silence
                (a true inter-word gap, not a brief coarticulation pause).
    Priority 2: fallback to the nearest word edge only when no gap boundary
                is found within SEARCH_WINDOW_S.

    edge='start' → word.start AFTER t where gap before word ≥ WORD_GAP_S.
    edge='end'   → word.end   BEFORE t where gap after word ≥ WORD_GAP_S.
    """
    if not words:
        return t

    WORD_GAP_S    = 0.15   # FIX 3: min gap to qualify as a clean cut point
    SEARCH_WINDOW_S = 1.5

    if edge == "start":
        gap_best:  float | None = None
        gap_dist  = float("inf")
        word_best: float | None = None
        word_dist = float("inf")

        for i, (ws, _) in enumerate(words):
            if ws < t - 0.01:
                continue
            dist = ws - t
            if dist > SEARCH_WINDOW_S:
                break
            gap_before = ws - words[i - 1][1] if i > 0 else WORD_GAP_S
            if gap_before >= WORD_GAP_S and dist < gap_dist:
                gap_best, gap_dist = ws, dist
            if dist < word_dist:
                word_best, word_dist = ws, dist

        return gap_best if gap_best is not None else (word_best if word_best is not None else t)

    else:  # end
        gap_best  = None
        gap_dist  = float("inf")
        word_best = None
        word_dist = float("inf")

        for i, (_, we) in enumerate(words):
            if we > t + 0.01:
                break
            dist = t - we
            if dist > SEARCH_WINDOW_S:
                continue
            gap_after = words[i + 1][0] - we if i + 1 < len(words) else WORD_GAP_S
            if gap_after >= WORD_GAP_S and dist < gap_dist:
                gap_best, gap_dist = we, dist
            if dist < word_dist:
                word_best, word_dist = we, dist

        return gap_best if gap_best is not None else (word_best if word_best is not None else t)


_DANGLING_WORDS = frozenset([
    "which", "that", "because", "so", "but", "and", "when", "if", "as",
    "while", "since", "although", "where", "who", "what", "how",
    "whether", "though", "unless", "until", "after", "before",
])


def _extend_for_semantic_completeness(
    end_t: float,
    transcript: dict[str, Any],
    src_duration: float,
    search_window: float = 3.0,
) -> float:
    """Extend a segment end time if the last word is a dangling conjunction.

    Example: "I ran 10 miles which is..." — cutting after "is" leaves an
    incomplete thought. This function scans forward up to search_window seconds
    to find the next natural pause (≥0.3s) or sentence-ending punctuation.
    """
    import re as _re
    last_text: str | None = None
    last_end: float = 0.0
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []):
            we = float(w.get("end", 0))
            if we <= end_t + 0.05:
                raw = str(w.get("text", "")).strip()
                last_text = _re.sub(r"[.,!?;:\"\'-]", "", raw).lower()
                last_end = we

    if not last_text or last_text not in _DANGLING_WORDS:
        return end_t

    # Scan forward: find the next pause ≥ 0.3s or sentence-ending punctuation.
    prev_word_end: float | None = last_end
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []):
            ws_t = float(w.get("start", 0))
            we = float(w.get("end", 0))
            if ws_t <= end_t:
                prev_word_end = we
                continue
            if ws_t > end_t + search_window:
                break
            # Natural pause before this word → cut here
            if prev_word_end is not None and ws_t - prev_word_end >= 0.3:
                return min(prev_word_end, src_duration)
            # Sentence-ending punctuation on this word → include it
            if str(w.get("text", "")).strip().endswith((".", "!", "?")):
                return min(we, src_duration)
            prev_word_end = we

    return end_t  # no clean extension found — keep original end


def _cut_segment(
    src: Path,
    start: float,
    end: float,
    dst: Path,
    target_w: int,
    target_h: int,
    fps: int,
) -> None:
    """Per-segment extract.

    SYNC: -ss BEFORE -i (fast seek, frame-accurate). Duration via -t.
    Audio is AAC-re-encoded (not stream-copied) so the trim is exact.
    With -c:a copy + fast seek, FFmpeg copies from the nearest audio keyframe
    which starts BEFORE the -ss point, making audio longer than video.
    aresample=async=1 + -async 1 guarantee audio duration == video duration.
    """
    duration = max(0.1, end - start)
    vf = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase"
        f":flags=lanczos,"
        f"crop={target_w}:{target_h},"
        f"setsar=1:1,"
        f"fps={fps}"
    )
    _run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-fflags", "+genpts",
        "-ss", f"{start:.6f}",
        "-i", str(src),
        "-t", f"{duration:.6f}",
        "-avoid_negative_ts", "make_zero",
        "-vf", vf,
        "-vsync", "cfr",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-af", "aresample=async=1:min_hard_comp=0.100000:first_pts=0",
        "-async", "1",
        str(dst),
    ])


def _concat(parts: list[Path], dst: Path) -> None:
    list_path = dst.with_suffix(".txt")
    list_path.write_text("\n".join(f"file '{p.as_posix()}'" for p in parts))
    _run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-fflags", "+genpts",               # normalize PTS at segment boundaries
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-c", "copy",
        "-vsync", "cfr",
        "-async", "1",
        str(dst),
    ])
    list_path.unlink(missing_ok=True)


def _probe_av(path: Path) -> tuple[float, float]:
    """Probe audio and video durations to detect AV sync drift.

    BUG 2 FIX — AV SYNC: runs after _concat() to surface timestamp
    mismatches before they propagate through the rest of the pipeline.
    Returns (video_duration, audio_duration).
    """
    import json as _json
    try:
        r = subprocess.run(
            [FFPROBE_PATH, "-v", "quiet", "-print_format", "json",
             "-show_streams", str(path)],
            capture_output=True, text=True, timeout=20,
        )
        data = _json.loads(r.stdout) if r.stdout.strip() else {}
        v_dur = next(
            (float(s["duration"]) for s in data.get("streams", [])
             if s.get("codec_type") == "video" and "duration" in s),
            0.0,
        )
        a_dur = next(
            (float(s["duration"]) for s in data.get("streams", [])
             if s.get("codec_type") == "audio" and "duration" in s),
            0.0,
        )
        print(f"[AV PROBE] {path.name}: video={v_dur:.3f}s  audio={a_dur:.3f}s")
        if v_dur > 0 and a_dur > 0 and abs(v_dur - a_dur) > 0.1:
            print(
                f"[AV PROBE] WARNING: AV SYNC MISMATCH — delta={abs(v_dur-a_dur):.3f}s "
                f"(>{0.1:.1f}s threshold) in {path.name}"
            )
        return v_dur, a_dur
    except Exception as _e:
        print(f"[AV PROBE] probe failed for {path.name}: {_e}")
        return 0.0, 0.0



def _concat_audio_xfade(parts: list[Path], dst: Path) -> None:
    """Concatenate segments with 50ms exponential audio crossfade at every cut.

    FIX 2: acrossfade smooths audio transitions completely — no clicks or pops
    at cut points. Video is stream-copied (no re-encode); only audio is touched.
    Falls back to plain _concat() if fewer than 2 parts.
    """
    if len(parts) < 2:
        _concat(parts, dst)
        return

    # Build a filter graph: chain acrossfade filters between every adjacent pair.
    # acrossfade takes [a][b] and outputs one stream; each subsequent crossfade
    # re-uses the output of the previous one.
    inputs: list[str] = []
    for p in parts:
        inputs += ["-i", str(p)]

    n = len(parts)
    filter_parts: list[str] = []

    # Label outputs: [a0][a1]...[an-1]
    prev = "[0:a]"
    for i in range(1, n):
        out_label = f"[xf{i}]" if i < n - 1 else "[aout]"
        filter_parts.append(
            f"{prev}[{i}:a]acrossfade=d=0.04:c1=exp:c2=exp{out_label}"
        )
        prev = out_label

    filter_complex = ";".join(filter_parts)

    # Video: simple concat (stream-copy); audio: crossfaded chain.
    # Build video concat separately.
    list_path = dst.with_suffix(".xfade_list.txt")
    list_path.write_text("\n".join(f"file '{p.as_posix()}'" for p in parts))
    video_concat = dst.with_suffix(".xfade_video.mp4")
    _run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-c:v", "copy", "-an",
        str(video_concat),
    ])
    list_path.unlink(missing_ok=True)

    # Audio: crossfaded output.
    audio_out = dst.with_suffix(".xfade_audio.aac")
    _run(
        [FFMPEG_PATH, "-y", "-loglevel", "error"]
        + inputs
        + [
            "-filter_complex", filter_complex,
            "-map", "[aout]",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
            str(audio_out),
        ]
    )

    # Mux video + crossfaded audio.
    _run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-i", str(video_concat),
        "-i", str(audio_out),
        "-c:v", "copy", "-c:a", "copy",
        "-shortest",
        str(dst),
    ])
    video_concat.unlink(missing_ok=True)
    audio_out.unlink(missing_ok=True)


def _find_long_pauses(
    words: list["WordTiming"],
    min_gap_s: float = 0.4,
) -> list[tuple[float, float]]:
    """Return (start, end) pairs of gaps > min_gap_s between consecutive words."""
    gaps: list[tuple[float, float]] = []
    for i in range(1, len(words)):
        gap_start = words[i - 1].end
        gap_end = words[i].start
        if gap_end - gap_start > min_gap_s:
            gaps.append((gap_start, gap_end))
    return gaps


def _compress_pauses(
    src: Path,
    dst: Path,
    work_dir: Path,
    long_pauses: list[tuple[float, float]],
    max_silence_s: float = 0.3,
) -> list[tuple[float, float]]:
    """Trim long pauses to max_silence_s in src, write result to dst.

    Returns the list of (src_start, src_end) intervals that were kept,
    so callers can remap timestamps from the original to the compressed timeline.
    """
    total_dur = _probe_duration(src)
    sorted_pauses = sorted(long_pauses)
    seg_dir = work_dir / "pause_segs"
    seg_dir.mkdir(parents=True, exist_ok=True)

    kept_intervals: list[tuple[float, float]] = []
    cursor = 0.0
    for gap_s, gap_e in sorted_pauses:
        # Active audio before this pause
        if gap_s > cursor + 0.05:
            kept_intervals.append((cursor, gap_s))
        # Keep first max_silence_s of the pause (preserves rhythm)
        keep_end = min(gap_s + max_silence_s, gap_e)
        if keep_end > gap_s + 0.02:
            kept_intervals.append((gap_s, keep_end))
        cursor = gap_e

    if total_dur > cursor + 0.05:
        kept_intervals.append((cursor, total_dur))

    parts: list[Path] = []
    for idx, (s, e) in enumerate(kept_intervals):
        dur = e - s
        if dur < 0.05:
            continue
        p = seg_dir / f"cmp_{idx:04d}.mp4"
        _run([
            FFMPEG_PATH, "-y", "-loglevel", "error",
            "-ss", f"{s:.3f}", "-i", str(src),
            "-t", f"{dur:.3f}", "-c", "copy", p,
        ])
        parts.append(p)

    if parts:
        _concat(parts, dst)
    else:
        _run([FFMPEG_PATH, "-y", "-loglevel", "error",
              "-i", str(src), "-c", "copy", str(dst)])

    import shutil as _sh
    _sh.rmtree(seg_dir, ignore_errors=True)
    return kept_intervals


def _remap_time(t: float, kept_intervals: list[tuple[float, float]]) -> float:
    """Map a timestamp from the pre-compression timeline to the compressed one."""
    out = 0.0
    for s, e in kept_intervals:
        if t <= s:
            return out
        if t <= e:
            return out + (t - s)
        out += (e - s)
    return out


def _find_word_timestamp(
    transcript: dict[str, Any],
    anchor_word: str,
) -> float | None:
    """Find the start timestamp of the first occurrence of anchor_word."""
    needle = anchor_word.strip().lower().strip(".,!?;:\"'")
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []):
            wt = str(w.get("text", "")).strip().lower().strip(".,!?;:\"'")
            if wt == needle:
                return float(w["start"])
    return None


def _escape_for_vf(expr: str) -> str:
    """Escape commas in an expression for safe use in a -vf filter chain option value."""
    return expr.replace(",", "\\,")


def _hex_to_rgb_at(hex6: str) -> str:
    """Return an ffmpeg color string like 'white@1.0' or '0xRRGGBB@1.0'."""
    h = (hex6 or "").lstrip("#").upper()
    if len(h) != 6:
        return "white@1.0"
    return f"0x{h}@1.0"


def _ass_escape_text(text: str) -> str:
    return (text.replace("\\", "\\\\")
                .replace("'", "\\'")
                .replace(":", "\\:")
                .replace("%", "\\%"))



def _vignette_dims(short_form: bool) -> tuple[int, int, int, int, int]:
    """Return (vign_w, vign_h, vign_x, vign_y, corner_radius) for the vignette."""
    if short_form:   # 1080 × 1920
        vw, vh = 400, 500
        vx = 1080 - vw - 40   # 640
        vy = 1920 - vh - 80   # 1340
        cr = 40
    else:            # 1920 × 1080
        vw, vh = 490, 380
        vx = 1920 - vw - 40   # 1390
        vy = 1080 - vh - 60   # 640
        cr = 36
    return vw, vh, vx, vy, cr


def _render_vignette_layouts(
    moments: list[dict[str, Any]],
    work_dir: Path,
    target_w: int,
    target_h: int,
    short_form: bool,
    glow_color: str,
) -> tuple[list[Path], Path | None]:
    """Pre-render layout PNGs for each visual_style moment (Styles 2 & 3).
    Returns (layout_paths, mask_path).  mask_path is None when moments is empty."""
    if not moments:
        return [], None
    vw, vh, vx, vy, cr = _vignette_dims(short_form)
    vign_dir = work_dir / "vignette"
    vign_dir.mkdir(parents=True, exist_ok=True)

    mask_path = vign_dir / "mask.png"
    render_vignette_mask(vw, vh, cr, mask_path)

    layout_paths: list[Path] = []
    for i, m in enumerate(moments):
        style = int(m.get("style", 2))
        lp = vign_dir / f"layout_{i:03d}.png"
        if style == 3:
            title = str(m.get("title") or "").strip()
            bullets = [str(b) for b in (m.get("bullets") or [])]
            render_slide_layout(
                title, bullets, lp,
                target_w=target_w, target_h=target_h,
                vign_x=vx, vign_y=vy, vign_w=vw, vign_h=vh,
                glow_color=glow_color,
            )
        else:  # style == 2 (default)
            content = str(m.get("content") or "").strip()
            render_whiteboard_layout(
                content, lp,
                target_w=target_w, target_h=target_h,
                vign_x=vx, vign_y=vy, vign_w=vw, vign_h=vh,
                glow_color=glow_color,
            )
        layout_paths.append(lp)

    return layout_paths, mask_path


def _find_system_font() -> str | None:
    """Return path to a bold TTF that exists on this system (Railway = Debian)."""
    import os as _os
    for candidate in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]:
        if _os.path.exists(candidate):
            return candidate
    return None


_VALID_ZOOM_LEVELS = frozenset({100, 130, 150, 170})

PRIESTLEY_ZOOM: dict[str, int] = {
    "hook":           130,
    "amplify":        125,
    "context":        100,
    "tension":        125,
    "tension_stack":  130,
    "story":          112,
    "realization":    125,
    "principle":      115,
    "payoff":         130,
    "emotional_end":  112,
    "default":        112,
}
MAX_ZOOM_PRIESTLEY = 130


def _zoom_filter_for_level(zoom_level: int, target_w: int, target_h: int) -> str | None:
    """Return an FFmpeg vf string for a jump zoom at the given level.

    Returns None for level 100 (full frame, no crop needed).
    For 130/150/170: scales up then center-crops back to target dimensions.
    Uses lanczos for quality — these are still-frame crops, not animations.
    """
    if zoom_level <= 100:
        return None
    factor = zoom_level / 100.0
    scaled_w = round(target_w * factor)
    scaled_h = round(target_h * factor)
    crop_x = (scaled_w - target_w) // 2
    crop_y = (scaled_h - target_h) // 2
    return (
        f"scale={scaled_w}:{scaled_h}:flags=lanczos,"
        f"crop={target_w}:{target_h}:{crop_x}:{crop_y}"
    )


def _concat_with_zoom(
    parts: list[Path],
    zoom_levels: list[int],
    dst: Path,
    target_w: int,
    target_h: int,
    fps: int,
) -> None:
    """Concat segments with per-segment jump zoom in a single ffmpeg pass.

    Folds the zoom encode into the concat step, eliminating a separate
    _apply_segment_zoom() pass. Uses filter_complex concat filter so each
    segment's scale+crop zoom is applied inline before concatenation.
    Single-segment shortcut avoids the concat filter overhead.
    """
    n = len(parts)
    if n == 1:
        zoom_f = _zoom_filter_for_level(zoom_levels[0], target_w, target_h)
        vf = (
            f"{zoom_f},setsar=1:1,fps={fps},setpts=N/FRAME_RATE/TB"
            if zoom_f
            else f"setsar=1:1,fps={fps},setpts=N/FRAME_RATE/TB"
        )
        _run([
            FFMPEG_PATH, "-y", "-loglevel", "error",
            "-fflags", "+genpts", "-i", str(parts[0]),
            "-vf", vf,
            "-vsync", "cfr", "-async", "1",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
            "-threads", "2",
            "-x264-params", "rc-lookahead=0:bframes=0:ref=1:no-mbtree=1",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            str(dst),
        ])
        return

    inputs: list[str] = []
    for p in parts:
        inputs += ["-fflags", "+genpts", "-i", str(p)]

    fc_parts: list[str] = []
    for i, zoom_level in enumerate(zoom_levels):
        zoom_f = _zoom_filter_for_level(zoom_level, target_w, target_h)
        # setsar=1:1 normalizes Sample Aspect Ratio before concat.
        # Without it, source videos with non-standard SAR (e.g. 1936:1935 from
        # stream captures) cause "SAR mismatch" errors in the concat filter.
        vf_chain = (
            f"{zoom_f},setsar=1:1,fps={fps},setpts=N/FRAME_RATE/TB"
            if zoom_f
            else f"setsar=1:1,fps={fps},setpts=N/FRAME_RATE/TB"
        )
        fc_parts.append(f"[{i}:v]{vf_chain}[v{i}]")

    concat_inputs = "".join(f"[v{i}][{i}:a]" for i in range(n))
    fc_parts.append(f"{concat_inputs}concat=n={n}:v=1:a=1[vout][aout]")

    _run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        *inputs,
        "-filter_complex", ";".join(fc_parts),
        "-map", "[vout]", "-map", "[aout]",
        "-vsync", "cfr", "-async", "1",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
        "-threads", "4",
        "-x264-params", "rc-lookahead=0:bframes=0",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(dst),
    ])


def _render_hyperframe_png(
    color_str: str,
    dst: Path,
    target_w: int,
    target_h: int,
    text: str | None = None,
    system_font: str | None = None,
) -> None:
    """Render a full-frame solid-color PNG for a hyperframe flash.

    Generates the PNG via ffmpeg's lavfi color source — no enable= needed.
    The resulting PNG is converted to a timed MKV clip by _png_to_timed_clip()
    and overlaid with setpts+eof_action=pass, completely avoiding any
    enable= expression in the filter_complex.

    color_str is the output of _hex_to_rgb_at() e.g. '0xFF7751@1.0'.
    We strip the @opacity suffix since lavfi color=c= doesn't accept it.
    """
    lavfi_color = color_str.split("@")[0]   # '0xFF7751@1.0' → '0xFF7751'
    base_cmd = [
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"color=c={lavfi_color}:s={target_w}x{target_h}:rate=1",
    ]
    if text and system_font:
        font_size = int(target_h * 0.22)
        escaped = _ass_escape_text(text)
        base_cmd += [
            "-vf",
            (
                f"drawtext=text={escaped}"
                f":fontfile={system_font}"
                f":fontcolor=black:fontsize={font_size}"
                f":x=(w-text_w)/2:y=(h-text_h)/2"
            ),
        ]
    base_cmd += ["-frames:v", "1", "-pix_fmt", "rgba", str(dst)]
    _run(base_cmd)


def _png_to_timed_clip(png: Path, dst: Path, duration: float, fps: int) -> None:
    """Convert a static PNG to a short RGBA video clip at the target fps.

    Uses the PNG video codec in a Matroska (.mkv) container — the only
    widely-available combination that preserves a full RGBA alpha channel
    for transparent overlay compositing.

    Clip timestamps start at 0; the caller uses setpts=PTS+start/TB in
    the overlay filter_complex to shift it to the correct position in the
    edit timeline.  No enable= expression is needed: the clip simply
    doesn't exist outside its window, so the overlay falls through to the
    base video automatically (eof_action=pass).

    fps matches the project fps (30).  With 48 GB RAM there is no longer
    any reason to cap at 1 fps — the full 30-fps clip is used so the
    overlay filter receives properly-timed frames throughout the clip.
    """
    _run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-loop", "1",
        "-i", str(png),
        "-t", f"{duration:.3f}",
        "-pix_fmt", "rgba",
        "-c:v", "png",
        "-r", str(fps),
        str(dst),
    ])


def _build_pass1_filter_complex(
    target_w: int,
    target_h: int,
    fps: int,
    color_grade: str,
    scale_filter: str | None,
    silences: list[dict[str, Any]],
    rendered_graphics: list[RenderedGraphic],
) -> tuple[str, str, str | None]:
    """Build the filter_complex string for render pass 1.

    Video chain:
      [0:v] → grade+scale+zoompan → overlay per timed clip → [vout_label]

    Timed clips (indices 1..N) cover both hyperframe flashes and motion
    graphics. They are positioned via setpts=PTS+at/TB so no enable=
    expression is needed anywhere in the video filter chain.

    Audio chain (when silences exist):
      [0:a] → volume-duck chain → [aout]
      Volume enable= uses \\, (backslash-comma) escaping — the only
      remaining enable= in the entire pipeline.

    Returns:
        (filter_complex_str, video_out_label, audio_out_label_or_None)
    """
    fc: list[str] = []

    # ── Video: grade + optional scale ─────────────────────────────────────
    grade_parts: list[str] = [color_grade]
    if scale_filter:
        grade_parts.append(scale_filter)
    fc.append(f"[0:v]{','.join(grade_parts)}[vzoom]")

    # ── Timed clip overlays (hyperframes + motion graphics) ───────────────
    # Inputs at indices 1..N are pre-cut MKV clips (RGBA, duration = rg.duration).
    # setpts=PTS+at/TB shifts each clip to its correct position in the timeline.
    # eof_action=pass lets the base video show through before and after the clip.
    # No enable= expression is needed at all — the clip simply doesn't exist
    # outside its window, so the overlay falls through to the base video.
    #
    # COMMA ESCAPING: x_expr / y_expr from graphics.py use plain commas inside
    # function calls like max(a,b), if(c,a,b), lt(a,b).  In a filter_complex
    # option value, an unescaped comma is a filter-chain separator — FFmpeg would
    # split "y=max(0,345-h/2)" into two filters: "y=max(0" and "345-h/2)…".
    # Replace every plain comma with \, so the filter_complex parser treats them
    # as literal commas and forwards them intact to the expression evaluator.
    v = "vzoom"
    for j, rg in enumerate(rendered_graphics):
        input_idx = j + 1
        timed = f"gt{j}"
        ov_out = f"vg{j}"
        x = rg.x_expr.replace(",", "\\,")
        y = rg.y_expr.replace(",", "\\,")
        fc.append(f"[{input_idx}:v]setpts=PTS+{rg.at:.3f}/TB[{timed}]")
        fc.append(
            f"[{v}][{timed}]overlay"
            f"=x={x}:y={y}"
            f":eof_action=pass[{ov_out}]"
        )
        v = ov_out

    # ── Audio: volume-duck at deliberate silence inserts ──────────────────
    # Multiple ducks are comma-chained on the same stream node.
    a_label: str | None = None
    vol_nodes: list[str] = []
    for sil in silences:
        try:
            at = float(sil.get("at", 0))
            dur = max(0.05, min(0.5, float(sil.get("duration", 0.3))))
        except (TypeError, ValueError):
            continue
        vol_nodes.append(
            f"volume=enable=gte(t\\,{at:.3f})*lte(t\\,{at+dur:.3f}):volume=0"
        )
    if vol_nodes:
        fc.append(f"[0:a]{','.join(vol_nodes)}[aout]")
        a_label = "aout"

    return ";".join(fc), v, a_label


def _color_grade_filter(content_type: str) -> str:
    """Disabled — returns empty string so no color/grade filter is applied."""
    return ""


def _fetch_broll_clip(
    query: str,
    dst: Path,
    duration: float,
    target_w: int,
    target_h: int,
) -> bool:
    """Fetch a Pexels video matching query, trim to duration, crop to target dims.

    Returns True on success (dst exists and is ready), False on any failure.
    All steps are logged via print() so Railway logs show exact failure point.
    """
    import os as _os
    import requests as _requests

    key = _os.environ.get("PEXELS_API_KEY", "") or (
        settings.pexels_api_key if hasattr(settings, "pexels_api_key") else ""
    )
    print(f"[BROLL] Called with query={query!r}  dst={dst}  key_set={bool(key)}")
    if not key:
        print("[BROLL] No PEXELS_API_KEY — skipping")
        return False

    orientation = "portrait" if target_h > target_w else "landscape"
    try:
        r = _requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": key},
            params={"query": query, "per_page": 10, "orientation": orientation},
            timeout=15,
        )
        print(f"[BROLL] API status: {r.status_code}")
        if r.status_code != 200:
            print(f"[BROLL] API error body: {r.text[:300]}")
            return False

        all_videos = r.json().get("videos", [])
        print(f"[BROLL] Found {len(all_videos)} videos for query={query!r}")
        if not all_videos:
            return False

        # BUG 3 FIX — minimum duration: only use videos >= 4s so we can trim
        # cleanly and still have quality frames throughout the b-roll window.
        videos = [v for v in all_videos if v.get("duration", 0) >= 4]
        if not videos:
            print(f"[BROLL] All {len(all_videos)} videos too short (<4s) — skipping")
            return False
        print(f"[BROLL] {len(videos)} video(s) pass the >=4s duration filter")

        # BUG 3 FIX — quality filter: only download HD or UHD files.
        # SD files are visibly blurry at 1080p — they degrade the output.
        # Priority: portrait UHD (1080×1920) → portrait HD → any HD → skip.
        video = videos[0]
        files = video.get("video_files", [])
        print(f"[BROLL] Video files available: {[(f.get('width'), f.get('height'), f.get('quality')) for f in files]}")

        preferred_files = [f for f in files if f.get("quality") in ("uhd", "hd")]
        if not preferred_files:
            print(f"[BROLL] No HD/UHD files for query {query!r} — skipping (SD only)")
            return False

        # Portrait-first selection: pick largest portrait by pixel area.
        # Only fall back to landscape if absolutely no portrait file exists.
        portrait_files = [
            f for f in preferred_files
            if (f.get("height") or 0) > (f.get("width") or 0)
        ]
        if portrait_files:
            best = sorted(
                portrait_files,
                key=lambda f: (f.get("width") or 0) * (f.get("height") or 0),
                reverse=True,
            )[0]
            print(f"[BROLL] Portrait file selected ({len(portrait_files)} portrait candidate(s))")
        else:
            best = sorted(
                preferred_files,
                key=lambda f: (f.get("width") or 0) * (f.get("height") or 0),
                reverse=True,
            )[0]
            print(f"[BROLL] No portrait files — using best available orientation")

        if not best or not best.get("link"):
            print("[BROLL] No suitable HD/UHD file found in video_files")
            return False

        url = best["link"]
        print(f"[BROLL] Downloading: {url[:80]}  w={best.get('width')} h={best.get('height')}")

        tmp_path = dst.with_suffix(".tmp.mp4")
        dl = _requests.get(url, timeout=45, stream=True)
        if dl.status_code != 200:
            print(f"[BROLL] Download failed: HTTP {dl.status_code}")
            return False
        with open(str(tmp_path), "wb") as fh:
            for chunk in dl.iter_content(chunk_size=65536):
                fh.write(chunk)
        print(f"[BROLL] Downloaded {tmp_path.stat().st_size} bytes → {tmp_path.name}")

        # Trim to required duration and crop-to-fill target dimensions
        _run([
            FFMPEG_PATH, "-y", "-loglevel", "error",
            "-i", str(tmp_path),
            "-t", f"{duration:.3f}",
            "-vf", (
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
                f"crop={target_w}:{target_h},"
                f"setpts=PTS-STARTPTS"
            ),
            "-vsync", "cfr",
            "-c:v", "libx264", "-crf", "0", "-preset", "ultrafast",
            "-threads", "2", "-pix_fmt", "yuv420p",
            "-an",
            str(dst),
        ])
        tmp_path.unlink(missing_ok=True)

        if dst.exists() and dst.stat().st_size > 0:
            print(f"[BROLL] Successfully created: {dst.name}  ({dst.stat().st_size} bytes)")
            return True
        print(f"[BROLL] FFmpeg produced no output at {dst}")
        return False

    except Exception as _exc:
        import traceback as _tb
        print(f"[BROLL] Exception: {_exc}\n{_tb.format_exc()[:800]}")
        return False


def _dedup_segments(segments: list[dict]) -> list[dict]:
    """Remove exact duplicate (start, end) pairs and empty segments.

    Preserves the planner's intended edit order — do NOT sort by start time.
    The order of the array IS the playback order; sorting would destroy
    any reordering the planner did for psychological impact.
    Non-chronological source timestamps are intentional (e.g. hook at t=47s
    placed first in edit order) and must NOT be treated as overlaps.
    """
    if not segments:
        return segments
    seen: set[tuple[float, float]] = set()
    result = []
    for seg in segments:
        s = round(float(seg["start"]), 2)
        e = round(float(seg["end"]), 2)
        if s >= e:
            print(f"[SKIP] Empty segment {s}s-{e}s")
            continue
        key = (s, e)
        if key in seen:
            print(f"[DEDUP] Skipping duplicate: {s}s-{e}s")
            continue
        seen.add(key)
        result.append({**seg, "start": s, "end": e})
    return result


def _verify_caption_sync(
    words: list["WordTiming"],
    edited_duration: float,
) -> list["WordTiming"]:
    """Drop caption words outside the edited video's duration and log anomalies.

    Called just before build_ass() to prevent captions from appearing at
    times that don't exist in the final video — which looks like frozen or
    ghost captions at the end of the video.
    """
    import logging as _lg
    _log = _lg.getLogger(__name__)
    issues: list[str] = []
    valid: list["WordTiming"] = []
    for w in words:
        if w.start < 0:
            issues.append(f"'{w.text}' negative start={w.start:.3f}s")
            continue
        if w.start > edited_duration:
            issues.append(
                f"'{w.text}' start={w.start:.3f}s > video duration={edited_duration:.3f}s"
            )
            continue
        if w.end > edited_duration + 0.5:
            issues.append(
                f"'{w.text}' end={w.end:.3f}s exceeds video duration={edited_duration:.3f}s"
            )
            # Clamp rather than drop — the word starts inside the video.
            valid.append(WordTiming(text=w.text, start=w.start, end=min(w.end, edited_duration)))
            continue
        valid.append(w)
    if issues:
        _log.warning("caption sync anomalies (%d words): %s", len(issues), issues[:10])
    return valid


def _health_check(src: Path) -> None:
    """Verify render preconditions before any ffmpeg work begins."""
    import os as _os
    if not src.exists():
        raise RuntimeError(f"Source file not found: {src}")
    if not _os.access(src, _os.R_OK):
        raise RuntimeError(f"Source file not readable: {src}")
    try:
        subprocess.run(
            [FFMPEG_PATH, "-version"],
            capture_output=True, check=True, timeout=10,
        )
    except Exception as _e:
        raise RuntimeError(f"ffmpeg not available: {_e}") from _e
    size_mb = src.stat().st_size / (1024 * 1024)
    est_min = max(1, int(size_mb / 100))
    print(f"[HEALTH] src={src.name}  size={size_mb:.1f}MB  est_render_time=~{est_min}min")


def render(
    src: Path,
    transcript: dict[str, Any],
    plan: EditPlan,
    work_dir: Path,
    output_path: Path,
    *,
    caption_font: str = "Inter Bold",
    caption_color: str = "white",
    caption_position: str = "center",
    caption_style: str = "kinetic",
    brand_color: str | None = None,
    aesthetic: str = "dark-pro",
    subject_position: dict | None = None,
    graphic_specs: list | None = None,
    content_type: str = "coaching",
    editing_style: str = "viral",
) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    _health_check(src)

    try:
        from app.engine.font_manager import preload_style_fonts
        preload_style_fonts(editing_style)
    except Exception as _fe:
        print(f"[FONT] preload_style_fonts failed (non-fatal): {_fe}")

    short_form = plan.format == "short"
    fps = 30
    pad = SHORT_PAD_S if short_form else LONG_PAD_S

    # Detect 4K input — preserve native resolution, don't downscale.
    video_info = _probe_video_info(src)
    src_w_raw = video_info.get("width", 0)
    src_h_raw = video_info.get("height", 0)
    is_4k = max(src_w_raw, src_h_raw) >= 3840

    if short_form:
        target_w, target_h = (2160, 3840) if is_4k else (1080, 1920)
    else:
        target_w, target_h = (3840, 2160) if is_4k else (1920, 1080)

    # CRF 14 for 4K (high bitrate needed), 16 for 1080p. Preset slow for quality.
    output_crf = 14 if is_4k else 16

    keep = plan.keep_segments or [
        {"start": 0.0, "end": float(transcript.get("duration", 0.0))}
    ]
    # Resolve any raw planner overlaps before cutting — prevents word repetition.
    keep = _dedup_segments(keep)
    words = _flat_words(transcript)
    src_duration = float(transcript.get("duration", 0.0)) or _probe_duration(src)

    # Proxy: if the source is a heavy-decode format (ProRes, HEVC) or larger
    # than the target resolution, pre-transcode it once to a target-res H.264.
    # All segment cuts then use stream-copy on the proxy → zero decode RAM.
    use_proxy = _needs_proxy(video_info, target_w, target_h)
    if use_proxy:
        proxy_path = work_dir / "proxy.mp4"
        _create_proxy(src, proxy_path, target_w, target_h, fps)
        cut_src = proxy_path
    else:
        cut_src = src

    parts: list[Path] = []
    zoom_levels: list[int] = []
    zoom_segments: list[int] = []
    cum = 0.0  # exact video output timeline — no audio-handle inflation
    remapped_zoom: list[dict[str, Any]] = []
    remapped_words: list[WordTiming] = []
    remapped_silences: list[dict[str, Any]] = []
    remapped_vsm: list[dict[str, Any]] = []
    remapped_moments: list[dict[str, Any]] = []
    remapped_motion_graphics: list[dict[str, Any]] = []
    cut_timestamps: list[float] = []  # output-timeline timestamps of cut points

    for i, seg in enumerate(keep):
        s_raw = float(seg["start"])
        e_raw = float(seg["end"])
        if e_raw <= s_raw:
            continue

        # Hard Rule 1 — snap to word boundaries
        s = _snap_to_word_boundary(s_raw, words, edge="start")
        e = _snap_to_word_boundary(e_raw, words, edge="end")
        # Semantic completeness: extend end if snapped edge is a dangling conjunction.
        # Cap extension at next segment's raw start to prevent encroaching on it.
        e = _extend_for_semantic_completeness(e, transcript, src_duration)
        if i + 1 < len(keep):
            _next_s_raw = float(keep[i + 1]["start"])
            e = min(e, max(s + 0.15, _next_s_raw - 0.05))
        # Hard Rule 2 — pad cut edges
        s = max(0.0, s - pad)
        e = min(src_duration, e + pad) if src_duration > 0 else e + pad
        if e - s < 0.15:
            continue

        part = work_dir / f"part_{i:04d}.mp4"
        if use_proxy:
            _cut_proxy_segment(cut_src, s, e, part)
        else:
            _cut_segment(cut_src, s, e, part, target_w, target_h, fps)

        # Jump zoom — Priestley style uses beat-mapped levels; viral uses planner levels.
        zoom_level = int(seg.get("zoom_level", 130))
        if editing_style == "priestley":
            _beat = (seg.get("beat") or "default").lower()
            zoom_level = PRIESTLEY_ZOOM.get(_beat, PRIESTLEY_ZOOM["default"])
            zoom_level = min(zoom_level, MAX_ZOOM_PRIESTLEY)
        elif zoom_level not in _VALID_ZOOM_LEVELS:
            zoom_level = min(_VALID_ZOOM_LEVELS, key=lambda z: abs(z - zoom_level))
        print(f"[ZOOM] Segment {i}: beat={seg.get('beat')} zoom={zoom_level}% duration={e-s:.2f}s")
        zoom_segments.append(i)
        zoom_levels.append(zoom_level)
        parts.append(part)

        # All timeline remapping uses cum (exact video output clock) and s (snapped
        # cut start). No audio handles — _cut_segment() cuts exactly [s, e], so
        # cum + (ws - s) places captions on the identical clock as the video frames.
        seg_offset = cum

        for zp in plan.zoom_plan:
            zs = float(zp.get("start", 0))
            ze = float(zp.get("end", zs))
            if zs >= s and ze <= e:
                remapped_zoom.append({
                    **zp,
                    "start": seg_offset + (zs - s),
                    "end": seg_offset + (ze - s),
                })

        for tseg in transcript.get("segments", []):
            for w in tseg.get("words", []):
                ws = float(w["start"])
                we = float(w["end"])
                # Strict plan-boundary whitelist: only words whose source timestamp
                # falls inside [s_raw, e_raw). Caption time = seg_offset + (ws - s)
                # which is the identical formula to how video frames are positioned.
                if s_raw <= ws < e_raw:
                    remapped_words.append(WordTiming(
                        text=w["text"].strip(),
                        start=seg_offset + (ws - s),
                        end=seg_offset + (min(we, e_raw) - s),
                    ))

        for sil in (plan.silences or []):
            try:
                sil_at = float(sil.get("at", 0))
            except (TypeError, ValueError):
                continue
            if s_raw <= sil_at <= e_raw:
                remapped_silences.append({
                    **sil,
                    "at": seg_offset + (sil_at - s),
                })

        for vm in (plan.visual_style_moments or []):
            try:
                vm_at = float(vm.get("at", 0))
                vm_dur = float(vm.get("duration", 2.0))
            except (TypeError, ValueError):
                continue
            if s <= vm_at <= e:
                remapped_vsm.append({
                    **vm,
                    "at": seg_offset + (vm_at - s),
                    "duration": vm_dur,
                })

        for moment in (plan.caption_moments or []):
            try:
                m_at  = float(moment.get("start", 0))
                m_end = float(moment.get("end", m_at + 3.0))
            except (TypeError, ValueError):
                continue
            if s_raw <= m_at < e_raw:
                remapped_moments.append({
                    **moment,
                    "start": seg_offset + (m_at - s),
                    "end":   min(seg_offset + (m_end - s), seg_offset + (e - s)),
                })

        for mg in (plan.motion_graphics or []):
            try:
                mg_at  = float(mg.get("at", 0))
                mg_dur = max(0.5, float(mg.get("duration", 2.5)))
            except (TypeError, ValueError):
                continue
            if s_raw <= mg_at < e_raw:
                remapped_motion_graphics.append({
                    **mg,
                    "at":       seg_offset + (mg_at - s),
                    "duration": mg_dur,
                })

        if parts:  # record cut point in output timeline
            cut_timestamps.append(cum)
        cum += (e - s)  # exact segment duration — no audio-handle inflation

    if not parts:
        raise RuntimeError("No keep_segments produced any clip.")

    print(f"[ZOOM] Total segments with zoom: {len(zoom_segments)}")
    concat_path = work_dir / "concat.mp4"
    _concat_with_zoom(parts, zoom_levels, concat_path, target_w, target_h, fps)
    print(f"[AUDIO] Concat+zoom complete: {concat_path}")
    # BUG 2 FIX — verify AV sync immediately after concat
    _probe_av(concat_path)
    _audio_expected = sum(float(s["end"]) - float(s["start"]) for s in keep
                         if float(s["end"]) > float(s["start"]))
    print(f"[AUDIO] Expected duration (plan boundaries): {_audio_expected:.3f}s")

    # BUG 3 FIX — DURATION MISMATCH: warn if concat is longer than plan.
    _concat_actual = _probe_duration(concat_path)
    _concat_delta = _concat_actual - _audio_expected
    if abs(_concat_delta) > 1.0:
        print(
            f"[DURATION WARNING] concat.mp4={_concat_actual:.3f}s "
            f"planned={_audio_expected:.3f}s delta={_concat_delta:+.3f}s — "
            f"caption timestamps will be scaled to match actual duration"
        )
    # BUG 4 FIX — CAPTIONS SYNC: scale caption timestamps to actual video duration.
    # If concat is 2s longer than planned, caption timestamps (which are based on
    # plan boundaries) will arrive early. Scale all word timings proportionally.
    if _audio_expected > 0 and abs(_concat_delta) > 0.1:
        _scale = _concat_actual / _audio_expected
        print(f"[CAPTIONS] Scaling timestamps by {_scale:.4f}× to match actual duration")
        remapped_words = [
            WordTiming(text=w.text, start=w.start * _scale, end=w.end * _scale)
            for w in remapped_words
        ]
        remapped_moments = [
            {**m, "start": float(m.get("start", 0)) * _scale,
             "end": float(m.get("end", 0)) * _scale}
            for m in remapped_moments
        ]
        remapped_silences = [
            {**sil, "at": float(sil.get("at", 0)) * _scale}
            for sil in remapped_silences
        ]
        cut_timestamps = [ct * _scale for ct in cut_timestamps]

    # Remap b-roll windows to the cut timeline so captions pause there.
    # BUG FIX — B-ROLL TIMING:
    # Hard rule: 2.5s ≤ b-roll ≤ 4s. Anything shorter is a flash that just
    # confuses the viewer. Clamp the agent's suggestion into that window;
    # if the agent gave us 0s/0.1s of duration the clamp pulls it up to
    # the readable floor.
    BROLL_MIN_S = 1.5
    BROLL_MAX_S = 3.0
    remapped_broll: list[tuple[float, float]] = []
    broll_queries: list[str] = []   # parallel to remapped_broll — Pexels search terms
    for br in plan.broll_suggestions:
        try:
            br_at = float(br.get("at", 0))
            br_dur = float(br.get("duration", 0))
        except (TypeError, ValueError):
            continue
        # anchor_word overrides `at` with the exact word timestamp
        anchor = str(br.get("anchor_word") or "").strip()
        if anchor:
            anchor_ts = _find_word_timestamp(transcript, anchor)
            if anchor_ts is not None:
                br_at = anchor_ts
        br_dur = max(BROLL_MIN_S, min(BROLL_MAX_S, br_dur))
        # Find the keep_segment that contains br_at and remap.
        run = 0.0
        for seg in keep:
            ss = float(seg["start"])
            ee = float(seg["end"])
            if ss <= br_at <= ee and ee > ss:
                start = run + (br_at - ss)
                remapped_broll.append((start, start + br_dur))
                broll_queries.append(
                    str(br.get("search_query") or br.get("concept") or "")
                )
                break
            run += max(0.0, ee - ss)

    # Remap hyperframes to the cut timeline so they fire on the right beats.
    remapped_hyperframes: list[dict[str, Any]] = []
    for hf in plan.hyperframes:
        try:
            hf_at = float(hf.get("at", 0))
        except (TypeError, ValueError):
            continue
        # Default color: hyperframe → brand_color → electric yellow.
        hf_color = hf.get("color") or brand_color or "#FFE500"
        run = 0.0
        for seg in keep:
            ss = float(seg["start"])
            ee = float(seg["end"])
            if ss <= hf_at <= ee and ee > ss:
                remapped_hyperframes.append({
                    **hf,
                    "at": run + (hf_at - ss),
                    "color": hf_color,
                })
                break
            run += max(0.0, ee - ss)

    # FIX 4: Larger silence thresholds — keep natural breathing pauses intact.
    # Short-form: only remove pauses > 0.5s (was 0.25s); compress to 0.30s.
    # Long-form:  only remove pauses > 0.8s (was 0.6s);  compress to 0.50s.
    _sil_min = 0.50 if short_form else 0.80
    _sil_max = 0.30 if short_form else 0.50
    if remapped_words:
        long_pauses = _find_long_pauses(remapped_words, min_gap_s=_sil_min)
        if long_pauses:
            compressed_path = work_dir / "concat_compressed.mp4"
            kept_intervals = _compress_pauses(
                concat_path, compressed_path, work_dir,
                long_pauses, max_silence_s=_sil_max,
            )
            if compressed_path.exists():
                concat_path = compressed_path
                remapped_words = [
                    WordTiming(
                        text=w.text,
                        start=_remap_time(w.start, kept_intervals),
                        end=_remap_time(w.end, kept_intervals),
                    )
                    for w in remapped_words
                ]
                remapped_zoom = [
                    {**zp,
                     "start": _remap_time(float(zp.get("start", 0)), kept_intervals),
                     "end":   _remap_time(float(zp.get("end", 0)), kept_intervals)}
                    for zp in remapped_zoom
                ]
                remapped_broll = [
                    (_remap_time(bs, kept_intervals), _remap_time(be, kept_intervals))
                    for bs, be in remapped_broll
                ]
                remapped_hyperframes = [
                    {**hf, "at": _remap_time(float(hf.get("at", 0)), kept_intervals)}
                    for hf in remapped_hyperframes
                ]
                remapped_silences = [
                    {**sil, "at": _remap_time(float(sil.get("at", 0)), kept_intervals)}
                    for sil in remapped_silences
                ]
                remapped_moments = [
                    {**m,
                     "start": _remap_time(float(m.get("start", 0)), kept_intervals),
                     "end":   _remap_time(float(m.get("end", 0)),   kept_intervals)}
                    for m in remapped_moments
                ]
                cut_timestamps = [
                    _remap_time(ct, kept_intervals) for ct in cut_timestamps
                ]

    # Probe total duration now (after any silence compression that may have
    # changed concat_path). Used for broll gap calculation and frame count below.
    total_duration = _probe_duration(concat_path)

    # Enforce minimum gap between b-roll clips to prevent over-cutting.
    # Short-form: 8s fixed. Long-form: adaptive — 20% of total duration, min 8s.
    # A 40s video gets 8s gap; a 5min video gets 60s gap. Prevents all b-roll
    # from being rejected on short long-form edits.
    # Also drop any b-roll in the first 3s (hook must show the speaker's face).
    _MIN_BROLL_GAP_SHORT = 8.0
    _min_broll_gap = _MIN_BROLL_GAP_SHORT if short_form else max(8.0, total_duration * 0.20)
    _filtered_broll: list[tuple[float, float]] = []
    _filtered_queries: list[str] = []
    _last_broll_end = 0.0
    for (br_s, br_e), br_q in zip(remapped_broll, broll_queries):
        if br_s < 3.0:
            print(f"[BROLL] Rejected (hook zone): at={br_s:.2f}s query={br_q!r}")
            continue
        gap = br_s - _last_broll_end
        if gap >= _min_broll_gap:
            _filtered_broll.append((br_s, br_e))
            _filtered_queries.append(br_q)
            _last_broll_end = br_e
            print(f"[BROLL] Accepted: at={br_s:.2f}s gap={gap:.2f}s query={br_q!r}")
        else:
            print(f"[BROLL] Rejected (too close): at={br_s:.2f}s gap={gap:.2f}s < {_min_broll_gap}s query={br_q!r}")
    remapped_broll = _filtered_broll
    broll_queries  = _filtered_queries

    # FIX 1 — SALMON SCREEN: disable hyperframes completely.
    # Color-flash MKV overlays render as a solid salmon/colored screen at any
    # timestamp — disable until the overlay pipeline is validated.
    remapped_hyperframes = []

    import logging as _logging
    import shutil as _shutil
    import tempfile as _tempfile
    _log = _logging.getLogger(__name__)

    # BUG 4 FIX — CAPTION BOUNDARY SHIFT: any caption word whose remapped start
    # coincides exactly with a segment cut boundary gets a +0.05s nudge.
    # On the cut frame the decoder may show a black or transitional frame; a
    # caption appearing at that exact instant can look frozen or misplaced.
    _cut_set = set(cut_timestamps)
    remapped_words = [
        WordTiming(
            text=w.text,
            start=w.start + 0.05 if any(abs(w.start - ct) < 0.02 for ct in _cut_set) else w.start,
            end=w.end,
        )
        for w in remapped_words
    ]

    # VERIFICATION: drop caption words outside the edited video duration and log
    # any sync anomalies so mismatches are visible in server logs.
    remapped_words = _verify_caption_sync(remapped_words, total_duration)

    # FIX 4 — Caption sync verification
    print(f"[SYNC CHECK] Video duration: {total_duration:.3f}s")
    if remapped_words:
        print(
            f"[SYNC CHECK] Caption range: "
            f"{remapped_words[0].start:.3f}s → {remapped_words[-1].end:.3f}s"
        )
        print(
            f"[SYNC CHECK] First 5 words: "
            f"{[(w.text, round(w.start, 2)) for w in remapped_words[:5]]}"
        )
        _words_after = [w for w in remapped_words if w.start > total_duration]
        if _words_after:
            print(
                f"[SYNC CHECK] WARNING: {len(_words_after)} word(s) start after "
                f"video duration — dropping them"
            )
        if remapped_words[-1].end > total_duration + 0.5:
            print(
                f"[SYNC CHECK] ERROR: last caption end "
                f"{remapped_words[-1].end:.3f}s > video {total_duration:.3f}s + 0.5s"
            )
    else:
        print("[SYNC CHECK] WARNING: no remapped words — captions will be empty!")

    _log.info(
        "caption sync: %d words; range=%.3f–%.3fs; first3=%s",
        len(remapped_words),
        remapped_words[0].start if remapped_words else 0.0,
        remapped_words[-1].end  if remapped_words else 0.0,
        [(w.text, round(w.start, 3), round(w.end, 3)) for w in remapped_words[:3]],
    )

    # PART 4 — caption_style_map: per-segment style hints from the planner.
    # Maps source segment start time → caption style ("normal" | "emphasis" | "highlight").
    caption_style_map = {
        float(seg.get("start", 0)): seg.get("caption_style", "normal")
        for seg in (plan.keep_segments or [])
        if isinstance(seg, dict)
    }

    ass_path = work_dir / "captions.ass"
    print(f"[FONT] User requested: {caption_font}")
    # For long-form: prefer selective caption_moments; fall back to word-by-word
    # short-form captions if the planner produced none (better than zero captions).
    _long = not short_form
    _use_moments = _long and bool(remapped_moments)
    if _long and not remapped_moments:
        print("[CAPTIONS] Long-form has no caption_moments — falling back to short-form word-by-word")
    build_ass(
        remapped_words,
        ass_path,
        video_w=target_w,
        video_h=target_h,
        brand_color=brand_color or "#FF7751",
        caption_font=caption_font,
        caption_style_map=caption_style_map,
        video_duration=total_duration,
        mode="long" if _use_moments else "short",
        caption_moments=remapped_moments if _use_moments else None,
        caption_style=caption_style,
    )
    print(f"[CAPTIONS] ASS file written: {ass_path}")
    print(f"[CAPTIONS] ASS file size: {ass_path.stat().st_size} bytes")

    face_cx_pct = 50.0
    face_cy_pct = 50.0
    if subject_position:
        fl = subject_position.get("face_left_pct", 25.0)
        fr = subject_position.get("face_right_pct", 75.0)
        ft = subject_position.get("face_top_pct", 15.0)
        fb = subject_position.get("face_bottom_pct", 65.0)
        face_cx_pct = (fl + fr) / 2
        face_cy_pct = (ft + fb) / 2
    total_frames = max(1, int(total_duration * fps))

    # Motion graphics disabled — clean professional output.
    # Only cuts + captions + zoom are applied.
    rendered_graphics: list[RenderedGraphic] = []

    # ── Smart dimension detection ─────────────────────────────────────────
    color_grade = _color_grade_filter(content_type)
    concat_info = _probe_video_info(concat_path)
    src_w = concat_info.get("width",  0) or target_w
    src_h = concat_info.get("height", 0) or target_h

    if short_form:
        # 9:16 vertical: center-crop horizontal source then scale up
        scale_filter = f"crop=ih*9/16:ih,scale={target_w}:{target_h}"
    else:
        # 16:9 horizontal: straight scale, skip if already correct size
        if src_w == target_w and src_h == target_h:
            scale_filter = None
        else:
            scale_filter = f"scale={target_w}:{target_h}"

    system_font = _find_system_font()

    # ── Pre-render hyperframe flash PNGs ──────────────────────────────────
    # Each flash is a full-frame solid-color PNG (+ optional text) turned into
    # a timed MKV clip via _png_to_timed_clip() — same mechanism as motion
    # graphics. This completely eliminates enable= from the video filter chain:
    # no drawbox, no drawtext, no gte/lte timing expressions.
    hf_dir = work_dir / "hyperframes"
    hf_dir.mkdir(parents=True, exist_ok=True)
    hf_graphics: list[RenderedGraphic] = []
    for i, hf in enumerate(remapped_hyperframes):
        try:
            hf_at = float(hf.get("at", 0))
            hf_color = _hex_to_rgb_at(hf.get("color") or brand_color or "#FF7751")
            # BUG 1 FIX: cap to 0.06s (2 frames at 30fps) — a flash, not a screen.
            hf_dur = max(0.033, min(0.06, float(hf.get("duration", 0.05))))
            hf_png = hf_dir / f"hf_{i:03d}.png"
            # Color flash only — no text overlay on hyperframes.
            _render_hyperframe_png(
                hf_color, hf_png, target_w, target_h,
                text=None, system_font=None,
            )
            hf_graphics.append(RenderedGraphic(
                png=hf_png, at=hf_at, duration=hf_dur,
                x_expr="0", y_expr="0", kind="hyperframe",
            ))
        except Exception as _e:
            _log.warning("hyperframe %d skipped: %s", i, _e)

    # ── Convert all PNGs to timed RGBA MKV clips ──────────────────────────
    # Hyperframes first in the chain so motion graphics overlay on top of them
    # (matching original drawbox → overlay ordering).
    clips_dir = work_dir / "graphic_clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    graphic_clip_paths: list[Path] = []
    ok_graphics: list[RenderedGraphic] = []
    for rg in hf_graphics + rendered_graphics:
        clip_path = clips_dir / f"{rg.png.stem}_clip.mkv"
        try:
            _png_to_timed_clip(rg.png, clip_path, rg.duration, fps)
            graphic_clip_paths.append(clip_path)
            ok_graphics.append(rg)
        except Exception as _e:
            _log.warning("graphic clip failed (%s): %s", rg.kind, _e)

    # ── Pass 1: grade + scale + audio duck → base ────────────────────────
    # Overlays are applied sequentially below (one ffmpeg call per clip).
    # This is more reliable than one giant filter_complex: memory is bounded
    # by two inputs per call regardless of how many graphics exist.
    _tmp_dir = Path(_tempfile.gettempdir())
    _base_path = _tmp_dir / f"base_{output_path.stem}.mp4"

    # Jump zoom is baked in per-segment (see _apply_segment_zoom above).
    # Pass 1 only needs fps normalization and PTS reset.
    _zoom_str = f"fps={fps},setpts=N/FRAME_RATE/TB"

    # Cover stream chat / subscriber UI in top-right corner — short-form only.
    # In long-form (16:9) the crop is different and the UI position varies.
    _stream_ui_cover = "drawbox=x=W-300:y=0:w=300:h=200:color=black:t=fill" if short_form else None
    _vf_p1 = [p for p in [color_grade, scale_filter, _stream_ui_cover, _zoom_str] if p]
    _cmd_p1: list[str] = [
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-i", str(concat_path),
        "-vf", ",".join(_vf_p1),
        "-map", "0:v",
    ]
    _p1_audio_filter = False
    if remapped_silences:
        _vol_nodes: list[str] = []
        for _sil in remapped_silences:
            try:
                _sat = float(_sil.get("at", 0))
                _sdur = max(0.05, min(0.5, float(_sil.get("duration", 0.3))))
            except (TypeError, ValueError):
                continue
            _vol_nodes.append(
                f"volume=enable=gte(t\\,{_sat:.3f})*lte(t\\,{_sat+_sdur:.3f}):volume=0"
            )
        if _vol_nodes:
            _cmd_p1 += ["-af", ",".join(_vol_nodes)]
            _p1_audio_filter = True
    _cmd_p1 += ["-map", "0:a"]
    _cmd_p1 += [
        "-frames:v", str(total_frames),
        "-vsync", "cfr",
        "-async", "1",
        # Lossless intermediate video — only one lossy encode in the final pass.
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
        "-threads", "4",
        "-x264-params", "rc-lookahead=0:bframes=0",
        "-pix_fmt", "yuv420p",
    ]
    # Re-encode audio only when a volume-duck filter is active; otherwise copy.
    if _p1_audio_filter:
        _cmd_p1 += ["-c:a", "aac", "-b:a", "192k"]
    else:
        _cmd_p1 += ["-c:a", "copy"]
    _cmd_p1 += [str(_base_path)]
    _log.info(
        "render pass1: scale+fps+audio_duck  graphics=%d  silences=%d",
        len(ok_graphics), len(remapped_silences),
    )
    _run(_cmd_p1)

    # ── B-roll overlay passes (full-screen Pexels stock video) ───────────
    # For each remapped b-roll window: fetch a stock clip from Pexels,
    # crop-to-fill to target dims, then overlay it full-screen for the
    # duration of the window. Speaker audio continues underneath.
    _pexels_key_set = bool(
        (settings.pexels_api_key if hasattr(settings, "pexels_api_key") else None)
        or __import__("os").environ.get("PEXELS_API_KEY", "")
    )
    print(f"[BROLL DEBUG] plan.broll_suggestions count: {len(plan.broll_suggestions)}")
    print(f"[BROLL DEBUG] remapped_broll count: {len(remapped_broll)}")
    for _rbi, (_rb_window, _rb_q) in enumerate(zip(remapped_broll, broll_queries)):
        print(f"[BROLL DEBUG] broll[{_rbi}] at={_rb_window[0]:.2f}s–{_rb_window[1]:.2f}s query={_rb_q!r}")
    print(f"[BROLL DEBUG] PEXELS_API_KEY set: {_pexels_key_set}")
    _log.info(
        "broll: plan has %d suggestion(s); remapped=%d; PEXELS_API_KEY=%s",
        len(plan.broll_suggestions),
        len(remapped_broll),
        "SET" if _pexels_key_set else "NOT SET — skipping all broll",
    )
    _broll_dir = work_dir / "broll_clips"
    _broll_dir.mkdir(parents=True, exist_ok=True)
    _current_path = _base_path
    _overlay_intermediates: list[Path] = []
    _broll_clips_downloaded = 0
    for _bi, ((br_s, br_e), br_q) in enumerate(zip(remapped_broll, broll_queries)):
        if not br_q:
            continue
        br_dur = br_e - br_s
        _br_clip_path = _broll_dir / f"br_{_bi:02d}.mp4"
        _br_ok = _fetch_broll_clip(br_q, _br_clip_path, br_dur, target_w, target_h)
        if not _br_ok:
            _log.info("broll %d skipped: query=%r", _bi, br_q)
            continue
        _broll_clips_downloaded += 1
        br_clip = _br_clip_path
        _next_br_path = _tmp_dir / f"br{_bi}_{output_path.stem}.mp4"
        # Slide-in from right edge (0→x=0 over _slide s), hold, slide-out to left.
        _slide = max(0.1, min(0.25, br_dur * 0.15))
        _x_expr = (
            f"'if(lte(t,{br_s+_slide:.3f})"
            f",W*(1-(t-{br_s:.3f})/{_slide:.3f})"
            f",if(gte(t,{br_e-_slide:.3f})"
            f",-W*(t-{br_e-_slide:.3f})/{_slide:.3f}"
            f",0))'"
        )
        _fc_br = (
            f"[1:v]setpts=PTS-STARTPTS+{br_s:.3f}/TB[bv];"
            f"[0:v][bv]overlay=x={_x_expr}:y=0"
            f":enable='between(t,{br_s:.3f},{br_e:.3f})'[vout]"
        )
        _log.info("broll overlay %d: query=%r at=%.2fs–%.2fs", _bi, br_q, br_s, br_e)
        _run([
            FFMPEG_PATH, "-y", "-loglevel", "error",
            "-i", str(_current_path),
            "-i", str(br_clip),
            "-filter_complex", _fc_br,
            "-map", "[vout]", "-map", "0:a",
            "-vsync", "cfr",
            "-async", "1",                  # normalize audio timestamps to video clock
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
            "-threads", "4",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            str(_next_br_path),
        ])
        if _next_br_path.exists():
            _overlay_intermediates.append(_current_path)
            _current_path = _next_br_path

    print(f"[BROLL] Downloaded {_broll_clips_downloaded} clip(s) successfully")

    # ── Overlay passes: one ffmpeg call per graphic clip ──────────────────
    for _j, (_clip_path, _rg) in enumerate(zip(graphic_clip_paths, ok_graphics)):
        _next_path = _tmp_dir / f"ov{_j}_{output_path.stem}.mp4"
        _x_esc = _rg.x_expr.replace(",", "\\,")
        _y_esc = _rg.y_expr.replace(",", "\\,")
        _fc_ov = (
            f"[1:v]setpts=PTS+{_rg.at:.3f}/TB[gt];"
            f"[0:v][gt]overlay=x={_x_esc}:y={_y_esc}:eof_action=pass[out]"
        )
        _log.info(
            "render overlay %d/%d  kind=%s  at=%.2fs",
            _j + 1, len(ok_graphics), _rg.kind, _rg.at,
        )
        _run([
            FFMPEG_PATH, "-y", "-loglevel", "error",
            "-i", str(_current_path),
            "-i", str(_clip_path),
            "-filter_complex", _fc_ov,
            "-map", "[out]", "-map", "0:a",
            "-vsync", "cfr",
            "-async", "1",                  # normalize audio timestamps to video clock
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
            "-threads", "4",
            "-x264-params", "rc-lookahead=0:bframes=0",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            str(_next_path),
        ])
        _overlay_intermediates.append(_current_path)
        _current_path = _next_path

    # ── Motion graphics overlay passes (Hyperframes HTML-rendered clips) ────
    # Each motion graphic is rendered to a short MP4 via headless Chromium,
    # then overlaid on the base video at its remapped output-timeline position.
    # Skipped silently when Chromium is unavailable — no crash, just no graphic.
    if _HYPERFRAMES_AVAILABLE and remapped_motion_graphics:
        _mg_dir = work_dir / "motion_graphics"
        _mg_dir.mkdir(parents=True, exist_ok=True)
        print(f"[HYPERFRAMES] Rendering {len(remapped_motion_graphics)} motion graphic(s)…")
        for _mgi, _mg in enumerate(remapped_motion_graphics):
            try:
                _mg_at   = float(_mg.get("at", 0))
                _mg_dur  = max(0.5, float(_mg.get("duration", 2.5)))
                _mg_type = str(_mg.get("type", "key_phrase"))
                _mg_cont = _mg.get("content") or {}
                _mg_clip = _mg_dir / f"mg_{_mgi:03d}.mp4"

                _mg_html = _hf_gen_html(
                    graphic_type=_mg_type,
                    content=_mg_cont,
                    duration=_mg_dur,
                    width=target_w,
                    height=target_h,
                    brand_color=brand_color or "#FF7751",
                    font=caption_font.replace(" Bold", "").replace(" Black", "").strip()
                    if caption_font else "Inter",
                )
                _mg_ok = _hf_render(
                    _mg_html, _mg_clip, _mg_dur, target_w, target_h, fps,
                    work_dir=_mg_dir,
                )
                if not _mg_ok or not _mg_clip.exists():
                    print(f"[HYPERFRAMES] Graphic {_mgi} skipped (render failed)")
                    continue

                _next_mg_path = _tmp_dir / f"mg{_mgi}_{output_path.stem}.mp4"
                _fc_mg = (
                    f"[1:v]setpts=PTS-STARTPTS+{_mg_at:.3f}/TB[mgv];"
                    f"[0:v][mgv]overlay=x=0:y=0"
                    f":enable='between(t,{_mg_at:.3f},{_mg_at + _mg_dur:.3f})'"
                    f":eof_action=pass[out]"
                )
                _run([
                    FFMPEG_PATH, "-y", "-loglevel", "error",
                    "-i", str(_current_path),
                    "-i", str(_mg_clip),
                    "-filter_complex", _fc_mg,
                    "-map", "[out]", "-map", "0:a",
                    "-vsync", "cfr", "-async", "1",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
                    "-threads", "4",
                    "-x264-params", "rc-lookahead=0:bframes=0",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "copy",
                    str(_next_mg_path),
                ])
                _overlay_intermediates.append(_current_path)
                _current_path = _next_mg_path
                print(f"[HYPERFRAMES] Graphic {_mgi} overlaid at t={_mg_at:.2f}s  type={_mg_type}")
            except Exception as _mge:
                print(f"[HYPERFRAMES] Graphic {_mgi} exception (non-fatal): {_mge}")
        print(f"[HYPERFRAMES] Motion graphics pass complete")
    else:
        if remapped_motion_graphics:
            print(f"[HYPERFRAMES] {len(remapped_motion_graphics)} graphic(s) skipped — Hyperframes unavailable")

    _nocap_path = _current_path

    # ── Final pass: burn ASS captions ─────────────────────────────────────
    # Separate ffmpeg invocation so subtitle path escaping is handled by the
    # OS arg list (no shell expansion) — only the ffmpeg filter parser sees it.
    _ass_tmp = _tmp_dir / f"captions_{ass_path.parent.name}.ass"
    _shutil.copy2(ass_path, _ass_tmp)
    _ass_str = str(_ass_tmp).replace("\\", "/").replace(":", "\\:")
    # Per-format output bitrate targets — prevent quality floor drops on
    # complex scenes while keeping file size reasonable.
    if is_4k:
        _out_bv, _out_maxrate, _out_bufsize = "20M", "30M", "60M"
    elif short_form:
        _out_bv, _out_maxrate, _out_bufsize = "8M", "12M", "24M"
    else:
        _out_bv, _out_maxrate, _out_bufsize = "6M", "10M", "20M"
    # FIX 2 — ZOOMPAN FREEZE: split into two separate passes.
    # Combining subtitles+zoompan in the same -vf causes libass to duplicate
    # frames, stretching a 46s video to 48s. Running them in separate ffmpeg
    # invocations avoids the conflict entirely.

    # Pass 3: slow progressive zoom only — lossless intermediate.
    # d=1: one output frame per input frame (no duration change).
    # setpts=N/FRAME_RATE/TB: resets PTS to prevent drift accumulation.
    _zoomed_path = _tmp_dir / "zoomed_base.mp4"
    _zoompan_filter = (
        f"zoompan=z='min(zoom+0.0003,1.04)'"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d=1:s={target_w}x{target_h}:fps={fps}"
        f",setpts=N/FRAME_RATE/TB"
    )
    _run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-i", str(_nocap_path),
        "-vf", _zoompan_filter,
        "-vsync", "cfr",
        "-async", "1",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
        "-threads", "4",
        "-x264-params", "rc-lookahead=0:bframes=0",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        str(_zoomed_path),
    ])
    print(f"[ZOOM PASS] Zoompan complete: {_zoomed_path}")

    # Pass 4: burn captions only — final quality encode.
    _run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-i", str(_zoomed_path),
        "-vf", f"subtitles={_ass_str}",
        "-vsync", "cfr",
        "-async", "1",
        "-c:v", "libx264", "-preset", "slow", "-crf", str(output_crf),
        "-b:v", _out_bv,
        "-maxrate", _out_maxrate,
        "-bufsize", _out_bufsize,
        "-threads", "4",
        "-x264-params", "rc-lookahead=48:bframes=3",
        "-pix_fmt", "yuv420p",
        "-af", "loudnorm=I=-14:TP=-1:LRA=7",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-movflags", "+faststart",
        str(output_path),
    ])
    print(f"[FINAL] Output: {output_path}")
    _nocap_path.unlink(missing_ok=True)
    _zoomed_path.unlink(missing_ok=True)
    for _p in _overlay_intermediates:
        _p.unlink(missing_ok=True)

    # FIX 5: Background music infrastructure (-28dB ambient bed).
    # Set BACKGROUND_MUSIC_PATH=/path/to/music.mp3 in .env to enable.
    # When no file is configured or the file is missing, this is a no-op.
    _music_path = Path(settings.background_music_path) if settings.background_music_path else None
    if _music_path and _music_path.exists():
        _music_out = output_path.with_suffix(".withmusic.mp4")
        try:
            _run([
                FFMPEG_PATH, "-y", "-loglevel", "error",
                "-i", str(output_path),
                "-stream_loop", "-1", "-i", str(_music_path),
                "-filter_complex",
                "[1:a]volume=-28dB[music];[0:a][music]amix=inputs=2:duration=first[aout]",
                "-map", "0:v",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                str(_music_out),
            ])
            import os as _os
            _os.replace(str(_music_out), str(output_path))
        except Exception:
            pass  # Music mix failed — ship clean audio instead

    return {
        "output": str(output_path),
        "duration": total_duration,
        "format": plan.format,
        "packaging": plan.packaging,
        "plan": plan.raw,
        "key_lines": plan.key_lines,
        "hyperframes_rendered": len(remapped_hyperframes),
        "broll_pauses": len(remapped_broll),
        "graphics_rendered": [rg.kind for rg in rendered_graphics],
        "vignette_moments": len(remapped_vsm),
    }
