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
    "fullscreen":       {"left": 0,    "top": 0, "width": 1920, "height": 1080},
    "lower-third":      {"left": 0,    "top": 756, "width": 1920, "height": 324},
    "side-panel":       {"left": 0,    "top": 0, "width": 806,  "height": 1080},
    "side-panel-left":  {"left": 0,    "top": 0, "width": 806,  "height": 1080},
    "side-panel-right": {"left": 1114, "top": 0, "width": 806,  "height": 1080},
    "whiteboard-area":  {"left": 40,   "top": 40, "width": 1840, "height": 1000},
    "video-overlay":    {"left": 0,    "top": 0, "width": 1920, "height": 1080},
}

# Zone → pixel bounds for portrait (1080×1920)
_ZONE_BOUNDS_PORTRAIT = {
    "fullscreen":       {"left": 0, "top": 0,    "width": 1080, "height": 1920},
    "lower-third":      {"left": 0, "top": 1344, "width": 1080, "height": 576},
    "side-panel":       {"left": 0, "top": 1152, "width": 1080, "height": 768},
    "side-panel-top":   {"left": 0, "top": 0,    "width": 1080, "height": 768},
    "whiteboard-area":  {"left": 40, "top": 864, "width": 1000, "height": 1016},
    "video-overlay":    {"left": 0, "top": 0,    "width": 1080, "height": 1920},
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


# Data cards are remapped to a side panel when Claude places them in a center zone.
# Hero cards (key_phrase, quote, question, definition, etc.) remain in Claude's
# chosen zone — they carry the visual message and need the full canvas.
_DATA_PANEL_TYPES = {"stat", "list", "comparison", "checklist", "score", "trend"}
_CENTER_ZONES = {"fullscreen", "video-overlay"}
_SIDE_PANEL_ZONES = {"side-panel", "side-panel-left", "side-panel-right", "side-panel-top"}


def _build_card_host(card: dict, layout: str, track_index: int, pack: dict | None = None) -> str:
    """Build a card-host div with correct classes, data attributes, and inline bounds."""
    card_id = card["id"]
    start = round(float(card.get("startSec", 0)), 3)
    _end_raw = float(card.get("endSec", start + 3))
    # Subtract 1ms: HyperFrames lint computes end = Number(data-start) + Number(data-duration)
    # in float64 JS, so 12.760 + 3.020 = 15.780000000000001 > 15.780 → overlap error.
    # 1ms gap is invisible at 30fps (one frame = 33ms).
    duration = max(0.0, round(_end_raw - start, 3) - 0.001)
    zone = card.get("zone", "lower-third")

    is_caption = card.get("type") == "caption"

    if not is_caption and zone == "lower-third":
        zone = "video-overlay"

    bounds = _zone_bounds(zone, layout)

    if is_caption:
        inner = _build_caption_card_html(card, pack=pack)
    else:
        compact = zone in _SIDE_PANEL_ZONES
        inner = _build_graphic_card_html(card, pack=pack, compact=compact)

    return (
        f'<div class="card-host clip" data-card-id="{card_id}" '
        f'data-start="{start:.3f}" data-duration="{duration:.3f}" '
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
_EASE_LEDGER_IN = "cubic-bezier(0.25, 0.1, 0.25, 1.0)"
_EASE_CRAFT_IN = "cubic-bezier(0.34, 0.80, 0.44, 0.98)"
_EASE_CINEMA_IN = "cubic-bezier(0.16, 0.60, 0.40, 1.00)"

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
    "panel_filter": "",
    "title_glow": "0 0 40px rgba(76,201,240,0.25)",
    "title_glow_intense": "0 0 56px rgba(76,201,240,0.45)",
    "has_grain": True,
    "shimmer_color": "rgba(76,201,240,0.15)",
    "accent_line_glow": "0 0 12px #4cc9f0",
    "accent_line_glow_bright": "0 0 20px #4cc9f0",
    "backdrop_dim": "brightness(0.25)",
    "backdrop_restore": "brightness(1)",
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
    "backdrop_dim": "brightness(1.6) saturate(0.3)",
    "backdrop_restore": "brightness(1) saturate(1)",
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
    "backdrop_dim": "brightness(0.35) saturate(1.3)",
    "backdrop_restore": "brightness(1) saturate(1)",
}

_LEAN_LEDGER = {
    "id": "lean_ledger",
    "bg": "#0A1628",
    "text": "#E8EBF0",
    "text_secondary": "rgba(232,235,240,0.5)",
    "accent": "#00C896",
    "font": '"IBM Plex Mono", "JetBrains Mono", "Courier New", monospace',
    "font_weight": "600",
    "title_size": "60px",
    "number_size": "88px",
    "kicker_size": "18px",
    "detail_size": "22px",
    "border": "1px solid rgba(0,200,150,0.2)",
    "radius": "4px",
    "shadow": "0 2px 12px rgba(0,0,0,0.3)",
    "shadow_inset": "",
    "panel_filter": "",
    "title_glow": "",
    "title_glow_intense": "",
    "has_grain": True,
    "grain_type": "grid",
    "shimmer_color": "rgba(0,200,150,0.08)",
    "accent_line_glow": "0 0 8px rgba(0,200,150,0.2)",
    "accent_line_glow_bright": "0 0 12px rgba(0,200,150,0.3)",
    "backdrop_dim": "brightness(0.2)",
    "backdrop_restore": "brightness(1)",
}

_LEAN_CRAFT = {
    "id": "lean_craft",
    "bg": "#E8D9C5",
    "text": "#3D2B1F",
    "text_secondary": "rgba(61,43,31,0.55)",
    "accent": "#D97757",
    "font": '"Permanent Marker", cursive',
    "font_detail": '"Inter", ui-sans-serif, system-ui, sans-serif',
    "font_weight": "400",
    "title_size": "56px",
    "number_size": "80px",
    "kicker_size": "20px",
    "detail_size": "22px",
    "border": "1.5px solid rgba(217,119,87,0.25)",
    "radius": "12px 8px 10px 14px",
    "shadow": "0 3px 16px rgba(61,43,31,0.1)",
    "shadow_inset": "",
    "panel_filter": "",
    "title_glow": "",
    "title_glow_intense": "",
    "has_grain": True,
    "grain_type": "paper",
    "shimmer_color": "rgba(217,119,87,0.10)",
    "accent_line_glow": "0 0 6px rgba(217,119,87,0.25)",
    "accent_line_glow_bright": "0 0 10px rgba(217,119,87,0.35)",
    "backdrop_dim": "brightness(0.3) sepia(0.2)",
    "backdrop_restore": "brightness(1) sepia(0)",
}

_LEAN_CINEMA = {
    "id": "lean_cinema",
    "bg": "#0D0D0D",
    "text": "#F5F0E8",
    "text_secondary": "rgba(245,240,232,0.5)",
    "accent": "#C9A86A",
    "font": '"Playfair Display", Georgia, serif',
    "font_detail": '"Inter", ui-sans-serif, system-ui, sans-serif',
    "font_weight": "700",
    "title_size": "60px",
    "number_size": "88px",
    "kicker_size": "18px",
    "detail_size": "22px",
    "border": "none",
    "radius": "0px",
    "shadow": "0 4px 24px rgba(0,0,0,0.5)",
    "shadow_inset": "",
    "panel_filter": "",
    "title_glow": "",
    "title_glow_intense": "",
    "has_grain": True,
    "grain_type": "film",
    "shimmer_color": "rgba(201,168,106,0.06)",
    "accent_line_glow": "0 0 6px rgba(201,168,106,0.15)",
    "accent_line_glow_bright": "0 0 10px rgba(201,168,106,0.25)",
    "backdrop_dim": "brightness(0.15)",
    "backdrop_restore": "brightness(1)",
    "has_letterbox": True,
}

_PACKS = {"lean_glass": _LEAN_GLASS, "lean_paper": _LEAN_PAPER, "lean_vibe": _LEAN_VIBE, "lean_ledger": _LEAN_LEDGER, "lean_craft": _LEAN_CRAFT, "lean_cinema": _LEAN_CINEMA}

# Per-pack hero punch-in parameters ({scale, in_dur, in_ease, out_dur, out_ease}).
# lean_paper: None → no punch-in; clean/minimal aesthetic.
_PUNCH_IN_PARAMS: dict = {
    "lean_glass":  {"scale": 1.030, "in_dur": 0.40, "in_ease": "power2.in",          "out_dur": 0.40, "out_ease": "power2.out"},
    "lean_paper":  None,
    "lean_vibe":   {"scale": 1.060, "in_dur": 0.50, "in_ease": "back.out(1.7)",      "out_dur": 0.35, "out_ease": "power2.out"},
    "lean_ledger": {"scale": 1.020, "in_dur": 0.25, "in_ease": "linear",              "out_dur": 0.20, "out_ease": "linear"},
    "lean_craft":  {"scale": 1.040, "in_dur": 0.50, "in_ease": "elastic.out(1,0.3)", "out_dur": 0.60, "out_ease": "power2.out"},
    "lean_cinema": {"scale": 1.025, "in_dur": 0.60, "in_ease": "power2.in",           "out_dur": 0.80, "out_ease": "power2.out"},
}

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
_GRID_SVG = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='40' height='40'%3E"
    "%3Cline x1='0' y1='40' x2='40' y2='40' stroke='rgba(0,200,150,0.06)' stroke-width='1'/%3E"
    "%3Cline x1='40' y1='0' x2='40' y2='40' stroke='rgba(0,200,150,0.06)' stroke-width='1'/%3E"
    "%3C/svg%3E"
)
_PAPER_GRAIN_SVG = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E"
    "%3Cfilter id='pg'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.55' "
    "numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E"
    "%3Crect width='100%25' height='100%25' filter='url(%23pg)' opacity='0.06'/%3E%3C/svg%3E"
)
_FILM_GRAIN_SVG = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E"
    "%3Cfilter id='fg'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' "
    "numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E"
    "%3Crect width='100%25' height='100%25' filter='url(%23fg)' opacity='0.025'/%3E%3C/svg%3E"
)


