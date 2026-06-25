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
  5. Single re-encode pass: scale/crop -> burn ASS subtitles LAST
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
from app.engine.hyperframes_engine import (
    CHROMA_KEY_HEX,
    generate_composition_html,
    generate_custom_motion_graphic,
    render_composition_to_video,
    render_slide_to_video,
    render_with_hyperframes,
)


SHORT_PAD_S    = 0.12   # 120ms -- word-safe start/end buffer
LONG_PAD_S     = 0.20   # 200ms -- cinematic breathing room
AUDIO_FADE_S   = 0.05   # 50ms -- anti-pop fade at every segment boundary
AUDIO_HANDLE_S = 0.08   # 80ms audio handle kept before/after each cut edge



def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0:
        return
    stderr = proc.stderr[-2000:] or "(empty -- process produced no stderr)"
    if proc.returncode < 0:
        sig = -proc.returncode
        hint = ""
        if sig == 9:
            hint = (
                " -- SIGKILL. The kernel killed ffmpeg, almost always because "
                "the container ran out of memory. Try a shorter video, lower "
                "WHISPER_MODEL, or upgrade your hosting plan to give the "
                "encoder more headroom."
            )
        elif sig == 15:
            hint = " -- SIGTERM. Something asked ffmpeg to stop."
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
    - Source resolution is larger than the target (4K->1080p decode is expensive).
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
    stream-copy -- zero decode memory, near-zero CPU.

    Keyframe every 2 s (60 frames at 30 fps) so stream-copy cuts have ≤ 2 s
    of alignment error (acceptable -- the final re-encode corrects it).
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

    Stream-copy means zero decode memory -- we just copy the H.264 bitstream
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

    edge='start' -> word.start AFTER t where gap before word ≥ WORD_GAP_S.
    edge='end'   -> word.end   BEFORE t where gap after word ≥ WORD_GAP_S.
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
    "the", "a", "an", "this", "that", "these", "those", "my", "your",
    "his", "her", "its", "our", "their", "which", "because", "so", "but",
    "and", "when", "if", "as", "while", "since", "although", "where",
    "who", "what", "how", "whether", "though", "unless", "until", "after",
    "before", "is", "are", "was", "were", "will", "would", "can", "could",
    "should", "to", "of", "in", "on", "at", "for", "with", "by", "from",
    "into", "not", "no", "or", "nor",
])


def _is_sentence_boundary(end_t: float, transcript: dict[str, Any]) -> bool:
    """Check if end_t lands at a sentence boundary using segment-level text."""
    for seg in transcript.get("segments", []):
        seg_text = str(seg.get("text", ""))
        seg_end = float(seg.get("end", 0))
        if abs(seg_end - end_t) < 0.3 and seg_text.rstrip().endswith((".", "!", "?")):
            return True
        seg_words = seg.get("words", [])
        for wi, w in enumerate(seg_words):
            we = float(w.get("end", 0))
            if abs(we - end_t) < 0.15:
                pos = seg_text.find(str(w.get("text", "")))
                if pos >= 0:
                    after = seg_text[pos + len(str(w.get("text", ""))):pos + len(str(w.get("text", ""))) + 2]
                    if any(c in after for c in ".!?"):
                        return True
    return False


def _extend_for_semantic_completeness(
    end_t: float,
    transcript: dict[str, Any],
    src_duration: float,
    search_window: float = 3.0,
) -> float:
    """Extend a segment end if it cuts mid-sentence.

    Checks: (1) is the last word a dangling word (article, conjunction,
    preposition, auxiliary verb)? (2) does the cut point land at a
    sentence boundary (period/question/exclamation in segment text)?
    If mid-sentence, extends forward to the next sentence boundary or
    natural pause, capped at search_window seconds.
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

    if not last_text:
        return end_t

    needs_extension = last_text in _DANGLING_WORDS
    if not needs_extension and not _is_sentence_boundary(end_t, transcript):
        needs_extension = True

    if not needs_extension:
        return end_t

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
            if prev_word_end is not None and ws_t - prev_word_end >= 0.3:
                print(f"[BOUNDARY FIX] extended segment end {end_t:.3f} -> {prev_word_end:.3f} (pause)")
                return min(prev_word_end, src_duration)
            if _is_sentence_boundary(we, transcript):
                print(f"[BOUNDARY FIX] extended segment end {end_t:.3f} -> {we:.3f} (sentence end)")
                return min(we, src_duration)
            prev_word_end = we

    return end_t


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

    SYNC: -ss BEFORE -i with -accurate_seek (frame-accurate decode from keyframe).
    Duration via -t. Audio re-encoded to AAC so it aligns with the video trim
    point rather than copying from the keyframe boundary (which causes +0–3s drift).
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
        "-accurate_seek",
        "-i", str(src),
        "-t", f"{duration:.6f}",
        "-avoid_negative_ts", "make_zero",
        "-vf", vf,
        "-vsync", "cfr",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        str(dst),
    ])
    actual_dur = _probe_duration(dst)
    if abs(actual_dur - duration) > 0.5:
        print(
            f"[CUT WARNING] {dst.name}: expected={duration:.3f}s "
            f"actual={actual_dur:.3f}s delta={actual_dur - duration:+.3f}s"
        )


def _concat(parts: list[Path], dst: Path) -> None:
    list_path = dst.with_suffix(".txt")
    list_path.write_text("\n".join(f"file '{p.as_posix()}'" for p in parts))
    _run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-fflags", "+genpts",               # normalize PTS at segment boundaries
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-c", "copy",
        "-vsync", "cfr",
        str(dst),
    ])
    list_path.unlink(missing_ok=True)


def _probe_av(path: Path) -> tuple[float, float]:
    """Probe audio and video durations to detect AV sync drift.

    BUG 2 FIX -- AV SYNC: runs after _concat() to surface timestamp
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
                f"[AV PROBE] WARNING: AV SYNC MISMATCH -- delta={abs(v_dur-a_dur):.3f}s "
                f"(>{0.1:.1f}s threshold) in {path.name}"
            )
        return v_dur, a_dur
    except Exception as _e:
        print(f"[AV PROBE] probe failed for {path.name}: {_e}")
        return 0.0, 0.0


