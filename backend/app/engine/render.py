"""
FFmpeg renderer. Takes the EditPlan + transcript + raw video and produces the
final edited video.

Pipeline (production-correct order):
  1. SNAP each keep_segment edge to the nearest word boundary (Hard Rule 1).
  2. PAD the edges: 30â€“200ms working window (Hard Rule 2). Tighter for short
     form, looser for long form.
  3. Per-segment extract with 30ms audio fades baked in (Hard Rule 6, prevents
     audible pops at every cut).
  4. Lossless `-c copy` concat of segments (no double-encode).
  5. Single re-encode pass: scale/crop â†’ zoompan â†’ burn ASS subtitles LAST
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


SHORT_PAD_S = 0.05   # 50ms â€” tight, energetic
LONG_PAD_S = 0.15    # 150ms â€” cinematic breathing room
AUDIO_FADE_S = 0.03  # 30ms â€” anti-pop fade at every segment boundary


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0:
        return
    stderr = proc.stderr[-2000:] or "(empty â€” process produced no stderr)"
    if proc.returncode < 0:
        sig = -proc.returncode
        hint = ""
        if sig == 9:
            hint = (
                " â€” SIGKILL. The kernel killed ffmpeg, almost always because "
                "the container ran out of memory. Try a shorter video, lower "
                "WHISPER_MODEL, or upgrade your hosting plan to give the "
                "encoder more headroom."
            )
        elif sig == 15:
            hint = " â€” SIGTERM. Something asked ffmpeg to stop."
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
    - Source resolution is larger than the target (4Kâ†’1080p decode is expensive).
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
    stream-copy â€” zero decode memory, near-zero CPU.

    Keyframe every 2 s (60 frames at 30 fps) so stream-copy cuts have â‰¤ 2 s
    of alignment error (acceptable â€” the final re-encode corrects it).
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

    Stream-copy means zero decode memory â€” we just copy the H.264 bitstream
    bytes. Cuts snap to the nearest keyframe (â‰¤ 2 s error), which the final
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
    END of the previous word â€” so we never cut INTO a word.
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
    # entire mdat, doubling peak RSS on large videos â†’ SIGKILL on small dynos).
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

    BUG FIX â€” ZOOM SHAKE:
    Linear interpolation between keyframes causes a velocity discontinuity at
    every segment boundary, which the eye reads as a tiny jolt â€” repeated, it
    feels like shake. Use smoothstep easing s = t*t*(3-2*t), which has zero
    derivative at the endpoints. The zoom passes through every keyframe at
    the same value but with smooth velocity, so transitions feel butter.

    When face_cx_pct/face_cy_pct are not exactly 50, anchors the zoom to the
    detected face center instead of the geometric frame center.
    """
    if face_cx_pct != 50.0 or face_cy_pct != 50.0:
        fx = f"max(0,min(iw-(iw/zoom),iw*{face_cx_pct/100:.4f}-(iw/zoom/2)))"
        fy = f"max(0,min(ih-(ih/zoom),ih*{face_cy_pct/100:.4f}-(ih/zoom/2)))"
    else:
        fx = "iw/2-(iw/zoom/2)"
        fy = "ih/2-(ih/zoom/2)"

    if not zoom_plan:
        return "1", f"{fx};{fy}"

    sorted_plan = sorted(zoom_plan, key=lambda p: float(p.get("start", 0)))

    # Fill gaps between zoom segments with hold-at-last-value entries so z
    # never snaps back to the default "1" during silent/non-zoom moments â€”
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
        t = f"min(1,max(0,(on-{f0})/{seg_dur}))"

        if kind == "punch_in":
            # Hard cut â€” no interpolation. The whole window holds at z_to.
            seg_expr = f"{z_to}"
        else:
            # Smoothstep easing: s = t*t*(3-2*t). Zero derivative at both ends
            # â†’ no velocity discontinuity at segment boundaries â†’ no shake.
            ease = f"({t})*({t})*(3-2*({t}))"
            seg_expr = f"({z_from}+({z_to}-{z_from})*({ease}))"

        # gte*lte instead of between() â€” FFmpeg 7.x parses between()'s commas
        # as filter-chain separators. gte/lte are 2-arg functions (one comma each).
        z_expr = f"if(gte(on,{f0})*lte(on,{f1}),{seg_expr},{z_expr})"

    return z_expr, f"{fx};{fy}"


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
    if short_form:   # 1080 Ã— 1920
        vw, vh = 400, 500
        vx = 1080 - vw - 40   # 640
        vy = 1920 - vh - 80   # 1340
        cr = 40
    else:            # 1920 Ã— 1080
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


