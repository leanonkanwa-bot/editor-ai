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

    is_caption = card.get("type") == "caption"

    # Graphic cards must NEVER use lower-third (reserved for captions)
    if not is_caption and zone == "lower-third":
        zone = "video-overlay"

    bounds = _zone_bounds(zone, layout)

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


# ── LeanGlass Style Pack ─────────────────────────────────────────────
# Dark glass panels with volumetric cyan glow, backdrop blur, subtle
# grain texture. One consistent visual identity across every card.

_LEAN_GLASS = {
    "bg": "linear-gradient(160deg, rgba(18,18,28,0.85), rgba(8,8,16,0.92))",
    "text": "#F1F1F1",
    "text_secondary": "rgba(255,255,255,0.6)",
    "accent": "#4cc9f0",
    "font": '"Inter", ui-sans-serif, system-ui, sans-serif',
    "title_size": "64px",
    "number_size": "96px",
    "kicker_size": "22px",
    "detail_size": "26px",
    "border": "1px solid rgba(76,201,240,0.12)",
    "radius": "20px",
    "shadow": "0 0 60px rgba(76,201,240,0.15), 0 8px 32px rgba(0,0,0,0.4)",
    "shadow_inset": "inset 0 1px 0 rgba(255,255,255,0.06)",
    "title_glow": "0 0 40px rgba(76,201,240,0.25)",
}

# Inline SVG grain texture (deterministic, no external asset)
_GRAIN_SVG = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E"
    "%3Cfilter id='g'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.65' "
    "numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E"
    "%3Crect width='100%25' height='100%25' filter='url(%23g)' opacity='0.04'/%3E%3C/svg%3E"
)


def _build_graphic_card_html(card: dict) -> str:
    """Build inner HTML for a LeanGlass graphic overlay card."""
    card_id = card["id"]
    hints = card.get("contentHints", {})
    kicker = hints.get("kicker", "")
    title = hints.get("title", "")
    detail = hints.get("detail", "")
    number = hints.get("number", "")
    p = _LEAN_GLASS

    display_text = number if number else title
    title_size = p["number_size"] if number else p["title_size"]

    parts = [f'<div class="card" data-card-id="{card_id}">']
    parts.append('<style>')
    parts.append(f'.card[data-card-id="{card_id}"] .root {{')
    parts.append('  width: 100%; height: 100%; display: flex; flex-direction: column;')
    parts.append('  justify-content: center; align-items: center;')
    parts.append('  padding: 48px; gap: 16px;')
    parts.append('}')
    parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{')
    parts.append(f'  background: {p["bg"]};')
    parts.append(f'  border-radius: {p["radius"]};')
    parts.append(f'  border: {p["border"]};')
    parts.append(f'  padding: 44px 52px;')
    parts.append(f'  display: flex; flex-direction: column; align-items: center;')
    parts.append(f'  gap: 14px; max-width: 85%; position: relative;')
    parts.append(f'  box-shadow: {p["shadow"]}, {p["shadow_inset"]};')
    parts.append('}')
    parts.append(f'.card[data-card-id="{card_id}"] .card-panel::after {{')
    parts.append(f'  content: ""; position: absolute; inset: 0;')
    parts.append(f'  border-radius: {p["radius"]};')
    parts.append(f'  background-image: url("{_GRAIN_SVG}");')
    parts.append(f'  background-repeat: repeat; pointer-events: none;')
    parts.append('}')
    if kicker:
        parts.append(f'.card[data-card-id="{card_id}"] .kicker {{')
        parts.append(f'  font-family: {p["font"]}; font-size: {p["kicker_size"]};')
        parts.append(f'  font-weight: 700; letter-spacing: 0.15em; text-transform: uppercase;')
        parts.append(f'  color: {p["accent"]};')
        parts.append('}')
    parts.append(f'.card[data-card-id="{card_id}"] .title {{')
    parts.append(f'  font-family: {p["font"]}; font-size: {title_size};')
    parts.append(f'  font-weight: 800; line-height: 1.15; text-align: center;')
    parts.append(f'  color: {p["text"]}; max-width: 100%;')
    parts.append(f'  text-shadow: {p["title_glow"]};')
    parts.append(f'  font-variant-numeric: tabular-nums;')
    parts.append('}')
    if detail:
        parts.append(f'.card[data-card-id="{card_id}"] .detail {{')
        parts.append(f'  font-family: {p["font"]}; font-size: {p["detail_size"]};')
        parts.append(f'  font-weight: 400; text-align: center;')
        parts.append(f'  color: {p["text_secondary"]}; max-width: 90%;')
        parts.append('}')
    parts.append(f'.card[data-card-id="{card_id}"] .accent-line {{')
    parts.append(f'  width: 0; height: 3px; background: {p["accent"]};')
    parts.append(f'  border-radius: 2px; box-shadow: 0 0 12px {p["accent"]};')
    parts.append('}')
    # Shimmer sweep (registry component: shimmer-sweep)
    parts.append(f'.card[data-card-id="{card_id}"] .card-panel .shimmer-mask {{')
    parts.append(f'  position: absolute; top: 0; left: 0; width: 100%; height: 100%;')
    parts.append(f'  pointer-events: none; border-radius: {p["radius"]};')
    parts.append(f'  background: linear-gradient(120deg,')
    parts.append(f'    transparent 0%,')
    parts.append(f'    transparent calc(var(--shimmer-pos, -20%) - 10%),')
    parts.append(f'    rgba(76,201,240,0.15) var(--shimmer-pos, -20%),')
    parts.append(f'    transparent calc(var(--shimmer-pos, -20%) + 10%),')
    parts.append(f'    transparent 100%);')
    parts.append(f'  mix-blend-mode: overlay; z-index: 2;')
    parts.append('}')
    parts.append('</style>')
    parts.append('<div class="root">')
    parts.append('  <div class="card-panel">')
    if kicker:
        parts.append(f'    <div class="kicker" id="{card_id}-kicker">{_esc(kicker)}</div>')
    parts.append(f'    <div class="title" id="{card_id}-title">{_esc(display_text)}</div>')
    if detail:
        parts.append(f'    <div class="detail" id="{card_id}-detail">{_esc(detail)}</div>')
    parts.append(f'    <div class="accent-line" id="{card_id}-line"></div>')
    parts.append(f'    <div class="shimmer-mask" id="{card_id}-shimmer"></div>')
    parts.append('  </div>')
    parts.append('</div>')
    parts.append('</div>')
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
        f'  font-family: {_LEAN_GLASS["font"]};\n'
        f'  font-size: 48px; font-weight: 700; color: #FFFFFF;\n'
        f'  text-shadow: 0 2px 8px rgba(0,0,0,0.8), 0 0 2px rgba(0,0,0,0.9);\n'
        f'  text-align: center; line-height: 1.3;\n'
        f'}}\n'
        f'.card[data-card-id="{card_id}"] .cap-emphasis {{\n'
        f'  color: {_LEAN_GLASS["accent"]}; transform: scale(1.08);\n'
        f'}}\n'
        f'</style>\n'
        f'<div class="cap-line" id="{card_id}-line">\n'
        f'  {" ".join(word_spans)}\n'
        f'</div>\n'
        f'</div>'
    )