def _probe_av_durations(path: Path, label: str = "") -> tuple[float, float]:
    """Probe per-stream video/audio durations and log delta for AV-sync diagnostics.

    Used to bracket every encode pass (concat, zoompan, b-roll overlay, motion
    graphics overlay, final output) and pinpoint where video and audio
    durations diverge.
    """
    import json as _json
    try:
        result = subprocess.run(
            [FFPROBE_PATH, "-v", "quiet", "-print_format", "json",
             "-show_streams", str(path)],
            capture_output=True, text=True, timeout=20,
        )
        data = _json.loads(result.stdout) if result.stdout.strip() else {}
        v_dur = a_dur = 0.0
        for s in data.get("streams", []):
            if s.get("codec_type") == "video":
                v_dur = float(s.get("duration", 0) or 0)
            elif s.get("codec_type") == "audio":
                a_dur = float(s.get("duration", 0) or 0)
        print(f"[AV PROBE {label}] video={v_dur:.3f}s audio={a_dur:.3f}s delta={a_dur-v_dur:+.3f}s")
        return v_dur, a_dur
    except Exception as _e:
        print(f"[AV PROBE {label}] probe failed for {path.name}: {_e}")
        return 0.0, 0.0


def _concat_audio_xfade(parts: list[Path], dst: Path) -> None:
    """Concatenate segments with 50ms exponential audio crossfade at every cut.

    FIX 2: acrossfade smooths audio transitions completely -- no clicks or pops
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

MOMENTUM_ZOOM: dict[str, int] = {
    "hook":           150,
    "amplify":        140,
    "context":        110,
    "tension":        140,
    "tension_stack":  150,
    "story":          120,
    "realization":    135,
    "principle":      125,
    "payoff":         150,
    "emotional_end":  120,
    "default":        120,
}
MAX_ZOOM_MOMENTUM = 150


def _build_zoom_t_expr(
    zoom_entries: list[dict],
    default_zoom: float = 1.0,
) -> str:
    """Build an FFmpeg time-based expression for zoom factor Z(t).

    Uses `t` (seconds) instead of frame numbers. Returns a nested if()
    expression with cosine ease-in-out for drift, quadratic for punch_in.
    """
    if not zoom_entries:
        return str(default_zoom)

    sorted_entries = sorted(zoom_entries, key=lambda e: float(e.get("start", 0)))

    parts: list[str] = []
    for entry in sorted_entries:
        es = float(entry.get("start", 0))
        ee = float(entry.get("end", es + 0.1))
        zf = float(entry.get("from", default_zoom))
        zt = float(entry.get("to", zf))
        kind = str(entry.get("kind", "drift"))
        ed = max(0.001, ee - es)

        p = f"(t-{es:.4f})/{ed:.4f}"
        if kind == "punch_in" or kind == "pull_out":
            ease = f"{zf}+({zt}-{zf})*{p}*{p}"
        else:
            ease = f"{zf}+({zt}-{zf})*(1-cos({p}*PI))/2"

        parts.append(f"if(between(t\\,{es:.4f}\\,{ee:.4f})\\,{ease}")

    expr = ""
    for p in parts:
        expr += p + "\\,"
    expr += str(default_zoom)
    expr += ")" * len(parts)
    return expr


def _apply_animated_zoom(
    src: Path, dst: Path,
    zoom_entries: list[dict],
    seg_offset: float,
    target_w: int, target_h: int,
    fps: int, duration: float,
    default_zoom: float = 1.3,
) -> bool:
    """Apply animated zoom using scale+crop with time-varying expressions.

    Much faster than zoompan — operates on decoded video frames rather than
    re-reading source per frame. zoom_entries are in edit-timeline coords;
    seg_offset converts to per-segment local time.
    """
    dz = default_zoom / 100.0 if default_zoom > 10 else default_zoom

    local_entries = []
    for ze in zoom_entries:
        zs = float(ze.get("start", 0)) - seg_offset
        zend = float(ze.get("end", 0)) - seg_offset
        if zend <= 0 or zs >= duration:
            continue
        local_entries.append({
            **ze,
            "start": max(0, zs),
            "end": min(duration, zend),
        })

    z_expr = _build_zoom_t_expr(local_entries, dz)

    max_zoom = dz
    for e in local_entries:
        max_zoom = max(max_zoom, float(e.get("from", dz)), float(e.get("to", dz)))
    max_zoom = min(max_zoom + 0.05, 2.5)

    sw = int(target_w * max_zoom)
    sh = int(target_h * max_zoom)
    sw += sw % 2
    sh += sh % 2

    cw = target_w
    ch = target_h
    cx = f"(iw-{cw})*({z_expr}-1)/({max_zoom:.4f}-1)/2"
    cy = f"(ih-{ch})*({z_expr}-1)/({max_zoom:.4f}-1)/2"

    vf = (
        f"scale={sw}:{sh}:flags=bilinear,"
        f"crop={cw}:{ch}:{cx}:{cy},"
        f"setsar=1:1"
    )

    _run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-i", str(src),
        "-vf", vf,
        "-vsync", "cfr",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
        "-threads", "4",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        str(dst),
    ])
    return dst.exists()


def _zoom_filter_for_level(zoom_level: int, target_w: int, target_h: int) -> str | None:
    """Return an FFmpeg vf string for a jump zoom at the given level.

    Returns None for level 100 (full frame, no crop needed).
    For 130/150/170: scales up then center-crops back to target dimensions.
    Uses lanczos for quality -- these are still-frame crops, not animations.
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
    zoom_entries_per_seg: list[list[dict]] | None = None,
    seg_durations: list[float] | None = None,
) -> None:
    """Concat segments with per-segment zoom (animated or static fallback).

    If zoom_entries_per_seg is provided and a segment has entries, uses
    animated scale+crop zoom (smooth drift/punch_in interpolation).
    Otherwise falls back to the static jump-zoom crop.
    """
    import tempfile as _tf

    n = len(parts)
    zoomed_parts: list[Path] = []
    _tmp_zoomed: list[Path] = []

    for i in range(n):
        entries = (zoom_entries_per_seg[i] if zoom_entries_per_seg and i < len(zoom_entries_per_seg) else [])
        dur = (seg_durations[i] if seg_durations and i < len(seg_durations) else 0)
        has_animation = bool(entries) and dur > 0

        if has_animation:
            zoomed = Path(_tf.gettempdir()) / f"zoom_seg_{i}_{parts[i].stem}.mp4"
            ok = _apply_animated_zoom(
                parts[i], zoomed, entries, seg_offset=0.0,
                target_w=target_w, target_h=target_h,
                fps=fps, duration=dur,
                default_zoom=zoom_levels[i] / 100.0,
            )
            if ok and zoomed.exists():
                zoomed_parts.append(zoomed)
                _tmp_zoomed.append(zoomed)
                continue
        zoomed_parts.append(parts[i])

    # Concat all parts (with static zoom applied inline for non-animated segments)
    if n == 1:
        p = zoomed_parts[0]
        is_animated = p != parts[0]
        if is_animated:
            vf = f"fps={fps},setpts=N/FRAME_RATE/TB"
        else:
            zoom_f = _zoom_filter_for_level(zoom_levels[0], target_w, target_h)
            vf = (
                f"{zoom_f},setsar=1:1,fps={fps},setpts=N/FRAME_RATE/TB"
                if zoom_f
                else f"setsar=1:1,fps={fps},setpts=N/FRAME_RATE/TB"
            )
        _run([
            FFMPEG_PATH, "-y", "-loglevel", "error",
            "-fflags", "+genpts", "-i", str(p),
            "-vf", vf,
            "-vsync", "cfr",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
            "-threads", "2",
            "-x264-params", "rc-lookahead=0:bframes=0:ref=1:no-mbtree=1",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            str(dst),
        ])
    else:
        inputs: list[str] = []
        for p in zoomed_parts:
            inputs += ["-fflags", "+genpts", "-i", str(p)]

        fc_parts: list[str] = []
        for i in range(n):
            is_animated = zoomed_parts[i] != parts[i]
            if is_animated:
                vf_chain = f"setsar=1:1,fps={fps},setpts=N/FRAME_RATE/TB"
            else:
                zoom_f = _zoom_filter_for_level(zoom_levels[i], target_w, target_h)
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
            "-vsync", "cfr",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
            "-threads", "4",
            "-x264-params", "rc-lookahead=0:bframes=0",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            str(dst),
        ])

    for tmp in _tmp_zoomed:
        tmp.unlink(missing_ok=True)