def _render_hyperframe_png(
    color_str: str,
    dst: Path,
    target_w: int,
    target_h: int,
    text: str | None = None,
    system_font: str | None = None,
) -> None:
    """Render a full-frame solid-color PNG for a hyperframe flash.

    Generates the PNG via ffmpeg's lavfi color source â€” no enable= needed.
    The resulting PNG is converted to a timed MKV clip by _png_to_timed_clip()
    and overlaid with setpts+eof_action=pass, completely avoiding any
    enable= expression in the filter_complex.

    color_str is the output of _hex_to_rgb_at() e.g. '0xFF7751@1.0'.
    We strip the @opacity suffix since lavfi color=c= doesn't accept it.
    """
    lavfi_color = color_str.split("@")[0]   # '0xFF7751@1.0' â†’ '0xFF7751'
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
    """Convert a static PNG to a short RGBA video clip.

    Uses the PNG video codec in a Matroska (.mkv) container â€” the only
    widely-available combination that preserves a full RGBA alpha channel
    for transparent overlay compositing.

    Clip timestamps start at 0; the caller uses setpts=PTS+start/TB in
    the filter_complex to shift it to the correct position in the edit
    timeline. No enable= expression is needed: the clip simply doesn't
    exist outside its window, so the overlay falls through to the base
    video automatically (eof_action=pass).
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
      [0:v] â†’ grade+scale+zoompan â†’ overlay per timed clip â†’ [vout_label]

    Timed clips (indices 1..N) cover both hyperframe flashes and motion
    graphics. They are positioned via setpts=PTS+at/TB so no enable=
    expression is needed anywhere in the video filter chain.

    Audio chain (when silences exist):
      [0:a] â†’ volume-duck chain â†’ [aout]
      Volume enable= uses \\, (backslash-comma) escaping â€” the only
      remaining enable= in the entire pipeline.

    Returns:
        (filter_complex_str, video_out_label, audio_out_label_or_None)
    """
    fc: list[str] = []

    # â”€â”€ Video: grade + optional scale + constant zoompan â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    grade_parts: list[str] = [color_grade]
    if scale_filter:
        grade_parts.append(scale_filter)
    grade_parts.append(
        f"zoompan=z=1.04"
        f":x=iw/2-(iw/zoom/2)"
        f":y=ih/2-(ih/zoom/2)"
        f":d=1:s={target_w}x{target_h}:fps={fps}"
    )
    fc.append(f"[0:v]{','.join(grade_parts)}[vzoom]")

    # â”€â”€ Timed clip overlays (hyperframes + motion graphics) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Inputs at indices 1..N are pre-cut MKV clips (RGBA, duration = rg.duration).
    # setpts=PTS+at/TB shifts each clip to its correct position in the timeline.
    # eof_action=pass lets the base video show through before and after the clip.
    # No enable= expression is needed at all â€” the clip simply doesn't exist
    # outside its window, so the overlay falls through to the base video.
    #
    # COMMA ESCAPING: x_expr / y_expr from graphics.py use plain commas inside
    # function calls like max(a,b), if(c,a,b), lt(a,b).  In a filter_complex
    # option value, an unescaped comma is a filter-chain separator â€” FFmpeg would
    # split "y=max(0,345-h/2)" into two filters: "y=max(0" and "345-h/2)â€¦".
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

    # â”€â”€ Audio: volume-duck at deliberate silence inserts â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
    """Return an FFmpeg video filter string for the given content type.
    Applied FIRST in the filter chain so all subsequent overlays inherit the grade.
    """
    profiles: dict[str, str] = {
        "coaching":   "eq=contrast=1.15:saturation=1.1,colorbalance=rs=0.05:bs=-0.03",
        "education":  "eq=brightness=0.05:contrast=1.1,unsharp=5:5:1.0:5:5:0.0",
        "motivation": "eq=contrast=1.2:gamma=0.95,colorbalance=rs=0.08:gs=0.03",
        "story":      "eq=saturation=0.9:contrast=1.05,colorbalance=rs=0.03",
    }
    return profiles.get(content_type.lower(), "eq=contrast=1.1:saturation=1.05")


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
    graphic_specs: list | None = None,
    content_type: str = "coaching",
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
    # All segment cuts then use stream-copy on the proxy â†’ zero decode RAM.
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
    remapped_vsm: list[dict[str, Any]] = []

    for i, seg in enumerate(keep):
        s_raw = float(seg["start"])
        e_raw = float(seg["end"])
        if e_raw <= s_raw:
            continue
        # Hard Rule 1 â€” snap to word boundaries
        s = _snap_to_word_boundary(s_raw, words, edge="start")
        e = _snap_to_word_boundary(e_raw, words, edge="end")
        # Hard Rule 2 â€” pad cut edges
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

        for vm in (plan.visual_style_moments or []):
            try:
                vm_at = float(vm.get("at", 0))
                vm_dur = float(vm.get("duration", 2.0))
            except (TypeError, ValueError):
                continue
            if s <= vm_at <= e:
                remapped_vsm.append({
                    **vm,
                    "at": cum + (vm_at - s),
                    "duration": vm_dur,
                })

        cum += (e - s)

    if not parts:
        raise RuntimeError("No keep_segments produced any clip.")

    concat_path = work_dir / "concat.mp4"
    _concat(parts, concat_path)
    total_duration = _probe_duration(concat_path)

    # Remap b-roll windows to the cut timeline so captions pause there.
    # BUG FIX â€” B-ROLL TIMING:
    # Hard rule: 2.5s â‰¤ b-roll â‰¤ 4s. Anything shorter is a flash that just
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
        # Default color: hyperframe â†’ brand_color â†’ electric yellow.
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
    total_frames = max(1, int(total_duration * fps))

    import logging as _logging
    import shutil as _shutil
    import tempfile as _tempfile
    _log = _logging.getLogger(__name__)

    # â”€â”€ Pre-render motion-graphics PNGs â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    glow_color = brand_color or "#4FC3F7"
    mg_dir = work_dir / "motion_graphics"
    rendered_graphics: list[RenderedGraphic] = []
    for idx, mg in enumerate(plan.motion_graphics or []):
        try:
            rg = render_motion_graphic(
                mg, mg_dir, idx,
                target_w=target_w, target_h=target_h,
                accent_hex=glow_color,
                aesthetic=aesthetic,
                subject_pos=subject_position,
            )
            if rg is not None:
                rendered_graphics.append(rg)
        except Exception as _e:
            _log.warning("motion graphic %d (%s) skipped: %s",
                         idx, mg.get("kind"), _e)

    # â”€â”€ Smart dimension detection â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # _cut_segment / _create_proxy already scale to target dims, so this
    # is usually a no-op â€” but we handle every edge case explicitly.
    color_grade = _color_grade_filter(content_type)
    concat_info = _probe_video_info(concat_path)
    src_w = concat_info.get("width",  0) or target_w
    src_h = concat_info.get("height", 0) or target_h

    if src_w == target_w and src_h == target_h:
        scale_filter: str | None = None          # already correct â€” skip re-scale
    elif src_w >= src_h:
        # Landscape / square â†’ portrait: fill frame, no letterbox
        scale_filter = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h}"
        )
    else:
        # Portrait source: fix the constrained axis, auto-compute the other
        scale_filter = (
            f"scale=-2:{target_h}" if short_form else f"scale={target_w}:-2"
        )

    system_font = _find_system_font()

    # â”€â”€ Pre-render hyperframe flash PNGs â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Each flash is a full-frame solid-color PNG (+ optional text) turned into
    # a timed MKV clip via _png_to_timed_clip() â€” same mechanism as motion
    # graphics. This completely eliminates enable= from the video filter chain:
    # no drawbox, no drawtext, no gte/lte timing expressions.
    hf_dir = work_dir / "hyperframes"
    hf_dir.mkdir(parents=True, exist_ok=True)
    hf_graphics: list[RenderedGraphic] = []
    for i, hf in enumerate(remapped_hyperframes):
        try:
            hf_at = float(hf.get("at", 0))
            hf_dur = max(0.05, min(0.2, float(hf.get("duration", 0.1))))
            hf_color = _hex_to_rgb_at(hf.get("color") or brand_color or "#FFE500")
            hf_text = str(hf.get("content") or "").strip()
            hf_kind = (hf.get("kind") or "color").lower()
            hf_png = hf_dir / f"hf_{i:03d}.png"
            _render_hyperframe_png(
                hf_color, hf_png, target_w, target_h,
                text=hf_text if hf_kind in {"word", "number"} else None,
                system_font=system_font,
            )
            hf_graphics.append(RenderedGraphic(
                png=hf_png, at=hf_at, duration=hf_dur,
                x_expr="0", y_expr="0", kind="hyperframe",
            ))
        except Exception as _e:
            _log.warning("hyperframe %d skipped: %s", i, _e)

    # â”€â”€ Convert all PNGs to timed RGBA MKV clips â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Hyperframes first in the chain so motion graphics overlay on top of them
    # (matching original drawbox â†’ overlay ordering).
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

    # â”€â”€ Decide pipeline: filter_complex or simple -vf â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    use_fc = bool(ok_graphics or remapped_silences)

    _tmp_dir = Path(_tempfile.gettempdir())
    _nocap_path = _tmp_dir / f"nocap_{output_path.stem}.mp4"

    # â”€â”€ Pass 1: grade + scale + zoompan + timed clips + volume duck â”€â”€â”€â”€â”€â”€â”€
    if use_fc:
        fc_str, v_out, a_out = _build_pass1_filter_complex(
            target_w, target_h, fps,
            color_grade, scale_filter,
            remapped_silences,
            ok_graphics,
        )
        cmd1: list[str] = [
            FFMPEG_PATH, "-y", "-loglevel", "error",
            "-i", str(concat_path),
        ]
        for cp in graphic_clip_paths:   # MKV clip inputs at indices 1..N
            cmd1 += ["-i", str(cp)]
        cmd1 += ["-filter_complex", fc_str, "-map", f"[{v_out}]"]
        cmd1 += ["-map", f"[{a_out}]"] if a_out else ["-map", "0:a"]
        _log.info(
            "render pass1: filter_complex  hf=%d  graphics=%d  silences=%d",
            len(hf_graphics), len([r for r in ok_graphics if r.kind != "hyperframe"]),
            len(remapped_silences),
        )
    else:
        # Simple -vf: grade + scale + constant zoompan
        zoom_str = (
            f"zoompan=z=1.04"
            f":x=iw/2-(iw/zoom/2)"
            f":y=ih/2-(ih/zoom/2)"
            f":d=1:s={target_w}x{target_h}:fps={fps}"
        )
        vf_parts = [p for p in [color_grade, scale_filter, zoom_str] if p]
        cmd1 = [
            FFMPEG_PATH, "-y", "-loglevel", "error",
            "-i", str(concat_path),
            "-vf", ",".join(vf_parts),
            "-map", "0:v", "-map", "0:a",
        ]
        _log.info("render pass1: simple -vf pipeline")

    cmd1 += [
        "-frames:v", str(total_frames),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-threads", "2",
        "-x264-params", "rc-lookahead=0:bframes=0",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(_nocap_path),
    ]
    _run(cmd1)

    # â”€â”€ Pass 2: burn ASS captions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Separate ffmpeg invocation so subtitle path escaping is handled by the
    # OS arg list (no shell expansion) â€” only the ffmpeg filter parser sees it.
    _ass_tmp = _tmp_dir / f"captions_{ass_path.parent.name}.ass"
    _shutil.copy2(ass_path, _ass_tmp)
    _ass_str = str(_ass_tmp).replace("\\", "/").replace(":", "\\:")
    _run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-i", str(_nocap_path),
        "-vf", f"subtitles={_ass_str}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-threads", "2",
        "-x264-params", "rc-lookahead=0:bframes=0",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ])
    _nocap_path.unlink(missing_ok=True)

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