def _accent_bg_css(p: dict) -> str:
    """CSS property lines (no selector) that set up the background-based highlight swipe.

    Sets background-size to 0 so GSAP can animate it to 100% on entry.
    Returns '' for packs that use a non-background treatment or no swipe.
    """
    pid = p["id"]
    acc = p["accent"]
    if pid == "lean_paper":
        return ""
    if pid == "lean_cinema":
        return ""  # uses letter-spacing expand instead
    if pid == "lean_vibe":
        return (
            f"  background-image: linear-gradient({acc}55, {acc}55);\n"
            f"  background-repeat: no-repeat; background-position: 0 0;\n"
            f"  background-size: 0% 100%; padding: 0 3px; border-radius: 4px;\n"
        )
    if pid == "lean_ledger":
        return (
            f"  background-image: linear-gradient({acc}70, {acc}70);\n"
            f"  background-repeat: no-repeat; background-position: 0 0;\n"
            f"  background-size: 0% 100%;\n"
        )
    # lean_glass: 3px underline sweep; lean_craft: 4px brush stroke
    h = "4px" if pid == "lean_craft" else "3px"
    return (
        f"  background-image: linear-gradient({acc}, {acc});\n"
        f"  background-repeat: no-repeat; background-position: 0 100%;\n"
        f"  background-size: 0% {h};\n"
    )


def _accent_treatment(p: dict, sel: str, t: float) -> list[str]:
    """Return GSAP tween lines for the per-pack accent/highlight-swipe animation.

    sel: full GSAP CSS selector string.
    t:   timeline position (seconds) when the swipe fires.
    """
    pid = p["id"]
    out: list[str] = []
    if pid == "lean_paper":
        pass  # CSS color only, no animation needed
    elif pid == "lean_glass":
        out.append(
            f"  tl.fromTo('{sel}', "
            f"{{ backgroundSize: '0% 3px' }}, "
            f"{{ backgroundSize: '100% 3px', duration: 0.30, ease: 'power2.out' }}, "
            f"{t:.4f});"
        )
        if p.get("title_glow"):
            out.append(
                f"  tl.to('{sel}', "
                f"{{ textShadow: '{_esc_js(p['title_glow'])}', duration: 0.20 }}, "
                f"{t + 0.10:.4f});"
            )
    elif pid == "lean_vibe":
        out.append(
            f"  tl.fromTo('{sel}', "
            f"{{ backgroundSize: '0% 100%' }}, "
            f"{{ backgroundSize: '100% 100%', duration: 0.22, ease: 'power2.out' }}, "
            f"{t:.4f});"
        )
        out.append(
            f"  tl.to('{sel}', "
            f"{{ scale: 1.10, duration: 0.12, ease: 'power2.in' }}, "
            f"{t + 0.08:.4f});"
        )
        out.append(
            f"  tl.to('{sel}', "
            f"{{ scale: 1, duration: 0.14, ease: 'power2.out' }}, "
            f"{t + 0.20:.4f});"
        )
    elif pid == "lean_ledger":
        # Two-phase: full-height scan (0.08s) collapses to 3px underline (0.15s)
        out.append(
            f"  tl.fromTo('{sel}', "
            f"{{ backgroundSize: '0% 100%' }}, "
            f"{{ backgroundSize: '100% 100%', duration: 0.08, ease: 'none' }}, "
            f"{t:.4f});"
        )
        out.append(
            f"  tl.to('{sel}', "
            f"{{ backgroundSize: '100% 3px', duration: 0.15, ease: 'none' }}, "
            f"{t + 0.10:.4f});"
        )
    elif pid == "lean_craft":
        out.append(
            f"  tl.fromTo('{sel}', "
            f"{{ backgroundSize: '0% 4px' }}, "
            f"{{ backgroundSize: '100% 4px', duration: 0.45, ease: 'elastic.out(1,0.4)' }}, "
            f"{t:.4f});"
        )
    elif pid == "lean_cinema":
        # Letter-spacing expands wide then collapses back to normal
        out.append(
            f"  tl.fromTo('{sel}', "
            f"{{ letterSpacing: '0.12em' }}, "
            f"{{ letterSpacing: '0em', duration: 0.60, ease: 'power2.out' }}, "
            f"{t:.4f});"
        )
    return out


def _split_title_accent(title: str, accent_word: str, card_id: str) -> str:
    """Return title as HTML with accent_word wrapped in a GSAP-targetable span.

    Falls back to plain escaped text if accent_word is absent or not found.
    """
    if not accent_word:
        return _esc(title)
    idx = title.lower().find(accent_word.lower())
    if idx == -1:
        return _esc(title)
    before = title[:idx]
    the_word = title[idx: idx + len(accent_word)]
    after = title[idx + len(accent_word):]
    return (
        f"{_esc(before)}"
        f'<span class="accent-word" id="{card_id}-accent">{_esc(the_word)}</span>'
        f"{_esc(after)}"
    )


