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


def _build_card_host(card: dict, layout: str, track_index: int, pack: dict | None = None) -> str:
    """Build a card-host div with correct classes, data attributes, and inline bounds."""
    card_id = card["id"]
    start = float(card.get("startSec", 0))
    duration = float(card.get("endSec", start + 3)) - start
    zone = card.get("zone", "lower-third")

    is_caption = card.get("type") == "caption"

    if not is_caption and zone == "lower-third":
        zone = "video-overlay"

    bounds = _zone_bounds(zone, layout)

    if is_caption:
        inner = _build_caption_card_html(card, pack=pack)
    else:
        inner = _build_graphic_card_html(card, pack=pack)

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


# ── Style Packs ──────────────────────────────────────────────────────
# Cross-pack constants (brand signature, not pack-specific)
_EASE_IN = "cubic-bezier(0.22, 0.68, 0.35, 1.03)"
_EASE_OUT_FAST = "cubic-bezier(0.55, 0, 0.85, 0.36)"
_EASE_VIBE_IN = "cubic-bezier(0.18, 0.89, 0.32, 1.12)"

_LEAN_GLASS = {
    "id": "lean_glass",
    "bg": "linear-gradient(160deg, rgba(18,18,28,0.85), rgba(8,8,16,0.92))",
    "text": "#F1F1F1",
    "text_secondary": "rgba(255,255,255,0.6)",
    "accent": "#4cc9f0",
    "font": '"Inter", ui-sans-serif, system-ui, sans-serif',
    "font_weight": "800",
    "title_size": "64px",
    "number_size": "96px",
    "kicker_size": "22px",
    "detail_size": "26px",
    "border": "1px solid rgba(76,201,240,0.12)",
    "radius": "20px",
    "shadow": "0 0 60px rgba(76,201,240,0.15), 0 8px 32px rgba(0,0,0,0.4)",
    "shadow_inset": "inset 0 1px 0 rgba(255,255,255,0.06)",
    "panel_filter": "blur(16px) saturate(1.4)",
    "title_glow": "0 0 40px rgba(76,201,240,0.25)",
    "title_glow_intense": "0 0 56px rgba(76,201,240,0.45)",
    "has_grain": True,
    "shimmer_color": "rgba(76,201,240,0.15)",
    "accent_line_glow": "0 0 12px #4cc9f0",
    "accent_line_glow_bright": "0 0 20px #4cc9f0",
    "backdrop_dim": "brightness(0.25) blur(4px)",
    "backdrop_restore": "brightness(1) blur(0px)",
}

_LEAN_PAPER = {
    "id": "lean_paper",
    "bg": "#FAFAF8",
    "text": "#1A1A1A",
    "text_secondary": "rgba(0,0,0,0.45)",
    "accent": "#4F6BFF",
    "font": '"Inter", ui-sans-serif, system-ui, sans-serif',
    "font_weight": "600",
    "title_size": "64px",
    "number_size": "96px",
    "kicker_size": "22px",
    "detail_size": "26px",
    "border": "1px solid rgba(0,0,0,0.06)",
    "radius": "12px",
    "shadow": "0 4px 24px rgba(0,0,0,0.06)",
    "shadow_inset": "",
    "panel_filter": "",
    "title_glow": "",
    "title_glow_intense": "",
    "has_grain": False,
    "shimmer_color": "rgba(79,107,255,0.10)",
    "accent_line_glow": "0 0 8px rgba(79,107,255,0.3)",
    "accent_line_glow_bright": "0 0 14px rgba(79,107,255,0.45)",
    "backdrop_dim": "brightness(1.6) blur(6px) saturate(0.3)",
    "backdrop_restore": "brightness(1) blur(0px) saturate(1)",
}

_LEAN_VIBE = {
    "id": "lean_vibe",
    "bg": "linear-gradient(135deg, #FF6B9D, #FFA94D)",
    "text": "#FFFFFF",
    "text_secondary": "rgba(255,255,255,0.75)",
    "accent": "#FFE66D",
    "font": '"Poppins", ui-sans-serif, system-ui, sans-serif',
    "font_weight": "800",
    "title_size": "64px",
    "number_size": "96px",
    "kicker_size": "22px",
    "detail_size": "26px",
    "border": "3px solid #FFFFFF",
    "radius": "24px",
    "shadow": "0 8px 32px rgba(255,107,157,0.3), 0 4px 16px rgba(0,0,0,0.15)",
    "shadow_inset": "",
    "panel_filter": "",
    "title_glow": "0 0 24px rgba(255,230,109,0.3)",
    "title_glow_intense": "0 0 40px rgba(255,230,109,0.5)",
    "has_grain": True,
    "grain_type": "confetti",
    "shimmer_color": "rgba(255,230,109,0.18)",
    "accent_line_glow": "0 0 10px rgba(255,230,109,0.4)",
    "accent_line_glow_bright": "0 0 18px rgba(255,230,109,0.6)",
    "backdrop_dim": "brightness(0.35) blur(5px) saturate(1.3)",
    "backdrop_restore": "brightness(1) blur(0px) saturate(1)",
}

_PACKS = {"lean_glass": _LEAN_GLASS, "lean_paper": _LEAN_PAPER, "lean_vibe": _LEAN_VIBE}