def _reencode_clean(src: Path, dst: Path, fps: int) -> None:
    """Re-encode a segment to force a clean PTS-zero baseline.

    After accurate-seek extraction, PTS may not start at exactly 0 even with
    avoid_negative_ts. Reordered segments (hook at t=35s played first) arrive
    with intact container PTS that the concat filter cannot normalise across
    streams. A full re-encode with setpts=PTS-STARTPTS guarantees every segment
    handed to _concat_with_zoom starts at t=0 in both video and audio tracks.
    """
    _run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-i", str(src),
        "-vf", f"setpts=PTS-STARTPTS,fps={fps}",
        "-af", "asetpts=PTS-STARTPTS",
        "-vsync", "cfr",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        str(dst),
    ])


def _trim_to_audio(src: Path, dst: Path, audio_dur: float) -> None:
    """Trim src to audio_dur seconds using stream-copy (no re-encode).

    Used to remove the trailing freeze frames that appear when the video
    stream runs slightly longer than the audio stream after concat.
    """
    _run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-i", str(src),
        "-t", f"{audio_dur:.6f}",
        "-c", "copy",
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

    Generates the PNG via ffmpeg's lavfi color source -- no enable= needed.
    The resulting PNG is converted to a timed MKV clip by _png_to_timed_clip()
    and overlaid with setpts+eof_action=pass, completely avoiding any
    enable= expression in the filter_complex.

    color_str is the output of _hex_to_rgb_at() e.g. '0xFF7751@1.0'.
    We strip the @opacity suffix since lavfi color=c= doesn't accept it.
    """
    lavfi_color = color_str.split("@")[0]   # '0xFF7751@1.0' -> '0xFF7751'
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

    Uses the PNG video codec in a Matroska (.mkv) container -- the only
    widely-available combination that preserves a full RGBA alpha channel
    for transparent overlay compositing.

    Clip timestamps start at 0; the caller uses setpts=PTS+start/TB in
    the overlay filter_complex to shift it to the correct position in the
    edit timeline.  No enable= expression is needed: the clip simply
    doesn't exist outside its window, so the overlay falls through to the
    base video automatically (eof_action=pass).

    fps matches the project fps (30).  With 48 GB RAM there is no longer
    any reason to cap at 1 fps -- the full 30-fps clip is used so the
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
      [0:v] -> grade+scale+zoompan -> overlay per timed clip -> [vout_label]

    Timed clips (indices 1..N) cover both hyperframe flashes and motion
    graphics. They are positioned via setpts=PTS+at/TB so no enable=
    expression is needed anywhere in the video filter chain.

    Audio chain (when silences exist):
      [0:a] -> volume-duck chain -> [aout]
      Volume enable= uses \\, (backslash-comma) escaping -- the only
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
    # No enable= expression is needed at all -- the clip simply doesn't exist
    # outside its window, so the overlay falls through to the base video.
    #
    # COMMA ESCAPING: x_expr / y_expr from graphics.py use plain commas inside
    # function calls like max(a,b), if(c,a,b), lt(a,b).  In a filter_complex
    # option value, an unescaped comma is a filter-chain separator -- FFmpeg would
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
    """Disabled -- returns empty string so no color/grade filter is applied."""
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
        print("[BROLL] No PEXELS_API_KEY -- skipping")
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

        # BUG 3 FIX -- minimum duration: only use videos >= 4s so we can trim
        # cleanly and still have quality frames throughout the b-roll window.
        videos = [v for v in all_videos if v.get("duration", 0) >= 4]
        if not videos:
            print(f"[BROLL] All {len(all_videos)} videos too short (<4s) -- skipping")
            return False
        print(f"[BROLL] {len(videos)} video(s) pass the >=4s duration filter")

        # BUG 3 FIX -- quality filter: only download HD or UHD files.
        # SD files are visibly blurry at 1080p -- they degrade the output.
        # Priority: portrait UHD (1080×1920) -> portrait HD -> any HD -> skip.
        video = videos[0]
        files = video.get("video_files", [])
        print(f"[BROLL] Video files available: {[(f.get('width'), f.get('height'), f.get('quality')) for f in files]}")

        preferred_files = [f for f in files if f.get("quality") in ("uhd", "hd")]
        if not preferred_files:
            print(f"[BROLL] No HD/UHD files for query {query!r} -- skipping (SD only)")
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
            print(f"[BROLL] No portrait files -- using best available orientation")

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
        print(f"[BROLL] Downloaded {tmp_path.stat().st_size} bytes -> {tmp_path.name}")

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

    Preserves the planner's intended edit order -- do NOT sort by start time.
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


def _merge_short_segments(segments: list[dict], min_duration: float = 2.0) -> list[dict]:
    """Merge segments shorter than min_duration with the next segment.

    Short segments cause zoompan frame-stretching artifacts and look like
    glitches. Merging with the next segment preserves the content while
    giving zoompan enough frames to operate correctly.
    Preserves edit order -- does NOT sort by source timestamp.
    """
    if not segments:
        return segments
    merged: list[dict] = []
    i = 0
    while i < len(segments):
        seg = dict(segments[i])
        dur = float(seg["end"]) - float(seg["start"])
        if dur < min_duration and i + 1 < len(segments):
            next_seg = segments[i + 1]
            merged_dur = float(next_seg["end"]) - float(seg["start"])
            print(f"[MERGE] Segment {i} too short ({dur:.2f}s < {min_duration:.1f}s) -- merging with next -> {merged_dur:.2f}s")
            seg["end"] = next_seg["end"]
            # Keep current segment's beat/zoom metadata
            merged.append(seg)
            i += 2  # skip next (already merged)
        else:
            merged.append(seg)
            i += 1
    return merged


# Short function words repeat constantly in fluent speech without being a
# transcription stutter -- never trim on these alone (BUG 1).
_BOUNDARY_COMMON_WORDS = {
    "i", "a", "the", "to", "and", "but", "so", "that", "it", "is", "of",
    "in", "on", "for", "you", "we", "they", "he", "she", "this", "with",
}


def _fix_word_boundaries(segments: list[dict], all_words: list[dict]) -> list[dict]:
    """Trim a segment's end when it ends with a duplicate of how the next
    segment starts.

    Reordered/adjacent segments can land on the same repeated word(s) (e.g.
    "...I wanted I wanted to show support" split across a junction), which
    plays as an audible repeat. Trimming segment N back removes the dupe
    while leaving segment N+1 (and its own boundary) untouched.

    The ±0.5s/+0.2s word-matching window mirrors the tolerance used for
    caption remapping (`words_in_range`), so boundary words that snapping
    pushed slightly outside [start, end) are still considered.

    Checks (in order):
      1. Single-word duplicate -- only for substantive words (not common
         function words, which coincidentally repeat in normal speech).
      2. Two-word duplicate -- "I wanted" / "I wanted ..." -- a strong signal
         of an actual repeat even when the single-word check doesn't fire
         (e.g. last word of N is "wanted" but first word of N+1 is "I").
    """
    for i in range(len(segments) - 1):
        seg_a = segments[i]
        seg_b = segments[i + 1]

        words_a = [w for w in all_words if float(seg_a["start"]) - 0.5 <= float(w["start"]) < float(seg_a["end"]) + 0.2]
        words_b = [w for w in all_words if float(seg_b["start"]) - 0.5 <= float(w["start"]) < float(seg_b["end"]) + 0.2]

        if not words_a or not words_b:
            continue

        # ── Single-word duplicate check (exact match, non-common words) ───
        last_word_a = str(words_a[-1]["text"]).strip().lower().rstrip(".,!?;:")
        first_word_b = str(words_b[0]["text"]).strip().lower().rstrip(".,!?;:")

        if (
            last_word_a
            and last_word_a == first_word_b
            and last_word_a not in _BOUNDARY_COMMON_WORDS
        ):
            print(f"[BOUNDARY FIX] Single duplicate '{last_word_a}' seg {i}/{i+1}")
            if len(words_a) >= 2:
                segments[i] = {**seg_a, "end": float(words_a[-2]["end"]) + 0.05}
            continue

        # ── Two-word duplicate check ──────────────────────────────────────
        if len(words_a) >= 2 and len(words_b) >= 2:
            last_two = " ".join(str(w["text"]).strip().lower().rstrip(".,!?;:") for w in words_a[-2:])
            first_two = " ".join(str(w["text"]).strip().lower().rstrip(".,!?;:") for w in words_b[:2])
            if last_two == first_two and len(last_two) > 3:
                print(f"[BOUNDARY FIX] Two-word duplicate '{last_two}' seg {i}/{i+1}")
                if len(words_a) >= 3:
                    segments[i] = {**seg_a, "end": float(words_a[-3]["end"]) + 0.05}

    return segments


def _verify_caption_sync(
    words: list["WordTiming"],
    edited_duration: float,
) -> list["WordTiming"]:
    """Drop caption words outside the edited video's duration and log anomalies.

    Called just before build_ass() to prevent captions from appearing at
    times that don't exist in the final video -- which looks like frozen or
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
            # Clamp rather than drop -- the word starts inside the video.
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