def _build_timeline_js(
    cards: list[dict],
    zoom_entries: list[dict] | None = None,
    subject_position: dict | None = None,
) -> str:
    """Build the master GSAP timeline script including zoom/pan on the video wrapper."""
    lines = [
        "(function () {",
        '  const tl = window.gsap.timeline({ paused: true });',
        "",
    ]

    # Face-aware transform origin for zoom (Phase D)
    if subject_position:
        fl = float(subject_position.get("face_left_pct", 25.0))
        fr = float(subject_position.get("face_right_pct", 75.0))
        ft = float(subject_position.get("face_top_pct", 15.0))
        fb = float(subject_position.get("face_bottom_pct", 65.0))
        face_cx = max(20.0, min(80.0, (fl + fr) / 2))
        face_cy = max(20.0, min(80.0, (ft + fb) / 2))
    else:
        face_cx, face_cy = 50.0, 50.0
    transform_origin = f"{face_cx:.1f}% {face_cy:.1f}%"

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
                f'transformOrigin: "{transform_origin}" }}, '
                f'{zs:.4f});'
            )
        lines.append("")

    for card in cards:
        card_id = _esc_js(str(card.get("id", f"unknown-{id(card)}")))
        start = float(card.get("startSec", 0))
        end = float(card.get("endSec", start + 3))
        dur = end - start
        sel = f'.card-host[data-card-id="{card_id}"]'

        is_caption = card.get("type") == "caption"

        if is_caption:
            fade_in_dur = 0.18
            fade_out_dur = 0.15
        else:
            fade_in_dur = min(0.4, dur * 0.15)
            fade_out_dur = min(0.35, dur * 0.12)

        # Wrap each card's animations in try-catch so one bad card
        # cannot crash the entire timeline registration.
        lines.append(f'  try {{')

        lines.append(f'  tl.set(\'{sel}\', {{ visibility: "visible" }}, {start:.4f});')

        if is_caption:
            lines.append(
                f'  tl.fromTo(\'{sel}\', '
                f'{{ opacity: 0 }}, '
                f'{{ opacity: 1, duration: {fade_in_dur:.3f}, ease: "power2.out" }}, '
                f'{start:.4f});'
            )
            word_sel = f'.card[data-card-id="{card_id}"] .cap-word'
            word_count = len(card.get("words", []))
            if word_count > 0:
                lines.append(
                    f'  tl.set(\'{word_sel}\', {{ opacity: 1, y: 0 }}, {start:.4f});'
                )
        else:
            panel_sel = f'.card[data-card-id="{card_id}"] .card-panel'
            lines.append(
                f'  tl.fromTo(\'{sel}\', '
                f'{{ opacity: 0 }}, '
                f'{{ opacity: 1, duration: 0.300, ease: "power2.out" }}, '
                f'{start:.4f});'
            )
            lines.append(
                f'  tl.fromTo(\'{panel_sel}\', '
                f'{{ scale: 1.04, y: 14 }}, '
                f'{{ scale: 1, y: 0, duration: 0.350, ease: "power2.out" }}, '
                f'{start:.4f});'
            )

            content_style = card.get("contentHints", {}).get("style", "key_phrase")
            title_sel = f'.card[data-card-id="{card_id}"] #{card_id}-title'
            kicker_sel = f'.card[data-card-id="{card_id}"] #{card_id}-kicker'
            line_sel = f'.card[data-card-id="{card_id}"] #{card_id}-line'
            t_in = start + 0.15

            if content_style == "stat" and card.get("contentHints", {}).get("number"):
                num_val, num_suffix = _safe_number(card["contentHints"]["number"])
                if num_val is not None:
                    # Count-up with color flash + scale pulse (apple-money-count pattern)
                    count_dur = min(1.5, max(0.6, dur * 0.25))
                    count_end = t_in + count_dur
                    lines.append(
                        f'  (function(){{ var o={{v:0}}; tl.to(o, {{v:{num_val}, '
                        f'duration: {count_dur:.3f}, ease: "power2.out", onUpdate: function(){{ '
                        f'var el=document.querySelector(\'{title_sel}\'); '
                        f'if(el) el.textContent=Math.round(o.v).toLocaleString()+\'{_esc_js(num_suffix)}\'; '
                        f'}}}}, {t_in:.4f}); }})();'
                    )
                    # Scale pulse on count completion
                    lines.append(
                        f'  tl.to(\'{title_sel}\', '
                        f'{{ scale: 1.08, duration: 0.12, ease: "back.out(2)" }}, '
                        f'{count_end:.4f});'
                    )
                    lines.append(
                        f'  tl.to(\'{title_sel}\', '
                        f'{{ scale: 1, duration: 0.20, ease: "power2.out" }}, '
                        f'{count_end + 0.12:.4f});'
                    )
                    # Accent color flash on count completion
                    lines.append(
                        f'  tl.to(\'{title_sel}\', '
                        f'{{ color: "{_LEAN_GLASS["accent"]}", '
                        f'textShadow: "0 0 48px rgba(76,201,240,0.4)", '
                        f'duration: 0.15 }}, {count_end:.4f});'
                    )
                    lines.append(
                        f'  tl.to(\'{title_sel}\', '
                        f'{{ color: "{_LEAN_GLASS["text"]}", '
                        f'textShadow: "{_esc_js(_LEAN_GLASS["title_glow"])}", '
                        f'duration: 0.6 }}, {count_end + 0.15:.4f});'
                    )
                else:
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ opacity: 0, y: 20 }}, '
                        f'{{ opacity: 1, y: 0, duration: 0.400, ease: "power2.out" }}, '
                        f'{t_in:.4f});'
                    )
            elif content_style == "key_phrase":
                lines.append(
                    f'  tl.fromTo(\'{title_sel}\', '
                    f'{{ clipPath: "inset(0 100% 0 0)" }}, '
                    f'{{ clipPath: "inset(0 0% 0 0)", duration: 0.500, ease: "power2.inOut" }}, '
                    f'{t_in:.4f});'
                )
            elif content_style == "quote":
                lines.append(
                    f'  tl.fromTo(\'{title_sel}\', '
                    f'{{ opacity: 0, y: 40 }}, '
                    f'{{ opacity: 1, y: 0, duration: 0.500, ease: "power3.out" }}, '
                    f'{t_in:.4f});'
                )
            else:
                lines.append(
                    f'  tl.fromTo(\'{title_sel}\', '
                    f'{{ opacity: 0, y: 20 }}, '
                    f'{{ opacity: 1, y: 0, duration: 0.400, ease: "power2.out" }}, '
                    f'{t_in:.4f});'
                )

            lines.append(
                f'  tl.fromTo(\'{kicker_sel}\', '
                f'{{ opacity: 0, y: -8 }}, '
                f'{{ opacity: 1, y: 0, duration: 0.250, ease: "power2.out" }}, '
                f'{start + 0.10:.4f});'
            )
            lines.append(
                f'  tl.fromTo(\'{line_sel}\', '
                f'{{ width: 0 }}, '
                f'{{ width: 120, duration: 0.400, ease: "power2.out" }}, '
                f'{t_in + 0.30:.4f});'
            )
            # Shimmer sweep across glass panel after materialization
            shimmer_sel = f'.card[data-card-id="{card_id}"] #{card_id}-shimmer'
            shimmer_start = start + 0.50
            lines.append(
                f'  tl.fromTo(\'{shimmer_sel}\', '
                f'{{ "--shimmer-pos": "-20%" }}, '
                f'{{ "--shimmer-pos": "120%", duration: 0.9, ease: "power2.inOut" }}, '
                f'{shimmer_start:.4f});'
            )

        # Exit
        if is_caption:
            exit_start = end - fade_out_dur
            lines.append(
                f'  tl.to(\'{sel}\', '
                f'{{ opacity: 0, duration: {fade_out_dur:.3f}, ease: "power2.in" }}, '
                f'{exit_start:.4f});'
            )
        else:
            exit_start = end - 0.30
            panel_sel = f'.card[data-card-id="{card_id}"] .card-panel'
            lines.append(
                f'  tl.to(\'{sel}\', '
                f'{{ opacity: 0, duration: 0.300, ease: "power2.in" }}, '
                f'{exit_start:.4f});'
            )
            lines.append(
                f'  tl.to(\'{panel_sel}\', '
                f'{{ scale: 0.97, duration: 0.300, ease: "power2.in" }}, '
                f'{exit_start:.4f});'
            )
        lines.append(f'  tl.set(\'{sel}\', {{ visibility: "hidden" }}, {end:.4f});')

        lines.append(f'  }} catch(_e) {{ console.warn("card {card_id} animation error:", _e); }}')
        lines.append("")

    # Caption suppression: fade captions out while graphic cards are visible
    graphic_windows = [
        (float(c.get("startSec", 0)), float(c.get("endSec", 0)))
        for c in cards if c.get("type") != "caption"
    ]
    caption_ids = [
        _esc_js(str(c.get("id", "")))
        for c in cards if c.get("type") == "caption"
    ]
    if graphic_windows and caption_ids:
        lines.append("  // ── Caption suppression during graphic cards ──")
        cap_sel = ", ".join(
            f'.card-host[data-card-id="{cid}"]' for cid in caption_ids
        )
        for gs, ge in graphic_windows:
            lines.append(
                f'  tl.to(\'{cap_sel}\', '
                f'{{ opacity: 0, duration: 0.15, ease: "power2.in" }}, '
                f'{gs:.4f});'
            )
            lines.append(
                f'  tl.to(\'{cap_sel}\', '
                f'{{ opacity: 1, duration: 0.20, ease: "power2.out" }}, '
                f'{ge:.4f});'
            )
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