# Inline SVG textures
_GRAIN_SVG = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E"
    "%3Cfilter id='g'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.65' "
    "numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E"
    "%3Crect width='100%25' height='100%25' filter='url(%23g)' opacity='0.04'/%3E%3C/svg%3E"
)
_CONFETTI_SVG = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E"
    "%3Ccircle cx='25' cy='40' r='2' fill='%23fff' opacity='0.08'/%3E"
    "%3Ccircle cx='80' cy='15' r='1.5' fill='%23FFE66D' opacity='0.1'/%3E"
    "%3Ccircle cx='140' cy='65' r='2.5' fill='%23fff' opacity='0.06'/%3E"
    "%3Ccircle cx='50' cy='130' r='1.5' fill='%23FFE66D' opacity='0.08'/%3E"
    "%3Ccircle cx='170' cy='110' r='2' fill='%23fff' opacity='0.07'/%3E"
    "%3Ccircle cx='110' cy='170' r='1.5' fill='%23FFE66D' opacity='0.09'/%3E"
    "%3Ccircle cx='30' cy='180' r='2' fill='%23fff' opacity='0.05'/%3E"
    "%3Ccircle cx='160' cy='30' r='1.5' fill='%23fff' opacity='0.07'/%3E"
    "%3C/svg%3E"
)


