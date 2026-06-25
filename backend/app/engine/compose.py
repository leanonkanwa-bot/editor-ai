"""
Composition assembly: build a HyperFrames project directory from a storyboard.

Stage 3 of the HyperFrames pipeline. Takes a storyboard JSON (from
storyboard.py) and the trimmed video, writes a complete HyperFrames
project directory that `npx hyperframes render` can consume.

Follows the graphic-overlays SKILL.md composition template exactly:
  - Root: <div data-composition-id> with data-start/duration/fps/width/height
  - Video: .video-wrapper > <video muted playsinline> on track 1
  - Cards: .card-host.clip on track 2 (graphics) / track 3 (captions)
  - Script: single paused GSAP timeline registered on window.__timelines
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app.engine.transcribe import FFMPEG_PATH

_COMP_ID = "graphic-overlays"

# Zone → pixel bounds for landscape (1920×1080)
_ZONE_BOUNDS_LANDSCAPE = {
    "fullscreen":      {"left": 0, "top": 0, "width": 1920, "height": 1080},
    "lower-third":     {"left": 0, "top": 756, "width": 1920, "height": 324},
    "side-panel":      {"left": 0, "top": 0, "width": 806, "height": 1080},
    "whiteboard-area": {"left": 40, "top": 40, "width": 1840, "height": 1000},
    "video-overlay":   {"left": 0, "top": 0, "width": 1920, "height": 1080},
}

# Zone → pixel bounds for portrait (1080×1920)
_ZONE_BOUNDS_PORTRAIT = {
    "fullscreen":      {"left": 0, "top": 0, "width": 1080, "height": 1920},
    "lower-third":     {"left": 0, "top": 1344, "width": 1080, "height": 576},
    "side-panel":      {"left": 0, "top": 1152, "width": 1080, "height": 768},
    "whiteboard-area": {"left": 40, "top": 864, "width": 1000, "height": 1016},
    "video-overlay":   {"left": 0, "top": 0, "width": 1080, "height": 1920},
}

# Theme palettes from graphic-overlays SKILL.md
_THEMES = {
    "noir":    {"bg": "#1a1a1a", "text": "#f1f1f1", "accents": ["#4cc9f0", "#f72585", "#4ade80", "#fb923c", "#a78bfa"]},
    "classic": {"bg": "#FFF9E3", "text": "#1e1e1e", "accents": ["#1971c2", "#e03131", "#2f9e44", "#e8590c", "#9c36b5"]},
    "slate":   {"bg": "#1e293b", "text": "#f1f5f9", "accents": ["#0ea5e9", "#ef4444", "#22c55e", "#f97316", "#a855f7"]},
    "mono":    {"bg": "#fff",    "text": "#000",    "accents": ["#000", "#555", "#888", "#aaa", "#ccc"]},
}


def _zone_bounds(zone: str, layout: str) -> dict:
    table = _ZONE_BOUNDS_PORTRAIT if layout == "portrait" else _ZONE_BOUNDS_LANDSCAPE
    return table.get(zone, table["lower-third"])


def _build_card_host(card: dict, layout: str, track_index: int) -> str:
    """Build a card-host div with correct classes, data attributes, and inline bounds."""
    card_id = card["id"]
    start = float(card.get("startSec", 0))
    duration = float(card.get("endSec", start + 3)) - start
    zone = card.get("zone", "lower-third")
    bounds = _zone_bounds(zone, layout)

    is_caption = card.get("type") == "caption"

    if is_caption:
        inner = _build_caption_card_html(card)
    else:
        inner = _build_graphic_card_html(card)

    return (
        f'<div class="card-host clip" data-card-id="{card_id}" '
        f'data-start="{start:.4f}" data-duration="{duration:.4f}" '
        f'data-track-index="{track_index}" '
        f'style="left:{bounds["left"]}px;top:{bounds["top"]}px;'
        f'width:{bounds["width"]}px;height:{bounds["height"]}px;'
        f'visibility:hidden;opacity:0;z-index:{20 if is_caption else 10};">\n'
        f'{inner}\n'
        f'</div>'
    )


def _build_graphic_card_html(card: dict) -> str:
    """Build inner HTML for a graphic overlay card."""
    card_id = card["id"]
    hints = card.get("contentHints", {})
    kicker = hints.get("kicker", "")
    title = hints.get("title", "")
    detail = hints.get("detail", "")
    number = hints.get("number", "")
    style = hints.get("style", "key_phrase")

    parts = [f'<div class="card" data-card-id="{card_id}">']
    parts.append(f'<style>')
    parts.append(f'.card[data-card-id="{card_id}"] .root {{')
    parts.append(f'  width: 100%; height: 100%; display: flex; flex-direction: column;')
    parts.append(f'  justify-content: center; align-items: center; padding: 40px;')
    parts.append(f'  font-family: "Inter", "Montserrat", ui-sans-serif, system-ui, sans-serif;')
    parts.append(f'  color: var(--text); gap: 12px;')
    parts.append(f'}}')
    if kicker:
        parts.append(f'.card[data-card-id="{card_id}"] .kicker {{')
        parts.append(f'  font-size: 24px; font-weight: 700; letter-spacing: 0.15em;')
        parts.append(f'  text-transform: uppercase; color: var(--accent-0); opacity: 0.9;')
        parts.append(f'}}')
    parts.append(f'.card[data-card-id="{card_id}"] .title {{')
    if number:
        parts.append(f'  font-size: 96px; font-weight: 900; line-height: 1.1;')
    else:
        parts.append(f'  font-size: 64px; font-weight: 800; line-height: 1.15;')
    parts.append(f'  text-align: center; max-width: 90%;')
    parts.append(f'}}')
    if detail:
        parts.append(f'.card[data-card-id="{card_id}"] .detail {{')
        parts.append(f'  font-size: 28px; font-weight: 400; opacity: 0.7; text-align: center;')
        parts.append(f'  max-width: 80%;')
        parts.append(f'}}')
    parts.append(f'</style>')
    parts.append(f'<div class="root">')
    if kicker:
        parts.append(f'  <div class="kicker" id="{card_id}-kicker">{_esc(kicker)}</div>')
    display_text = number if number else title
    parts.append(f'  <div class="title" id="{card_id}-title">{_esc(display_text)}</div>')
    if detail:
        parts.append(f'  <div class="detail" id="{card_id}-detail">{_esc(detail)}</div>')
    parts.append(f'</div>')
    parts.append(f'</div>')
    return "\n".join(parts)


def _build_caption_card_html(card: dict) -> str:
    """Build inner HTML for a caption card with per-word spans."""
    card_id = card["id"]
    words = card.get("words", [])

    word_spans = []
    for w in words:
        text = w.get("text", "")
        emphasis = w.get("emphasis", False)
        cls = "cap-word cap-emphasis" if emphasis else "cap-word"
        word_spans.append(f'<span class="{cls}">{_esc(text)}</span>')

    return (
        f'<div class="card caption-card" data-card-id="{card_id}">\n'
        f'<style>\n'
        f'.card[data-card-id="{card_id}"] .cap-line {{\n'
        f'  display: flex; flex-wrap: wrap; justify-content: center; align-items: baseline;\n'
        f'  gap: 0.3em; padding: 16px 24px;\n'
        f'  font-family: "Inter", "Montserrat", ui-sans-serif, system-ui, sans-serif;\n'
        f'  font-size: 48px; font-weight: 700; color: #FFFFFF;\n'
        f'  text-shadow: 0 2px 8px rgba(0,0,0,0.8), 0 0 2px rgba(0,0,0,0.9);\n'
        f'  text-align: center; line-height: 1.3;\n'
        f'}}\n'
        f'.card[data-card-id="{card_id}"] .cap-emphasis {{\n'
        f'  color: var(--accent-0); transform: scale(1.1);\n'
        f'}}\n'
        f'</style>\n'
        f'<div class="cap-line" id="{card_id}-line">\n'
        f'  {" ".join(word_spans)}\n'
        f'</div>\n'
        f'</div>'
    )


def _build_timeline_js(cards: list[dict], zoom_entries: list[dict] | None = None) -> str:
    """Build the master GSAP timeline script including zoom/pan on the video wrapper."""
    lines = [
        "(function () {",
        '  const tl = window.gsap.timeline({ paused: true });',
        "",
    ]

    # ── Zoom/pan: CSS transform on #video-wrap ──────────────────────────
    # Each zoom entry becomes a tween that scales the video wrapper from
    # `from` to `to` over the entry's time range, using the same easing
    # curves proven mathematically identical to the FFmpeg expressions
    # (cosine for drift, quadratic for punch_in — verified 0.00 delta).
    if zoom_entries:
        lines.append("  // ── Zoom/pan on video wrapper ──")
        for ze in zoom_entries:
            zs = float(ze.get("start", 0))
            ze_end = float(ze.get("end", zs + 1))
            zfrom = float(ze.get("from", 1.0))
            zto = float(ze.get("to", zfrom))
            kind = ze.get("kind", "drift")
            zdur = max(0.001, ze_end - zs)

            if kind == "punch_in" or kind == "pull_out":
                ease = '"power2.in"'
            else:
                ease = '"sine.inOut"'

            lines.append(
                f'  tl.fromTo("#video-wrap", '
                f'{{ scale: {zfrom:.4f} }}, '
                f'{{ scale: {zto:.4f}, duration: {zdur:.4f}, ease: {ease}, '
                f'transformOrigin: "center center" }}, '
                f'{zs:.4f});'
            )
        lines.append("")

    for card in cards:
        card_id = card["id"]
        start = float(card.get("startSec", 0))
        end = float(card.get("endSec", start + 3))
        dur = end - start
        sel = f'.card-host[data-card-id="{card_id}"]'

        is_caption = card.get("type") == "caption"

        if is_caption:
            # Captions: near-instant appearance (no cinematic fade)
            fade_in_dur = 0.05
            fade_out_dur = 0.05
        else:
            fade_in_dur = min(0.4, dur * 0.15)
            fade_out_dur = min(0.35, dur * 0.12)

        # Enter: set visible + fade in
        lines.append(f'  tl.set(\'{sel}\', {{ visibility: "visible" }}, {start:.4f});')
        lines.append(
            f'  tl.fromTo(\'{sel}\', '
            f'{{ opacity: 0 }}, '
            f'{{ opacity: 1, duration: {fade_in_dur:.3f}, ease: "power2.out" }}, '
            f'{start:.4f});'
        )

        # Caption cards: all words appear together (no stagger delay)
        if is_caption:
            word_sel = f'.card[data-card-id="{card_id}"] .cap-word'
            word_count = len(card.get("words", []))
            if word_count > 0:
                lines.append(
                    f'  tl.set(\'{word_sel}\', {{ opacity: 1, y: 0 }}, {start:.4f});'
                )

        # Graphic cards: scale-pop entrance on title
        else:
            title_sel = f'.card[data-card-id="{card_id}"] #{card_id}-title'
            lines.append(
                f'  tl.fromTo(\'{title_sel}\', '
                f'{{ opacity: 0, scale: 0.85, y: 20 }}, '
                f'{{ opacity: 1, scale: 1, y: 0, duration: 0.5, ease: "back.out(1.4)" }}, '
                f'{start + fade_in_dur * 0.5:.4f});'
            )
            kicker_sel = f'.card[data-card-id="{card_id}"] #{card_id}-kicker'
            lines.append(
                f'  tl.fromTo(\'{kicker_sel}\', '
                f'{{ opacity: 0, y: -10 }}, '
                f'{{ opacity: 0.9, y: 0, duration: 0.3, ease: "power2.out" }}, '
                f'{start + fade_in_dur * 0.3:.4f});'
            )

        # Exit: fade out + set hidden
        exit_start = end - fade_out_dur
        lines.append(
            f'  tl.to(\'{sel}\', '
            f'{{ opacity: 0, duration: {fade_out_dur:.3f}, ease: "power2.in" }}, '
            f'{exit_start:.4f});'
        )
        lines.append(f'  tl.set(\'{sel}\', {{ visibility: "hidden" }}, {end:.4f});')
        lines.append("")

    lines.append('  window.__timelines = window.__timelines || {};')
    lines.append(f'  window.__timelines["{_COMP_ID}"] = tl;')
    lines.append("})();")
    return "\n".join(lines)


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def compose(
    storyboard: dict,
    trimmed_video: Path,
    work_dir: Path,
    zoom_entries: list[dict] | None = None,
) -> Path:
    """Assemble a HyperFrames project directory from a storyboard.

    zoom_entries: list of {start, end, from, to, kind} in the trimmed
    video's timeline. These become CSS transform: scale() tweens on the
    video wrapper, replacing FFmpeg's scale+crop zoom pipeline.

    Returns the project directory path (containing public/index.html).
    """
    comp = storyboard.get("composition", {})
    width = comp.get("width", 1920)
    height = comp.get("height", 1080)
    fps = comp.get("fps", 30)
    duration = comp.get("durationSeconds", 60)
    layout = comp.get("layout", "landscape")
    theme_id = comp.get("themeId", "noir")
    theme = _THEMES.get(theme_id, _THEMES["noir"])

    project_dir = work_dir / "hf_project"
    public_dir = project_dir / "public"
    vendor_dir = public_dir / "vendor"
    public_dir.mkdir(parents=True, exist_ok=True)
    vendor_dir.mkdir(parents=True, exist_ok=True)

    # Copy GSAP from the graphic-overlays skill assets
    gsap_src = Path(__file__).resolve().parent.parent.parent.parent / ".agents" / "skills" / "graphic-overlays" / "assets" / "vendor" / "gsap.min.js"
    gsap_dst = vendor_dir / "gsap.min.js"
    if gsap_src.exists():
        shutil.copy2(gsap_src, gsap_dst)
    else:
        # Fallback: try the engine's node_modules
        gsap_fallback = Path(__file__).resolve().parent / "node_modules" / "gsap" / "dist" / "gsap.min.js"
        if gsap_fallback.exists():
            shutil.copy2(gsap_fallback, gsap_dst)
        else:
            print("[COMPOSE] WARNING: gsap.min.js not found")

    # Re-encode video with dense keyframes for HyperFrames seekability
    video_dst = public_dir / "input-video.mp4"
    import subprocess
    subprocess.run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-i", str(trimmed_video),
        "-c:v", "libx264", "-crf", "18",
        "-g", str(fps), "-keyint_min", str(fps),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "192k",
        str(video_dst),
    ], capture_output=True, text=True, timeout=300)

    # Separate cards by type for track assignment
    all_cards = storyboard.get("cards", [])
    graphic_cards = [c for c in all_cards if c.get("type") != "caption"]
    caption_cards = [c for c in all_cards if c.get("type") == "caption"]

    # Build card host divs
    card_hosts = []
    for c in graphic_cards:
        card_hosts.append(_build_card_host(c, layout, track_index=2))
    for c in caption_cards:
        card_hosts.append(_build_card_host(c, layout, track_index=3))

    # Build master timeline
    timeline_js = _build_timeline_js(all_cards, zoom_entries=zoom_entries)

    # CSS custom properties from theme
    accent_vars = "\n".join(
        f"    --accent-{i}: {color};" for i, color in enumerate(theme["accents"])
    )

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<style>
  :root {{
    --bg: {theme["bg"]};
    --text: {theme["text"]};
{accent_vars}
  }}
  * {{ box-sizing: border-box; }}
  html, body {{
    margin: 0; padding: 0; width: 100%; height: 100%;
    overflow: hidden; background: #000;
    font-family: "Inter", "Montserrat", ui-sans-serif, system-ui, sans-serif;
  }}
  #stage {{ position: relative; width: 100%; height: 100%; overflow: hidden; }}
  .video-wrapper {{
    position: absolute; left: 0; top: 0;
    width: {width}px; height: {height}px;
    overflow: hidden; border-radius: 0; box-shadow: none;
    transform-origin: center center;
  }}
  .video-wrapper video {{ width: 100%; height: 100%; object-fit: cover; }}
  #stage {{ overflow: hidden; }}
  .video-wrapper.framed {{
    border-radius: 16px;
    box-shadow: 0 12px 40px rgba(0,0,0,0.35);
  }}
  .card-host {{
    position: absolute; pointer-events: none; overflow: hidden;
  }}
  .card-host .card {{ position: relative; width: 100%; height: 100%; overflow: hidden; }}
  .card-host .char {{ display: inline-block; visibility: visible; }}
</style>
</head>
<body>
  <div id="stage"
       data-composition-id="{_COMP_ID}"
       data-start="0"
       data-duration="{duration:.3f}"
       data-fps="{fps}"
       data-width="{width}"
       data-height="{height}">

    <div class="video-wrapper" id="video-wrap">
      <video id="bg-video" src="input-video.mp4" muted playsinline
             data-start="0" data-duration="{duration:.3f}"
             data-track-index="1"></video>
    </div>

    <audio id="bg-audio" src="input-video.mp4"
           data-start="0" data-duration="{duration:.3f}"
           data-track-index="4" data-volume="1"></audio>

{chr(10).join(f"    {host}" for host in card_hosts)}

    <script src="vendor/gsap.min.js"></script>
    <script>
{timeline_js}
    </script>
  </div>
</body>
</html>"""

    (public_dir / "index.html").write_text(html, encoding="utf-8")
    print(f"[COMPOSE] Project written: {project_dir}")
    print(f"[COMPOSE] {len(graphic_cards)} graphic + {len(caption_cards)} caption card-hosts")

    return project_dir