import re as _re

_NUM_RE = _re.compile(r"[\d]+(?:[,.][\d]+)*")


def _safe_number(raw: str) -> tuple[float | None, str]:
    """Extract a clean numeric value and display suffix from a Claude-generated number string.

    Returns (numeric_value, suffix) where suffix is '%', '$', or ''.
    Returns (None, '') if no number can be extracted.
    """
    if not raw or not raw.strip():
        return None, ""
    suffix = ""
    if "%" in raw:
        suffix = "%"
    elif "$" in raw:
        suffix = "$"
    m = _NUM_RE.search(raw)
    if not m:
        return None, suffix
    try:
        return float(m.group(0).replace(",", "")), suffix
    except (ValueError, OverflowError):
        return None, suffix


def _esc_js(s: str) -> str:
    """Escape a string for safe embedding inside a JS single-quoted string literal."""
    return s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "")


def compose(
    storyboard: dict,
    trimmed_video: Path,
    work_dir: Path,
    zoom_entries: list[dict] | None = None,
    style_pack: str = "lean_glass",
    subject_position: dict | None = None,
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

    # Copy the pre-trimmed video directly — pretrim.py already re-encoded
    # with dense keyframes (g=30, keyint_min=30). A second re-encode would
    # introduce PTS rounding errors that compound into progressive drift.
    video_dst = public_dir / "input-video.mp4"
    shutil.copy2(str(trimmed_video), str(video_dst))
    print(f"[COMPOSE] Video copied: {video_dst} ({video_dst.stat().st_size // 1024}KB)")
    print(f"[COMPOSE] Storyboard duration: {duration:.3f}s")

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
    timeline_js = _build_timeline_js(all_cards, zoom_entries=zoom_entries, subject_position=subject_position)

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