def _build_graphic_card_html(card: dict, pack: dict | None = None, compact: bool = False) -> str:
    """Build inner HTML for a graphic overlay card using the given style pack."""
    card_id = card["id"]
    hints = card.get("contentHints", {})
    kicker = hints.get("kicker", "")
    title = hints.get("title", "")
    detail = hints.get("detail", "")
    number = hints.get("number", "")
    p = pack or _LEAN_GLASS

    # Compact variant for side-panel zones — scale typography and tighten padding so
    # content fits the 806px-wide container without awkward wrapping or overflow.
    if compact:
        def _s(px_str: str, f: float) -> str:
            return f"{int(float(px_str.replace('px', '')) * f)}px"
        title_size_eff  = _s(p["title_size"],  0.65)
        number_size_eff = _s(p["number_size"], 0.67)
        detail_size_eff = "20px"
        kicker_size_eff = "16px"
        list_item_size  = "18px"
        chk_item_size   = "17px"
        panel_padding   = "28px 32px"
        root_padding    = "32px"
        text_align      = "left"
        panel_align     = "flex-start"
        max_width_eff   = "92%"
    else:
        title_size_eff  = p["title_size"]
        number_size_eff = p["number_size"]
        detail_size_eff = p["detail_size"]
        kicker_size_eff = p["kicker_size"]
        list_item_size  = "28px"
        chk_item_size   = "26px"
        panel_padding   = "44px 52px"
        root_padding    = "48px"
        text_align      = "center"
        panel_align     = "center"
        max_width_eff   = "85%"

    display_text = number if number else title
    title_size   = number_size_eff if number else title_size_eff

    shadow_val = f'{p["shadow"]}, {p["shadow_inset"]}' if p["shadow_inset"] else p["shadow"]
    parts = [f'<div class="card" data-card-id="{card_id}">']
    parts.append('<style>')
    parts.append(f'.card[data-card-id="{card_id}"] .root {{')
    parts.append('  width: 100%; height: 100%; display: flex; flex-direction: column;')
    parts.append('  justify-content: center; align-items: center;')
    parts.append(f'  padding: {root_padding}; gap: 16px;')
    parts.append('}')
    parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{')
    parts.append(f'  background: {p["bg"]};')
    parts.append(f'  border-radius: {p["radius"]};')
    parts.append(f'  border: {p["border"]};')
    parts.append(f'  padding: {panel_padding};')
    parts.append(f'  display: flex; flex-direction: column; align-items: {panel_align};')
    parts.append(f'  gap: 14px; max-width: {max_width_eff}; position: relative;')
    parts.append(f'  box-shadow: {shadow_val};')
    parts.append('}')
    if p["has_grain"]:
        gt = p.get("grain_type", "")
        tex_svg = {"confetti": _CONFETTI_SVG, "grid": _GRID_SVG, "paper": _PAPER_GRAIN_SVG, "film": _FILM_GRAIN_SVG}.get(gt, _GRAIN_SVG)
        parts.append(f'.card[data-card-id="{card_id}"] .card-panel::after {{')
        parts.append(f'  content: ""; position: absolute; inset: 0;')
        parts.append(f'  border-radius: {p["radius"]};')
        parts.append(f'  background-image: url("{tex_svg}");')
        parts.append(f'  background-repeat: repeat; pointer-events: none;')
        parts.append('}')
    if kicker:
        parts.append(f'.card[data-card-id="{card_id}"] .kicker {{')
        parts.append(f'  font-family: {p["font"]}; font-size: {kicker_size_eff};')
        parts.append(f'  font-weight: 700; letter-spacing: 0.15em; text-transform: uppercase;')
        parts.append(f'  color: {p["accent"]};')
        parts.append('}')
    glow_css = f'  text-shadow: {p["title_glow"]};' if p["title_glow"] else ''
    parts.append(f'.card[data-card-id="{card_id}"] .title {{')
    parts.append(f'  font-family: {p["font"]}; font-size: {title_size};')
    parts.append(f'  font-weight: {p["font_weight"]}; line-height: 1.15; text-align: {text_align};')
    parts.append(f'  color: {p["text"]}; max-width: 100%;')
    if glow_css:
        parts.append(glow_css)
    parts.append(f'  font-variant-numeric: tabular-nums;')
    parts.append('}')
    # accent-word span: inherits .title font/size; adds color + background for swipe
    accent_word_hint = hints.get("accent_word", "")
    if accent_word_hint:
        _abg = _accent_bg_css(p)
        parts.append(f'.card[data-card-id="{card_id}"] .accent-word {{')
        parts.append(f'  color: {p["accent"]};')
        if _abg:
            parts.append(_abg.rstrip())
        parts.append('}')
    detail_font = p.get("font_detail", p["font"])
    if detail:
        parts.append(f'.card[data-card-id="{card_id}"] .detail {{')
        parts.append(f'  font-family: {detail_font}; font-size: {detail_size_eff};')
        parts.append(f'  font-weight: 400; text-align: {text_align};')
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
    # Comparison: two-column layout with text containment
    if content_style == "comparison":
        lv = hints.get("left_value", "")
        rv = hints.get("right_value", "")
        max_val_len = max(len(str(lv)), len(str(rv)))
        if compact:
            val_size = "23px" if max_val_len > 15 else "31px" if max_val_len > 8 else title_size_eff
        else:
            val_size = "36px" if max_val_len > 15 else "48px" if max_val_len > 8 else title_size_eff
        parts.append(f'.card[data-card-id="{card_id}"] .cmp-row {{')
        parts.append(f'  display: flex; gap: 24px; align-items: flex-start; width: 100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cmp-side {{')
        parts.append(f'  flex: 1; text-align: center; min-width: 0; overflow-wrap: break-word;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cmp-label {{')
        parts.append(f'  font-family: {p["font"]}; font-size: {kicker_size_eff};')
        parts.append(f'  font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase;')
        parts.append(f'  color: {p["text_secondary"]}; margin-bottom: 8px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cmp-value {{')
        parts.append(f'  font-family: {p["font"]}; font-size: {val_size};')
        parts.append(f'  font-weight: {p["font_weight"]}; color: {p["text"]};')
        parts.append(f'  font-variant-numeric: tabular-nums;')
        parts.append(f'  overflow-wrap: break-word; word-wrap: break-word;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cmp-sep {{')
        parts.append(f'  width: 2px; height: 0; background: {p["accent"]};')
        parts.append(f'  border-radius: 1px; flex-shrink: 0;')
        if p["title_glow"]:
            parts.append(f'  box-shadow: {p["accent_line_glow"]};')
        parts.append('}')
    # Timeline: adaptive layout (horizontal or vertical based on label length)
    if content_style == "timeline":
        is_paper_tl = p["id"] == "lean_paper"
        steps = hints.get("steps", [])
        n_steps = min(len(steps), 6)
        avg_label_len = sum(len(str(s)) for s in steps[:n_steps]) / max(n_steps, 1)
        total_label_chars = sum(len(str(s)) for s in steps[:n_steps])
        use_vertical = total_label_chars > 60 or avg_label_len > 18 or n_steps > 4
        if use_vertical:
            parts.append(f'.card[data-card-id="{card_id}"] .tl-track {{')
            parts.append(f'  display: flex; flex-direction: column; gap: 20px; width: 100%;')
            parts.append(f'  position: relative; padding: 16px 0;')
            parts.append('}')
            parts.append(f'.card[data-card-id="{card_id}"] .tl-line {{')
            parts.append(f'  position: absolute; left: 9px; top: 0; width: 3px; height: 0;')
            parts.append(f'  background: {p["accent"]};')
            if is_paper_tl:
                parts.append(f'  border-left: 2px dashed {p["accent"]};')
                parts.append(f'  background: transparent; width: 0;')
            parts.append('}')
            parts.append(f'.card[data-card-id="{card_id}"] .tl-step {{')
            parts.append(f'  display: flex; align-items: center; gap: 16px; z-index: 1;')
            parts.append('}')
        else:
            parts.append(f'.card[data-card-id="{card_id}"] .tl-track {{')
            parts.append(f'  display: flex; align-items: center; gap: 0;')
            parts.append(f'  width: 100%; position: relative; padding: 32px 0;')
            parts.append('}')
            parts.append(f'.card[data-card-id="{card_id}"] .tl-line {{')
            parts.append(f'  position: absolute; top: 50%; left: 0; height: 3px; width: 0;')
            parts.append(f'  background: {p["accent"]};')
            if is_paper_tl:
                parts.append(f'  border-top: 2px dashed {p["accent"]};')
                parts.append(f'  background: transparent; height: 0;')
            parts.append('}')
            parts.append(f'.card[data-card-id="{card_id}"] .tl-step {{')
            parts.append(f'  display: flex; flex-direction: column; align-items: center;')
            parts.append(f'  gap: 10px; flex: 1; z-index: 1;')
            parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tl-dot {{')
        parts.append(f'  width: 18px; height: 18px; border-radius: 50%;')
        parts.append(f'  background: {p["text_secondary"]}; flex-shrink: 0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tl-label {{')
        parts.append(f'  font-family: {p["font"]}; font-size: 20px;')
        parts.append(f'  font-weight: {p["font_weight"]}; color: {p["text"]};')
        if use_vertical:
            parts.append(f'  text-align: left;')
        else:
            parts.append(f'  text-align: center; white-space: nowrap;')
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
        parts.append(f'  font-size: {kicker_size_eff}; font-weight: 700;')
        parts.append(f'  color: {p["accent"]}; margin-bottom: 4px;')
        parts.append('}')
    # Trend: simple SVG line
    if content_style == "trend":
        parts.append(f'.card[data-card-id="{card_id}"] .trend-wrap {{')
        parts.append(f'  position: relative; width: 100%; height: 120px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .trend-label {{')
        parts.append(f'  font-family: {p["font"]}; font-size: {title_size_eff};')
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
        parts.append(f'  font-family: {p["font"]}; font-size: {list_item_size};')
        parts.append(f'  font-weight: {p["font_weight"]}; color: {p["text"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .list-bullet {{')
        parts.append(f'  width: 28px; height: 28px; border-radius: 50%;')
        parts.append(f'  background: {p["accent"]}; color: #fff;')
        parts.append(f'  display: flex; align-items: center; justify-content: center;')
        parts.append(f'  font-size: 14px; font-weight: 800; flex-shrink: 0;')
        parts.append('}')
    # Carousel: cycling slides
    if content_style == "carousel":
        parts.append(f'.card[data-card-id="{card_id}"] .carousel-slide {{')
        parts.append(f'  position: absolute; inset: 0; display: flex; align-items: center;')
        parts.append(f'  justify-content: center; font-family: {p["font"]};')
        parts.append(f'  font-size: 40px; font-weight: {p["font_weight"]};')
        parts.append(f'  color: {p["text"]}; text-align: center; padding: 20px;')
        parts.append(f'  opacity: 0;')
        parts.append('}')
    # Definition: term + explanation
    if content_style == "definition":
        parts.append(f'.card[data-card-id="{card_id}"] .def-term {{')
        parts.append(f'  font-family: {p["font"]}; font-size: {title_size_eff};')
        parts.append(f'  font-weight: {p["font_weight"]}; color: {p["text"]};')
        if p["title_glow"]:
            parts.append(f'  text-shadow: {p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .def-text {{')
        parts.append(f'  font-family: {p["font"]}; font-size: {detail_size_eff};')
        parts.append(f'  font-weight: 400; color: {p["text_secondary"]};')
        parts.append(f'  margin-top: 12px; text-align: center; max-width: 90%;')
        parts.append(f'  line-height: 1.5;')
        parts.append('}')
    # Checklist: items with checkmarks
    if content_style == "checklist":
        parts.append(f'.card[data-card-id="{card_id}"] .chk-items {{')
        parts.append(f'  display: flex; flex-direction: column; gap: 14px; width: 100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .chk-item {{')
        parts.append(f'  display: flex; align-items: center; gap: 14px;')
        parts.append(f'  font-family: {p["font"]}; font-size: {chk_item_size};')
        parts.append(f'  font-weight: {p["font_weight"]}; color: {p["text"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .chk-mark {{')
        parts.append(f'  width: 28px; height: 28px; flex-shrink: 0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .chk-mark path {{')
        parts.append(f'  stroke: {p["accent"]}; stroke-width: 3; fill: none;')
        parts.append(f'  stroke-dasharray: 30; stroke-dashoffset: 30;')
        parts.append('}')
    # Score: large impact score
    if content_style == "score":
        parts.append(f'.card[data-card-id="{card_id}"] .score-display {{')
        parts.append(f'  font-family: {p["font"]}; font-size: {number_size_eff};')
        parts.append(f'  font-weight: {p["font_weight"]}; color: {p["text"]};')
        parts.append(f'  text-align: center; font-variant-numeric: tabular-nums;')
        if p["title_glow"]:
            parts.append(f'  text-shadow: {p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .score-label {{')
        parts.append(f'  font-family: {p["font"]}; font-size: {detail_size_eff};')
        parts.append(f'  color: {p["text_secondary"]}; margin-top: 8px; text-align: center;')
        parts.append('}')
    # Mindmap: center + branches
    if content_style == "mindmap":
        parts.append(f'.card[data-card-id="{card_id}"] .mm-wrap {{')
        parts.append(f'  position: relative; width: 100%; min-height: 200px;')
        parts.append(f'  display: flex; align-items: center; justify-content: center;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .mm-center {{')
        parts.append(f'  font-family: {p["font"]}; font-size: 36px;')
        parts.append(f'  font-weight: {p["font_weight"]}; color: {p["text"]};')
        parts.append(f'  text-align: center; z-index: 2; position: relative;')
        parts.append(f'  padding: 16px 24px; background: {p["bg"]};')
        parts.append(f'  border-radius: {p["radius"]}; border: {p["border"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .mm-branch {{')
        parts.append(f'  position: absolute; display: flex; align-items: center;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .mm-branch-line {{')
        parts.append(f'  width: 0; height: 2px; background: {p["accent"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .mm-branch-label {{')
        parts.append(f'  font-family: {p["font"]}; font-size: 22px;')
        parts.append(f'  font-weight: {p["font_weight"]}; color: {p["accent"]};')
        parts.append(f'  white-space: nowrap; padding: 6px 12px;')
        parts.append('}')
    parts.append('</style>')
    # Timeline: full-screen overlay, no card-panel wrapper
    if content_style == "timeline":
        steps = hints.get("steps", [])
        n_steps = min(len(steps), 6)
        avg_label_len = sum(len(str(s)) for s in steps[:n_steps]) / max(n_steps, 1)
        total_label_chars = sum(len(str(s)) for s in steps[:n_steps])
        use_vertical = total_label_chars > 60 or avg_label_len > 18 or n_steps > 4
        parts.append(f'<div class="root" style="padding:60px 80px;justify-content:center">')
        if kicker:
            parts.append(f'  <div class="kicker" id="{card_id}-kicker" style="margin-bottom:24px">{_esc(kicker)}</div>')
        parts.append(f'  <div class="tl-track" data-layout="{"vertical" if use_vertical else "horizontal"}">')
        parts.append(f'    <div class="tl-line" id="{card_id}-tl-line"></div>')
        for i, step in enumerate(steps[:n_steps]):
            parts.append(f'    <div class="tl-step" id="{card_id}-step-{i}">')
            parts.append(f'      <div class="tl-dot" id="{card_id}-dot-{i}"></div>')
            parts.append(f'      <div class="tl-label">{_esc(str(step))}</div>')
            parts.append(f'    </div>')
        parts.append(f'  </div>')
        parts.append(f'  <div class="accent-line" id="{card_id}-line" style="margin-top:24px"></div>')
        parts.append('</div>')
        parts.append('</div>')
        return "\n".join(parts)
    parts.append('<div class="root">')
    if p.get("has_letterbox"):
        parts.append('  <div style="position:absolute;top:0;left:0;right:0;height:60px;background:#000;z-index:3"></div>')
        parts.append('  <div style="position:absolute;bottom:0;left:0;right:0;height:60px;background:#000;z-index:3"></div>')
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
        parts.append(f'    <div class="title" id="{card_id}-title">{_split_title_accent(display_text, accent_word_hint, card_id)}</div>')
        if attribution:
            parts.append(f'    <div class="attr-line" id="{card_id}-attr">{_esc(attribution)}</div>')
        if detail:
            parts.append(f'    <div class="detail" id="{card_id}-detail">{_esc(detail)}</div>')
    elif content_style == "carousel":
        slides = hints.get("slides", [])
        parts.append(f'    <div style="position:relative;width:100%;min-height:80px">')
        for i, slide in enumerate(slides[:4]):
            parts.append(f'      <div class="carousel-slide" id="{card_id}-slide-{i}">{_esc(str(slide))}</div>')
        parts.append(f'    </div>')
    elif content_style == "definition":
        term = hints.get("term", title)
        defn = hints.get("definition", detail)
        parts.append(f'    <div class="def-term" id="{card_id}-term">{_esc(term)}</div>')
        if defn:
            parts.append(f'    <div class="def-text" id="{card_id}-def">{_esc(defn)}</div>')
    elif content_style == "checklist":
        items = hints.get("items", [])
        parts.append(f'    <div class="chk-items">')
        for i, item in enumerate(items[:6]):
            parts.append(f'      <div class="chk-item" id="{card_id}-chk-{i}">')
            parts.append(f'        <svg class="chk-mark" viewBox="0 0 28 28" id="{card_id}-chk-svg-{i}"><path d="M6 14 L12 20 L22 8"/></svg>')
            parts.append(f'        <span>{_esc(str(item))}</span>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "score":
        score_text = hints.get("score_text", display_text)
        score_label = hints.get("label", "")
        parts.append(f'    <div class="score-display" id="{card_id}-score">{_esc(score_text)}</div>')
        if score_label:
            parts.append(f'    <div class="score-label" id="{card_id}-score-label">{_esc(score_label)}</div>')
    elif content_style == "mindmap":
        center_text = hints.get("center", title)
        branches = hints.get("branches", [])
        n_br = min(len(branches), 3)
        positions = [
            ("top: 10%; left: 50%; transform: translateX(-50%)", "flex-direction: column-reverse"),
            ("bottom: 10%; left: 15%", "flex-direction: row"),
            ("bottom: 10%; right: 15%", "flex-direction: row-reverse"),
        ]
        parts.append(f'    <div class="mm-wrap">')
        parts.append(f'      <div class="mm-center" id="{card_id}-mm-center">{_esc(center_text)}</div>')
        for i, br in enumerate(branches[:n_br]):
            pos_style, flex_dir = positions[i % len(positions)]
            parts.append(f'      <div class="mm-branch" id="{card_id}-br-{i}" style="{pos_style};{flex_dir}">')
            parts.append(f'        <div class="mm-branch-line" id="{card_id}-br-line-{i}"></div>')
            parts.append(f'        <div class="mm-branch-label" id="{card_id}-br-label-{i}">{_esc(str(br))}</div>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    else:
        # key_phrase, quote, callout and any unknown style
        parts.append(f'    <div class="title" id="{card_id}-title">{_split_title_accent(display_text, accent_word_hint, card_id)}</div>')
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
        f'  color: {p["accent"]};\n'
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
    is_ledger = p["id"] == "lean_ledger"
    is_craft = p["id"] == "lean_craft"
    is_cinema = p["id"] == "lean_cinema"
    is_paper = p["id"] == "lean_paper"
    ease_in = (_EASE_CINEMA_IN if is_cinema else _EASE_LEDGER_IN if is_ledger
               else _EASE_VIBE_IN if is_vibe else _EASE_CRAFT_IN if is_craft
               else _EASE_IN)
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

            if kind == "jump_cut":
                # Instantaneous scale jump — gsap.set has no duration.
                # transform-origin is always "center center" for cut jumps
                # (face-aware origin would shift the subject laterally at the cut).
                lines.append(
                    f'  tl.set("#video-wrap", '
                    f'{{ scale: {zto:.4f}, transformOrigin: "center center" }}, '
                    f'{zs:.4f});'
                )
            else:
                zdur = max(0.001, ze_end - zs)
                # Per-entry ease takes precedence; fall back to kind-based defaults.
                ze_ease_raw = ze.get("ease")
                if ze_ease_raw:
                    ease = f'"{ze_ease_raw}"'
                elif kind in ("punch_in", "pull_out"):
                    ease = '"power2.in"'
                else:
                    ease = '"sine.inOut"'
                lines.append(
                    f'  tl.fromTo("#video-wrap", '
                    f'{{ scale: {zfrom:.4f} }}, '
                    f'{{ scale: {zto:.4f}, duration: {zdur:.4f}, ease: {ease}, '
                    f'transformOrigin: "{transform_origin}", overwrite: "auto" }}, '
                    f'{zs:.4f});'
                )
        lines.append("")

    for card in cards:
        card_id = _esc_js(str(card.get("id", "")))
        if not card_id:
            # Card has no id — all GSAP selectors would be empty/invalid; skip it.
            print(f"[COMPOSE] WARNING: skipping card with missing id in timeline JS (startSec={card.get('startSec')})", flush=True)
            continue
        start = round(float(card.get("startSec", 0)), 3)
        end = round(float(card.get("endSec", start + 3)), 3)
        dur = round(end - start, 3)
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
            # Caption emphasis = accent color only. No swipe/underline — captions are
            # already karaoke-animated; a swipe on top creates visual overload.
        else:
            panel_sel = f'.card[data-card-id="{card_id}"] .card-panel'
            ent_dur = 0.550 if is_cinema else 0.320
            lines.append(
                f'  tl.fromTo(\'{sel}\', '
                f'{{ opacity: 0 }}, '
                f'{{ opacity: 1, duration: {ent_dur:.3f}, ease: _eIn }}, '
                f'{start:.4f});'
            )
            # Per-pack panel entry (the card-panel slides/scales into view)
            if is_cinema:
                pass  # cinema: slow opacity only, no panel movement
            elif is_ledger:
                # Scan down: clip from top (matches ledger's terminal aesthetic)
                lines.append(
                    f'  tl.fromTo(\'{panel_sel}\', '
                    f'{{ clipPath: "inset(100% 0 0% 0)" }}, '
                    f'{{ clipPath: "inset(0% 0 0% 0)", duration: 0.350, ease: _eIn }}, '
                    f'{start:.4f});'
                )
            elif is_vibe:
                # Bouncy: more scale, more y, slight tilt
                lines.append(
                    f'  tl.fromTo(\'{panel_sel}\', '
                    f'{{ scale: 1.08, y: 20, rotation: -1.5 }}, '
                    f'{{ scale: 1, y: 0, rotation: 0, duration: 0.400, ease: _eIn }}, '
                    f'{start:.4f});'
                )
            elif is_craft:
                # Handwritten tilt: slight rotation on entry
                lines.append(
                    f'  tl.fromTo(\'{panel_sel}\', '
                    f'{{ scale: 1.05, y: 10, rotation: 1 }}, '
                    f'{{ scale: 1, y: 0, rotation: 0, duration: 0.450, ease: _eIn }}, '
                    f'{start:.4f});'
                )
            elif is_paper:
                # Minimal: barely perceptible scale (clean aesthetic)
                lines.append(
                    f'  tl.fromTo(\'{panel_sel}\', '
                    f'{{ scale: 1.01, y: 6 }}, '
                    f'{{ scale: 1, y: 0, duration: 0.300, ease: _eIn }}, '
                    f'{start:.4f});'
                )
            else:
                # lean_glass (default)
                lines.append(
                    f'  tl.fromTo(\'{panel_sel}\', '
                    f'{{ scale: 1.04, y: 14 }}, '
                    f'{{ scale: 1, y: 0, duration: 0.350, ease: _eIn }}, '
                    f'{start:.4f});'
                )

            # Premium backdrop dim: every center-zone card darkens the video behind it.
            # Uses a separate overlay div (not CSS filter) — filter: brightness()
            # is not composited by SwiftShader on Railway.
            card_zone = card.get("zone", "")
            center_zone = card_zone in ("fullscreen", "video-overlay")
            if center_zone:
                lines.append(
                    f'  tl.to("#backdrop-dim", '
                    f'{{ opacity: 1, duration: 0.30, ease: _eIn }}, {start:.4f});'
                )
                lines.append(
                    f'  tl.to("#backdrop-dim", '
                    f'{{ opacity: 0, duration: 0.18, ease: _eOut }}, {end - 0.18:.4f});'
                )
                # Punch-in is handled as independent zoom entries via
                # _build_punch_in_zoom_entries() — not wired to card entry events.

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
                    if is_craft:
                        # Settle from 1.2x overshoot down to final value
                        overshoot_val = round(num_val * 1.2, 1)
                        lines.append(
                            f'  (function(){{ var o={{v:{overshoot_val}}}; tl.to(o, {{v:{num_val}, '
                            f'duration: {count_dur:.3f}, ease: _eIn, onUpdate: function(){{ '
                            f'var el=document.querySelector(\'{title_sel}\'); '
                            f'if(el) el.textContent=Math.round(o.v).toLocaleString()+\'{_esc_js(num_suffix)}\'; '
                            f'}}}}, {t_in:.4f}); }})();'
                        )
                    elif is_ledger:
                        lines.append(
                            f'  (function(){{ var o={{v:0}}; tl.to(o, {{v:{num_val}, '
                            f'duration: {count_dur:.3f}, ease: "none", onUpdate: function(){{ '
                            f'var el=document.querySelector(\'{title_sel}\'); '
                            f'if(el) el.textContent=Math.round(o.v).toLocaleString()+\'{_esc_js(num_suffix)}\'; '
                            f'}}}}, {t_in:.4f}); }})();'
                        )
                    elif is_paper:
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
                    if not is_ledger:
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
                if is_cinema:
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ opacity: 0 }}, '
                        f'{{ opacity: 1, duration: 0.600, ease: _eIn }}, '
                        f'{t_in:.4f});'
                    )
                elif is_craft:
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ opacity: 0, rotation: 2 }}, '
                        f'{{ opacity: 1, rotation: 0, duration: 0.450, ease: _eIn }}, '
                        f'{t_in:.4f});'
                    )
                elif is_paper:
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
                elif is_ledger:
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ clipPath: "inset(0 100% 0 0)" }}, '
                        f'{{ clipPath: "inset(0 0% 0 0)", duration: 0.500, ease: _eIn }}, '
                        f'{t_in:.4f});'
                    )
                else:
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ clipPath: "inset(0 100% 0 0)" }}, '
                        f'{{ clipPath: "inset(0 0% 0 0)", duration: 0.500, ease: "power2.inOut" }}, '
                        f'{t_in:.4f});'
                    )
            elif content_style == "quote":
                if is_cinema:
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ opacity: 0 }}, '
                        f'{{ opacity: 1, duration: 0.600, ease: _eIn }}, '
                        f'{t_in:.4f});'
                    )
                elif is_ledger:
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ opacity: 0 }}, '
                        f'{{ opacity: 1, duration: 0.200, ease: _eIn }}, '
                        f'{t_in:.4f});'
                    )
                else:
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
                avg_ll = sum(len(str(s)) for s in steps[:n_steps]) / max(n_steps, 1)
                total_ll = sum(len(str(s)) for s in steps[:n_steps])
                tl_vertical = total_ll > 60 or avg_ll > 18 or n_steps > 4
                tl_line_sel = f'.card[data-card-id="{card_id}"] #{card_id}-tl-line'
                line_dur = min(1.5, max(0.4, n_steps * 0.25))
                line_prop = "height" if tl_vertical else "width"
                lines.append(
                    f'  tl.to(\'{tl_line_sel}\', '
                    f'{{ {line_prop}: "100%", duration: {line_dur:.3f}, ease: "power2.inOut" }}, '
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
            elif content_style == "carousel":
                slides = card.get("contentHints", {}).get("slides", [])
                n_slides = min(len(slides), 4)
                if n_slides > 0:
                    slide_dur = max(1.0, dur / n_slides)
                    for si in range(n_slides):
                        sl_sel = f'.card[data-card-id="{card_id}"] #{card_id}-slide-{si}'
                        sl_in = start + si * slide_dur
                        sl_out = sl_in + slide_dur - 0.3
                        if is_paper:
                            lines.append(
                                f'  tl.to(\'{sl_sel}\', '
                                f'{{ opacity: 1, duration: 0.300, ease: _eIn }}, '
                                f'{sl_in:.4f});')
                            lines.append(
                                f'  tl.to(\'{sl_sel}\', '
                                f'{{ opacity: 0, duration: 0.250, ease: _eOut }}, '
                                f'{sl_out:.4f});')
                        elif is_vibe:
                            lines.append(
                                f'  tl.fromTo(\'{sl_sel}\', '
                                f'{{ opacity: 0, scale: 0.8 }}, '
                                f'{{ opacity: 1, scale: 1, duration: 0.300, ease: _eIn }}, '
                                f'{sl_in:.4f});')
                            lines.append(
                                f'  tl.to(\'{sl_sel}\', '
                                f'{{ opacity: 0, scale: 1.1, duration: 0.200, ease: _eOut }}, '
                                f'{sl_out:.4f});')
                        else:
                            lines.append(
                                f'  tl.fromTo(\'{sl_sel}\', '
                                f'{{ opacity: 0, x: 16 }}, '
                                f'{{ opacity: 1, x: 0, duration: 0.300, ease: _eIn }}, '
                                f'{sl_in:.4f});')
                            lines.append(
                                f'  tl.to(\'{sl_sel}\', '
                                f'{{ opacity: 0, x: -16, duration: 0.250, ease: _eOut }}, '
                                f'{sl_out:.4f});')
            elif content_style == "definition":
                term_sel = f'.card[data-card-id="{card_id}"] #{card_id}-term'
                def_sel = f'.card[data-card-id="{card_id}"] #{card_id}-def'
                if is_paper:
                    lines.append(
                        f'  tl.fromTo(\'{term_sel}\', '
                        f'{{ opacity: 0 }}, {{ opacity: 1, duration: 0.300, ease: _eIn }}, '
                        f'{t_in:.4f});')
                elif is_vibe:
                    lines.append(
                        f'  tl.fromTo(\'{term_sel}\', '
                        f'{{ opacity: 0, scale: 0.7 }}, '
                        f'{{ opacity: 1, scale: 1.05, duration: 0.350, ease: _eIn }}, '
                        f'{t_in:.4f});')
                    lines.append(
                        f'  tl.to(\'{term_sel}\', '
                        f'{{ scale: 1, duration: 0.200, ease: _eOut }}, {t_in + 0.35:.4f});')
                else:
                    lines.append(
                        f'  tl.fromTo(\'{term_sel}\', '
                        f'{{ clipPath: "inset(0 100% 0 0)" }}, '
                        f'{{ clipPath: "inset(0 0% 0 0)", duration: 0.500, ease: "power2.inOut" }}, '
                        f'{t_in:.4f});')
                lines.append(
                    f'  tl.fromTo(\'{def_sel}\', '
                    f'{{ opacity: 0 }}, {{ opacity: 1, duration: 0.300, ease: _eIn }}, '
                    f'{t_in + 0.20:.4f});')
            elif content_style == "checklist":
                items = card.get("contentHints", {}).get("items", [])
                n_items = min(len(items), 6)
                cascade_limit = min(n_items, 4)
                for i in range(n_items):
                    item_sel = f'.card[data-card-id="{card_id}"] #{card_id}-chk-{i}'
                    svg_sel = f'.card[data-card-id="{card_id}"] #{card_id}-chk-svg-{i} path'
                    stagger = i * 0.12 if i < cascade_limit else cascade_limit * 0.12
                    lines.append(
                        f'  tl.fromTo(\'{item_sel}\', '
                        f'{{ opacity: 0 }}, {{ opacity: 1, duration: 0.250, ease: _eIn }}, '
                        f'{t_in + stagger:.4f});')
                    lines.append(
                        f'  tl.to(\'{svg_sel}\', '
                        f'{{ strokeDashoffset: 0, duration: 0.300, ease: _eIn }}, '
                        f'{t_in + stagger + 0.10:.4f});')
                    if is_vibe:
                        lines.append(
                            f'  tl.fromTo(\'{svg_sel}\', '
                            f'{{ scale: 1 }}, {{ scale: 1.3, duration: 0.150, ease: _eIn }}, '
                            f'{t_in + stagger + 0.40:.4f});')
                        lines.append(
                            f'  tl.to(\'{svg_sel}\', '
                            f'{{ scale: 1, duration: 0.120, ease: _eOut }}, '
                            f'{t_in + stagger + 0.55:.4f});')
            elif content_style == "score":
                score_sel = f'.card[data-card-id="{card_id}"] #{card_id}-score'
                label_sel = f'.card[data-card-id="{card_id}"] #{card_id}-score-label'
                pop_scale = "1.15" if is_vibe else "1.08" if not is_paper else "1.04"
                lines.append(
                    f'  tl.fromTo(\'{score_sel}\', '
                    f'{{ opacity: 0, scale: 0.5 }}, '
                    f'{{ opacity: 1, scale: {pop_scale}, duration: 0.300, ease: _eIn }}, '
                    f'{t_in:.4f});')
                lines.append(
                    f'  tl.to(\'{score_sel}\', '
                    f'{{ scale: 1, duration: 0.200, ease: _eOut }}, '
                    f'{t_in + 0.30:.4f});')
                if not is_paper and p["title_glow"]:
                    lines.append(
                        f'  tl.to(\'{score_sel}\', '
                        f'{{ color: "{p["accent"]}", '
                        f'textShadow: "{_esc_js(p["title_glow_intense"])}", '
                        f'duration: 0.15 }}, {t_in + 0.30:.4f});')
                    lines.append(
                        f'  tl.to(\'{score_sel}\', '
                        f'{{ color: "{p["text"]}", '
                        f'textShadow: "{_esc_js(p["title_glow"])}", '
                        f'duration: 0.5 }}, {t_in + 0.45:.4f});')
                lines.append(
                    f'  tl.fromTo(\'{label_sel}\', '
                    f'{{ opacity: 0 }}, {{ opacity: 1, duration: 0.250, ease: _eIn }}, '
                    f'{t_in + 0.20:.4f});')
            elif content_style == "mindmap":
                center_sel = f'.card[data-card-id="{card_id}"] #{card_id}-mm-center'
                lines.append(
                    f'  tl.fromTo(\'{center_sel}\', '
                    f'{{ opacity: 0, scale: 0.8 }}, '
                    f'{{ opacity: 1, scale: 1, duration: 0.400, ease: _eIn }}, '
                    f'{t_in:.4f});')
                branches = card.get("contentHints", {}).get("branches", [])
                n_br = min(len(branches), 3)
                for bi in range(n_br):
                    bl_sel = f'.card[data-card-id="{card_id}"] #{card_id}-br-line-{bi}'
                    blbl_sel = f'.card[data-card-id="{card_id}"] #{card_id}-br-label-{bi}'
                    br_t = t_in + 0.30 + bi * 0.20
                    lines.append(
                        f'  tl.to(\'{bl_sel}\', '
                        f'{{ width: 100, duration: 0.400, ease: _eIn }}, '
                        f'{br_t:.4f});')
                    if is_vibe:
                        lines.append(
                            f'  tl.fromTo(\'{blbl_sel}\', '
                            f'{{ opacity: 0, scale: 0.7 }}, '
                            f'{{ opacity: 1, scale: 1, duration: 0.300, ease: _eIn }}, '
                            f'{br_t + 0.30:.4f});')
                    elif is_paper:
                        lines.append(
                            f'  tl.fromTo(\'{blbl_sel}\', '
                            f'{{ opacity: 0 }}, '
                            f'{{ opacity: 1, duration: 0.300, ease: _eIn }}, '
                            f'{br_t + 0.35:.4f});')
                    else:
                        lines.append(
                            f'  tl.fromTo(\'{blbl_sel}\', '
                            f'{{ opacity: 0, y: 8 }}, '
                            f'{{ opacity: 1, y: 0, duration: 0.300, ease: _eIn }}, '
                            f'{br_t + 0.30:.4f});')
            else:
                if is_cinema:
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ opacity: 0 }}, '
                        f'{{ opacity: 1, duration: 0.600, ease: _eIn }}, '
                        f'{t_in:.4f});'
                    )
                elif is_craft:
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ opacity: 0 }}, '
                        f'{{ opacity: 1, duration: 0.350, ease: _eIn }}, '
                        f'{t_in:.4f});'
                    )
                elif is_ledger:
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ opacity: 0 }}, '
                        f'{{ opacity: 1, duration: 0.200, ease: _eIn }}, '
                        f'{t_in:.4f});'
                    )
                elif is_paper:
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
                        f'{{ opacity: 0, y: 20 }}, '
                        f'{{ opacity: 1, y: 0, duration: 0.400, ease: _eIn }}, '
                        f'{t_in:.4f});'
                    )

            # Per-pack accent-word highlight swipe (fires 0.40s after title animates in)
            _aw = card.get("contentHints", {}).get("accent_word", "")
            if _aw:
                _aw_sel = f'.card[data-card-id="{card_id}"] #{card_id}-accent'
                lines.extend(_accent_treatment(p, _aw_sel, t_in + 0.40))

            if card.get("contentHints", {}).get("kicker"):
                lines.append(
                    f'  tl.fromTo(\'{kicker_sel}\', '
                    f'{{ opacity: 0, y: -8 }}, '
                    f'{{ opacity: 1, y: 0, duration: 0.250, ease: _eIn }}, '
                    f'{start + 0.10:.4f});'
                )
            # Accent-line is suppressed when accent_word is active — the word-level
            # underline swipe takes precedence; two competing line emphases clash.
            if not _aw:
                _line_w = 80 if card.get("zone", "") in _SIDE_PANEL_ZONES else 120
                lines.append(
                    f'  tl.fromTo(\'{line_sel}\', '
                    f'{{ width: 0 }}, '
                    f'{{ width: {_line_w}, duration: 0.400, ease: _eIn }}, '
                    f'{t_in + 0.30:.4f});'
                )
                # Breathing glow — half speed for question cards
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
            # Shimmer sweep — only for cards that have a shimmer-mask in DOM
            # (timeline cards return early in _build_graphic_card_html, no shimmer-mask)
            if content_style != "timeline":
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
            exit_dur = 0.500 if is_cinema else 0.180
            exit_ease = "_eIn" if is_cinema else "_eOut"
            exit_start = end - exit_dur
            panel_sel = f'.card[data-card-id="{card_id}"] .card-panel'
            lines.append(
                f'  tl.to(\'{sel}\', '
                f'{{ opacity: 0, duration: {exit_dur:.3f}, ease: {exit_ease} }}, '
                f'{exit_start:.4f});'
            )
            lines.append(
                f'  tl.to(\'{panel_sel}\', '
                f'{{ scale: 0.97, duration: 0.180, ease: _eOut }}, '
                f'{exit_start:.4f});'
            )
        lines.append(f'  tl.set(\'{sel}\', {{ opacity: 0, visibility: "hidden" }}, {end:.4f});')

        lines.append(f'  }} catch(_e) {{ console.warn("card {card_id} animation error:", _e); }}')
        lines.append("")

    # Caption suppression: fade captions out while graphic cards are visible
    graphic_windows = [
        (round(float(c.get("startSec", 0)), 3), round(float(c.get("endSec", 0)), 3))
        for c in cards if c.get("type") != "caption"
    ]
    caption_ids = [
        _esc_js(str(cid))
        for c in cards if c.get("type") == "caption"
        for cid in [c.get("id", "")]
        if cid  # skip captions with missing/empty id — sel would be invalid
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

    # Speaker-aware zone remap — applied before _clamp_overlaps so the same
    # zone value is seen by both _build_card_host (HTML) and _build_timeline_js (GSAP).
    _has_face = subject_position is not None
    if _has_face:
        _fl = float(subject_position.get("face_left_pct", 25.0))
        _fr = float(subject_position.get("face_right_pct", 75.0))
        _ft = float(subject_position.get("face_top_pct", 15.0))
        _fb = float(subject_position.get("face_bottom_pct", 65.0))
        _face_cx = (_fl + _fr) / 2   # 0–100 percent, left→right
        _face_cy = (_ft + _fb) / 2   # 0–100 percent, top→bottom
    else:
        _face_cx, _face_cy = 50.0, 50.0

    def _remap_zone(card: dict) -> dict:
        style = card.get("contentHints", {}).get("style", "")
        zone = card.get("zone", "video-overlay")
        if style not in _DATA_PANEL_TYPES or zone not in _CENTER_ZONES:
            return card
        if layout == "landscape":
            new_zone = "side-panel-left" if _face_cx > 50.0 else "side-panel-right"
        else:
            # Portrait: speaker framed low (face centre > 60%) → panel at top
            new_zone = "side-panel-top" if (_has_face and _face_cy > 60.0) else "side-panel"
        print(
            f"[COMPOSE] zone remap: {card.get('id', '?')} ({style}) {zone!r} → {new_zone!r}",
            flush=True,
        )
        return {**card, "zone": new_zone}

    graphic_cards = [_remap_zone(c) for c in graphic_cards]

    # Guard against overlapping clips on the same HyperFrames track.
    # Must run before _build_card_host AND _build_timeline_js so both
    # consume the clamped endSec (HTML attributes + GSAP exit keyframes).
    def _clamp_overlaps(cards: list, track_name: str) -> list:
        cards = sorted(cards, key=lambda c: float(c.get("startSec", 0)))
        kept: list = []
        for card in cards:
            start = float(card.get("startSec", 0))
            end   = float(card.get("endSec", start + 1))
            if kept:
                prev_end = float(kept[-1].get("endSec", 0))
                if end <= start or start < prev_end:
                    if start <= float(kept[-1].get("startSec", 0)) or end <= start:
                        # Fully contained or zero-duration — drop it
                        print(
                            f"[COMPOSE] WARNING: {track_name} card dropped (fully overlapped): "
                            f"id={card.get('id', '?')} [{start:.3f}s–{end:.3f}s] inside prev end {prev_end:.3f}s",
                            flush=True,
                        )
                        continue
                    # Partial overlap — clamp previous card's endSec
                    clamped = start - 0.001
                    if clamped <= float(kept[-1].get("startSec", 0)):
                        # Clamp would make previous card zero/negative — drop it instead
                        dropped = kept.pop()
                        print(
                            f"[COMPOSE] WARNING: {track_name} card dropped (fully overlapped after clamp): "
                            f"id={dropped.get('id', '?')} [{float(dropped.get('startSec',0)):.3f}s–{float(dropped.get('endSec',0)):.3f}s]",
                            flush=True,
                        )
                    else:
                        print(
                            f"[COMPOSE] WARNING: {track_name} overlap — card[{kept[-1].get('id','?')}].endSec "
                            f"clamped {float(kept[-1].get('endSec',0)):.3f}→{clamped:.3f} "
                            f"(next card starts at {start:.3f})",
                            flush=True,
                        )
                        kept[-1]["endSec"] = clamped
            kept.append(card)
        return kept

    graphic_cards = _clamp_overlaps(graphic_cards, "graphic")
    caption_cards = _clamp_overlaps(caption_cards, "caption")
    # Single source of truth: all_cards feeds BOTH _build_card_host and
    # _build_timeline_js so HTML attributes and GSAP animations are always in sync.
    all_cards = graphic_cards + caption_cards

    # Build card host divs — iterate all_cards (not graphic/caption separately)
    # so there is exactly one list reference shared with _build_timeline_js below.
    card_hosts = []
    for c in all_cards:
        track = 3 if c.get("type") == "caption" else 2
        card_hosts.append(_build_card_host(c, layout, track_index=track, pack=pack))

    # Build master timeline
    timeline_js = _build_timeline_js(all_cards, zoom_entries=zoom_entries, subject_position=subject_position, pack=pack)

    # CSS custom properties from theme
    accent_vars = "\n".join(
        f"    --accent-{i}: {color};" for i, color in enumerate(theme["accents"])
    )

    # Build Google Fonts import for pack-specific fonts
    _font_imports = {
        "lean_vibe": "Poppins:wght@400;800",
        "lean_craft": "Permanent+Marker",
        "lean_cinema": "Playfair+Display:wght@400;700",
        "lean_ledger": "IBM+Plex+Mono:wght@400;600",
    }
    font_link = ""
    fi = _font_imports.get(pack["id"], "")
    if fi:
        font_link = f'<link href="https://fonts.googleapis.com/css2?family={fi}&display=block" rel="stylesheet" />'

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
{font_link}
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
    <div id="backdrop-dim" style="position:absolute;inset:0;background:rgba(0,0,0,0.45);z-index:5;opacity:0;pointer-events:none;"></div>

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