def _render_hyperframes(
    src: Path,
    transcript: dict[str, Any],
    plan: EditPlan,
    work_dir: Path,
    output_path: Path,
    *,
    brand_color: str = "#FF7751",
    content_type: str = "coaching",
    editing_style: str = "viral",
) -> dict[str, Any]:
    """Full HyperFrames pipeline: pre-trim -> storyboard -> compose -> render."""
    from app.engine.pretrim import pretrim
    from app.engine.storyboard import generate_storyboard
    from app.engine.compose import compose
    from app.engine.hyperframes_engine import _render_with_hyperframes_cli

    _health_check(src)
    print("[RENDER] Using HyperFrames pipeline")

    # Dump plan diagnostics for debugging
    _n_keep = len(plan.keep_segments or [])
    _n_zoom = len(plan.zoom_plan or [])
    _n_words_src = sum(len(s.get("words", [])) for s in transcript.get("segments", []))
    print(f"[HF] Plan: {_n_keep} keep_segments, {_n_zoom} zoom_plan entries, {_n_words_src} source words")

    # Stage 1: Pre-trim
    print("[HF] Stage 1: Pre-trimming source video...")
    trimmed, timing_map = pretrim(src, transcript, plan, work_dir)
    print(f"[HF] Trimmed: {timing_map.output_duration:.1f}s, {len(timing_map.remapped_words)} words")
    print(f"[HF] Source intervals: {len(timing_map.source_intervals)}, compressed: {timing_map.compressed_intervals is not None}")

    # Remap zoom_plan entries to trimmed timeline
    remapped_zoom = []
    for zp in plan.zoom_plan:
        zs = float(zp.get("start", 0))
        ze = float(zp.get("end", zs))
        out_s = timing_map.source_to_output(zs)
        out_e = timing_map.source_to_output(ze)
        if out_e > out_s:
            remapped_zoom.append({
                **zp,
                "start": round(out_s, 4),
                "end": round(out_e, 4),
            })
    print(f"[HF] Remapped {len(remapped_zoom)} zoom entries to trimmed timeline")

    # Stage 2: Storyboard
    print("[HF] Stage 2: Generating storyboard...")
    storyboard = generate_storyboard(
        trimmed_duration=timing_map.output_duration,
        remapped_words=timing_map.remapped_words,
        transcript_segments=transcript.get("segments", []),
        script_structure=plan.script_structure or [],
        keep_segments=plan.keep_segments or [],
        key_lines=plan.key_lines or [],
        caption_emphasis_words=plan.caption_emphasis_words or [],
        word_categories=plan.word_categories or {},
        brand_color=brand_color,
        content_type=content_type,
        editing_style=editing_style,
        format_hint=plan.format,
        timing_map=timing_map,
    )
    n_graphic = sum(1 for c in storyboard.get("cards", []) if c.get("type") != "caption")
    n_caption = sum(1 for c in storyboard.get("cards", []) if c.get("type") == "caption")
    print(f"[HF] Storyboard: {n_graphic} graphic + {n_caption} caption cards")

    # Stage 3: Compose
    print("[HF] Stage 3: Assembling HyperFrames composition...")
    project_dir = compose(
        storyboard=storyboard,
        trimmed_video=trimmed,
        work_dir=work_dir,
        zoom_entries=remapped_zoom,
    )

    # Stage 4: Render via HyperFrames CLI
    print("[HF] Stage 4: Rendering via HyperFrames CLI...")
    import os as _os
    env = _os.environ.copy()
    env["DISPLAY"] = env.get("DISPLAY", ":99")
    fps = storyboard["composition"]["fps"]
    public_dir = project_dir / "public"

    proc = subprocess.run(
        [
            "npx", "hyperframes", "render",
            str(public_dir),
            "-o", str(output_path),
            "--fps", str(fps),
            "--quality", "draft",
            "--workers", "1",
            "--protocol-timeout", "600000",
            "--low-memory-mode",
        ],
        capture_output=True, text=True,
        timeout=max(600, int(timing_map.output_duration * 45)),
        env=env,
    )
    if proc.returncode != 0 or not output_path.exists():
        print(f"[HF] Render failed (rc={proc.returncode}): {proc.stderr[-500:]}")
        raise RuntimeError("HyperFrames CLI render failed")

    print(f"[HF] Done: {output_path}")
    _probe_av_durations(output_path, "hyperframes_output")

    return {
        "output": str(output_path),
        "duration": timing_map.output_duration,
        "format": plan.format,
        "packaging": plan.packaging,
        "plan": plan.raw,
        "key_lines": plan.key_lines,
        "hyperframes_rendered": n_graphic,
        "broll_pauses": 0,
        "graphics_rendered": ["hyperframes"],
        "vignette_moments": 0,
    }


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

    # ── HyperFrames pipeline (feature-flagged) ───────────────────────
    if settings.render_engine == "hyperframes":
        try:
            return _render_hyperframes(
                src, transcript, plan, work_dir, output_path,
                brand_color=brand_color or "#FF7751",
                content_type=content_type,
                editing_style=editing_style,
            )
        except Exception as _hf_err:
            import traceback as _tb
            print(f"[RENDER] HyperFrames pipeline failed: {_hf_err}")
            print(f"[RENDER] Traceback:\n{_tb.format_exc()}")
            print("[RENDER] Falling back to FFmpeg pipeline")

    # ── FFmpeg pipeline (default / fallback) ──────────────────────────
    _health_check(src)

    print(f"[RENDER] plan.format={plan.format!r} plan.raw.get('format')={plan.raw.get('format')!r} | short_form will be {plan.format == 'short'!r}")
    _n_slides = sum(1 for s in (plan.keep_segments or []) if isinstance(s, dict) and s.get("is_slide"))
    print(f"[MG] Total is_slide segments in plan: {_n_slides}")

    try:
        from app.engine.font_manager import preload_style_fonts
        preload_style_fonts(editing_style)
    except Exception as _fe:
        print(f"[FONT] preload_style_fonts failed (non-fatal): {_fe}")

    skip_captions = False
    short_form = plan.format == "short"
    if short_form:
        caption_style = "twolevel"
    fps = 30
    pad = SHORT_PAD_S if short_form else LONG_PAD_S

    # Detect 4K input -- preserve native resolution, don't downscale.
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
    # Resolve any raw planner overlaps before cutting -- prevents word repetition.
    keep = _dedup_segments(keep)
    keep = _merge_short_segments(keep)
    _all_words_flat = [w for tseg in transcript.get("segments", []) for w in tseg.get("words", [])]
    keep = _fix_word_boundaries(keep, _all_words_flat)
    # FIX 3: boundary trimming can shrink a segment's planned duration (end -
    # start) below the minimum -- re-run the merge pass so a segment that was
    # fine before trimming (e.g. a 5s hook trimmed down to 0.55s) still gets
    # merged with its neighbor instead of playing as a near-instant glitch.
    keep = _merge_short_segments(keep)
    planned_total = sum(float(s["end"]) - float(s["start"]) for s in keep)
    print(f"[PLAN] {len(keep)} segments, planned total={planned_total:.3f}s")
    words = _flat_words(transcript)
    src_duration = float(transcript.get("duration", 0.0)) or _probe_duration(src)

    # Proxy: if the source is a heavy-decode format (ProRes, HEVC) or larger
    # than the target resolution, pre-transcode it once to a target-res H.264.
    # All segment cuts then use stream-copy on the proxy -> zero decode RAM.
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
    seg_offsets: list[float] = []
    seg_durations: list[float] = []
    cum = 0.0  # exact video output timeline -- no audio-handle inflation
    remapped_zoom: list[dict[str, Any]] = []
    remapped_words: list[WordTiming] = []
    remapped_silences: list[dict[str, Any]] = []
    remapped_vsm: list[dict[str, Any]] = []
    remapped_moments: list[dict[str, Any]] = []
    remapped_motion_graphics: list[dict[str, Any]] = []
    cut_timestamps: list[float] = []  # output-timeline timestamps of cut points
    slide_ranges: list[tuple[float, float]] = []  # edit-timeline ranges occupied by MG slides

    for i, seg in enumerate(keep):
        s_raw = float(seg["start"])
        e_raw = float(seg["end"])
        if e_raw <= s_raw:
            continue

        # Hard Rule 1 -- snap to word boundaries
        s = _snap_to_word_boundary(s_raw, words, edge="start")
        e = _snap_to_word_boundary(e_raw, words, edge="end")
        # Semantic completeness: extend end if snapped edge is a dangling conjunction.
        # Cap extension at next segment's raw start to prevent encroaching on it.
        e = _extend_for_semantic_completeness(e, transcript, src_duration)
        if i + 1 < len(keep):
            _next_s_raw = float(keep[i + 1]["start"])
            e = min(e, max(s + 0.15, _next_s_raw - 0.05))
        e_effective = e  # source end AFTER extension, BEFORE padding — used for caption remap
        # Hard Rule 2 -- pad cut edges
        s = max(0.0, s - pad)
        e = min(src_duration, e + pad) if src_duration > 0 else e + pad
        if e - s < 0.15:
            continue

        part = work_dir / f"part_{i:04d}.mp4"

        # ── Full-screen motion graphic segments (long-form) ─────────────────
        is_slide = bool(seg.get("is_slide"))
        if is_slide:
            from app.core.config import settings as _cfg

            slide_content = dict(seg.get("slide_content") or {})
            accent = str(seg.get("accent_color", "#00C3FF"))
            slide_content.setdefault("accent_color", accent)
            slide_dur = max(0.5, e - s)
            _slide_dir = work_dir / "slides"
            _slide_dir.mkdir(parents=True, exist_ok=True)
            _slide_vid = _slide_dir / f"slide_{i:04d}.mp4"
            _slide_html: str | None = None
            _mg_path = "none"

            concept_desc = str(seg.get("concept_description", ""))
            slide_type = str(seg.get("slide_type", ""))

            if _cfg.motion_graphics_mode == "generated" and concept_desc:
                # ── AI-generated path ────────────────────────────────
                _slide_html, _mg_path = generate_custom_motion_graphic(
                    concept_description=concept_desc,
                    content=slide_content,
                    duration=slide_dur,
                    width=target_w, height=target_h,
                    accent_color=accent,
                    work_dir=_slide_dir,
                )

            if _slide_html is None and slide_type:
                # ── Template fallback path ───────────────────────────
                _slide_html = generate_composition_html(
                    slide_type, slide_content, slide_dur,
                    target_w, target_h,
                    brand_color=accent,
                )
                _mg_path = f"fallback:{slide_type}"

            _slide_ok = False
            if _slide_html:
                _slide_ok = render_slide_to_video(
                    _slide_html, _slide_vid, slide_dur,
                    target_w, target_h, fps=fps, work_dir=_slide_dir,
                )

            if _slide_ok and _slide_vid.exists():
                _audio_tmp = _slide_dir / f"slide_audio_{i:04d}.aac"
                try:
                    _run([
                        FFMPEG_PATH, "-y", "-loglevel", "error",
                        "-ss", f"{s:.6f}", "-accurate_seek",
                        "-i", str(cut_src),
                        "-t", f"{slide_dur:.6f}",
                        "-vn", "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
                        str(_audio_tmp),
                    ])
                    _run([
                        FFMPEG_PATH, "-y", "-loglevel", "error",
                        "-i", str(_slide_vid),
                        "-i", str(_audio_tmp),
                        "-map", "0:v", "-map", "1:a",
                        "-c:v", "copy", "-c:a", "copy",
                        "-avoid_negative_ts", "make_zero",
                        "-shortest", str(part),
                    ])
                    try:
                        _audio_tmp.unlink(missing_ok=True)
                        _slide_vid.unlink(missing_ok=True)
                    except Exception:
                        pass
                    print(f"[MG] Segment {i}: {_mg_path} ({slide_dur:.1f}s)")
                except Exception as _mux_err:
                    print(f"[MG] Segment {i}: audio/mux failed: {_mux_err} — falling back to speaker footage")
                    _slide_ok = False
            if not _slide_ok:
                print(f"[MG] Segment {i}: render failed, using speaker footage")
                if use_proxy:
                    _cut_proxy_segment(cut_src, s, e, part)
                else:
                    _cut_segment(cut_src, s, e, part, target_w, target_h, fps)

            zoom_level = 100
            zoom_segments.append(i)
            zoom_levels.append(zoom_level)
            parts.append(part)
            seg_offsets.append(cum)
            seg_durations.append(slide_dur)
            seg_offset = cum
            cut_timestamps.append(cum)
            slide_ranges.append((cum, cum + slide_dur))
            cum += slide_dur
            continue
        # ─────────────────────────────────────────────────────────────────────

        if use_proxy:
            _cut_proxy_segment(cut_src, s, e, part)
        else:
            _cut_segment(cut_src, s, e, part, target_w, target_h, fps)

        # Jump zoom -- Priestley/Momentum styles use beat-mapped levels; viral uses planner levels.
        zoom_level = int(seg.get("zoom_level", 130))
        if editing_style == "priestley":
            _beat = (seg.get("beat") or "default").lower()
            zoom_level = PRIESTLEY_ZOOM.get(_beat, PRIESTLEY_ZOOM["default"])
            zoom_level = min(zoom_level, MAX_ZOOM_PRIESTLEY)
        elif editing_style == "momentum":
            _beat = (seg.get("beat") or "default").lower()
            zoom_level = MOMENTUM_ZOOM.get(_beat, MOMENTUM_ZOOM["default"])
            zoom_level = min(zoom_level, MAX_ZOOM_MOMENTUM)
        elif zoom_level not in _VALID_ZOOM_LEVELS:
            zoom_level = min(_VALID_ZOOM_LEVELS, key=lambda z: abs(z - zoom_level))
        print(f"[ZOOM] Segment {i}: beat={seg.get('beat')} zoom={zoom_level}% duration={e-s:.2f}s")
        zoom_segments.append(i)
        zoom_levels.append(zoom_level)
        parts.append(part)

        # All timeline remapping uses cum (exact video output clock) and s (snapped
        # cut start). No audio handles -- _cut_segment() cuts exactly [s, e], so
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

        _word_count_before = len(remapped_words)
        all_words = [w for tseg in transcript.get("segments", []) for w in tseg.get("words", [])]
        print(f"[CAP DEBUG] seg {i}: looking for words between {s_raw:.3f} and {e_effective:.3f}")
        words_in_range = [w for w in all_words if (s_raw - 0.05) <= float(w["start"]) < (e_effective + 0.05)]
        print(f"[CAP DEBUG] found {len(words_in_range)} words in range")
        # Caption remap: direct offset from the padded cut start (s).
        # The audio is extracted verbatim (no time-stretch), so a word at
        # source time ws appears at edit time seg_offset + (ws - s).
        for w in words_in_range:
            ws = float(w["start"])
            we = float(w["end"])
            remapped_start = seg_offset + (ws - s)
            remapped_end = seg_offset + (we - s)
            remapped_words.append(WordTiming(
                text=w["text"].strip(),
                start=max(0.0, remapped_start),
                end=max(0.0, remapped_end),
            ))
        _word_count_after = len(remapped_words)
        print(
            f"[CAP] Segment {i}: s_raw={s_raw:.3f} e_raw={e_raw:.3f} "
            f"s={s:.3f} e={e:.3f} seg_offset={seg_offset:.3f} "
            f"words_added={_word_count_after - _word_count_before}"
        )

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
            if s_raw <= m_at < e_effective:
                rm_start = seg_offset + (m_at - s)
                rm_end = seg_offset + (min(m_end, e) - s)
                remapped_moments.append({
                    **moment,
                    "start": max(0.0, rm_start),
                    "end":   max(0.0, rm_end),
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
                    "at":       max(0.0, seg_offset + (mg_at - s)),
                    "duration": mg_dur,
                })

        seg_offsets.append(cum)
        seg_durations.append(e - s)
        if parts:  # record cut point in output timeline
            cut_timestamps.append(cum)
        cum += (e - s)  # exact segment duration -- no audio-handle inflation

    if not parts:
        raise RuntimeError("No keep_segments produced any clip.")

    print(f"[ZOOM] Total segments with zoom: {len(zoom_segments)}")

    # PROBLEM 2 FIX -- AV DESYNC after reordering:
    # Re-encode every extracted segment to a clean PTS-zero baseline before
    # concat. Reordered segments (e.g. hook from t=35s played first) carry
    # their original container PTS even after accurate-seek extraction; the
    # concat filter cannot reconcile PTS offsets across audio/video streams,
    # producing drift. Full re-encode with setpts=PTS-STARTPTS eliminates this.
    clean_dir = work_dir / "clean_parts"
    clean_dir.mkdir(parents=True, exist_ok=True)
    clean_parts: list[Path] = []
    for _pi, _p in enumerate(parts):
        _cp = clean_dir / f"clean_{_pi:04d}.mp4"
        print(f"[CLEAN] Re-encoding segment {_pi} -> {_cp.name}")
        _reencode_clean(_p, _cp, fps)
        clean_parts.append(_cp)

    # Build per-segment zoom entries from remapped_zoom for animated zoom.
    zoom_entries_per_seg: list[list[dict]] = [[] for _ in clean_parts]
    for rz in remapped_zoom:
        rz_start = float(rz.get("start", 0))
        for si in range(len(seg_offsets)):
            so = seg_offsets[si]
            sd = seg_durations[si]
            if so <= rz_start < so + sd:
                zoom_entries_per_seg[si].append({
                    **rz,
                    "start": rz_start - so,
                    "end": float(rz.get("end", rz_start)) - so,
                })
                break
    _n_animated = sum(1 for e in zoom_entries_per_seg if e)
    print(f"[ZOOM] Animated segments: {_n_animated}/{len(clean_parts)} (rest use static crop)")

    concat_path = work_dir / "concat.mp4"
    _concat_with_zoom(
        clean_parts, zoom_levels, concat_path, target_w, target_h, fps,
        zoom_entries_per_seg=zoom_entries_per_seg,
        seg_durations=seg_durations,
    )
    print(f"[AUDIO] Concat+zoom complete: {concat_path}")

    # Verify AV sync immediately after concat; capture audio duration for trim.
    _concat_v_dur, _concat_a_dur = _probe_av(concat_path)
    _probe_av_durations(concat_path, "concat")
    _audio_expected = sum(float(s["end"]) - float(s["start"]) for s in keep
                         if float(s["end"]) > float(s["start"]))
    print(f"[AUDIO] Expected duration (plan boundaries): {_audio_expected:.3f}s")

    # PROBLEM 1 FIX -- FREEZE (video longer than audio):
    # When video stream runs longer than audio, the last frames are silent
    # freeze frames. Trim concat to exact audio duration using [AV PROBE]
    # audio as the reference (not planned duration, which may differ).
    if _concat_a_dur > 0.5 and _concat_v_dur > _concat_a_dur + 0.1:
        _trimmed_path = work_dir / "concat_trimmed.mp4"
        print(
            f"[TRIM] Video ({_concat_v_dur:.3f}s) > audio ({_concat_a_dur:.3f}s) "
            f"by {_concat_v_dur - _concat_a_dur:+.3f}s -- trimming to audio duration"
        )
        _trim_to_audio(concat_path, _trimmed_path, _concat_a_dur)
        concat_path = _trimmed_path

    # BUG 3 FIX -- DURATION MISMATCH: log the delta for diagnostics only.
    # The words are already correctly positioned via seg_offset (exact video clock),
    # so scaling them again by actual/planned introduces drift rather than fixing it.
    _concat_actual = _probe_duration(concat_path)
    _concat_delta = _concat_actual - _audio_expected
    if abs(_concat_delta) > 1.0:
        print(
            f"[DURATION WARNING] concat.mp4={_concat_actual:.3f}s "
            f"planned={_audio_expected:.3f}s delta={_concat_delta:+.3f}s"
        )

    # NOTE: caption timestamp scaling removed -- words are mapped via seg_offset
    # (exact video output clock), not plan boundaries.  Scaling by actual/planned
    # was introducing drift and causing AV desync.

    # Remap b-roll windows to the cut timeline so captions pause there.
    # BUG FIX -- B-ROLL TIMING:
    # Hard rule: 2.5s ≤ b-roll ≤ 4s. Anything shorter is a flash that just
    # confuses the viewer. Clamp the agent's suggestion into that window;
    # if the agent gave us 0s/0.1s of duration the clamp pulls it up to
    # the readable floor.
    BROLL_MIN_S = 1.5
    BROLL_MAX_S = 3.0
    remapped_broll: list[tuple[float, float]] = []
    broll_queries: list[str] = []   # parallel to remapped_broll -- Pexels search terms
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
        # Default color: hyperframe -> brand_color -> electric yellow.
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

    # FIX 4: Larger silence thresholds -- keep natural breathing pauses intact.
    # Short-form: only remove pauses > 0.5s (was 0.25s); compress to 0.30s.
    # Long-form:  only remove pauses > 0.8s (was 0.6s);  compress to 0.50s.
    _sil_min = 0.50 if short_form else 0.80
    _sil_max = 0.30 if short_form else 0.50
    if remapped_words:
        long_pauses = _find_long_pauses(remapped_words, min_gap_s=_sil_min)
        if slide_ranges:
            long_pauses = [
                (ps, pe) for ps, pe in long_pauses
                if not any(ps < sr_e and pe > sr_s for sr_s, sr_e in slide_ranges)
            ]
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
    # Short-form: adaptive -- max(5s, 15% of total duration). A 37s video gets
    # 5.55s gap, meaning b-roll can actually land. Long-form: 20% of duration, min 8s.
    # Also drop any b-roll in the first 2s (hook must show the speaker's face).
    _min_broll_gap = max(5.0, total_duration * 0.15) if short_form else max(8.0, total_duration * 0.20)
    _filtered_broll: list[tuple[float, float]] = []
    _filtered_queries: list[str] = []
    _last_broll_end = 0.0
    for (br_s, br_e), br_q in zip(remapped_broll, broll_queries):
        if br_s < 2.0:
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

    # FIX 1 -- SALMON SCREEN: disable hyperframes completely.
    # Color-flash MKV overlays render as a solid salmon/colored screen at any
    # timestamp -- disable until the overlay pipeline is validated.
    remapped_hyperframes = []

    import logging as _logging
    import shutil as _shutil
    import tempfile as _tempfile
    _log = _logging.getLogger(__name__)

    # BUG 4 FIX -- CAPTION BOUNDARY SHIFT: any caption word whose remapped start
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

    # FIX 4 -- Caption sync verification
    print(f"[SYNC CHECK] Video duration: {total_duration:.3f}s")
    if remapped_words:
        print(
            f"[SYNC CHECK] Caption range: "
            f"{remapped_words[0].start:.3f}s -> {remapped_words[-1].end:.3f}s"
        )
        print(
            f"[SYNC CHECK] First 5 words: "
            f"{[(w.text, round(w.start, 2)) for w in remapped_words[:5]]}"
        )
        _words_after = [w for w in remapped_words if w.start > total_duration]
        if _words_after:
            print(
                f"[SYNC CHECK] WARNING: {len(_words_after)} word(s) start after "
                f"video duration -- dropping them"
            )
        if remapped_words[-1].end > total_duration + 0.5:
            print(
                f"[SYNC CHECK] ERROR: last caption end "
                f"{remapped_words[-1].end:.3f}s > video {total_duration:.3f}s + 0.5s"
            )
    else:
        print("[SYNC CHECK] WARNING: no remapped words -- captions will be empty!")

    _log.info(
        "caption sync: %d words; range=%.3f–%.3fs; first3=%s",
        len(remapped_words),
        remapped_words[0].start if remapped_words else 0.0,
        remapped_words[-1].end  if remapped_words else 0.0,
        [(w.text, round(w.start, 3), round(w.end, 3)) for w in remapped_words[:3]],
    )

    # PART 4 -- caption_style_map: per-segment style hints from the planner.
    # Maps source segment start time -> caption style ("normal" | "emphasis" | "highlight").
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
    print(f"[CAPTIONS] short_form={short_form} _long={_long} remapped_moments={len(remapped_moments)} _use_moments={_use_moments} -> mode={'long' if _use_moments else 'short'}")
    if _long and not remapped_moments:
        print("[CAPTIONS] Long-form has no caption_moments -- falling back to short-form word-by-word")
    if not skip_captions:
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
    else:
        print("[CAPTIONS] Disabled (skip_captions=True) -- no ASS file generated")

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

    # Motion graphics disabled -- clean professional output.
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
    # a timed MKV clip via _png_to_timed_clip() -- same mechanism as motion
    # graphics. This completely eliminates enable= from the video filter chain:
    # no drawbox, no drawtext, no gte/lte timing expressions.
    hf_dir = work_dir / "hyperframes"
    hf_dir.mkdir(parents=True, exist_ok=True)
    hf_graphics: list[RenderedGraphic] = []
    for i, hf in enumerate(remapped_hyperframes):
        try:
            hf_at = float(hf.get("at", 0))
            hf_color = _hex_to_rgb_at(hf.get("color") or brand_color or "#FF7751")
            # BUG 1 FIX: cap to 0.06s (2 frames at 30fps) -- a flash, not a screen.
            hf_dur = max(0.033, min(0.06, float(hf.get("duration", 0.05))))
            hf_png = hf_dir / f"hf_{i:03d}.png"
            # Color flash only -- no text overlay on hyperframes.
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
    # (matching original drawbox -> overlay ordering).
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

    # ── Pass 1: grade + scale + audio duck -> base ────────────────────────
    # Overlays are applied sequentially below (one ffmpeg call per clip).
    # This is more reliable than one giant filter_complex: memory is bounded
    # by two inputs per call regardless of how many graphics exist.
    _tmp_dir = Path(_tempfile.gettempdir())
    _base_path = _tmp_dir / f"base_{output_path.stem}.mp4"

    # Jump zoom is baked in per-segment (see _apply_segment_zoom above).
    # Pass 1 only needs fps normalization and PTS reset.
    _zoom_str = f"fps={fps},setpts=N/FRAME_RATE/TB"

    # Cover stream chat / subscriber UI in top-right corner -- short-form only.
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
        # Lossless intermediate video -- only one lossy encode in the final pass.
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
    _probe_av_durations(_base_path, "after_zoompan")

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
        "SET" if _pexels_key_set else "NOT SET -- skipping all broll",
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
        # Slide-in from right edge (0->x=0 over _slide s), hold, slide-out to left.
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
    _probe_av_durations(_current_path, "after_broll")

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
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
            "-threads", "4",
            "-x264-params", "rc-lookahead=0:bframes=0",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            str(_next_path),
        ])
        _overlay_intermediates.append(_current_path)
        _current_path = _next_path

    # ── Old kinetic_title/hf_prompt MG system DISABLED ─────────────────────
    # Replaced by the new generate_custom_motion_graphic() slide system.
    # The old system produced green text overlays via chroma-key templates.
    if remapped_motion_graphics:
        print(f"[MG] Old overlay system DISABLED — {len(remapped_motion_graphics)} graphic(s) skipped (using new slide generation path only)")

    _nocap_path = _current_path

    # ── Final pass: burn ASS captions ─────────────────────────────────────
    # Separate ffmpeg invocation so subtitle path escaping is handled by the
    # OS arg list (no shell expansion) -- only the ffmpeg filter parser sees it.
    if not skip_captions:
        _ass_tmp = _tmp_dir / f"captions_{ass_path.parent.name}.ass"
        _shutil.copy2(ass_path, _ass_tmp)
        _ass_fwd = str(_ass_tmp).replace("\\", "/").replace(":", "\\:")
        _ass_str = f"filename='{_ass_fwd}'"
    # Per-format output bitrate targets -- prevent quality floor drops on
    # complex scenes while keeping file size reasonable.
    if is_4k:
        _out_bv, _out_maxrate, _out_bufsize = "20M", "30M", "60M"
    elif short_form:
        _out_bv, _out_maxrate, _out_bufsize = "8M", "12M", "24M"
    else:
        _out_bv, _out_maxrate, _out_bufsize = "6M", "10M", "20M"
    # ── Final pass: burn ASS captions + quality encode ────────────────────
    _cmd_final = [
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-i", str(_nocap_path),
    ]
    if not skip_captions:
        _cmd_final += ["-vf", f"subtitles={_ass_str}"]
    _cmd_final += [
        "-vsync", "cfr",
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
        "-shortest",
        str(output_path),
    ]
    _run(_cmd_final)
    print(f"[FINAL] Output: {output_path}")
    _probe_av_durations(output_path, "final_output")
    _nocap_path.unlink(missing_ok=True)
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
            pass  # Music mix failed -- ship clean audio instead

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
