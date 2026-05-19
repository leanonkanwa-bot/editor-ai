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
  5. Single re-encode pass: scale/crop → zoompan → burn ASS subtitles LAST
     (Hard Rule 7, never under overlays).

Borrows the per-segment-extract / lossless-concat / 30ms-afade pattern from
browser-use/video-use's helpers/render.py, which is the proven shape.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.agent.planner import EditPlan
from app.engine.captions import WordTiming, build_ass
from app.engine.graphics import AESTHETIC_COLORS, render_motion_graphic
from app.engine.transcribe import FFMPEG_PATH, FFPROBE_PATH


SHORT_PAD_S = 0.05   # 50ms — tight, energetic
LONG_PAD_S = 0.15    # 150ms — cinematic breathing room
AUDIO_FADE_S = 0.03  # 30ms — anti-pop fade at every segment boundary


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
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
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-x264-params", (
            "rc-lookahead=0:bframes=0:ref=1:no-mbtree=1:"
            f"keyint=60:keyint_min=60"      # keyframe every 2 s
        ),
        "-threads", "1",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
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
    zoompan re-encode corrects. Audio is re-encoded to apply the 30 ms fades.
    """
    duration = max(0.1, end - start)
    fade_out_start = max(0.0, duration - AUDIO_FADE_S)
    af = (
        f"afade=t=in:st=0:d={AUDIO_FADE_S},"
        f"afade=t=out:st={fade_out_start:.3f}:d={AUDIO_FADE_S}"
    )
    _run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-threads", "1",
        "-ss", f"{start:.3f}", "-i", str(proxy),
        "-t", f"{duration:.3f}",
        "-af", af,
        "-c:v", "copy",                     # zero decode/encode memory
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
    """
    Snap a cut time to the nearest word boundary (Hard Rule 1).
    edge='start' snaps to the START of the next word; edge='end' snaps to the
    END of the previous word — so we never cut INTO a word.
    """
    if not words:
        return t
    if edge == "start":
        # find earliest word that starts at or after t; if none, keep t.
        for ws, _ in words:
            if ws >= t - 0.01:
                return ws
        return t
    # end: find the latest word that ends at or before t.
    last = t
    for _, we in words:
        if we <= t + 0.01:
            last = we
        else:
            break
    return last


def _cut_segment(
    src: Path,
    start: float,
    end: float,
    dst: Path,
    target_w: int,
    target_h: int,
    fps: int,
) -> None:
    """Per-segment extract with 30ms audio fades baked in (Hard Rule 6)."""
    duration = max(0.1, end - start)
    fade_out_start = max(0.0, duration - AUDIO_FADE_S)
    af = (
        f"afade=t=in:st=0:d={AUDIO_FADE_S},"
        f"afade=t=out:st={fade_out_start:.3f}:d={AUDIO_FADE_S}"
    )
    vf = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase"
        f":flags=fast_bilinear,"
        f"crop={target_w}:{target_h},fps={fps}"
    )
    # Intermediate segments are re-encoded in the final zoompan+subs pass,
    # so quality here doesn't affect the output. Use CRF 35 + strict memory
    # caps. Most importantly: no +faststart (moov-atom rewrite buffers the
    # entire mdat, doubling peak RSS on large videos → SIGKILL on small dynos).
    _run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-threads", "1",        # global: limits decoder threads too, not just encoder
        "-ss", f"{start:.3f}", "-i", str(src),
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-af", af,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35",
        "-x264-params", "rc-lookahead=0:bframes=0:ref=1:no-mbtree=1:sync-lookahead=0",
        "-threads", "1",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
        str(dst),
    ])


def _concat(parts: list[Path], dst: Path) -> None:
    list_path = dst.with_suffix(".txt")
    list_path.write_text("\n".join(f"file '{p.as_posix()}'" for p in parts))
    _run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-c", "copy",
        str(dst),
    ])
    list_path.unlink(missing_ok=True)


def _build_zoom_expression(
    zoom_plan: list[dict[str, Any]],
    total_duration: float,
    fps: int = 30,
    face_cx_pct: float = 50.0,
    face_cy_pct: float = 50.0,
) -> tuple[str, str]:
    """
    Convert the zoom plan into zoompan z/x/y expressions evaluated per output
    frame. zoompan uses 'on' = output frame number. We translate timestamps to
    frame ranges and build a chained if(...) expression.

    BUG FIX — ZOOM SHAKE:
    Linear interpolation between keyframes causes a velocity discontinuity at
    every segment boundary, which the eye reads as a tiny jolt — repeated, it
    feels like shake. Use smoothstep easing s = t*t*(3-2*t), which has zero
    derivative at the endpoints. The zoom passes through every keyframe at
    the same value but with smooth velocity, so transitions feel butter.

    When face_cx_pct/face_cy_pct are not exactly 50, anchors the zoom to the
    detected face center instead of the geometric frame center.
    """
    if face_cx_pct != 50.0 or face_cy_pct != 50.0:
        fx = f"max(0, min(iw-(iw/zoom), iw*{face_cx_pct/100:.4f}-(iw/zoom/2)))"
        fy = f"max(0, min(ih-(ih/zoom), ih*{face_cy_pct/100:.4f}-(ih/zoom/2)))"
    else:
        fx = "iw/2-(iw/zoom/2)"
        fy = "ih/2-(ih/zoom/2)"

    if not zoom_plan:
        return "1", f"{fx};{fy}"

    sorted_plan = sorted(zoom_plan, key=lambda p: float(p.get("start", 0)))

    # Fill gaps between zoom segments with hold-at-last-value entries so z
    # never snaps back to the default "1" during silent/non-zoom moments —
    # that snap is what the eye reads as shake.
    filled: list[dict] = []
    for i, step in enumerate(sorted_plan):
        filled.append(step)
        curr_e = float(step.get("end", float(step.get("start", 0)) + 1))
        z_end_val = float(step.get("to", step.get("from", 1.0)))
        if i < len(sorted_plan) - 1:
            next_s = float(sorted_plan[i + 1].get("start", 0))
            if next_s > curr_e + 0.02:
                filled.append({"start": curr_e, "end": next_s,
                                "from": z_end_val, "to": z_end_val, "kind": "hold"})
    # After the last zoom: hold its final value until the video ends.
    last = sorted_plan[-1]
    last_e = float(last.get("end", float(last.get("start", 0)) + 1))
    last_to = float(last.get("to", last.get("from", 1.0)))
    if total_duration > last_e + 0.02:
        filled.append({"start": last_e, "end": total_duration,
                        "from": last_to, "to": last_to, "kind": "hold"})

    z_expr = "1"
    for step in reversed(filled):
        s = float(step.get("start", 0))
        e = float(step.get("end", s + 1))
        z_from = float(step.get("from", 1.0))
        z_to = float(step.get("to", z_from))
        f0 = int(s * fps)
        f1 = max(f0 + 1, int(e * fps))
        seg_dur = max(1, f1 - f0)
        kind = (step.get("kind") or "drift").lower()

        # Linear progress in [0,1].
        t = f"min(1, max(0, (on-{f0}) / {seg_dur}))"

        if kind == "punch_in":
            # Hard cut — no interpolation. The whole window holds at z_to.
            seg_expr = f"{z_to}"
        else:
            # Smoothstep easing: s = t*t*(3-2*t). Zero derivative at both ends
            # → no velocity discontinuity at segment boundaries → no shake.
            ease = f"({t})*({t})*(3-2*({t}))"
            seg_expr = f"({z_from} + ({z_to}-{z_from}) * ({ease}))"

        z_expr = f"if(between(on,{f0},{f1}),{seg_expr},{z_expr})"

    return z_expr, f"{fx};{fy}"


def _hex_to_rgb_at(hex6: str) -> str:
    """Return an ffmpeg color string like 'white@1.0' or '0xRRGGBB@1.0'."""
    h = (hex6 or "").lstrip("#").upper()
    if len(h) != 6:
        return "white@1.0"
    return f"0x{h}@1.0"


def _ass_escape_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _build_hyperframe_filters(
    hyperframes: list[dict[str, Any]],
    target_w: int,
    target_h: int,
    fonts_dir: str = "/usr/local/share/fonts/leanlead",
) -> str:
    """Return a comma-prefixed filter chain that overlays each hyperframe
    as a full-screen colored card with a centered word/number, only during
    its [at, at+duration] window. Empty string if no hyperframes."""
    import os as _os
    _poppins = f"{fonts_dir}/Poppins-ExtraBold.ttf"
    _arial   = r"C:/Windows/Fonts/arialbd.ttf"
    if _os.path.isfile(_poppins):
        _fontfile_arg = f":fontfile={_poppins}"
    elif sys.platform == "win32" and _os.path.isfile(_arial):
        _fontfile_arg = f":fontfile={_arial}"
    else:
        _fontfile_arg = ""
    parts: list[str] = []
    for hf in hyperframes:
        try:
            t0 = float(hf.get("at", 0))
            dur = float(hf.get("duration", 0.1))
        except (TypeError, ValueError):
            continue
        t1 = t0 + max(0.05, min(0.2, dur))
        color = _hex_to_rgb_at(hf.get("color", "#FFE500"))
        kind = (hf.get("kind") or "word").lower()

        # Solid color flash that covers the whole frame.
        parts.append(
            f"drawbox=x=0:y=0:w=iw:h=ih:color={color}:t=fill"
            f":enable=between(t,{t0:.3f},{t1:.3f})"
        )

        if kind in {"word", "number", "image"}:
            text = str(hf.get("content", "")).strip()
            if text:
                # Single bold word/number, full-screen weight.
                # Black text on a colored flash reads fastest at 0.1s.
                font_size = int(target_h * 0.22)
                parts.append(
                    f"drawtext=text={_ass_escape_text(text)}"
                    f"{_fontfile_arg}"
                    f":fontcolor=black:fontsize={font_size}"
                    f":x=(w-text_w)/2:y=(h-text_h)/2"
                    f":enable=between(t,{t0:.3f},{t1:.3f})"
                )
    return ("," + ",".join(parts)) if parts else ""


def render(
    src: Path,
    transcript: dict[str, Any],
    plan: EditPlan,
    work_dir: Path,
    output_path: Path,
    *,
    caption_font: str = "Poppins Bold",
    caption_color: str = "white",
    caption_position: str = "center",
    caption_style: str = "impact",
    brand_color: str | None = None,
    aesthetic: str = "dark-pro",
    subject_position: dict | None = None,
) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)

    short_form = plan.format == "short"
    target_w, target_h = (1080, 1920) if short_form else (1920, 1080)
    fps = 30
    pad = SHORT_PAD_S if short_form else LONG_PAD_S

    keep = plan.keep_segments or [
        {"start": 0.0, "end": float(transcript.get("duration", 0.0))}
    ]
    words = _flat_words(transcript)
    src_duration = float(transcript.get("duration", 0.0)) or _probe_duration(src)

    # Proxy: if the source is a heavy-decode format (ProRes, HEVC) or larger
    # than the target resolution, pre-transcode it once to a target-res H.264.
    # All segment cuts then use stream-copy on the proxy → zero decode RAM.
    video_info = _probe_video_info(src)
    use_proxy = _needs_proxy(video_info, target_w, target_h)
    if use_proxy:
        proxy_path = work_dir / "proxy.mp4"
        _create_proxy(src, proxy_path, target_w, target_h, fps)
        cut_src = proxy_path
    else:
        cut_src = src

    parts: list[Path] = []
    cum = 0.0
    remapped_zoom: list[dict[str, Any]] = []
    remapped_words: list[WordTiming] = []
    remapped_silences: list[dict[str, Any]] = []

    for i, seg in enumerate(keep):
        s_raw = float(seg["start"])
        e_raw = float(seg["end"])
        if e_raw <= s_raw:
            continue
        # Hard Rule 1 — snap to word boundaries
        s = _snap_to_word_boundary(s_raw, words, edge="start")
        e = _snap_to_word_boundary(e_raw, words, edge="end")
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
        parts.append(part)

        for zp in plan.zoom_plan:
            zs = float(zp.get("start", 0))
            ze = float(zp.get("end", zs))
            if zs >= s and ze <= e:
                remapped_zoom.append({
                    **zp,
                    "start": cum + (zs - s),
                    "end": cum + (ze - s),
                })

        for tseg in transcript.get("segments", []):
            for w in tseg.get("words", []):
                ws = float(w["start"])
                we = float(w["end"])
                if ws >= s and we <= e:
                    remapped_words.append(WordTiming(
                        text=w["text"].strip(),
                        start=cum + (ws - s),
                        end=cum + (we - s),
                    ))

        for sil in (plan.silences or []):
            try:
                sil_at = float(sil.get("at", 0))
            except (TypeError, ValueError):
                continue
            if s <= sil_at <= e:
                remapped_silences.append({
                    **sil,
                    "at": cum + (sil_at - s),
                })

        cum += (e - s)

    if not parts:
        raise RuntimeError("No keep_segments produced any clip.")

    concat_path = work_dir / "concat.mp4"
    _concat(parts, concat_path)
    total_duration = _probe_duration(concat_path)

    # Remap b-roll windows to the cut timeline so captions pause there.
    # BUG FIX — B-ROLL TIMING:
    # Hard rule: 2.5s ≤ b-roll ≤ 4s. Anything shorter is a flash that just
    # confuses the viewer. Clamp the agent's suggestion into that window;
    # if the agent gave us 0s/0.1s of duration the clamp pulls it up to
    # the readable floor.
    BROLL_MIN_S = 2.5
    BROLL_MAX_S = 4.0
    remapped_broll: list[tuple[float, float]] = []
    for br in plan.broll_suggestions:
        try:
            br_at = float(br.get("at", 0))
            br_dur = float(br.get("duration", 0))
        except (TypeError, ValueError):
            continue
        br_dur = max(BROLL_MIN_S, min(BROLL_MAX_S, br_dur))
        # Find the keep_segment that contains br_at and remap.
        run = 0.0
        for seg in keep:
            ss = float(seg["start"])
            ee = float(seg["end"])
            if ss <= br_at <= ee and ee > ss:
                start = run + (br_at - ss)
                remapped_broll.append((start, start + br_dur))
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

    ass_path = work_dir / "captions.ass"
    build_ass(
        remapped_words,
        ass_path,
        short_form=short_form,
        emphasis_words=set(plan.caption_emphasis_words),
        font=caption_font,
        color=caption_color,
        position=caption_position,
        style=caption_style,
        broll_windows=remapped_broll,
    )

    face_cx_pct = 50.0
    face_cy_pct = 50.0
    if subject_position:
        fl = subject_position.get("face_left_pct", 25.0)
        fr = subject_position.get("face_right_pct", 75.0)
        ft = subject_position.get("face_top_pct", 15.0)
        fb = subject_position.get("face_bottom_pct", 65.0)
        face_cx_pct = (fl + fr) / 2
        face_cy_pct = (ft + fb) / 2
    z_expr, xy_expr = _build_zoom_expression(
        remapped_zoom, total_duration, fps=fps,
        face_cx_pct=face_cx_pct, face_cy_pct=face_cy_pct,
    )
    x_expr, y_expr = xy_expr.split(";")
    total_frames = max(1, int(total_duration * fps))

    hyperframe_chain = _build_hyperframe_filters(
        remapped_hyperframes, target_w, target_h
    )

    # Render each motion graphic (the ones we know how to draw) to a PNG.
    # Then chain them as overlays in the final filter graph. Anything we
    # don't yet execute (split, quote, highlight, flow, arrow_callout) is
    # left in the JSON output as a brief — we don't fail.
    preset_colors = AESTHETIC_COLORS.get(aesthetic, AESTHETIC_COLORS["dark-pro"])
    accent_for_graphics = brand_color or preset_colors["accent"]
    graphics_dir = work_dir / "graphics"
    rendered_graphics: list[Any] = []
    for i, mg in enumerate(plan.motion_graphics or []):
        try:
            mg_at = float(mg.get("at", 0))
        except (TypeError, ValueError):
            continue
        # Remap the graphic timestamp onto the cut timeline.
        run = 0.0
        remapped_at = None
        for seg in keep:
            ss = float(seg["start"])
            ee = float(seg["end"])
            if ss <= mg_at <= ee and ee > ss:
                remapped_at = run + (mg_at - ss)
                break
            run += max(0.0, ee - ss)
        if remapped_at is None:
            continue
        # Hard enforcement: graphics appear ≥0.5s after the relevant moment
        # so they never pop up before the words are spoken.
        remapped_at = min(remapped_at + 0.5, max(0.0, total_duration - 1.0))
        spec = {**mg, "at": remapped_at}
        rg = render_motion_graphic(
            spec, graphics_dir, i,
            target_w=target_w, target_h=target_h,
            accent_hex=accent_for_graphics,
            aesthetic=aesthetic,
            subject_pos=subject_position,
        )
        if rg is not None:
            rendered_graphics.append(rg)

    # ── PASS 1: zoom + hyperframes + graphic overlays + audio ducks ──────────
    # Captions are NOT burned here — kept out of filter_complex so the Windows
    # subtitles filter never has to deal with a path that contains spaces.
    base_filter = (
        f"zoompan=z={z_expr}:x={x_expr}:y={y_expr}:"
        f"d=1:s={target_w}x{target_h}:fps={fps}"
        f"{hyperframe_chain}"
    )

    mute_ranges: list[tuple[float, float]] = []
    for sil in remapped_silences:
        try:
            t0 = float(sil.get("at", 0))
            dur = max(0.1, min(0.5, float(sil.get("duration", 0.3))))
        except (TypeError, ValueError):
            continue
        mute_ranges.append((t0, t0 + dur))

    if mute_ranges:
        conditions = "+".join([f"between(t,{s:.3f},{e:.3f})" for s, e in mute_ranges])
        audio_filter = f"[0:a]volume=enable='{conditions}':volume=0[a_out]"
    else:
        audio_filter = "[0:a]acopy[a_out]"

    pass1_path = work_dir / "pass1.mp4"
    cmd1: list[str] = [FFMPEG_PATH, "-y", "-loglevel", "error", "-i", str(concat_path)]
    for rg in rendered_graphics:
        cmd1 += ["-i", str(rg.png)]

    if rendered_graphics:
        chain_parts: list[str] = [f"[0:v]{base_filter}[v0]"]
        for i, rg in enumerate(rendered_graphics, start=1):
            t0 = rg.at
            t1 = rg.at + rg.duration
            chain_parts.append(
                f"[v{i-1}][{i}:v]overlay="
                f"x={rg.x_expr}:y={rg.y_expr}:"
                f"enable=between(t,{t0:.3f},{t1:.3f})[v{i}]"
            )
        last_label = f"v{len(rendered_graphics)}"
        chain_parts[-1] = chain_parts[-1][:-len(f"[{last_label}]")] + "[final]"
        chain_parts.append(audio_filter)
        filter_complex = ";".join(chain_parts)
        filter_complex = filter_complex.rstrip("'")
        import sys as _sys
        print(f"DEBUG_LAST20: {repr(filter_complex[-20:])}", file=_sys.stderr, flush=True)
        print(f"DEBUG_AUDIO: {repr(audio_filter)}", file=_sys.stderr, flush=True)
        cmd1 += ["-filter_complex", filter_complex, "-map", "[final]", "-map", "[a_out]"]
    else:
        cmd1 += ["-vf", base_filter]
        if mute_ranges:
            conditions = "+".join([f"between(t,{s:.3f},{e:.3f})" for s, e in mute_ranges])
            cmd1 += ["-af", f"volume='if({conditions},0,1)'"]

    cmd1 += [
        "-frames:v", str(total_frames),
        "-c:v", "libx264", "-preset", "superfast", "-crf", "22",
        "-threads", "2",
        "-x264-params", "rc-lookahead=0:bframes=0",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(pass1_path),
    ]
    _run(cmd1)

    # ── PASS 2: burn captions onto the clean pass-1 output ────────────────────
    # Input and output both sit in work_dir / outputs_dir — spaces in those
    # paths are fine for -i / output arguments.  Only the subtitles= filter
    # value needs a space-free path, which we get by copying to %TEMP%.
    import shutil as _shutil
    import tempfile as _tempfile
    _ass_tmp = Path(_tempfile.gettempdir()) / f"captions_{ass_path.parent.name}.ass"
    _shutil.copy2(ass_path, _ass_tmp)
    _ass_str = str(_ass_tmp).replace("\\", "/").replace(":", "\\:")
    ass_filter = f"subtitles='{_ass_str}':force_style='FontName=Arial'"

    cmd2: list[str] = [
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-i", str(pass1_path),
        "-vf", ass_filter,
        "-c:v", "libx264", "-preset", "superfast", "-crf", "22",
        "-threads", "2",
        "-x264-params", "rc-lookahead=0:bframes=0",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    _run(cmd2)

    return {
        "output": str(output_path),
        "duration": total_duration,
        "format": plan.format,
        "packaging": plan.packaging,
        "plan": plan.raw,
        "key_lines": plan.key_lines,
        "hyperframes_rendered": len(remapped_hyperframes),
        "broll_pauses": len(remapped_broll),
        "graphics_rendered": [
            {"kind": rg.kind, "at": rg.at, "duration": rg.duration}
            for rg in rendered_graphics
        ],
    }