def _build_graphic_card_html(card: dict, pack: dict | None = None) -> str:
    """Build inner HTML for a graphic overlay card using the given style pack."""
    card_id = card["id"]
    hints = card.get("contentHints", {})
    kicker = hints.get("kicker", "")
    title = hints.get("title", "")
    detail = hints.get("detail", "")
    number = hints.get("number", "")
    p = pack or _LEAN_GLASS

    display_text = number if number else title
    title_size = p["number_size"] if number else p["title_size"]

    shadow_val = f'{p["shadow"]}, {p["shadow_inset"]}' if p["shadow_inset"] else p["shadow"]
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
    parts.append(f'  box-shadow: {shadow_val};')
    if p["panel_filter"]:
        parts.append(f'  backdrop-filter: {p["panel_filter"]};')
        parts.append(f'  -webkit-backdrop-filter: {p["panel_filter"]};')
    parts.append('}')
    if p["has_grain"]:
        tex_svg = _CONFETTI_SVG if p.get("grain_type") == "confetti" else _GRAIN_SVG
        parts.append(f'.card[data-card-id="{card_id}"] .card-panel::after {{')
        parts.append(f'  content: ""; position: absolute; inset: 0;')
        parts.append(f'  border-radius: {p["radius"]};')
        parts.append(f'  background-image: url("{tex_svg}");')
        parts.append(f'  background-repeat: repeat; pointer-events: none;')
        parts.append('}')
    if kicker:
        parts.append(f'.card[data-card-id="{card_id}"] .kicker {{')
        parts.append(f'  font-family: {p["font"]}; font-size: {p["kicker_size"]};')
        parts.append(f'  font-weight: 700; letter-spacing: 0.15em; text-transform: uppercase;')
        parts.append(f'  color: {p["accent"]};')
        parts.append('}')
    glow_css = f'  text-shadow: {p["title_glow"]};' if p["title_glow"] else ''
    parts.append(f'.card[data-card-id="{card_id}"] .title {{')
    parts.append(f'  font-family: {p["font"]}; font-size: {title_size};')
    parts.append(f'  font-weight: {p["font_weight"]}; line-height: 1.15; text-align: center;')
    parts.append(f'  color: {p["text"]}; max-width: 100%;')
    if glow_css:
        parts.append(glow_css)
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
    parts.append(f'  border-radius: 2px; box-shadow: {p["accent_line_glow"]};')
    parts.append('}')
    parts.append(f'.card[data-card-id="{card_id}"] .card-panel .shimmer-mask {{')
    parts.append(f'  position: absolute; top: 0; left: 0; width: 100%; height: 100%;')
    parts.append(f'  pointer-events: none; border-radius: {p["radius"]};')
    parts.append(f'  background: linear-gradient(120deg,')
    parts.append(f'    transparent 0%,')
    parts.append(f'    transparent calc(var(--shimmer-pos, -20%) - 10%),')
    parts.append(f'    {p["shimmer_color"]} var(--shimmer-pos, -20%),')
    parts.append(f'    transparent calc(var(--shimmer-pos, -20%) + 10%),')
    parts.append(f'    transparent 100%);')
    parts.append(f'  mix-blend-mode: overlay; z-index: 2;')
    parts.append('}')
    content_style = hints.get("style", "")
    # Comparison: two-column layout
    if content_style == "comparison":
        parts.append(f'.card[data-card-id="{card_id}"] .cmp-row {{')
        parts.append(f'  display: flex; gap: 24px; align-items: center; width: 100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cmp-side {{')
        parts.append(f'  flex: 1; text-align: center;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cmp-label {{')
        parts.append(f'  font-family: {p["font"]}; font-size: {p["kicker_size"]};')
        parts.append(f'  font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase;')
        parts.append(f'  color: {p["text_secondary"]}; margin-bottom: 8px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cmp-value {{')
        parts.append(f'  font-family: {p["font"]}; font-size: {p["title_size"]};')
        parts.append(f'  font-weight: {p["font_weight"]}; color: {p["text"]};')
        parts.append(f'  font-variant-numeric: tabular-nums;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cmp-sep {{')
        parts.append(f'  width: 2px; height: 0; background: {p["accent"]};')
        parts.append(f'  border-radius: 1px; flex-shrink: 0;')
        if p["title_glow"]:
            parts.append(f'  box-shadow: {p["accent_line_glow"]};')
        parts.append('}')
    # Timeline: horizontal track with step dots
    if content_style == "timeline":
        steps = hints.get("steps", [])
        n_steps = min(len(steps), 6)
        parts.append(f'.card[data-card-id="{card_id}"] .tl-track {{')
        parts.append(f'  display: flex; align-items: center; gap: 0; width: 100%;')
        parts.append(f'  position: relative; padding: 24px 0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tl-line {{')
        parts.append(f'  position: absolute; top: 50%; left: 0; height: 2px; width: 0;')
        parts.append(f'  background: {p["accent"]};')
        if is_paper := (p["id"] == "lean_paper"):
            parts.append(f'  border-top: 2px dashed {p["accent"]};')
            parts.append(f'  background: transparent; height: 0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tl-step {{')
        parts.append(f'  display: flex; flex-direction: column; align-items: center;')
        parts.append(f'  gap: 8px; flex: 1; z-index: 1;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tl-dot {{')
        parts.append(f'  width: 16px; height: 16px; border-radius: 50%;')
        parts.append(f'  background: {p["text_secondary"]}; flex-shrink: 0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tl-label {{')
        parts.append(f'  font-family: {p["font"]}; font-size: 18px;')
        parts.append(f'  font-weight: {p["font_weight"]}; color: {p["text"]};')
        parts.append(f'  text-align: center; max-width: 140px;')
        parts.append('}')
    # Dialogue: two-block exchange
    if content_style == "dialogue":
        parts.append(f'.card[data-card-id="{card_id}"] .dlg-exchange {{')
        parts.append(f'  display: flex; flex-direction: column; gap: 16px; width: 100%;')
        parts.append('}')
        is_paper = p["id"] == "lean_paper"
        for side in ("a", "b"):
            align = "flex-start" if side == "a" else "flex-end"
            if is_paper:
                parts.append(f'.card[data-card-id="{card_id}"] .dlg-{side} {{')
                parts.append(f'  align-self: {align}; max-width: 80%;')
                parts.append(f'  border-left: 3px solid {p["accent"]}; padding-left: 16px;')
                parts.append(f'  font-family: {p["font"]}; font-size: 24px; color: {p["text"]};')
                parts.append('}')
            else:
                parts.append(f'.card[data-card-id="{card_id}"] .dlg-{side} {{')
                parts.append(f'  align-self: {align}; max-width: 80%;')
                parts.append(f'  background: rgba(255,255,255,0.04); border-radius: 16px;')
                parts.append(f'  border: 1px solid {p["accent"]}20; padding: 16px 20px;')
                parts.append(f'  font-family: {p["font"]}; font-size: 24px; color: {p["text"]};')
                if p["title_glow"]:
                    parts.append(f'  box-shadow: 0 0 20px {p["accent"]}15;')
                parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .dlg-speaker {{')
        parts.append(f'  font-size: {p["kicker_size"]}; font-weight: 700;')
        parts.append(f'  color: {p["accent"]}; margin-bottom: 4px;')
        parts.append('}')
    # Trend: simple SVG line
    if content_style == "trend":
        parts.append(f'.card[data-card-id="{card_id}"] .trend-wrap {{')
        parts.append(f'  position: relative; width: 100%; height: 120px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .trend-label {{')
        parts.append(f'  font-family: {p["font"]}; font-size: {p["title_size"]};')
        parts.append(f'  font-weight: {p["font_weight"]}; color: {p["text"]};')
        parts.append(f'  text-align: center; margin-bottom: 12px;')
        parts.append('}')
    # Attributed quote: quote + attribution line
    if content_style == "attributed_quote":
        parts.append(f'.card[data-card-id="{card_id}"] .attr-line {{')
        parts.append(f'  font-family: {p["font"]}; font-size: 20px;')
        parts.append(f'  font-weight: 500; font-style: italic;')
        parts.append(f'  color: {p["accent"]}; margin-top: 8px;')
        parts.append('}')
    # List: item rows
    if content_style == "list":
        parts.append(f'.card[data-card-id="{card_id}"] .list-items {{')
        parts.append(f'  display: flex; flex-direction: column; gap: 12px; width: 100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .list-item {{')
        parts.append(f'  display: flex; align-items: center; gap: 14px;')
        parts.append(f'  font-family: {p["font"]}; font-size: 28px;')
        parts.append(f'  font-weight: {p["font_weight"]}; color: {p["text"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .list-bullet {{')
        parts.append(f'  width: 28px; height: 28px; border-radius: 50%;')
        parts.append(f'  background: {p["accent"]}; color: #fff;')
        parts.append(f'  display: flex; align-items: center; justify-content: center;')
        parts.append(f'  font-size: 14px; font-weight: 800; flex-shrink: 0;')
        parts.append('}')
    parts.append('</style>')
    parts.append('<div class="root">')
    parts.append('  <div class="card-panel">')
    if kicker:
        parts.append(f'    <div class="kicker" id="{card_id}-kicker">{_esc(kicker)}</div>')
    if content_style == "comparison":
        ll = _esc(hints.get("left_label", ""))
        lv = _esc(hints.get("left_value", ""))
        rl = _esc(hints.get("right_label", ""))
        rv = _esc(hints.get("right_value", ""))
        parts.append(f'    <div class="cmp-row">')
        parts.append(f'      <div class="cmp-side" id="{card_id}-left">')
        parts.append(f'        <div class="cmp-label">{ll}</div>')
        parts.append(f'        <div class="cmp-value">{lv}</div>')
        parts.append(f'      </div>')
        parts.append(f'      <div class="cmp-sep" id="{card_id}-sep"></div>')
        parts.append(f'      <div class="cmp-side" id="{card_id}-right">')
        parts.append(f'        <div class="cmp-label">{rl}</div>')
        parts.append(f'        <div class="cmp-value">{rv}</div>')
        parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "list":
        items = hints.get("items", [])
        parts.append(f'    <div class="list-items">')
        for i, item in enumerate(items[:8]):
            parts.append(f'      <div class="list-item" id="{card_id}-item-{i}">')
            parts.append(f'        <div class="list-bullet">{i + 1}</div>')
            parts.append(f'        <span>{_esc(str(item))}</span>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "timeline":
        steps = hints.get("steps", [])
        parts.append(f'    <div class="tl-track">')
        parts.append(f'      <div class="tl-line" id="{card_id}-tl-line"></div>')
        for i, step in enumerate(steps[:6]):
            parts.append(f'      <div class="tl-step" id="{card_id}-step-{i}">')
            parts.append(f'        <div class="tl-dot" id="{card_id}-dot-{i}"></div>')
            parts.append(f'        <div class="tl-label">{_esc(str(step))}</div>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "dialogue":
        line_a = hints.get("line_a", "")
        line_b = hints.get("line_b", "")
        spk_a = hints.get("speaker_a", "")
        spk_b = hints.get("speaker_b", "")
        parts.append(f'    <div class="dlg-exchange">')
        parts.append(f'      <div class="dlg-a" id="{card_id}-dlg-a">')
        if spk_a:
            parts.append(f'        <div class="dlg-speaker">{_esc(spk_a)}</div>')
        parts.append(f'        <div>{_esc(line_a)}</div>')
        parts.append(f'      </div>')
        parts.append(f'      <div class="dlg-b" id="{card_id}-dlg-b">')
        if spk_b:
            parts.append(f'        <div class="dlg-speaker">{_esc(spk_b)}</div>')
        parts.append(f'        <div>{_esc(line_b)}</div>')
        parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "trend":
        direction = hints.get("trend_direction", "up")
        y1, y2 = ("100", "10") if direction == "up" else ("10", "100")
        parts.append(f'    <div class="trend-label" id="{card_id}-title">{_esc(display_text)}</div>')
        parts.append(f'    <div class="trend-wrap">')
        parts.append(f'      <svg viewBox="0 0 400 120" width="100%" height="120" id="{card_id}-trend-svg">')
        parts.append(f'        <path d="M 10 {y1} C 130 {y1}, 270 {y2}, 390 {y2}" '
                     f'stroke="{p["accent"]}" stroke-width="3" fill="none" '
                     f'stroke-dasharray="600" stroke-dashoffset="600" id="{card_id}-trend-path" />')
        parts.append(f'        <circle cx="390" cy="{y2}" r="6" fill="{p["accent"]}" '
                     f'opacity="0" id="{card_id}-trend-dot" />')
        parts.append(f'      </svg>')
        parts.append(f'    </div>')
    elif content_style == "attributed_quote":
        attribution = hints.get("attribution", "")
        parts.append(f'    <div class="title" id="{card_id}-title">{_esc(display_text)}</div>')
        if attribution:
            parts.append(f'    <div class="attr-line" id="{card_id}-attr">{_esc(attribution)}</div>')
        if detail:
            parts.append(f'    <div class="detail" id="{card_id}-detail">{_esc(detail)}</div>')
    else:
        parts.append(f'    <div class="title" id="{card_id}-title">{_esc(display_text)}</div>')
        if detail:
            parts.append(f'    <div class="detail" id="{card_id}-detail">{_esc(detail)}</div>')
    parts.append(f'    <div class="accent-line" id="{card_id}-line"></div>')
    parts.append(f'    <div class="shimmer-mask" id="{card_id}-shimmer"></div>')
    parts.append('  </div>')
    parts.append('</div>')
    parts.append('</div>')
    return "\n".join(parts)


def _build_caption_card_html(card: dict, pack: dict | None = None) -> str:
    """Build inner HTML for a caption card with per-word spans."""
    card_id = card["id"]
    words = card.get("words", [])
    p = pack or _LEAN_GLASS

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
        f'  font-family: {p["font"]};\n'
        f'  font-size: 48px; font-weight: 700; color: #FFFFFF;\n'
        f'  text-shadow: 0 2px 8px rgba(0,0,0,0.8), 0 0 2px rgba(0,0,0,0.9);\n'
        f'  text-align: center; line-height: 1.3;\n'
        f'}}\n'
        f'.card[data-card-id="{card_id}"] .cap-emphasis {{\n'
        f'  color: {p["accent"]}; transform: scale(1.08);\n'
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
    pack: dict | None = None,
) -> str:
    """Build the master GSAP timeline script including zoom/pan on the video wrapper."""
    p = pack or _LEAN_GLASS
    is_vibe = p["id"] == "lean_vibe"
    ease_in = _EASE_VIBE_IN if is_vibe else _EASE_IN
    lines = [
        "(function () {",
        '  const tl = window.gsap.timeline({ paused: true });',
        f'  var _eIn = "{ease_in}";',
        f'  var _eOut = "{_EASE_OUT_FAST}";',
        "",
    ]

    # Face-aware transform origin for zoom (Phase D)
    has_face_data = subject_position is not None
    if has_face_data:
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
                f'{{ opacity: 1, duration: {fade_in_dur:.3f}, ease: _eIn }}, '
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
            # Entrance: 0.32s with spring ease
            lines.append(
                f'  tl.fromTo(\'{sel}\', '
                f'{{ opacity: 0 }}, '
                f'{{ opacity: 1, duration: 0.320, ease: _eIn }}, '
                f'{start:.4f});'
            )
            lines.append(
                f'  tl.fromTo(\'{panel_sel}\', '
                f'{{ filter: "blur(12px)", scale: 1.02 }}, '
                f'{{ filter: "blur(0px)", scale: 1, duration: 0.350, ease: _eIn }}, '
                f'{start:.4f});'
            )

            # Dimmed backdrop for center-zone cards conflicting with speaker
            card_zone = card.get("zone", "")
            center_zone = card_zone in ("fullscreen", "video-overlay")
            face_centered = has_face_data and 30.0 <= face_cx <= 70.0
            if center_zone and face_centered:
                lines.append(
                    f'  tl.to("#video-wrap", '
                    f'{{ filter: "{_esc_js(p["backdrop_dim"])}", '
                    f'duration: 0.30, ease: _eIn }}, {start:.4f});'
                )
                lines.append(
                    f'  tl.to("#video-wrap", '
                    f'{{ filter: "{_esc_js(p["backdrop_restore"])}", '
                    f'duration: 0.18, ease: _eOut }}, {end - 0.18:.4f});'
                )

            content_style = card.get("contentHints", {}).get("style", "key_phrase")
            title_sel = f'.card[data-card-id="{card_id}"] #{card_id}-title'
            kicker_sel = f'.card[data-card-id="{card_id}"] #{card_id}-kicker'
            line_sel = f'.card[data-card-id="{card_id}"] #{card_id}-line'
            t_in = start + 0.15

            is_paper = p["id"] == "lean_paper"

            if content_style == "stat" and card.get("contentHints", {}).get("number"):
                num_val, num_suffix = _safe_number(card["contentHints"]["number"])
                if num_val is not None:
                    count_dur = min(1.5, max(0.6, dur * 0.25))
                    count_end = t_in + count_dur
                    if is_paper:
                        lines.append(
                            f'  (function(){{ var o={{v:0}}; tl.to(o, {{v:{num_val}, '
                            f'duration: {count_dur:.3f}, ease: _eIn, onUpdate: function(){{ '
                            f'var el=document.querySelector(\'{title_sel}\'); '
                            f'if(el){{ el.textContent=Math.round(o.v).toLocaleString()+\'{_esc_js(num_suffix)}\'; '
                            f'var r=1-o.v/{num_val}; '
                            f'el.style.color="rgba(26,26,26,"+(0.3+0.7*r)+")"; '
                            f'}} }}}}, {t_in:.4f}); }})();'
                        )
                    elif is_vibe:
                        lines.append(
                            f'  (function(){{ var o={{v:0}}; tl.to(o, {{v:{num_val}, '
                            f'duration: {count_dur:.3f}, ease: _eIn, onUpdate: function(){{ '
                            f'var el=document.querySelector(\'{title_sel}\'); '
                            f'if(el){{ el.textContent=Math.round(o.v).toLocaleString()+\'{_esc_js(num_suffix)}\'; '
                            f'el.style.opacity=0.7+0.3*(o.v/{num_val}); '
                            f'}} }}}}, {t_in:.4f}); }})();'
                        )
                    else:
                        lines.append(
                            f'  (function(){{ var o={{v:0}}; tl.to(o, {{v:{num_val}, '
                            f'duration: {count_dur:.3f}, ease: _eIn, onUpdate: function(){{ '
                            f'var el=document.querySelector(\'{title_sel}\'); '
                            f'if(el){{ el.textContent=Math.round(o.v).toLocaleString()+\'{_esc_js(num_suffix)}\'; '
                            f'var r=o.v/{num_val}; '
                            f'el.style.textShadow="0 0 "+(40+16*r)+"px rgba(76,201,240,"+(0.25+0.20*r)+")"; '
                            f'}} }}}}, {t_in:.4f}); }})();'
                        )
                    pop_scale = "1.15" if is_vibe else "1.08"
                    lines.append(
                        f'  tl.to(\'{title_sel}\', '
                        f'{{ scale: {pop_scale}, duration: 0.12, ease: _eIn }}, '
                        f'{count_end:.4f});'
                    )
                    lines.append(
                        f'  tl.to(\'{title_sel}\', '
                        f'{{ scale: 1, duration: 0.20, ease: _eOut }}, '
                        f'{count_end + 0.12:.4f});'
                    )
                    lines.append(
                        f'  tl.to(\'{title_sel}\', '
                        f'{{ color: "{p["accent"]}", '
                        + (f'textShadow: "{_esc_js(p["title_glow_intense"])}", ' if p["title_glow_intense"] else '')
                        + f'duration: 0.15 }}, {count_end:.4f});'
                    )
                    lines.append(
                        f'  tl.to(\'{title_sel}\', '
                        f'{{ color: "{p["text"]}", '
                        + (f'textShadow: "{_esc_js(p["title_glow"])}", ' if p["title_glow"] else '')
                        + f'duration: 0.6 }}, {count_end + 0.15:.4f});'
                    )
                else:
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ opacity: 0 }}, '
                        f'{{ opacity: 1, duration: 0.400, ease: _eIn }}, '
                        f'{t_in:.4f});'
                    )
            elif content_style == "key_phrase":
                if is_paper:
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ opacity: 0 }}, '
                        f'{{ opacity: 1, duration: 0.250, ease: _eIn }}, '
                        f'{t_in:.4f});'
                    )
                    ul_sel = f'.card[data-card-id="{card_id}"] #{card_id}-line'
                    lines.append(
                        f'  tl.fromTo(\'{ul_sel}\', '
                        f'{{ width: 0, height: "2px" }}, '
                        f'{{ width: 160, duration: 0.600, ease: "power2.inOut" }}, '
                        f'{t_in + 0.15:.4f});'
                    )
                elif is_vibe:
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ opacity: 0, scale: 0.7 }}, '
                        f'{{ opacity: 1, scale: 1.05, duration: 0.350, ease: _eIn }}, '
                        f'{t_in:.4f});'
                    )
                    lines.append(
                        f'  tl.to(\'{title_sel}\', '
                        f'{{ scale: 1, duration: 0.200, ease: _eOut }}, '
                        f'{t_in + 0.35:.4f});'
                    )
                else:
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
                    f'{{ opacity: 1, y: 0, duration: 0.500, ease: _eIn }}, '
                    f'{t_in:.4f});'
                )
            elif content_style == "comparison":
                left_sel = f'.card[data-card-id="{card_id}"] #{card_id}-left'
                right_sel = f'.card[data-card-id="{card_id}"] #{card_id}-right'
                sep_sel = f'.card[data-card-id="{card_id}"] #{card_id}-sep'
                lines.append(
                    f'  tl.fromTo(\'{left_sel}\', '
                    f'{{ opacity: 0, x: -60 }}, '
                    f'{{ opacity: 1, x: 0, duration: 0.450, ease: _eIn }}, '
                    f'{t_in:.4f});'
                )
                lines.append(
                    f'  tl.fromTo(\'{right_sel}\', '
                    f'{{ opacity: 0, x: 60 }}, '
                    f'{{ opacity: 1, x: 0, duration: 0.450, ease: _eIn }}, '
                    f'{t_in + 0.15:.4f});'
                )
                lines.append(
                    f'  tl.fromTo(\'{sep_sel}\', '
                    f'{{ height: 0 }}, '
                    f'{{ height: 80, duration: 0.400, ease: _eIn }}, '
                    f'{t_in + 0.20:.4f});'
                )
                if is_vibe:
                    lines.append(
                        f'  tl.to(\'{sep_sel}\', '
                        f'{{ boxShadow: "0 0 16px {p["accent"]}", duration: 0.200 }}, '
                        f'{t_in + 0.60:.4f});'
                    )
            elif content_style == "list":
                items = card.get("contentHints", {}).get("items", [])
                n_items = min(len(items), 8)
                cascade_limit = min(n_items, 4)
                for i in range(n_items):
                    item_sel = f'.card[data-card-id="{card_id}"] #{card_id}-item-{i}'
                    bullet_sel = f'{item_sel} .list-bullet'
                    stagger = i * 0.12 if i < cascade_limit else cascade_limit * 0.12
                    if is_paper:
                        lines.append(
                            f'  tl.fromTo(\'{item_sel}\', '
                            f'{{ opacity: 0 }}, '
                            f'{{ opacity: 1, duration: 0.300, ease: _eIn }}, '
                            f'{t_in + stagger:.4f});'
                        )
                        lines.append(
                            f'  tl.fromTo(\'{bullet_sel}\', '
                            f'{{ scale: 0.6 }}, '
                            f'{{ scale: 1, duration: 0.200, ease: _eIn }}, '
                            f'{t_in + stagger:.4f});'
                        )
                    elif is_vibe:
                        lines.append(
                            f'  tl.fromTo(\'{item_sel}\', '
                            f'{{ opacity: 0, scale: 0 }}, '
                            f'{{ opacity: 1, scale: 1, duration: 0.300, ease: _eIn }}, '
                            f'{t_in + stagger:.4f});'
                        )
                        lines.append(
                            f'  tl.fromTo(\'{bullet_sel}\', '
                            f'{{ scale: 0.2 }}, '
                            f'{{ scale: 1.2, duration: 0.200, ease: _eIn }}, '
                            f'{t_in + stagger - 0.05:.4f});'
                        )
                        lines.append(
                            f'  tl.to(\'{bullet_sel}\', '
                            f'{{ scale: 1, duration: 0.150, ease: _eOut }}, '
                            f'{t_in + stagger + 0.15:.4f});'
                        )
                    else:
                        lines.append(
                            f'  tl.fromTo(\'{item_sel}\', '
                            f'{{ opacity: 0, x: -12 }}, '
                            f'{{ opacity: 1, x: 0, duration: 0.300, ease: _eIn }}, '
                            f'{t_in + stagger:.4f});'
                        )
                        lines.append(
                            f'  tl.fromTo(\'{bullet_sel}\', '
                            f'{{ scale: 0.3 }}, '
                            f'{{ scale: 1, duration: 0.250, ease: _eIn }}, '
                            f'{t_in + stagger - 0.05:.4f});'
                        )
            elif content_style == "question":
                if is_paper:
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ opacity: 0 }}, '
                        f'{{ opacity: 1, duration: 0.250, ease: _eIn }}, '
                        f'{t_in:.4f});'
                    )
                elif is_vibe:
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ opacity: 0, scale: 0.7 }}, '
                        f'{{ opacity: 1, scale: 1.05, duration: 0.350, ease: _eIn }}, '
                        f'{t_in:.4f});'
                    )
                    lines.append(
                        f'  tl.to(\'{title_sel}\', '
                        f'{{ scale: 1, duration: 0.200, ease: _eOut }}, '
                        f'{t_in + 0.35:.4f});'
                    )
                else:
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ clipPath: "inset(0 100% 0 0)" }}, '
                        f'{{ clipPath: "inset(0 0% 0 0)", duration: 0.500, ease: "power2.inOut" }}, '
                        f'{t_in:.4f});'
                    )
            elif content_style == "timeline":
                steps = card.get("contentHints", {}).get("steps", [])
                n_steps = min(len(steps), 6)
                tl_line_sel = f'.card[data-card-id="{card_id}"] #{card_id}-tl-line'
                line_dur = min(1.5, max(0.4, n_steps * 0.25))
                lines.append(
                    f'  tl.to(\'{tl_line_sel}\', '
                    f'{{ width: "100%", duration: {line_dur:.3f}, ease: "power2.inOut" }}, '
                    f'{t_in:.4f});'
                )
                dot_pop_scale = "1.5" if is_vibe else "1.3"
                for si in range(n_steps):
                    dot_sel = f'.card[data-card-id="{card_id}"] #{card_id}-dot-{si}'
                    dot_t = t_in + (si + 1) * (line_dur / max(n_steps, 1))
                    if is_paper:
                        lines.append(
                            f'  tl.to(\'{dot_sel}\', '
                            f'{{ background: "{p["accent"]}", duration: 0.200, ease: _eIn }}, '
                            f'{dot_t:.4f});'
                        )
                    else:
                        lines.append(
                            f'  tl.to(\'{dot_sel}\', '
                            f'{{ background: "{p["accent"]}", scale: {dot_pop_scale}, '
                            f'boxShadow: "0 0 14px {p["accent"]}", '
                            f'duration: 0.200, ease: _eIn }}, {dot_t:.4f});'
                        )
                        lines.append(
                            f'  tl.to(\'{dot_sel}\', '
                            f'{{ scale: 1, duration: 0.150, ease: _eOut }}, '
                            f'{dot_t + 0.20:.4f});'
                        )
            elif content_style == "dialogue":
                dlg_a_sel = f'.card[data-card-id="{card_id}"] #{card_id}-dlg-a'
                dlg_b_sel = f'.card[data-card-id="{card_id}"] #{card_id}-dlg-b'
                if is_vibe:
                    lines.append(
                        f'  tl.fromTo(\'{dlg_a_sel}\', '
                        f'{{ opacity: 0, scale: 0.8 }}, '
                        f'{{ opacity: 1, scale: 1.05, duration: 0.400, ease: _eIn }}, '
                        f'{t_in:.4f});'
                    )
                    lines.append(
                        f'  tl.to(\'{dlg_a_sel}\', '
                        f'{{ scale: 1, duration: 0.180, ease: _eOut }}, '
                        f'{t_in + 0.40:.4f});'
                    )
                    lines.append(
                        f'  tl.fromTo(\'{dlg_b_sel}\', '
                        f'{{ opacity: 0, scale: 0.8 }}, '
                        f'{{ opacity: 1, scale: 1.05, duration: 0.400, ease: _eIn }}, '
                        f'{t_in + 0.25:.4f});'
                    )
                    lines.append(
                        f'  tl.to(\'{dlg_b_sel}\', '
                        f'{{ scale: 1, duration: 0.180, ease: _eOut }}, '
                        f'{t_in + 0.65:.4f});'
                    )
                else:
                    lines.append(
                        f'  tl.fromTo(\'{dlg_a_sel}\', '
                        f'{{ opacity: 0, x: -30 }}, '
                        f'{{ opacity: 1, x: 0, duration: 0.400, ease: _eIn }}, '
                        f'{t_in:.4f});'
                    )
                    lines.append(
                        f'  tl.fromTo(\'{dlg_b_sel}\', '
                        f'{{ opacity: 0, x: 30 }}, '
                        f'{{ opacity: 1, x: 0, duration: 0.400, ease: _eIn }}, '
                        f'{t_in + 0.25:.4f});'
                    )
            elif content_style == "trend":
                path_sel = f'.card[data-card-id="{card_id}"] #{card_id}-trend-path'
                dot_sel = f'.card[data-card-id="{card_id}"] #{card_id}-trend-dot'
                lines.append(
                    f'  tl.fromTo(\'{title_sel}\', '
                    f'{{ opacity: 0 }}, '
                    f'{{ opacity: 1, duration: 0.300, ease: _eIn }}, '
                    f'{t_in:.4f});'
                )
                lines.append(
                    f'  tl.to(\'{path_sel}\', '
                    f'{{ attr: {{ "stroke-dashoffset": 0 }}, '
                    f'duration: 1.2, ease: "power2.inOut" }}, '
                    f'{t_in + 0.15:.4f});'
                )
                if is_paper:
                    lines.append(
                        f'  tl.to(\'{dot_sel}\', '
                        f'{{ opacity: 1, duration: 0.300, ease: _eIn }}, '
                        f'{t_in + 1.35:.4f});'
                    )
                elif is_vibe:
                    lines.append(
                        f'  tl.to(\'{dot_sel}\', '
                        f'{{ opacity: 1, scale: 2.0, '
                        f'filter: "drop-shadow(0 0 12px {p["accent"]})", '
                        f'duration: 0.250, ease: _eIn }}, {t_in + 1.20:.4f});'
                    )
                    lines.append(
                        f'  tl.to(\'{dot_sel}\', '
                        f'{{ scale: 1, duration: 0.200, ease: _eOut }}, '
                        f'{t_in + 1.45:.4f});'
                    )
                else:
                    lines.append(
                        f'  tl.to(\'{dot_sel}\', '
                        f'{{ opacity: 1, scale: 1.4, '
                        f'filter: "drop-shadow(0 0 8px {p["accent"]})", '
                        f'duration: 0.200, ease: _eIn }}, {t_in + 1.20:.4f});'
                    )
                    lines.append(
                        f'  tl.to(\'{dot_sel}\', '
                        f'{{ scale: 1, duration: 0.200, ease: _eOut }}, '
                        f'{t_in + 1.40:.4f});'
                    )
            elif content_style == "attributed_quote":
                lines.append(
                    f'  tl.fromTo(\'{title_sel}\', '
                    f'{{ opacity: 0, y: 40 }}, '
                    f'{{ opacity: 1, y: 0, duration: 0.500, ease: _eIn }}, '
                    f'{t_in:.4f});'
                )
                attr_sel = f'.card[data-card-id="{card_id}"] #{card_id}-attr'
                if is_vibe:
                    lines.append(
                        f'  tl.fromTo(\'{attr_sel}\', '
                        f'{{ opacity: 0, scale: 0.8 }}, '
                        f'{{ opacity: 1, scale: 1, duration: 0.300, ease: _eIn }}, '
                        f'{t_in + 0.20:.4f});'
                    )
                else:
                    lines.append(
                        f'  tl.fromTo(\'{attr_sel}\', '
                        f'{{ opacity: 0 }}, '
                        f'{{ opacity: 1, duration: 0.300, ease: _eIn }}, '
                        f'{t_in + 0.20:.4f});'
                    )
            else:
                if is_paper:
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ opacity: 0, scale: 1.04 }}, '
                        f'{{ opacity: 1, scale: 1, duration: 0.400, ease: _eIn }}, '
                        f'{t_in:.4f});'
                    )
                elif is_vibe:
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ opacity: 0, rotation: -3, scale: 0.95 }}, '
                        f'{{ opacity: 1, rotation: 0, scale: 1, duration: 0.400, ease: _eIn }}, '
                        f'{t_in:.4f});'
                    )
                else:
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ opacity: 0, filter: "blur(8px)" }}, '
                        f'{{ opacity: 1, filter: "blur(0px)", duration: 0.400, ease: _eIn }}, '
                        f'{t_in:.4f});'
                    )

            lines.append(
                f'  tl.fromTo(\'{kicker_sel}\', '
                f'{{ opacity: 0, y: -8 }}, '
                f'{{ opacity: 1, y: 0, duration: 0.250, ease: _eIn }}, '
                f'{start + 0.10:.4f});'
            )
            lines.append(
                f'  tl.fromTo(\'{line_sel}\', '
                f'{{ width: 0 }}, '
                f'{{ width: 120, duration: 0.400, ease: _eIn }}, '
                f'{t_in + 0.30:.4f});'
            )
            # Breathing underline — half speed for question cards
            breath_period = 2.5 if content_style == "question" else 1.25
            pulse_dur = max(0.5, dur - 1.0)
            pulse_repeats = max(1, int(pulse_dur / (breath_period * 2)))
            lines.append(
                f'  tl.fromTo(\'{line_sel}\', '
                f'{{ boxShadow: "{_esc_js(p["accent_line_glow"])}" }}, '
                f'{{ boxShadow: "{_esc_js(p["accent_line_glow_bright"])}", '
                f'duration: {breath_period:.2f}, ease: "sine.inOut", '
                f'repeat: {pulse_repeats}, yoyo: true }}, '
                f'{t_in + 0.70:.4f});'
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

        # Exit — faster than entrance (asymmetric timing)
        if is_caption:
            exit_start = end - fade_out_dur
            lines.append(
                f'  tl.to(\'{sel}\', '
                f'{{ opacity: 0, duration: {fade_out_dur:.3f}, ease: _eOut }}, '
                f'{exit_start:.4f});'
            )
        else:
            exit_start = end - 0.18
            panel_sel = f'.card[data-card-id="{card_id}"] .card-panel'
            lines.append(
                f'  tl.to(\'{sel}\', '
                f'{{ opacity: 0, duration: 0.180, ease: _eOut }}, '
                f'{exit_start:.4f});'
            )
            lines.append(
                f'  tl.to(\'{panel_sel}\', '
                f'{{ scale: 0.97, duration: 0.180, ease: _eOut }}, '
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

    # Resolve style pack
    pack = _PACKS.get(style_pack, _LEAN_GLASS)
    print(f"[COMPOSE] Style pack: {pack['id']}")

    # Separate cards by type for track assignment
    all_cards = storyboard.get("cards", [])
    graphic_cards = [c for c in all_cards if c.get("type") != "caption"]
    caption_cards = [c for c in all_cards if c.get("type") == "caption"]

    # Build card host divs
    card_hosts = []
    for c in graphic_cards:
        card_hosts.append(_build_card_host(c, layout, track_index=2, pack=pack))
    for c in caption_cards:
        card_hosts.append(_build_card_host(c, layout, track_index=3, pack=pack))

    # Build master timeline
    timeline_js = _build_timeline_js(all_cards, zoom_entries=zoom_entries, subject_position=subject_position, pack=pack)

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
