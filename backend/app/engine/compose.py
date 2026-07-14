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
    "fullscreen":       {"left": 0,    "top": 0,   "width": 1920, "height": 1080},
    "lower-third":      {"left": 0,    "top": 756, "width": 1920, "height": 324},
    "side-panel":       {"left": 0,    "top": 0,   "width": 806,  "height": 1080},
    "side-panel-left":  {"left": 0,    "top": 0,   "width": 806,  "height": 1080},
    "side-panel-right": {"left": 1114, "top": 0,   "width": 806,  "height": 1080},
    "whiteboard-area":  {"left": 40,   "top": 40,  "width": 1840, "height": 1000},
    "video-overlay":    {"left": 0,    "top": 0,   "width": 1920, "height": 1080},
    # B-roll data cards — upper-right compact, above caption zone (top < 400, caption at 756+)
    "upper-right":          {"left": 1300, "top": 80,  "width": 580,  "height": 320},
    "upper-data":           {"left": 1300, "top": 80,  "width": 580,  "height": 320},  # alias
    "lower-third-name":     {"left": 0,    "top": 620, "width": 1920, "height": 120},  # speaker ID above captions
}

# Zone → pixel bounds for portrait 9:16 (1080×1920)
#
# Fixed vertical bands (% of 1920px):
#   0–15%  (0–288)    : hook-title  — hook/titre overlay
#   15–70% (288–1344) : subject     — visage, NEVER overlaid
#   upper-right card  : top=100, bottom=420 — structurally above caption zone
#   70–85% (1344–1632): lower-third — captions ONLY
#   85–100%(1632–1920): safe margin
#
# Rule: upper-right cards end at px 420. Caption zone starts at px 1344.
# Gap = 924 px — zero structural overlap possible, by construction.
_ZONE_BOUNDS_PORTRAIT = {
    "fullscreen":     {"left": 0,   "top": 0,    "width": 1080, "height": 1920},
    "hook-title":     {"left": 0,   "top": 0,    "width": 1080, "height": 288},
    "upper-right":          {"left": 540, "top": 100,  "width": 500,  "height": 320},   # B-roll compact upper-right
    "upper-data":           {"left": 540, "top": 100,  "width": 500,  "height": 320},   # alias
    "lower-third":          {"left": 0,   "top": 1344, "width": 1080, "height": 288},   # captions ONLY
    "lower-third-name":     {"left": 0,   "top": 1150, "width": 1080, "height": 140},   # speaker ID above captions
    "side-panel":     {"left": 540, "top": 100,  "width": 500,  "height": 320},   # alias → upper-right
    "side-panel-top": {"left": 0,   "top": 0,    "width": 1080, "height": 288},
    "whiteboard-area":{"left": 60,  "top": 384,  "width": 960,  "height": 384},
    "video-overlay":  {"left": 0,   "top": 0,    "width": 1080, "height": 1920},
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
_DATA_PANEL_TYPES = {"stat", "list", "comparison", "checklist", "score", "trend", "rating", "progress_bar", "countdown", "step_number", "price_tag", "recap_summary", "formula_equation", "pros_cons", "star_rating_review", "income_reveal", "data_bar_chart", "number_ranking", "question_answer_pair", "cause_effect", "percentage_split", "red_flag_list", "client_avatar_persona", "tool_stack", "revenue_breakdown"}
_CENTER_ZONES = {"fullscreen", "video-overlay"}
_SIDE_PANEL_ZONES = {"side-panel", "side-panel-left", "side-panel-right", "side-panel-top", "upper-data", "upper-right"}


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

    # B-roll semantic cards render via their own type's render_html
    if "_broll_type" in card:
        try:
            from app.engine import broll_registry as _br
            _btype = _br.REGISTRY.get(card["_broll_type"])
            if _btype is not None:
                return _btype.render_html(card.get("_broll_params", {}), pack or {}, card_id)
        except Exception as _broll_html_exc:
            print(f"[BROLL] render_html error {card['_broll_type']}: {_broll_html_exc}", flush=True)
        # Fall through to default rendering on error

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
    # Callout: left accent stripe + tinted content area
    if content_style == "callout":
        acc_hex = p["accent"].lstrip("#")
        try:
            ar, ag, ab = int(acc_hex[0:2], 16), int(acc_hex[2:4], 16), int(acc_hex[4:6], 16)
            co_bg = f"rgba({ar},{ag},{ab},0.07)"
        except Exception:
            co_bg = "rgba(255,255,255,0.05)"
        parts.append(f'.card[data-card-id="{card_id}"] .co-wrap {{')
        parts.append(f'  display: flex; align-items: stretch; width: 100%;')
        parts.append(f'  background: {co_bg}; border-radius: {p["radius"]};')
        parts.append(f'  overflow: hidden;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .co-stripe {{')
        parts.append(f'  width: 4px; flex-shrink: 0; transform-origin: top center;')
        parts.append(f'  background: {p["accent"]};')
        if p.get("accent_line_glow"):
            parts.append(f'  box-shadow: {p["accent_line_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .co-body {{')
        parts.append(f'  padding: 16px 18px 16px 16px; flex: 1;')
        parts.append(f'  display: flex; flex-direction: column; justify-content: center;')
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
    # flowchart replaces mindmap — linear top→bottom with arrow connectors
    if content_style == "mindmap":
        parts.append(f'.card[data-card-id="{card_id}"] .fc-wrap {{')
        parts.append(f'  display: flex; flex-direction: column; align-items: center;')
        parts.append(f'  gap: 0; width: 100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .fc-node {{')
        parts.append(f'  font-family: {p["font"]}; font-size: {("14px" if compact else "18px")};')
        parts.append(f'  font-weight: {p["font_weight"]}; color: {p["text"]};')
        parts.append(f'  padding: 8px 16px; border-radius: {p["radius"]};')
        parts.append(f'  border: {p["border"]}; background: {p["bg"]};')
        parts.append(f'  text-align: center; opacity: 0; white-space: nowrap;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .fc-node.fc-root {{')
        parts.append(f'  color: {p["accent"]}; font-size: {("16px" if compact else "20px")};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .fc-arrow {{')
        parts.append(f'  width: 2px; height: 0; background: {p["accent"]};')
        parts.append(f'  opacity: 0.6; margin: 0 auto;')
        parts.append('}')
    # data_chart — animated bar chart (replaces trend for stat/score beats)
    if content_style == "data_chart":
        parts.append(f'.card[data-card-id="{card_id}"] .dc-wrap {{')
        parts.append(f'  width: 100%; display: flex; flex-direction: column; gap: 8px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .dc-row {{')
        parts.append(f'  display: flex; align-items: center; gap: 10px; opacity: 0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .dc-label {{')
        parts.append(f'  font-family: {p["font"]}; font-size: {("12px" if compact else "14px")};')
        parts.append(f'  font-weight: 600; color: {p["text_secondary"]}; width: 80px;')
        parts.append(f'  flex-shrink: 0; text-align: right;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .dc-track {{')
        parts.append(f'  flex: 1; height: 10px; background: rgba(255,255,255,0.08);')
        parts.append(f'  border-radius: 5px; overflow: hidden;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .dc-fill {{')
        parts.append(f'  height: 100%; width: 0%; background: {p["accent"]};')
        parts.append(f'  border-radius: 5px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .dc-val {{')
        parts.append(f'  font-family: {p["font"]}; font-size: {("12px" if compact else "14px")};')
        parts.append(f'  font-weight: 700; color: {p["accent"]}; width: 48px; flex-shrink: 0;')
        parts.append('}')
    # News-ticker: full-width horizontal crawl bar
    if content_style == "news_ticker":
        bg_solid = p.get("bg", "#0f0f13") if "gradient" not in p.get("bg","") else "#0f0f13"
        parts.append(f'.card[data-card-id="{card_id}"] .ticker-wrap {{')
        parts.append(f'  width:100%; height:100%; display:flex; align-items:center;')
        parts.append(f'  background:{bg_solid}; overflow:hidden; position:relative;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ticker-label {{')
        parts.append(f'  flex-shrink:0; padding:0 20px; font-family:{p["font"]};')
        parts.append(f'  font-size:{("14px" if compact else "16px")}; font-weight:800;')
        parts.append(f'  color:{p["bg"] if "gradient" not in p.get("bg","") else "#0f0f13"};')
        parts.append(f'  background:{p["accent"]}; height:100%;')
        parts.append(f'  display:flex; align-items:center;')
        parts.append(f'  white-space:nowrap; letter-spacing:0.10em; text-transform:uppercase;')
        parts.append(f'  z-index:2;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ticker-track {{')
        parts.append(f'  display:flex; will-change:transform;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ticker-item {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{("15px" if compact else "18px")};')
        parts.append(f'  font-weight:700; color:{p["text"]}; white-space:nowrap;')
        parts.append(f'  padding:0 40px; flex-shrink:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ticker-sep {{')
        parts.append(f'  color:{p["accent"]}; flex-shrink:0; font-size:20px;')
        parts.append('}')
    # Social overlay styles (instagram-follow, tiktok-follow, yt-lower-third)
    if content_style in ("instagram-follow", "tiktok-follow", "yt-lower-third"):
        parts.append(f'.card[data-card-id="{card_id}"] .so-wrap {{')
        parts.append(f'  display: inline-flex; align-items: center; gap: 12px;')
        parts.append(f'  padding: 12px 20px; border-radius: 40px;')
        if content_style == "instagram-follow":
            parts.append(f'  background: linear-gradient(135deg, #f09433 0%, #e6683c 25%, #dc2743 50%, #cc2366 75%, #bc1888 100%);')
        elif content_style == "tiktok-follow":
            parts.append(f'  background: #000000; border: 1.5px solid rgba(255,255,255,0.15);')
        else:  # yt-lower-third
            parts.append(f'  background: #FF0000;')
        parts.append(f'  opacity: 0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .so-icon {{')
        parts.append(f'  width: 28px; height: 28px; flex-shrink: 0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .so-text-col {{')
        parts.append(f'  display: flex; flex-direction: column; gap: 2px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .so-handle {{')
        parts.append(f'  font-family: {p["font"]}; font-size: {("13px" if compact else "15px")};')
        parts.append(f'  font-weight: 700; color: #FFFFFF; letter-spacing: 0.01em;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .so-cta {{')
        parts.append(f'  font-family: {p["font"]}; font-size: {("10px" if compact else "11px")};')
        parts.append(f'  font-weight: 600; color: rgba(255,255,255,0.8); letter-spacing: 0.08em;')
        parts.append(f'  text-transform: uppercase;')
        parts.append('}')
    # ── Wave 1 content types CSS ───────────────────────────────────────────
    if content_style == "rating":
        _w1_track_bg = "rgba(0,0,0,0.06)" if p["id"] in ("lean_paper", "lean_craft") else "rgba(255,255,255,0.08)"
        _w1_fill_r = "4px 2px 6px 3px" if p["id"] == "lean_craft" else "7px"
        parts.append(f'.card[data-card-id="{card_id}"] .rt-wrap {{')
        parts.append('  width:100%; display:flex; flex-direction:column; gap:14px; align-items:center;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .rt-value {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{number_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["accent"]};')
        parts.append('  font-variant-numeric:tabular-nums;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .rt-track {{')
        parts.append(f'  width:100%; height:16px; background:{_w1_track_bg}; border-radius:8px; overflow:hidden;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .rt-fill {{')
        parts.append(f'  height:100%; width:0%; background:{p["accent"]}; border-radius:{_w1_fill_r};')
        parts.append('}')
    if content_style == "map_location":
        parts.append(f'.card[data-card-id="{card_id}"] .ml-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:16px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ml-pin-wrap {{')
        parts.append('  position:relative; display:flex; align-items:center; justify-content:center;')
        parts.append('  width:64px; height:80px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ml-pulse {{')
        parts.append(f'  position:absolute; width:24px; height:24px; border-radius:50%;')
        parts.append(f'  border:2px solid {p["accent"]}; opacity:0;')
        parts.append('  top:50%; left:50%; transform:translate(-50%,-50%);')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ml-name {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ml-ctx {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  color:{p["text_secondary"]}; text-align:center;')
        parts.append('}')
    if content_style == "progress_bar":
        _w1_pb_track_bg = "rgba(0,0,0,0.06)" if p["id"] in ("lean_paper", "lean_craft") else "rgba(255,255,255,0.08)"
        _w1_pb_fill_r = "4px 2px 6px 3px" if p["id"] == "lean_craft" else "10px"
        parts.append(f'.card[data-card-id="{card_id}"] .pb-wrap {{')
        parts.append('  width:100%; display:flex; flex-direction:column; gap:12px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .pb-row {{')
        parts.append('  display:flex; justify-content:space-between; align-items:center;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .pb-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:700; color:{p["text"]}; letter-spacing:0.10em; text-transform:uppercase;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .pb-pct {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:800; color:{p["accent"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .pb-track {{')
        parts.append(f'  width:100%; height:20px; background:{_w1_pb_track_bg}; border-radius:10px; overflow:hidden;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .pb-fill {{')
        parts.append(f'  height:100%; width:0%; background:{p["accent"]}; border-radius:{_w1_pb_fill_r};')
        parts.append('}')
    if content_style == "before_after_image":
        parts.append(f'.card[data-card-id="{card_id}"] .ba-wrap {{')
        parts.append('  display:flex; flex-direction:row; align-items:stretch; width:100%; min-height:130px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ba-side {{')
        parts.append('  flex:1; display:flex; flex-direction:column; align-items:center;')
        parts.append('  justify-content:center; gap:10px; padding:18px; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ba-badge {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:800; letter-spacing:0.15em; text-transform:uppercase; color:{p["accent"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ba-text {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center;')
        parts.append('}')
        if p["id"] == "lean_craft":
            parts.append(f'.card[data-card-id="{card_id}"] .ba-div {{')
            parts.append('  width:24px; flex-shrink:0; display:flex; align-items:center; justify-content:center;')
            parts.append('}')
        else:
            parts.append(f'.card[data-card-id="{card_id}"] .ba-div {{')
            parts.append(f'  width:3px; background:{p["accent"]}; align-self:stretch; flex-shrink:0;')
            if p["accent_line_glow"]:
                parts.append(f'  box-shadow:{p["accent_line_glow"]};')
            parts.append('}')
    if content_style == "countdown":
        parts.append(f'.card[data-card-id="{card_id}"] .cd-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:8px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cd-num {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{number_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["accent"]};')
        parts.append('  font-variant-numeric:tabular-nums;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cd-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  color:{p["text_secondary"]}; text-align:center;')
        parts.append('}')
    if content_style == "poll_question":
        _w1_pq_opt_bg = "rgba(0,0,0,0.04)" if p["id"] in ("lean_paper", "lean_craft") else "rgba(255,255,255,0.04)"
        parts.append(f'.card[data-card-id="{card_id}"] .pq-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:16px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .pq-q {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .pq-opts {{')
        parts.append('  display:flex; flex-direction:column; gap:10px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .pq-opt {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  font-weight:600; color:{p["text"]};')
        parts.append(f'  padding:10px 18px; border-radius:{p["radius"]};')
        parts.append(f'  border:1px solid {p["accent"]}44; background:{_w1_pq_opt_bg}; opacity:0;')
        parts.append('}')
    if content_style == "myth_vs_fact":
        parts.append(f'.card[data-card-id="{card_id}"] .mvf-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:20px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .mvf-myth {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text_secondary"]};')
        parts.append('  position:relative; text-align:center;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .mvf-strike {{')
        parts.append(f'  position:absolute; top:50%; left:0; width:0; height:3px;')
        parts.append(f'  background:{p["accent"]}; border-radius:2px;')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .mvf-fact-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:8px; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .mvf-badge {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:800; letter-spacing:0.12em; text-transform:uppercase; color:{p["accent"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .mvf-fact {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
    # ── Wave 2 content types CSS ───────────────────────────────────────────
    if content_style == "step_number":
        parts.append(f'.card[data-card-id="{card_id}"] .sn-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:12px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .sn-num {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{number_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["accent"]}; line-height:1;')
        parts.append('  font-variant-numeric:tabular-nums;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .sn-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  font-weight:600; color:{p["text_secondary"]}; text-align:center;')
        parts.append('}')
    if content_style == "quote_carousel":
        parts.append(f'.card[data-card-id="{card_id}"] .qc-wrap {{')
        parts.append('  display:grid; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .qc-item {{')
        parts.append('  grid-area:1/1; opacity:0;')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
    if content_style == "emoji_reaction":
        parts.append(f'.card[data-card-id="{card_id}"] .er-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:0px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .er-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center; opacity:0;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
    if content_style == "price_tag":
        parts.append(f'.card[data-card-id="{card_id}"] .pt-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:8px;')
        if p["id"] == "lean_paper":
            parts.append(f'  border:2px solid {p["accent"]}66; border-radius:{p["radius"]}; padding:20px 28px;')
        elif p["id"] == "lean_craft":
            parts.append(f'  border:2px solid {p["accent"]}; border-radius:4px 12px 10px 6px; padding:18px 24px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .pt-price {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{number_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["accent"]}; line-height:1;')
        parts.append('  font-variant-numeric:tabular-nums;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .pt-ctx {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  color:{p["text_secondary"]}; text-align:center;')
        parts.append('}')
    if content_style == "warning_soft":
        parts.append(f'.card[data-card-id="{card_id}"] .ws-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:16px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ws-text {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
    if content_style == "testimonial":
        parts.append(f'.card[data-card-id="{card_id}"] .tm-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:16px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tm-qmark {{')
        parts.append(f'  font-family:{p["font"]}; font-size:60px; line-height:0.6;')
        parts.append(f'  color:{p["accent"]}; opacity:0.6;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tm-text {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tm-person {{')
        parts.append('  display:flex; align-items:center; gap:8px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tm-name {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:800; color:{p["accent"]}; letter-spacing:0.05em;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tm-sep {{')
        parts.append(f'  color:{p["text_secondary"]}; font-size:{kicker_size_eff};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tm-role {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  color:{p["text_secondary"]};')
        parts.append('}')
    if content_style == "versus_battle":
        _vb_vs_br = "50%" if p["id"] not in ("lean_craft", "lean_ledger") else "4px"
        parts.append(f'.card[data-card-id="{card_id}"] .vb-wrap {{')
        parts.append('  display:flex; flex-direction:row; align-items:center; gap:12px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .vb-side {{')
        parts.append('  flex:1; display:flex; align-items:center; justify-content:center; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .vb-text {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .vb-vs {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:900; color:{p["accent"]};')
        parts.append(f'  border:3px solid {p["accent"]}; border-radius:{_vb_vs_br};')
        parts.append('  width:52px; height:52px; flex-shrink:0; opacity:0;')
        parts.append('  display:flex; align-items:center; justify-content:center;')
        parts.append('  letter-spacing:0.05em;')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
    # ── Wave 3 content types CSS ──────────────────────────────────────────
    if content_style == "recap_summary":
        parts.append(f'.card[data-card-id="{card_id}"] .rs-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:8px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .rs-item {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]};')
        parts.append('  opacity:0; padding-left:18px; position:relative;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .rs-item::before {{')
        parts.append(f'  content:""; position:absolute; left:0; top:50%; transform:translateY(-50%);')
        parts.append(f'  width:6px; height:6px; border-radius:50%; background:{p["accent"]};')
        parts.append('}')
    if content_style == "location_journey":
        parts.append(f'.card[data-card-id="{card_id}"] .lj-wrap {{')
        parts.append('  display:flex; flex-direction:row; align-items:center; width:100%; gap:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .lj-point {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:6px; flex-shrink:0; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .lj-dot {{')
        parts.append(f'  width:14px; height:14px; border-radius:50%; background:{p["accent"]};')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .lj-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]};')
        parts.append('  text-align:center; max-width:90px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .lj-conn {{')
        parts.append(f'  flex:1; height:2px; background:{p["accent"]};')
        parts.append('  transform-origin:left center; transform:scaleX(0);')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
    if content_style == "formula_equation":
        parts.append(f'.card[data-card-id="{card_id}"] .fe-wrap {{')
        parts.append('  display:flex; justify-content:center; align-items:center; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .fe-parts {{')
        parts.append('  display:flex; align-items:center; gap:14px; flex-wrap:wrap; justify-content:center;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .fe-part {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .fe-op {{')
        parts.append(f'  color:{p["accent"]}; font-weight:900;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
    if content_style == "roadmap_milestone":
        parts.append(f'.card[data-card-id="{card_id}"] .rm-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:12px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .rm-icon {{')
        parts.append('  opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .rm-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; opacity:0; text-align:center;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .rm-ctx {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  color:{p["text_secondary"]}; opacity:0; text-align:center;')
        parts.append('}')
    if content_style == "pros_cons":
        parts.append(f'.card[data-card-id="{card_id}"] .pc-wrap {{')
        parts.append('  display:flex; flex-direction:row; gap:0; width:100%; align-items:flex-start;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .pc-col {{')
        parts.append('  flex:1; display:flex; flex-direction:column; gap:6px; padding:0 10px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .pc-hdr {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append('  font-weight:900; text-transform:uppercase; letter-spacing:0.08em;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .pc-hdr-pro {{')
        parts.append(f'  color:{p["accent"]};')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .pc-hdr-con {{')
        parts.append(f'  color:{p["text_secondary"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .pc-item {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .pc-div {{')
        parts.append(f'  width:1px; background:{p["accent"]}; align-self:stretch; flex-shrink:0;')
        parts.append('  transform-origin:top; transform:scaleY(0);')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
    if content_style == "star_rating_review":
        parts.append(f'.card[data-card-id="{card_id}"] .sr-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:10px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .sr-stars {{')
        parts.append('  display:flex; gap:4px; align-items:center;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .sr-star {{')
        parts.append('  font-size:32px; line-height:1; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .sr-star.filled {{')
        parts.append(f'  color:{p["accent"]};')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .sr-star.empty {{')
        parts.append(f'  color:{p["text_secondary"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .sr-text {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .sr-name {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  color:{p["text_secondary"]}; opacity:0;')
        parts.append('}')
    if content_style == "income_reveal":
        parts.append(f'.card[data-card-id="{card_id}"] .ir-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:8px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ir-value {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{number_size_eff};')
        parts.append('  font-weight:900; opacity:0; filter:blur(10px); letter-spacing:-0.02em;')
        parts.append(f'  color:{p["accent"]};')
        if p["title_glow_intense"]:
            parts.append(f'  text-shadow:{p["title_glow_intense"]};')
        elif p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ir-ctx {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  color:{p["text_secondary"]}; opacity:0;')
        parts.append('}')
    # ── Wave 4 CSS ────────────────────────────────────────────────────────────
    if content_style == "question_answer_pair":
        parts.append(f'.card[data-card-id="{card_id}"] .qap-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:14px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .qap-q {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:700; letter-spacing:0.08em; text-transform:uppercase;')
        parts.append(f'  color:{p["accent"]}; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .qap-div {{')
        parts.append(f'  width:0; height:2px; background:{p["accent"]}; border-radius:1px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .qap-a {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; line-height:1.2; color:{p["text"]}; opacity:0;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
    if content_style == "chapter_marker":
        parts.append(f'.card[data-card-id="{card_id}"] .cm-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:10px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cm-num {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{number_size_eff};')
        parts.append(f'  font-weight:900; color:{p["accent"]}; opacity:0; line-height:1;')
        if p["title_glow_intense"]:
            parts.append(f'  text-shadow:{p["title_glow_intense"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cm-line {{')
        parts.append(f'  width:0; height:3px; background:{p["accent"]}; border-radius:2px;')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cm-title {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center; opacity:0;')
        parts.append('}')
    if content_style == "secret_reveal":
        parts.append(f'.card[data-card-id="{card_id}"] .sec-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:10px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .sec-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:700; letter-spacing:0.15em; text-transform:uppercase;')
        parts.append(f'  color:{p["accent"]}; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .sec-text {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]};')
        parts.append(f'  text-align:center; filter:blur(12px); opacity:0;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
    if content_style == "objection_response":
        parts.append(f'.card[data-card-id="{card_id}"] .or-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:12px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .or-obj-hdr {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:700; letter-spacing:0.1em; text-transform:uppercase;')
        parts.append(f'  color:{p["text_secondary"]}; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .or-obj {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  color:{p["text_secondary"]}; font-style:italic; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .or-div {{')
        parts.append(f'  width:100%; height:2px; background:{p["accent"]}; transform:scaleX(0); transform-origin:left;')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .or-resp-hdr {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:700; letter-spacing:0.1em; text-transform:uppercase;')
        parts.append(f'  color:{p["accent"]}; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .or-resp {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; opacity:0;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
    if content_style == "data_bar_chart":
        _dbc_bg = "rgba(0,0,0,0.06)" if p["id"] in ("lean_paper", "lean_craft") else "rgba(255,255,255,0.08)"
        _dbc_fill_r = "3px 2px 5px 2px" if p["id"] == "lean_craft" else "8px"
        parts.append(f'.card[data-card-id="{card_id}"] .dbc-wrap {{')
        parts.append('  width:100%; display:flex; flex-direction:column; gap:10px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .dbc-row {{')
        parts.append('  display:flex; align-items:center; gap:10px; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .dbc-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:600; color:{p["text_secondary"]}; width:90px; flex-shrink:0; text-align:right;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .dbc-track {{')
        parts.append(f'  flex:1; height:14px; background:{_dbc_bg}; border-radius:7px; overflow:hidden;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .dbc-fill {{')
        parts.append(f'  height:100%; width:0%; background:{p["accent"]}; border-radius:{_dbc_fill_r};')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .dbc-val {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:800; color:{p["accent"]}; width:52px; flex-shrink:0; text-align:left;')
        parts.append('}')
    if content_style == "cause_effect":
        parts.append(f'.card[data-card-id="{card_id}"] .ceff-wrap {{')
        parts.append('  display:flex; flex-direction:row; align-items:center; gap:12px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ceff-box {{')
        parts.append(f'  flex:1; display:flex; flex-direction:column; align-items:center; gap:6px;')
        parts.append(f'  padding:14px 12px; border-radius:{p["radius"]}; text-align:center; opacity:0;')
        parts.append(f'  border:1px solid {p["accent"]}40; background:{p["accent"]}0A;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ceff-lbl {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:700; letter-spacing:0.1em; text-transform:uppercase;')
        parts.append(f'  color:{p["accent"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ceff-text {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ceff-arrow {{')
        parts.append(f'  flex-shrink:0; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ceff-arrow-path {{')
        parts.append(f'  stroke:{p["accent"]}; stroke-dasharray:100; stroke-dashoffset:100; fill:none;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ceff-arrowhead {{')
        parts.append(f'  fill:{p["accent"]}; opacity:0;')
        parts.append('}')
    if content_style == "number_ranking":
        parts.append(f'.card[data-card-id="{card_id}"] .nr-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:10px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .nr-item {{')
        parts.append('  display:flex; align-items:center; gap:14px; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .nr-pos {{')
        _nr_font_sz = "18px" if compact else "22px"
        parts.append(f'  width:40px; height:40px; border-radius:50%; flex-shrink:0;')
        parts.append(f'  display:flex; align-items:center; justify-content:center;')
        parts.append(f'  font-family:{p["font"]}; font-size:{_nr_font_sz}; font-weight:900;')
        parts.append(f'  color:{p["bg"]}; background:{p["accent"]};')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .nr-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .nr-item.nr-first .nr-pos {{')
        parts.append(f'  width:50px; height:50px;')
        if p["title_glow"]:
            parts.append(f'  box-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .nr-item.nr-first .nr-label {{')
        parts.append(f'  font-size:{number_size_eff};')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
    # ── Wave 5 CSS ────────────────────────────────────────────────────────────
    if content_style == "hand_written_note":
        parts.append(f'.card[data-card-id="{card_id}"] .hwn-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:0; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .hwn-text {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center;')
        parts.append(f'  opacity:0; transform:rotate(-1.5deg);')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .hwn-underline {{')
        parts.append(f'  width:0; height:2px; background:{p["accent"]}; margin-top:8px; border-radius:1px;')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
    if content_style == "speech_bubble_thought":
        parts.append(f'.card[data-card-id="{card_id}"] .sbt-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:12px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .sbt-bubbles {{')
        parts.append('  display:flex; gap:6px; align-items:flex-end; justify-content:center;')
        parts.append('}')
        _sbt_dot_sizes = ["8px", "12px", "18px"]
        for _sbt_i, _sbt_sz in enumerate(_sbt_dot_sizes):
            parts.append(f'.card[data-card-id="{card_id}"] .sbt-dot-{_sbt_i} {{')
            parts.append(f'  width:{_sbt_sz}; height:{_sbt_sz}; border-radius:50%;')
            parts.append(f'  background:{p["accent"]}; opacity:0;')
            if p["accent_line_glow"]:
                parts.append(f'  box-shadow:{p["accent_line_glow"]};')
            parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .sbt-text {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center;')
        parts.append(f'  opacity:0;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
    if content_style == "calendar_date_highlight":
        parts.append(f'.card[data-card-id="{card_id}"] .cal-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:8px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cal-cell {{')
        _cal_cell_sz = "120px" if compact else "140px"
        parts.append(f'  width:{_cal_cell_sz}; border-radius:{p["radius"]}; padding:16px 24px 20px;')
        parts.append(f'  background:{p["accent"]}; display:flex; align-items:center; justify-content:center;')
        parts.append(f'  opacity:0; transform:scale(0.85);')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cal-date {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{number_size_eff};')
        parts.append(f'  font-weight:900; color:{p["bg"]}; text-align:center; line-height:1;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cal-ctx {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text_secondary"]}; text-align:center;')
        parts.append(f'  opacity:0;')
        parts.append('}')
    if content_style == "percentage_split":
        parts.append(f'.card[data-card-id="{card_id}"] .psp-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:10px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .psp-bar-track {{')
        parts.append(f'  width:100%; height:28px; border-radius:{p["radius"]}; overflow:hidden;')
        parts.append(f'  background:{p["bg"]}; display:flex; opacity:0;')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .psp-segment {{')
        parts.append(f'  height:100%; width:0%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .psp-labels {{')
        parts.append('  display:flex; gap:16px; flex-wrap:wrap;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .psp-lbl {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; opacity:0;')
        parts.append('  display:flex; align-items:center; gap:6px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .psp-swatch {{')
        parts.append(f'  width:12px; height:12px; border-radius:3px; flex-shrink:0;')
        parts.append('}')
    if content_style == "red_flag_list":
        parts.append(f'.card[data-card-id="{card_id}"] .rfl-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:10px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .rfl-item {{')
        parts.append('  display:flex; align-items:center; gap:12px; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .rfl-flag {{')
        _rfl_flag_sz = "16px" if compact else "20px"
        parts.append(f'  width:{_rfl_flag_sz}; height:{_rfl_flag_sz}; flex-shrink:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .rfl-text {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]};')
        parts.append('}')
    if content_style == "success_metric_badge":
        parts.append(f'.card[data-card-id="{card_id}"] .smb-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:8px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .smb-badge {{')
        parts.append(f'  border-radius:{p["radius"]}; padding:20px 32px;')
        parts.append(f'  background:{p["accent"]}; display:flex; flex-direction:column;')
        parts.append(f'  align-items:center; gap:4px; opacity:0; transform:scale(0.85);')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .smb-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{number_size_eff};')
        parts.append(f'  font-weight:900; color:{p["bg"]}; text-align:center; line-height:1;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .smb-ctx {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["bg"]}; opacity:0.85; text-align:center;')
        parts.append('}')
    if content_style == "client_avatar_persona":
        parts.append(f'.card[data-card-id="{card_id}"] .cap-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:12px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cap-avatar {{')
        _cap_av_sz = "64px" if compact else "80px"
        parts.append(f'  width:{_cap_av_sz}; height:{_cap_av_sz}; border-radius:50%;')
        parts.append(f'  background:{p["accent"]}; display:flex; align-items:center; justify-content:center;')
        parts.append(f'  opacity:0; transform:scale(0.85);')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cap-initials {{')
        _cap_init_sz = "24px" if compact else "28px"
        parts.append(f'  font-family:{p["font"]}; font-size:{_cap_init_sz};')
        parts.append(f'  font-weight:900; color:{p["bg"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cap-name {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center; opacity:0;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cap-traits {{')
        parts.append('  display:flex; flex-wrap:wrap; gap:8px; justify-content:center;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cap-trait {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["accent"]};')
        parts.append(f'  border:1px solid {p["accent"]}; border-radius:999px;')
        parts.append(f'  padding:4px 12px; opacity:0;')
        parts.append('}')
    # ── Wave 6 CSS ────────────────────────────────────────────────────────────
    if content_style == "book_recommendation":
        parts.append(f'.card[data-card-id="{card_id}"] .br-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:10px; width:100%;')
        parts.append('}')
        _br_w = "80px" if compact else "96px"
        _br_h = "110px" if compact else "132px"
        parts.append(f'.card[data-card-id="{card_id}"] .br-cover {{')
        parts.append(f'  width:{_br_w}; height:{_br_h}; border-radius:3px 6px 6px 3px;')
        parts.append(f'  background:{p["accent"]}; border-left:6px solid {p["text"]};')
        parts.append(f'  opacity:0; transform:scale(0.80) perspective(400px) rotateY(-20deg);')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .br-title {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center; opacity:0;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .br-author {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text_secondary"]}; text-align:center; opacity:0;')
        parts.append('}')
    if content_style == "tool_stack":
        parts.append(f'.card[data-card-id="{card_id}"] .ts-wrap {{')
        parts.append('  display:flex; flex-wrap:wrap; gap:10px; justify-content:center; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ts-item {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]};')
        parts.append(f'  border:1.5px solid {p["accent"]}; border-radius:{p["radius"]};')
        parts.append(f'  padding:6px 14px; opacity:0;')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
    if content_style == "revenue_breakdown":
        parts.append(f'.card[data-card-id="{card_id}"] .rb-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:10px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .rb-row {{')
        parts.append('  display:flex; flex-direction:column; gap:4px; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .rb-meta {{')
        parts.append('  display:flex; justify-content:space-between; align-items:baseline;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .rb-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .rb-value {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  font-weight:900; color:{p["accent"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .rb-track {{')
        parts.append(f'  width:100%; height:10px; border-radius:{p["radius"]}; overflow:hidden;')
        parts.append(f'  background:{p["bg"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .rb-fill {{')
        parts.append(f'  height:100%; width:0%; background:{p["accent"]}; border-radius:{p["radius"]};')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
    if content_style == "age_milestone":
        parts.append(f'.card[data-card-id="{card_id}"] .am-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:8px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .am-number {{')
        _am_sz = "72px" if compact else "96px"
        parts.append(f'  font-family:{p["font"]}; font-size:{_am_sz};')
        parts.append(f'  font-weight:900; color:{p["accent"]}; line-height:1; text-align:center; opacity:0;')
        if p["title_glow_intense"]:
            parts.append(f'  text-shadow:{p["title_glow_intense"]};')
        elif p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .am-ctx {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center; opacity:0;')
        parts.append('}')
    if content_style == "contrarian_take":
        parts.append(f'.card[data-card-id="{card_id}"] .ct-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:10px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ct-text {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center; opacity:0;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ct-rule {{')
        parts.append(f'  width:0; height:2px; background:{p["accent"]}; border-radius:1px;')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
    if content_style == "action_step_cta":
        parts.append(f'.card[data-card-id="{card_id}"] .asc-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:10px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .asc-text {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{number_size_eff};')
        parts.append(f'  font-weight:900; color:{p["text"]}; text-align:center; opacity:0;')
        if p["title_glow_intense"]:
            parts.append(f'  text-shadow:{p["title_glow_intense"]};')
        elif p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .asc-rule {{')
        parts.append(f'  width:0; height:3px; background:{p["accent"]}; border-radius:2px;')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
    if content_style == "story_chapter_transition":
        parts.append(f'.card[data-card-id="{card_id}"] .sct-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; justify-content:center; gap:8px; width:100%; height:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .sct-text {{')
        _sct_sz = "28px" if compact else "36px"
        parts.append(f'  font-family:{p["font"]}; font-size:{_sct_sz};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center;')
        parts.append(f'  font-style:italic; opacity:0; letter-spacing:0.02em;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .sct-rule {{')
        parts.append(f'  width:0; height:1px; background:{p["accent"]}; border-radius:1px;')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
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
    if content_style == "news_ticker":
        ticker_text = _esc(title or kicker or "BREAKING")
        label_text  = _esc(kicker or "LIVE")
        # Repeat 4× so the CSS marquee always has content
        items_html = "".join(
            f'<span class="ticker-item">{ticker_text}</span>'
            f'<span class="ticker-sep">●</span>'
            for _ in range(4)
        )
        parts.append(f'<div class="root" style="padding:0;">')
        parts.append(f'  <div class="ticker-wrap" id="{card_id}-ticker-wrap">')
        parts.append(f'    <div class="ticker-label">{label_text}</div>')
        parts.append(f'    <div class="ticker-track" id="{card_id}-track">{items_html}</div>')
        parts.append(f'  </div>')
        parts.append(f'</div>')
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
        parts.append(f'    <div style="position:relative;width:100%;min-height:130px;flex:1">')
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
        # Rendered as native flowchart: root → branches in linear vertical flow
        center_text = hints.get("center", title)
        branches = hints.get("branches", [])
        n_br = min(len(branches), 4)
        parts.append(f'    <div class="fc-wrap">')
        parts.append(f'      <div class="fc-node fc-root" id="{card_id}-fc-root">{_esc(center_text)}</div>')
        for i, br in enumerate(branches[:n_br]):
            parts.append(f'      <div class="fc-arrow" id="{card_id}-fc-arrow-{i}" style="height:0"></div>')
            parts.append(f'      <div class="fc-node" id="{card_id}-fc-{i}">{_esc(str(br))}</div>')
        parts.append(f'    </div>')
    elif content_style == "data_chart":
        chart_items = hints.get("items", [])
        if not chart_items and hints.get("branches"):
            chart_items = hints.get("branches", [])
        # items can be "Label: value" strings or plain strings
        rows: list[tuple[str, float]] = []
        max_v = 1.0
        for raw in chart_items[:5]:
            parts_split = str(raw).split(":", 1)
            if len(parts_split) == 2:
                lbl, val_s = parts_split
                try:
                    val = float(val_s.strip().replace("%", "").replace(",", "."))
                except ValueError:
                    val = float(len(rows) + 1)
            else:
                lbl, val = str(raw), float(len(rows) + 1)
            rows.append((lbl.strip(), val))
            if val > max_v:
                max_v = val
        parts.append(f'    <div class="dc-wrap">')
        for i, (lbl, val) in enumerate(rows):
            pct = round(val / max_v * 100, 1)
            parts.append(f'      <div class="dc-row" id="{card_id}-dc-{i}">')
            parts.append(f'        <div class="dc-label">{_esc(lbl)}</div>')
            parts.append(f'        <div class="dc-track"><div class="dc-fill" id="{card_id}-dc-fill-{i}" data-pct="{pct}"></div></div>')
            parts.append(f'        <div class="dc-val">{val:g}</div>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style in ("instagram-follow", "tiktok-follow", "yt-lower-third"):
        handle = _esc(hints.get("title", kicker or "@handle"))
        if content_style == "instagram-follow":
            icon_svg = (
                '<svg viewBox="0 0 24 24" width="28" height="28" fill="none" xmlns="http://www.w3.org/2000/svg">'
                '<rect x="2" y="2" width="20" height="20" rx="5" stroke="#fff" stroke-width="1.8"/>'
                '<circle cx="12" cy="12" r="4.5" stroke="#fff" stroke-width="1.8"/>'
                '<circle cx="17.5" cy="6.5" r="1" fill="#fff"/>'
                '</svg>'
            )
            cta = "Suivre"
        elif content_style == "tiktok-follow":
            icon_svg = (
                '<svg viewBox="0 0 24 24" width="28" height="28" fill="white" xmlns="http://www.w3.org/2000/svg">'
                '<path d="M19.59 6.69a4.83 4.83 0 01-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 01-2.88 2.5 2.89 2.89 0 01-2.89-2.89 2.89 2.89 0 012.89-2.89c.28 0 .54.04.79.1V9.01a6.34 6.34 0 00-.79-.05 6.34 6.34 0 00-6.34 6.34 6.34 6.34 0 006.34 6.34 6.34 6.34 0 006.33-6.34V8.69a8.18 8.18 0 004.78 1.52V6.76a4.85 4.85 0 01-1.01-.07z"/>'
                '</svg>'
            )
            cta = "Suivre"
        else:  # yt-lower-third
            icon_svg = (
                '<svg viewBox="0 0 24 24" width="28" height="28" fill="white" xmlns="http://www.w3.org/2000/svg">'
                '<path d="M10 15l5.19-3L10 9v6z"/>'
                '<path d="M21.56 7.17a2.76 2.76 0 00-1.94-1.95C17.88 4.75 12 4.75 12 4.75s-5.88 0-7.62.47a2.76 2.76 0 00-1.94 1.95A28.6 28.6 0 002 12a28.6 28.6 0 00.44 4.83 2.76 2.76 0 001.94 1.95c1.74.47 7.62.47 7.62.47s5.88 0 7.62-.47a2.76 2.76 0 001.94-1.95A28.6 28.6 0 0022 12a28.6 28.6 0 00-.44-4.83z"/>'
                '</svg>'
            )
            cta = "S'abonner"
        parts.append(f'    <div class="so-wrap" id="{card_id}-so">')
        parts.append(f'      <div class="so-icon">{icon_svg}</div>')
        parts.append(f'      <div class="so-text-col">')
        parts.append(f'        <div class="so-handle">{handle}</div>')
        parts.append(f'        <div class="so-cta">{_esc(cta)}</div>')
        parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "callout":
        parts.append(f'    <div class="co-wrap" id="{card_id}-co">')
        parts.append(f'      <div class="co-stripe" id="{card_id}-co-stripe"></div>')
        parts.append(f'      <div class="co-body">')
        parts.append(f'        <div class="title" id="{card_id}-title">{_split_title_accent(display_text, accent_word_hint, card_id)}</div>')
        if detail:
            parts.append(f'        <div class="detail" id="{card_id}-co-detail">{_esc(detail)}</div>')
        parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "rating":
        _rv_raw = str(hints.get("rating_value") or "7")
        _rm_raw = str(hints.get("rating_max") or "10")
        try:
            _rv_f = float(_rv_raw.replace(",", "."))
            _rm_f_raw = float(_rm_raw.replace(",", "."))
            _rm_f = _rm_f_raw if _rm_f_raw > 0 else 10.0
            _rt_disp = f"{_rv_f:g}/{_rm_f:g}"
        except (ValueError, TypeError):
            _rt_disp = _rv_raw.replace(",", ".") or "—"
        parts.append(f'    <div class="rt-wrap">')
        parts.append(f'      <div class="rt-value" id="{card_id}-rt-val">{_esc(_rt_disp)}</div>')
        parts.append(f'      <div class="rt-track"><div class="rt-fill" id="{card_id}-rt-fill"></div></div>')
        parts.append(f'    </div>')
    elif content_style == "map_location":
        _loc_name = _esc(hints.get("location_name", ""))
        _loc_ctx = _esc(hints.get("location_context", ""))
        _acc_ml = p["accent"]
        if p["id"] == "lean_craft":
            _pin_svg = (
                f'<svg viewBox="0 0 48 48" width="48" height="48">'
                f'<line x1="4" y1="4" x2="44" y2="44" stroke="{_acc_ml}" stroke-width="5" stroke-linecap="round"/>'
                f'<line x1="44" y1="4" x2="4" y2="44" stroke="{_acc_ml}" stroke-width="5" stroke-linecap="round"/>'
                f'</svg>'
            )
        else:
            _pin_svg = (
                f'<svg viewBox="0 0 24 32" width="48" height="64">'
                f'<path d="M12 0C5.4 0 0 5.4 0 12c0 9 12 20 12 20S24 21 24 12C24 5.4 18.6 0 12 0z" fill="{_acc_ml}"/>'
                f'<circle cx="12" cy="12" r="4" fill="#fff" opacity="0.9"/>'
                f'</svg>'
            )
        parts.append(f'    <div class="ml-wrap">')
        parts.append(f'      <div class="ml-pin-wrap">')
        parts.append(f'        <div id="{card_id}-ml-pin" style="opacity:0">{_pin_svg}</div>')
        parts.append(f'        <div class="ml-pulse" id="{card_id}-ml-pulse"></div>')
        parts.append(f'      </div>')
        if _loc_name:
            parts.append(f'      <div class="ml-name" id="{card_id}-ml-name" style="opacity:0">{_loc_name}</div>')
        if _loc_ctx:
            parts.append(f'      <div class="ml-ctx" id="{card_id}-ml-ctx" style="opacity:0">{_loc_ctx}</div>')
        parts.append(f'    </div>')
    elif content_style == "progress_bar":
        _pb_pct_val = 70.0
        try:
            _pb_pct_val = min(100.0, max(0.0, float(str(hints.get("progress_percent", 70)))))
        except (ValueError, TypeError):
            pass
        _pb_label = _esc(hints.get("progress_label", ""))
        parts.append(f'    <div class="pb-wrap">')
        parts.append(f'      <div class="pb-row">')
        if _pb_label:
            parts.append(f'        <div class="pb-label">{_pb_label}</div>')
        parts.append(f'        <div class="pb-pct" id="{card_id}-pb-pct">0%</div>')
        parts.append(f'      </div>')
        parts.append(f'      <div class="pb-track"><div class="pb-fill" id="{card_id}-pb-fill"></div></div>')
        parts.append(f'    </div>')
    elif content_style == "before_after_image":
        _before = _esc(hints.get("before_label", "Avant"))
        _after = _esc(hints.get("after_label", "Après"))
        _acc_ba = p["accent"]
        parts.append(f'    <div class="ba-wrap">')
        parts.append(f'      <div class="ba-side" id="{card_id}-ba-before">')
        parts.append(f'        <div class="ba-badge">AVANT</div>')
        parts.append(f'        <div class="ba-text">{_before}</div>')
        parts.append(f'      </div>')
        if p["id"] == "lean_craft":
            parts.append(f'      <div class="ba-div" id="{card_id}-ba-div">')
            parts.append(f'        <svg viewBox="0 0 20 200" width="20" height="200" preserveAspectRatio="none">')
            parts.append(f'          <path d="M10 0 Q16 50 10 100 Q4 150 10 200" stroke="{_acc_ba}" stroke-width="3"')
            parts.append(f'                fill="none" stroke-dasharray="400" stroke-dashoffset="400" id="{card_id}-ba-path"/>')
            parts.append(f'        </svg>')
            parts.append(f'      </div>')
        else:
            parts.append(f'      <div class="ba-div" id="{card_id}-ba-div"></div>')
        parts.append(f'      <div class="ba-side" id="{card_id}-ba-after">')
        parts.append(f'        <div class="ba-badge">APRÈS</div>')
        parts.append(f'        <div class="ba-text">{_after}</div>')
        parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "countdown":
        _cd_from = 5
        try:
            _cd_from = max(1, int(float(str(hints.get("countdown_from", 5)))))
        except (ValueError, TypeError):
            pass
        _cd_label = _esc(hints.get("countdown_label", ""))
        parts.append(f'    <div class="cd-wrap">')
        parts.append(f'      <div class="cd-num" id="{card_id}-cd-num">{_cd_from}</div>')
        if _cd_label:
            parts.append(f'      <div class="cd-label">{_cd_label}</div>')
        parts.append(f'    </div>')
    elif content_style == "poll_question":
        _pq_q = _esc(hints.get("poll_question", ""))
        _pq_opts = hints.get("poll_options", [])
        parts.append(f'    <div class="pq-wrap">')
        if _pq_q:
            parts.append(f'      <div class="pq-q" id="{card_id}-pq-q">{_pq_q}</div>')
        parts.append(f'      <div class="pq-opts">')
        for _oi, _opt in enumerate(_pq_opts[:4]):
            parts.append(f'        <div class="pq-opt" id="{card_id}-pq-opt-{_oi}">{_esc(str(_opt))}</div>')
        parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "myth_vs_fact":
        _myth = _esc(hints.get("myth_text", ""))
        _fact = _esc(hints.get("fact_text", ""))
        parts.append(f'    <div class="mvf-wrap">')
        parts.append(f'      <div class="mvf-myth" id="{card_id}-mvf-myth">')
        parts.append(f'        {_myth}')
        parts.append(f'        <div class="mvf-strike" id="{card_id}-mvf-strike"></div>')
        parts.append(f'      </div>')
        parts.append(f'      <div class="mvf-fact-wrap" id="{card_id}-mvf-fact-wrap">')
        parts.append(f'        <div class="mvf-badge">FAIT</div>')
        parts.append(f'        <div class="mvf-fact" id="{card_id}-mvf-fact">{_fact}</div>')
        parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "step_number":
        _sn_num = _esc(hints.get("step_num", "1"))
        _sn_label = _esc(hints.get("step_label", ""))
        parts.append(f'    <div class="sn-wrap">')
        parts.append(f'      <div class="sn-num" id="{card_id}-sn-num">{_sn_num}</div>')
        if _sn_label:
            parts.append(f'      <div class="sn-label" id="{card_id}-sn-label">{_sn_label}</div>')
        parts.append(f'    </div>')
    elif content_style == "quote_carousel":
        _qc_quotes = hints.get("quotes", [])
        parts.append(f'    <div class="qc-wrap">')
        for _qi, _q in enumerate(_qc_quotes[:5]):
            parts.append(f'      <div class="qc-item" id="{card_id}-qc-{_qi}">{_esc(str(_q))}</div>')
        parts.append(f'    </div>')
    elif content_style == "emoji_reaction":
        _er_label = _esc(hints.get("emoji_label", hints.get("title", "")))
        parts.append(f'    <div class="er-wrap">')
        parts.append(f'      <div class="er-label" id="{card_id}-er-label">{_er_label}</div>')
        parts.append(f'    </div>')
    elif content_style == "price_tag":
        _pt_price = _esc(hints.get("price", ""))
        _pt_ctx = _esc(hints.get("price_context", ""))
        parts.append(f'    <div class="pt-wrap">')
        parts.append(f'      <div class="pt-price" id="{card_id}-pt-price">{_pt_price}</div>')
        if _pt_ctx:
            parts.append(f'      <div class="pt-ctx" id="{card_id}-pt-ctx">{_pt_ctx}</div>')
        parts.append(f'    </div>')
    elif content_style == "warning_soft":
        _ws_text = _esc(hints.get("warning_text", ""))
        _ws_acc = p["accent"]
        if p["id"] == "lean_craft":
            _ws_svg = (
                f'<svg viewBox="0 0 48 48" width="44" height="44">'
                f'<path d="M24 5 L43 41 H5 Z" fill="none" stroke="{_ws_acc}" stroke-width="2.5"'
                f' stroke-linejoin="round" stroke-linecap="round"/>'
                f'<path d="M24 19 L24 30" stroke="{_ws_acc}" stroke-width="2.5" stroke-linecap="round"/>'
                f'<circle cx="24" cy="35.5" r="1.8" fill="{_ws_acc}"/>'
                f'</svg>'
            )
        else:
            _ws_svg = (
                f'<svg viewBox="0 0 48 48" width="44" height="44">'
                f'<path d="M24 6L44 42H4L24 6Z" fill="none" stroke="{_ws_acc}" stroke-width="3"'
                f' stroke-linejoin="round"/>'
                f'<line x1="24" y1="19" x2="24" y2="31" stroke="{_ws_acc}" stroke-width="3"'
                f' stroke-linecap="round"/>'
                f'<circle cx="24" cy="36" r="2.5" fill="{_ws_acc}"/>'
                f'</svg>'
            )
        parts.append(f'    <div class="ws-wrap">')
        parts.append(f'      <div class="ws-icon" id="{card_id}-ws-icon" style="opacity:0">{_ws_svg}</div>')
        parts.append(f'      <div class="ws-text" id="{card_id}-ws-text" style="opacity:0">{_ws_text}</div>')
        parts.append(f'    </div>')
    elif content_style == "testimonial":
        _tm_text = _esc(hints.get("testimonial_text", ""))
        _tm_name = _esc(hints.get("person_name", ""))
        _tm_role = _esc(hints.get("person_role", ""))
        parts.append(f'    <div class="tm-wrap">')
        parts.append(f'      <div class="tm-qmark">“</div>')
        parts.append(f'      <div class="tm-text" id="{card_id}-tm-text" style="opacity:0">{_tm_text}</div>')
        if _tm_name or _tm_role:
            parts.append(f'      <div class="tm-person" id="{card_id}-tm-person" style="opacity:0">')
            if _tm_name:
                parts.append(f'        <span class="tm-name">{_tm_name}</span>')
            if _tm_name and _tm_role:
                parts.append(f'        <span class="tm-sep">·</span>')
            if _tm_role:
                parts.append(f'        <span class="tm-role">{_tm_role}</span>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "versus_battle":
        _vb_a = _esc(hints.get("side_a", ""))
        _vb_b = _esc(hints.get("side_b", ""))
        parts.append(f'    <div class="vb-wrap">')
        parts.append(f'      <div class="vb-side" id="{card_id}-vb-a"><div class="vb-text">{_vb_a}</div></div>')
        parts.append(f'      <div class="vb-vs" id="{card_id}-vb-vs">VS</div>')
        parts.append(f'      <div class="vb-side" id="{card_id}-vb-b"><div class="vb-text">{_vb_b}</div></div>')
        parts.append(f'    </div>')
    elif content_style == "recap_summary":
        _rs_items = hints.get("recap_items", [])
        _n_rs = min(len(_rs_items), 5)
        parts.append(f'    <div class="rs-wrap">')
        for _rs_i, _rs_it in enumerate(_rs_items[:_n_rs]):
            parts.append(f'      <div class="rs-item" id="{card_id}-rs-{_rs_i}">{_esc(str(_rs_it))}</div>')
        parts.append(f'    </div>')
    elif content_style == "location_journey":
        _lj_pts = hints.get("journey_points", [])
        _n_lj = min(len(_lj_pts), 5)
        parts.append(f'    <div class="lj-wrap">')
        for _lj_i, _lj_pt in enumerate(_lj_pts[:_n_lj]):
            parts.append(f'      <div class="lj-point" id="{card_id}-lj-{_lj_i}">')
            parts.append(f'        <div class="lj-dot"></div>')
            parts.append(f'        <div class="lj-label">{_esc(str(_lj_pt))}</div>')
            parts.append(f'      </div>')
            if _lj_i < _n_lj - 1:
                parts.append(f'      <div class="lj-conn" id="{card_id}-lj-c{_lj_i}"></div>')
        parts.append(f'    </div>')
    elif content_style == "formula_equation":
        _fe_parts = hints.get("formula_parts", [])
        _fe_op_chars = {"×", "÷", "+", "=", "→", "⇒", "≠", "≈", "/", "-"}
        _n_fe = min(len(_fe_parts), 8)
        parts.append(f'    <div class="fe-wrap">')
        parts.append(f'      <div class="fe-parts">')
        for _fe_i, _fe_p in enumerate(_fe_parts[:_n_fe]):
            _fe_cls = "fe-part fe-op" if str(_fe_p).strip() in _fe_op_chars else "fe-part"
            parts.append(f'        <span class="{_fe_cls}" id="{card_id}-fe-{_fe_i}">{_esc(str(_fe_p))}</span>')
        parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "roadmap_milestone":
        _rm_label = _esc(hints.get("milestone_label", ""))
        _rm_ctx = _esc(hints.get("milestone_context", ""))
        _rm_acc = p["accent"] if p else "#FFFFFF"
        if p and p.get("id") == "lean_craft":
            _rm_svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" width="36" height="36">'
                       f'<line x1="14" y1="4" x2="14" y2="44" stroke="{_rm_acc}" stroke-width="2.5" stroke-linecap="round"/>'
                       f'<path d="M14 8 L38 20 L14 32" fill="{_rm_acc}" opacity="0.85"/>'
                       f'</svg>')
        else:
            _rm_svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" width="36" height="36">'
                       f'<path d="M24 4 L44 24 L24 44 L4 24 Z" fill="none" stroke="{_rm_acc}" stroke-width="3" stroke-linejoin="round"/>'
                       f'<path d="M16 24 L22 30 L32 18" fill="none" stroke="{_rm_acc}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>'
                       f'</svg>')
        parts.append(f'    <div class="rm-wrap">')
        parts.append(f'      <div class="rm-icon" id="{card_id}-rm-icon">{_rm_svg}</div>')
        parts.append(f'      <div class="rm-label" id="{card_id}-rm-label">{_rm_label}</div>')
        parts.append(f'      <div class="rm-ctx" id="{card_id}-rm-ctx">{_rm_ctx}</div>')
        parts.append(f'    </div>')
    elif content_style == "pros_cons":
        _pc_pros = hints.get("pros", [])
        _pc_cons = hints.get("cons", [])
        parts.append(f'    <div class="pc-wrap">')
        parts.append(f'      <div class="pc-col">')
        parts.append(f'        <div class="pc-hdr pc-hdr-pro">&#x2713; Pour</div>')
        for _pc_i, _pc_it in enumerate(_pc_pros[:4]):
            parts.append(f'        <div class="pc-item" id="{card_id}-pc-pro-{_pc_i}">{_esc(str(_pc_it))}</div>')
        parts.append(f'      </div>')
        parts.append(f'      <div class="pc-div" id="{card_id}-pc-div"></div>')
        parts.append(f'      <div class="pc-col">')
        parts.append(f'        <div class="pc-hdr pc-hdr-con">&#x2717; Contre</div>')
        for _pc_i, _pc_it in enumerate(_pc_cons[:4]):
            parts.append(f'        <div class="pc-item" id="{card_id}-pc-con-{_pc_i}">{_esc(str(_pc_it))}</div>')
        parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "star_rating_review":
        _sr_stars_raw = hints.get("stars", 5)
        _sr_text = _esc(hints.get("review_text", ""))
        _sr_name = _esc(hints.get("reviewer_name", ""))
        try:
            _sr_n = max(0, min(5, int(_sr_stars_raw)))
        except (ValueError, TypeError):
            _sr_n = 5
        parts.append(f'    <div class="sr-wrap">')
        parts.append(f'      <div class="sr-stars">')
        for _sr_i in range(5):
            _sr_cls = "sr-star filled" if _sr_i < _sr_n else "sr-star empty"
            _sr_char = "&#9733;" if _sr_i < _sr_n else "&#9734;"
            parts.append(f'        <span class="{_sr_cls}" id="{card_id}-sr-s{_sr_i}">{_sr_char}</span>')
        parts.append(f'      </div>')
        parts.append(f'      <div class="sr-text" id="{card_id}-sr-text">{_sr_text}</div>')
        parts.append(f'      <div class="sr-name" id="{card_id}-sr-name">{_sr_name}</div>')
        parts.append(f'    </div>')
    elif content_style == "income_reveal":
        _ir_val = _esc(hints.get("income_value", ""))
        _ir_ctx = _esc(hints.get("income_context", ""))
        parts.append(f'    <div class="ir-wrap">')
        parts.append(f'      <div class="ir-value" id="{card_id}-ir-value">{_ir_val}</div>')
        parts.append(f'      <div class="ir-ctx" id="{card_id}-ir-ctx">{_ir_ctx}</div>')
        parts.append(f'    </div>')
    # ── Wave 4 HTML ───────────────────────────────────────────────────────────
    elif content_style == "question_answer_pair":
        _qap_q = _esc(hints.get("qa_question", ""))
        _qap_a = _esc(hints.get("qa_answer", ""))
        parts.append(f'    <div class="qap-wrap">')
        parts.append(f'      <div class="qap-q" id="{card_id}-qap-q">{_qap_q}</div>')
        parts.append(f'      <div class="qap-div" id="{card_id}-qap-div"></div>')
        parts.append(f'      <div class="qap-a" id="{card_id}-qap-a">{_qap_a}</div>')
        parts.append(f'    </div>')
    elif content_style == "chapter_marker":
        _cm_num = _esc(hints.get("chapter_num", ""))
        _cm_ttl = _esc(hints.get("chapter_title", ""))
        parts.append(f'    <div class="cm-wrap">')
        parts.append(f'      <div class="cm-num" id="{card_id}-cm-num">{_cm_num}</div>')
        parts.append(f'      <div class="cm-line" id="{card_id}-cm-line"></div>')
        parts.append(f'      <div class="cm-title" id="{card_id}-cm-title">{_cm_ttl}</div>')
        parts.append(f'    </div>')
    elif content_style == "secret_reveal":
        _sec_text = _esc(hints.get("secret_text", ""))
        parts.append(f'    <div class="sec-wrap">')
        _sec_lock_svg = (
            f'<svg width="28" height="28" viewBox="0 0 24 24" fill="none" '
            f'stroke="{p["accent"]}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            f'<rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>'
            f'<path d="M7 11V7a5 5 0 0 1 10 0v4"/>'
            f'</svg>'
        )
        parts.append(f'      <div class="sec-label" id="{card_id}-sec-label">{_sec_lock_svg}</div>')
        parts.append(f'      <div class="sec-text" id="{card_id}-sec-text">{_sec_text}</div>')
        parts.append(f'    </div>')
    elif content_style == "objection_response":
        _or_obj = _esc(hints.get("objection_text", ""))
        _or_resp = _esc(hints.get("response_text", ""))
        parts.append(f'    <div class="or-wrap">')
        parts.append(f'      <div class="or-obj-hdr" id="{card_id}-or-obj-hdr">&#x2715; Objection</div>')
        parts.append(f'      <div class="or-obj" id="{card_id}-or-obj">{_or_obj}</div>')
        parts.append(f'      <div class="or-div" id="{card_id}-or-div"></div>')
        parts.append(f'      <div class="or-resp-hdr" id="{card_id}-or-resp-hdr">&#x2713; R&#xe9;ponse</div>')
        parts.append(f'      <div class="or-resp" id="{card_id}-or-resp">{_or_resp}</div>')
        parts.append(f'    </div>')
    elif content_style == "data_bar_chart":
        _dbc_labels = hints.get("bar_labels", [])
        _dbc_values = hints.get("bar_values", [])
        _dbc_rows: list[tuple[str, float]] = []
        for _dbc_i in range(min(len(_dbc_labels), len(_dbc_values), 4)):
            try:
                _dbc_v = float(_dbc_values[_dbc_i])
            except (TypeError, ValueError):
                _dbc_v = 0.0
            _dbc_rows.append((str(_dbc_labels[_dbc_i]), _dbc_v))
        _dbc_max = max((v for _, v in _dbc_rows), default=1.0) or 1.0
        parts.append(f'    <div class="dbc-wrap">')
        for _dbc_i, (_dbc_lbl, _dbc_v) in enumerate(_dbc_rows):
            _dbc_pct = round(_dbc_v / _dbc_max * 100, 1)
            parts.append(f'      <div class="dbc-row" id="{card_id}-dbc-{_dbc_i}">')
            parts.append(f'        <div class="dbc-label">{_esc(_dbc_lbl)}</div>')
            parts.append(f'        <div class="dbc-track"><div class="dbc-fill" id="{card_id}-dbc-fill-{_dbc_i}" data-pct="{_dbc_pct}"></div></div>')
            parts.append(f'        <div class="dbc-val">{_dbc_v:g}</div>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "cause_effect":
        _ceff_cause = _esc(hints.get("cause_text", ""))
        _ceff_effect = _esc(hints.get("effect_text", ""))
        _ceff_acc = p["accent"]
        _ceff_arrow_svg = (
            f'<svg width="32" height="32" viewBox="0 0 32 32" class="ceff-arrow" id="{card_id}-ceff-arrow">'
            f'<path class="ceff-arrow-path" id="{card_id}-ceff-path" d="M 4 16 L 24 16" stroke-width="2.5" stroke-linecap="round"/>'
            f'<polygon class="ceff-arrowhead" id="{card_id}-ceff-head" points="20,10 28,16 20,22"/>'
            f'</svg>'
        )
        parts.append(f'    <div class="ceff-wrap">')
        parts.append(f'      <div class="ceff-box" id="{card_id}-ceff-cause">')
        parts.append(f'        <div class="ceff-lbl">Cause</div>')
        parts.append(f'        <div class="ceff-text">{_ceff_cause}</div>')
        parts.append(f'      </div>')
        parts.append(f'      {_ceff_arrow_svg}')
        parts.append(f'      <div class="ceff-box" id="{card_id}-ceff-effect">')
        parts.append(f'        <div class="ceff-lbl">Effet</div>')
        parts.append(f'        <div class="ceff-text">{_ceff_effect}</div>')
        parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "number_ranking":
        _nr_items = hints.get("rankings", [])
        parts.append(f'    <div class="nr-wrap">')
        for _nr_i, _nr_item in enumerate(_nr_items[:5]):
            _nr_cls = "nr-item nr-first" if _nr_i == 0 else "nr-item"
            parts.append(f'      <div class="{_nr_cls}" id="{card_id}-nr-{_nr_i}">')
            parts.append(f'        <div class="nr-pos">{_nr_i + 1}</div>')
            parts.append(f'        <div class="nr-label">{_esc(str(_nr_item))}</div>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    # ── Wave 5 HTML ───────────────────────────────────────────────────────────
    elif content_style == "hand_written_note":
        _hwn_text = _esc(hints.get("note_text", hints.get("title", "")))
        parts.append(f'    <div class="hwn-wrap">')
        parts.append(f'      <div class="hwn-text" id="{card_id}-hwn-text">{_hwn_text}</div>')
        parts.append(f'      <div class="hwn-underline" id="{card_id}-hwn-line"></div>')
        parts.append(f'    </div>')
    elif content_style == "speech_bubble_thought":
        _sbt_text = _esc(hints.get("thought_text", hints.get("title", "")))
        parts.append(f'    <div class="sbt-wrap">')
        parts.append(f'      <div class="sbt-bubbles">')
        for _sbt_i in range(3):
            parts.append(f'        <div class="sbt-dot-{_sbt_i}" id="{card_id}-sbt-dot-{_sbt_i}"></div>')
        parts.append(f'      </div>')
        parts.append(f'      <div class="sbt-text" id="{card_id}-sbt-text">{_sbt_text}</div>')
        parts.append(f'    </div>')
    elif content_style == "calendar_date_highlight":
        _cal_date = _esc(hints.get("date_value", ""))
        _cal_ctx  = _esc(hints.get("date_context", ""))
        parts.append(f'    <div class="cal-wrap">')
        parts.append(f'      <div class="cal-cell" id="{card_id}-cal-cell">')
        parts.append(f'        <div class="cal-date">{_cal_date}</div>')
        parts.append(f'      </div>')
        if _cal_ctx:
            parts.append(f'      <div class="cal-ctx" id="{card_id}-cal-ctx">{_cal_ctx}</div>')
        parts.append(f'    </div>')
    elif content_style == "percentage_split":
        _psp_labels = hints.get("split_labels", [])
        _psp_values = hints.get("split_values", [])
        _psp_n = min(len(_psp_labels), len(_psp_values), 5)
        _psp_total = sum(float(v) for v in _psp_values[:_psp_n]) or 1.0
        _psp_accent_colors = [p["accent"], p["text_secondary"], p["text"], p["accent"], p["text_secondary"]]
        parts.append(f'    <div class="psp-wrap">')
        parts.append(f'      <div class="psp-bar-track" id="{card_id}-psp-track">')
        for _psp_i in range(_psp_n):
            _psp_pct = float(_psp_values[_psp_i]) / _psp_total * 100
            _psp_col = _psp_accent_colors[_psp_i % len(_psp_accent_colors)]
            parts.append(f'        <div class="psp-segment" id="{card_id}-psp-seg-{_psp_i}" data-pct="{_psp_pct:.1f}" style="background:{_psp_col}"></div>')
        parts.append(f'      </div>')
        parts.append(f'      <div class="psp-labels">')
        for _psp_i in range(_psp_n):
            _psp_pct2 = float(_psp_values[_psp_i]) / _psp_total * 100
            _psp_col2 = _psp_accent_colors[_psp_i % len(_psp_accent_colors)]
            parts.append(f'        <div class="psp-lbl" id="{card_id}-psp-lbl-{_psp_i}">')
            parts.append(f'          <div class="psp-swatch" style="background:{_psp_col2}"></div>')
            parts.append(f'          {_esc(str(_psp_labels[_psp_i]))} — {_psp_pct2:.0f}%')
            parts.append(f'        </div>')
        parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "red_flag_list":
        _rfl_items = hints.get("flags", [])
        _rfl_acc = p["accent"]
        _rfl_svg = (f'<svg width="20" height="20" viewBox="0 0 24 24" fill="none" '
                    f'stroke="{_rfl_acc}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
                    f'<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/>'
                    f'<line x1="4" y1="22" x2="4" y2="15"/>'
                    f'</svg>')
        parts.append(f'    <div class="rfl-wrap">')
        for _rfl_i, _rfl_it in enumerate(_rfl_items[:5]):
            parts.append(f'      <div class="rfl-item" id="{card_id}-rfl-{_rfl_i}">')
            parts.append(f'        <div class="rfl-flag">{_rfl_svg}</div>')
            parts.append(f'        <div class="rfl-text">{_esc(str(_rfl_it))}</div>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "success_metric_badge":
        _smb_label = _esc(hints.get("badge_label", hints.get("title", "")))
        _smb_ctx   = _esc(hints.get("badge_context", ""))
        parts.append(f'    <div class="smb-wrap">')
        parts.append(f'      <div class="smb-badge" id="{card_id}-smb-badge">')
        parts.append(f'        <div class="smb-label">{_smb_label}</div>')
        if _smb_ctx:
            parts.append(f'        <div class="smb-ctx">{_smb_ctx}</div>')
        parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "client_avatar_persona":
        _cap_name   = _esc(hints.get("persona_name", hints.get("title", "")))
        _cap_traits = hints.get("persona_traits", [])
        _cap_init   = "".join(w[0].upper() for w in hints.get("persona_name", "?").split()[:2]) or "?"
        parts.append(f'    <div class="cap-wrap">')
        parts.append(f'      <div class="cap-avatar" id="{card_id}-cap-avatar">')
        parts.append(f'        <div class="cap-initials">{_esc(_cap_init)}</div>')
        parts.append(f'      </div>')
        parts.append(f'      <div class="cap-name" id="{card_id}-cap-name">{_cap_name}</div>')
        if _cap_traits:
            parts.append(f'      <div class="cap-traits">')
            for _cap_i, _cap_t in enumerate(_cap_traits[:4]):
                parts.append(f'        <div class="cap-trait" id="{card_id}-cap-trait-{_cap_i}">{_esc(str(_cap_t))}</div>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    # ── Wave 6 HTML ───────────────────────────────────────────────────────────
    elif content_style == "book_recommendation":
        _br_title  = _esc(hints.get("book_title", hints.get("title", "")))
        _br_author = _esc(hints.get("book_author", ""))
        parts.append(f'    <div class="br-wrap">')
        parts.append(f'      <div class="br-cover" id="{card_id}-br-cover"></div>')
        parts.append(f'      <div class="br-title" id="{card_id}-br-title">{_br_title}</div>')
        if _br_author:
            parts.append(f'      <div class="br-author" id="{card_id}-br-author">{_br_author}</div>')
        parts.append(f'    </div>')
    elif content_style == "tool_stack":
        _ts_tools = hints.get("tools", [])
        parts.append(f'    <div class="ts-wrap">')
        for _ts_i, _ts_t in enumerate(_ts_tools[:6]):
            parts.append(f'      <div class="ts-item" id="{card_id}-ts-{_ts_i}">{_esc(str(_ts_t))}</div>')
        parts.append(f'    </div>')
    elif content_style == "revenue_breakdown":
        _rb_sources = hints.get("revenue_sources", [])
        _rb_values  = hints.get("revenue_values", [])
        _rb_n = min(len(_rb_sources), len(_rb_values), 5)
        _rb_max = max((float(v) for v in _rb_values[:_rb_n]), default=1.0) or 1.0
        parts.append(f'    <div class="rb-wrap">')
        for _rb_i in range(_rb_n):
            _rb_pct = float(_rb_values[_rb_i]) / _rb_max * 100
            _rb_val_str = _esc(str(_rb_values[_rb_i]))
            parts.append(f'      <div class="rb-row" id="{card_id}-rb-{_rb_i}">')
            parts.append(f'        <div class="rb-meta">')
            parts.append(f'          <div class="rb-label">{_esc(str(_rb_sources[_rb_i]))}</div>')
            parts.append(f'          <div class="rb-value">{_rb_val_str}</div>')
            parts.append(f'        </div>')
            parts.append(f'        <div class="rb-track">')
            parts.append(f'          <div class="rb-fill" id="{card_id}-rb-fill-{_rb_i}" data-pct="{_rb_pct:.1f}"></div>')
            parts.append(f'        </div>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "age_milestone":
        _am_num = _esc(hints.get("age_value", hints.get("number", "")))
        _am_ctx = _esc(hints.get("age_context", hints.get("detail", "")))
        parts.append(f'    <div class="am-wrap">')
        parts.append(f'      <div class="am-number" id="{card_id}-am-number">{_am_num}</div>')
        if _am_ctx:
            parts.append(f'      <div class="am-ctx" id="{card_id}-am-ctx">{_am_ctx}</div>')
        parts.append(f'    </div>')
    elif content_style == "contrarian_take":
        _ct_text = _esc(hints.get("take_text", hints.get("title", "")))
        parts.append(f'    <div class="ct-wrap">')
        parts.append(f'      <div class="ct-text" id="{card_id}-ct-text">{_ct_text}</div>')
        parts.append(f'      <div class="ct-rule" id="{card_id}-ct-rule"></div>')
        parts.append(f'    </div>')
    elif content_style == "action_step_cta":
        _asc_text = _esc(hints.get("cta_text", hints.get("title", "")))
        parts.append(f'    <div class="asc-wrap">')
        parts.append(f'      <div class="asc-text" id="{card_id}-asc-text">{_asc_text}</div>')
        parts.append(f'      <div class="asc-rule" id="{card_id}-asc-rule"></div>')
        parts.append(f'    </div>')
    elif content_style == "story_chapter_transition":
        _sct_label = _esc(hints.get("transition_label", hints.get("title", "")))
        parts.append(f'    <div class="sct-wrap">')
        parts.append(f'      <div class="sct-rule" id="{card_id}-sct-rule-a"></div>')
        parts.append(f'      <div class="sct-text" id="{card_id}-sct-text">{_sct_label}</div>')
        parts.append(f'      <div class="sct-rule" id="{card_id}-sct-rule-b"></div>')
        parts.append(f'    </div>')
    else:
        # key_phrase, quote and any unknown style
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

    # Emphasis = accent colour only; zero background or box on any word, ever.
    style_extra = (
        f'.card[data-card-id="{card_id}"] .cap-emphasis {{\n'
        f'  color: {p["accent"]};\n'
        f'}}\n'
    )

    return (
        f'<div class="card caption-card" data-card-id="{card_id}">\n'
        f'<style>\n'
        f'.card[data-card-id="{card_id}"] .cap-line {{\n'
        f'  display: flex; flex-wrap: wrap; justify-content: center; align-items: center;\n'
        f'  gap: 0.3em; padding: 16px 24px;\n'
        f'  font-family: {p["font"]};\n'
        f'  font-size: 48px; font-weight: 700; color: #FFFFFF;\n'
        f'  text-shadow: 0 2px 8px rgba(0,0,0,0.8), 0 0 2px rgba(0,0,0,0.9);\n'
        f'  text-align: center; line-height: 1.4;\n'
        f'}}\n'
        f'{style_extra}'
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
            if len(card.get("words", [])) > 0:
                lines.append(
                    f'  tl.set(\'{word_sel}\', {{ opacity: 1, y: 0 }}, {start:.4f});'
                )
            # Caption: plain text, accent colour on emphasis words only — no boxes.
        else:
            panel_sel = f'.card[data-card-id="{card_id}"] .card-panel'
            ent_dur = 0.550 if is_cinema else 0.320
            lines.append(
                f'  tl.fromTo(\'{sel}\', '
                f'{{ opacity: 0 }}, '
                f'{{ opacity: 1, duration: {ent_dur:.3f}, ease: _eIn }}, '
                f'{start:.4f});'
            )
            # Per-pack panel entry (the card-panel slides/scales into view).
            # timeline and news_ticker are full-screen overlays built without a
            # .card-panel div, so skip the panel tween for those types.
            # content_style isn't assigned until ~line 2762, so look it up here.
            _early_style = card.get("contentHints", {}).get("style", "") if "_broll_type" not in card else "__broll__"
            if _early_style not in ("timeline", "news_ticker"):
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

            content_style = (
                "__broll__"
                if "_broll_type" in card
                else card.get("contentHints", {}).get("style", "key_phrase")
            )
            title_sel = f'.card[data-card-id="{card_id}"] #{card_id}-title'
            kicker_sel = f'.card[data-card-id="{card_id}"] #{card_id}-kicker'
            line_sel = f'.card[data-card-id="{card_id}"] #{card_id}-line'
            t_in = start + 0.15

            is_paper = p["id"] == "lean_paper"

            if content_style == "__broll__":
                try:
                    from app.engine import broll_registry as _br
                    _btype = _br.REGISTRY.get(card.get("_broll_type", ""))
                    if _btype is not None:
                        _broll_lines = _btype.render_gsap(
                            card.get("_broll_params", {}),
                            p,
                            card_id,
                            start,
                            end,
                        )
                        lines.extend(_broll_lines)
                except Exception as _broll_gsap_exc:
                    print(
                        f"[BROLL] render_gsap error {card.get('_broll_type','?')}: {_broll_gsap_exc}",
                        flush=True,
                    )
            elif content_style == "stat" and card.get("contentHints", {}).get("number"):
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
                    # Distribute available card time evenly from t_in.
                    # tl.set pins every slide's initial state explicitly so GSAP
                    # doesn't rely on CSS alone — fixes the "first slide frozen"
                    # symptom where fromTo failed to re-apply from-state on slides 1+.
                    avail = max(0.1, end - t_in)
                    each_dur = round(avail / n_slides, 3)
                    for si in range(n_slides):
                        sl_sel = f'.card[data-card-id="{card_id}"] #{card_id}-slide-{si}'
                        sl_in  = round(t_in + si * each_dur, 4)
                        if sl_in >= end:
                            break
                        sl_out = round(min(sl_in + each_dur - 0.22, end - 0.06), 4)
                        if is_paper:
                            lines.append(f'  tl.set(\'{sl_sel}\', {{ opacity: 0 }}, {t_in:.4f});')
                            lines.append(
                                f'  tl.to(\'{sl_sel}\', '
                                f'{{ opacity: 1, duration: 0.25, ease: _eIn }}, '
                                f'{sl_in:.4f});')
                            lines.append(
                                f'  tl.to(\'{sl_sel}\', '
                                f'{{ opacity: 0, duration: 0.20, ease: _eOut }}, '
                                f'{sl_out:.4f});')
                        elif is_vibe:
                            lines.append(f'  tl.set(\'{sl_sel}\', {{ opacity: 0, y: 10 }}, {t_in:.4f});')
                            lines.append(
                                f'  tl.to(\'{sl_sel}\', '
                                f'{{ opacity: 1, y: 0, duration: 0.25, ease: _eIn }}, '
                                f'{sl_in:.4f});')
                            lines.append(
                                f'  tl.to(\'{sl_sel}\', '
                                f'{{ opacity: 0, y: -10, duration: 0.20, ease: _eOut }}, '
                                f'{sl_out:.4f});')
                        else:
                            lines.append(f'  tl.set(\'{sl_sel}\', {{ opacity: 0, x: 12 }}, {t_in:.4f});')
                            lines.append(
                                f'  tl.to(\'{sl_sel}\', '
                                f'{{ opacity: 1, x: 0, duration: 0.25, ease: _eIn }}, '
                                f'{sl_in:.4f});')
                            lines.append(
                                f'  tl.to(\'{sl_sel}\', '
                                f'{{ opacity: 0, x: -12, duration: 0.20, ease: _eOut }}, '
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
                # Native flowchart: root node cascades to branch nodes
                root_sel = f'.card[data-card-id="{card_id}"] #{card_id}-fc-root'
                lines.append(
                    f'  tl.fromTo(\'{root_sel}\', '
                    f'{{ opacity: 0, y: -8 }}, '
                    f'{{ opacity: 1, y: 0, duration: 0.30, ease: _eIn }}, '
                    f'{t_in:.4f});')
                branches = card.get("contentHints", {}).get("branches", [])
                n_br = min(len(branches), 4)
                for bi in range(n_br):
                    arrow_sel = f'.card[data-card-id="{card_id}"] #{card_id}-fc-arrow-{bi}'
                    node_sel  = f'.card[data-card-id="{card_id}"] #{card_id}-fc-{bi}'
                    br_t = round(t_in + 0.25 + bi * 0.18, 4)
                    lines.append(
                        f'  tl.to(\'{arrow_sel}\', '
                        f'{{ height: 18, opacity: 0.6, duration: 0.12, ease: "none" }}, '
                        f'{br_t:.4f});')
                    lines.append(
                        f'  tl.fromTo(\'{node_sel}\', '
                        f'{{ opacity: 0, y: 6 }}, '
                        f'{{ opacity: 1, y: 0, duration: 0.22, ease: _eIn }}, '
                        f'{round(br_t + 0.10, 4):.4f});')
            elif content_style == "data_chart":
                chart_items = card.get("contentHints", {}).get("items",
                              card.get("contentHints", {}).get("branches", []))
                n_rows = min(len(chart_items), 5)
                for ri in range(n_rows):
                    row_sel  = f'.card[data-card-id="{card_id}"] #{card_id}-dc-{ri}'
                    fill_sel = f'.card[data-card-id="{card_id}"] #{card_id}-dc-fill-{ri}'
                    row_t = round(t_in + ri * 0.14, 4)
                    lines.append(
                        f'  tl.fromTo(\'{row_sel}\', '
                        f'{{ opacity: 0, x: -8 }}, '
                        f'{{ opacity: 1, x: 0, duration: 0.22, ease: _eIn }}, '
                        f'{row_t:.4f});')
                    lines.append(
                        f'  tl.to(\'{fill_sel}\', '
                        f'{{ width: "100%", duration: 0.50, ease: "power2.out" }}, '
                        f'{round(row_t + 0.15, 4):.4f});')
            elif content_style in ("instagram-follow", "tiktok-follow", "yt-lower-third"):
                so_sel = f'.card[data-card-id="{card_id}"] #{card_id}-so'
                lines.append(
                    f'  tl.fromTo(\'{so_sel}\', '
                    f'{{ opacity: 0, scale: 0.85, y: 12 }}, '
                    f'{{ opacity: 1, scale: 1, y: 0, duration: 0.35, ease: "back.out(1.4)" }}, '
                    f'{t_in:.4f});')
            elif content_style == "news_ticker":
                track_sel = f'.card[data-card-id="{card_id}"] #{card_id}-track'
                scroll_dur = round(max(6.0, dur * 0.85), 3)
                lines.append(
                    f'  gsap.to(\'{track_sel}\', '
                    f'{{ x: "-50%", duration: {scroll_dur:.3f}, ease: "none",'
                    f' repeat: -1, delay: {t_in:.4f} }});')
            elif content_style == "callout":
                stripe_sel = f'.card[data-card-id="{card_id}"] #{card_id}-co-stripe'
                if is_cinema:
                    lines.append(
                        f'  tl.fromTo(\'{stripe_sel}\', '
                        f'{{ scaleY: 0 }}, '
                        f'{{ scaleY: 1, duration: 0.50, ease: "power2.inOut" }}, '
                        f'{t_in:.4f});')
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ opacity: 0 }}, '
                        f'{{ opacity: 1, duration: 0.50, ease: _eIn }}, '
                        f'{t_in + 0.20:.4f});')
                elif is_ledger:
                    lines.append(
                        f'  tl.fromTo(\'{stripe_sel}\', '
                        f'{{ scaleY: 0 }}, '
                        f'{{ scaleY: 1, duration: 0.25, ease: "none" }}, '
                        f'{t_in:.4f});')
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ clipPath: "inset(0 100% 0 0)" }}, '
                        f'{{ clipPath: "inset(0 0% 0 0)", duration: 0.40, ease: _eIn }}, '
                        f'{t_in + 0.10:.4f});')
                elif is_vibe:
                    lines.append(
                        f'  tl.fromTo(\'{stripe_sel}\', '
                        f'{{ scaleY: 0 }}, '
                        f'{{ scaleY: 1, duration: 0.30, ease: "back.out(1.4)" }}, '
                        f'{t_in:.4f});')
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ opacity: 0, scale: 0.9 }}, '
                        f'{{ opacity: 1, scale: 1, duration: 0.35, ease: _eIn }}, '
                        f'{t_in + 0.10:.4f});')
                else:  # lean_glass, lean_paper, lean_craft
                    lines.append(
                        f'  tl.fromTo(\'{stripe_sel}\', '
                        f'{{ scaleY: 0 }}, '
                        f'{{ scaleY: 1, duration: 0.30, ease: "power2.inOut" }}, '
                        f'{t_in:.4f});')
                    lines.append(
                        f'  tl.fromTo(\'{title_sel}\', '
                        f'{{ opacity: 0, x: 12 }}, '
                        f'{{ opacity: 1, x: 0, duration: 0.35, ease: _eIn }}, '
                        f'{t_in + 0.10:.4f});')
            elif content_style == "rating":
                _w1_hints = card.get("contentHints", {})
                try:
                    _w1_rv = float(str(_w1_hints.get("rating_value") or "7").replace(",", "."))
                    _w1_rm = max(0.001, float(str(_w1_hints.get("rating_max") or "10").replace(",", ".")))
                    _w1_rt_pct = round(min(100.0, max(0.0, _w1_rv / _w1_rm * 100)), 1)
                except (ValueError, ZeroDivisionError, TypeError):
                    _w1_rt_pct = 70.0
                _w1_fill_sel = f'.card[data-card-id="{card_id}"] #{card_id}-rt-fill'
                _w1_val_sel = f'.card[data-card-id="{card_id}"] #{card_id}-rt-val'
                _w1_fill_dur = 2.0 if is_cinema else 0.40 if is_ledger else 0.80
                _w1_fill_ease = '"none"' if is_ledger else '"power1.out"' if is_cinema else '"power2.out"'
                lines.append(f'  tl.fromTo(\'{_w1_val_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in:.4f});')
                lines.append(f'  tl.fromTo(\'{_w1_fill_sel}\', {{ width: "0%" }}, {{ width: "{_w1_rt_pct:.1f}%", duration: {_w1_fill_dur:.3f}, ease: {_w1_fill_ease} }}, {t_in + 0.20:.4f});')
                if is_vibe:
                    _ov = min(100.0, _w1_rt_pct * 1.06)
                    lines.append(f'  tl.to(\'{_w1_fill_sel}\', {{ width: "{_ov:.1f}%", duration: 0.10, ease: "power2.in" }}, {t_in + 0.20 + _w1_fill_dur:.4f});')
                    lines.append(f'  tl.to(\'{_w1_fill_sel}\', {{ width: "{_w1_rt_pct:.1f}%", duration: 0.18, ease: "power2.out" }}, {t_in + 0.30 + _w1_fill_dur:.4f});')
                elif not is_ledger and not is_paper:
                    lines.append(f'  tl.to(\'{_w1_fill_sel}\', {{ boxShadow: "4px 0 14px {_esc_js(p["accent"])}", duration: 0.20 }}, {t_in + 0.20 + _w1_fill_dur:.4f});')
            elif content_style == "map_location":
                _w1_pin_sel = f'.card[data-card-id="{card_id}"] #{card_id}-ml-pin'
                _w1_name_sel = f'.card[data-card-id="{card_id}"] #{card_id}-ml-name'
                _w1_ctx_sel = f'.card[data-card-id="{card_id}"] #{card_id}-ml-ctx'
                _w1_pulse_sel = f'.card[data-card-id="{card_id}"] #{card_id}-ml-pulse'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w1_pin_sel}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w1_name_sel}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w1_ctx_sel}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w1_pin_sel}\', {{ opacity: 1, y: -60 }}, {{ y: 8, duration: 0.25, ease: "power2.in" }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w1_pin_sel}\', {{ y: 0, duration: 0.20, ease: "bounce.out" }}, {t_in + 0.25:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_name_sel}\', {{ opacity: 0, scale: 0.8 }}, {{ opacity: 1, scale: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.35:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_ctx_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.50:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w1_pin_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.60, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_name_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.60, ease: _eIn }}, {t_in + 0.30:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_ctx_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.40, ease: _eIn }}, {t_in + 0.60:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w1_pin_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_name_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.20:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_ctx_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.20, ease: _eIn }}, {t_in + 0.35:.4f});')
                else:  # glass + craft
                    lines.append(f'  tl.fromTo(\'{_w1_pin_sel}\', {{ opacity: 0, y: -20, scale: 0.8 }}, {{ opacity: 1, y: 0, scale: 1, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_name_sel}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.30, ease: _eIn }}, {t_in + 0.25:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_ctx_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.40:.4f});')
                    if not is_craft:  # glass only: radar pulse
                        for _pi in range(3):
                            lines.append(f'  tl.fromTo(\'{_w1_pulse_sel}\', {{ scale: 1, opacity: 0.8 }}, {{ scale: 3.5, opacity: 0, duration: 1.0, ease: "power1.out" }}, {t_in + 0.10 + _pi * 1.2:.4f});')
            elif content_style == "progress_bar":
                _w1_pb_h = card.get("contentHints", {})
                try:
                    _w1_pb_pct = round(min(100.0, max(0.0, float(str(_w1_pb_h.get("progress_percent", 70))))), 1)
                except (ValueError, TypeError):
                    _w1_pb_pct = 70.0
                _w1_fill_pb = f'.card[data-card-id="{card_id}"] #{card_id}-pb-fill'
                _w1_pct_pb = f'.card[data-card-id="{card_id}"] #{card_id}-pb-pct'
                _w1_pb_dur = 2.0 if is_cinema else 0.40 if is_ledger else 0.80
                _w1_pb_ease = '"none"' if is_ledger else '"power1.out"' if is_cinema else '"power2.out"'
                lines.append(f'  tl.fromTo(\'{_w1_fill_pb}\', {{ width: "0%" }}, {{ width: "{_w1_pb_pct:.1f}%", duration: {_w1_pb_dur:.3f}, ease: {_w1_pb_ease} }}, {t_in:.4f});')
                lines.append(
                    f'  (function(){{ var o={{v:0}};'
                    f' tl.to(o, {{v:{_w1_pb_pct}, duration:{_w1_pb_dur:.3f}, ease:{_w1_pb_ease},'
                    f' onUpdate:function(){{ var el=document.querySelector(\'{_w1_pct_pb}\');'
                    f' if(el) el.textContent=Math.round(o.v)+\'%\'; }}}}, {t_in:.4f}); }})();'
                )
                if is_vibe:
                    lines.append(f'  tl.to(\'{_w1_fill_pb}\', {{ scaleX: 1.02, duration: 0.10, ease: "power2.in" }}, {t_in + _w1_pb_dur:.4f});')
                    lines.append(f'  tl.to(\'{_w1_fill_pb}\', {{ scaleX: 1.0, duration: 0.15, ease: "power2.out" }}, {t_in + _w1_pb_dur + 0.10:.4f});')
                elif not is_ledger and not is_paper:
                    lines.append(f'  tl.to(\'{_w1_fill_pb}\', {{ boxShadow: "4px 0 14px {_esc_js(p["accent"])}", duration: 0.20 }}, {t_in + _w1_pb_dur:.4f});')
            elif content_style == "before_after_image":
                _w1_bef_sel = f'.card[data-card-id="{card_id}"] #{card_id}-ba-before'
                _w1_aft_sel = f'.card[data-card-id="{card_id}"] #{card_id}-ba-after'
                _w1_div_sel = f'.card[data-card-id="{card_id}"] #{card_id}-ba-div'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w1_bef_sel}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w1_div_sel}\', {{ scaleY: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w1_aft_sel}\', {{ opacity: 1 }}, {t_in + 0.05:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w1_bef_sel}\', {{ opacity: 0, x: -30 }}, {{ opacity: 1, x: 0, duration: 0.35, ease: "back.out(1.4)" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_div_sel}\', {{ scaleY: 0 }}, {{ scaleY: 1, transformOrigin: "top center", duration: 0.25, ease: _eIn }}, {t_in + 0.10:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_aft_sel}\', {{ opacity: 0, x: 30 }}, {{ opacity: 1, x: 0, duration: 0.35, ease: "back.out(1.4)" }}, {t_in + 0.15:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w1_bef_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.60, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_div_sel}\', {{ scaleY: 0 }}, {{ scaleY: 1, transformOrigin: "top center", duration: 0.50, ease: _eIn }}, {t_in + 0.20:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_aft_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.60, ease: _eIn }}, {t_in + 0.40:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w1_bef_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    _w1_path_sel = f'.card[data-card-id="{card_id}"] #{card_id}-ba-path'
                    lines.append(f'  tl.to(\'{_w1_path_sel}\', {{ attr: {{ "stroke-dashoffset": 0 }}, duration: 0.50, ease: "power2.inOut" }}, {t_in + 0.10:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_aft_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.35, ease: _eIn }}, {t_in + 0.35:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w1_bef_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_div_sel}\', {{ scaleY: 0 }}, {{ scaleY: 1, transformOrigin: "top center", duration: 0.25, ease: _eIn }}, {t_in + 0.10:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_aft_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.20:.4f});')
                else:  # glass
                    lines.append(f'  tl.fromTo(\'{_w1_bef_sel}\', {{ opacity: 0, x: -30 }}, {{ opacity: 1, x: 0, duration: 0.40, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_div_sel}\', {{ scaleY: 0 }}, {{ scaleY: 1, transformOrigin: "top center", duration: 0.35, ease: _eIn }}, {t_in + 0.15:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_aft_sel}\', {{ opacity: 0, x: 30 }}, {{ opacity: 1, x: 0, duration: 0.40, ease: _eIn }}, {t_in + 0.20:.4f});')
            elif content_style == "countdown":
                _w1_cd_h = card.get("contentHints", {})
                try:
                    _w1_cd_from = max(1, int(float(str(_w1_cd_h.get("countdown_from", 5)))))
                except (ValueError, TypeError):
                    _w1_cd_from = 5
                _w1_num_sel = f'.card[data-card-id="{card_id}"] #{card_id}-cd-num'
                _w1_cd_dur = round(min(max(float(_w1_cd_from) * 0.55, 1.2), max(dur - 0.8, 1.0)), 3)
                if is_cinema:
                    _w1_cd_dur = round(min(_w1_cd_dur * 1.4, dur - 0.5), 3)
                _w1_cd_ease = '"none"' if is_ledger else '"power1.in"'
                lines.append(
                    f'  (function(){{ var o={{v:{_w1_cd_from}}};'
                    f' tl.to(o, {{v:0, duration:{_w1_cd_dur:.3f}, ease:{_w1_cd_ease},'
                    f' onUpdate:function(){{ var el=document.querySelector(\'{_w1_num_sel}\');'
                    f' if(el) el.textContent=Math.ceil(o.v); }}}}, {t_in:.4f}); }})();'
                )
                if is_vibe:
                    lines.append(f'  tl.to(\'{_w1_num_sel}\', {{ scale: 1.3, duration: 0.15, ease: "back.out(2)" }}, {t_in + _w1_cd_dur:.4f});')
                    lines.append(f'  tl.to(\'{_w1_num_sel}\', {{ scale: 1, duration: 0.15, ease: "power2.out" }}, {t_in + _w1_cd_dur + 0.15:.4f});')
                elif is_craft:
                    lines.append(f'  tl.to(\'{_w1_num_sel}\', {{ scale: 1.2, duration: 0.08, ease: "power3.in" }}, {t_in + _w1_cd_dur:.4f});')
                    lines.append(f'  tl.to(\'{_w1_num_sel}\', {{ scale: 1, duration: 0.14, ease: "power2.out" }}, {t_in + _w1_cd_dur + 0.08:.4f});')
            elif content_style == "poll_question":
                _w1_pq_h = card.get("contentHints", {})
                _w1_pq_opts = _w1_pq_h.get("poll_options", [])
                _w1_n_opts = min(len(_w1_pq_opts), 4)
                _w1_pq_q_sel = f'.card[data-card-id="{card_id}"] #{card_id}-pq-q'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w1_pq_q_sel}\', {{ opacity: 1 }}, {t_in:.4f});')
                    for _oi in range(_w1_n_opts):
                        lines.append(f'  tl.set(\'.card[data-card-id="{card_id}"] #{card_id}-pq-opt-{_oi}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w1_pq_q_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.60, ease: _eIn }}, {t_in:.4f});')
                    for _oi in range(_w1_n_opts):
                        lines.append(f'  tl.fromTo(\'.card[data-card-id="{card_id}"] #{card_id}-pq-opt-{_oi}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.50, ease: _eIn }}, {t_in + 0.40 + _oi * 0.35:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w1_pq_q_sel}\', {{ opacity: 0, scale: 0.9 }}, {{ opacity: 1, scale: 1, duration: 0.30, ease: _eIn }}, {t_in:.4f});')
                    for _oi in range(_w1_n_opts):
                        lines.append(f'  tl.fromTo(\'.card[data-card-id="{card_id}"] #{card_id}-pq-opt-{_oi}\', {{ opacity: 0, scale: 0.8, y: 10 }}, {{ opacity: 1, scale: 1, y: 0, duration: 0.25, ease: _eIn }}, {t_in + 0.20 + _oi * 0.12:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w1_pq_q_sel}\', {{ opacity: 0, rotation: 1 }}, {{ opacity: 1, rotation: 0, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    for _oi in range(_w1_n_opts):
                        lines.append(f'  tl.fromTo(\'.card[data-card-id="{card_id}"] #{card_id}-pq-opt-{_oi}\', {{ opacity: 0, rotation: 1 }}, {{ opacity: 1, rotation: 0, duration: 0.30, ease: _eIn }}, {t_in + 0.25 + _oi * 0.14:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w1_pq_q_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in:.4f});')
                    for _oi in range(_w1_n_opts):
                        lines.append(f'  tl.fromTo(\'.card[data-card-id="{card_id}"] #{card_id}-pq-opt-{_oi}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.20, ease: _eIn }}, {t_in + 0.15 + _oi * 0.10:.4f});')
                else:  # glass
                    lines.append(f'  tl.fromTo(\'{_w1_pq_q_sel}\', {{ opacity: 0, y: -10 }}, {{ opacity: 1, y: 0, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    for _oi in range(_w1_n_opts):
                        _w1_opt_sel = f'.card[data-card-id="{card_id}"] #{card_id}-pq-opt-{_oi}'
                        lines.append(f'  tl.fromTo(\'{_w1_opt_sel}\', {{ opacity: 0, x: -16 }}, {{ opacity: 1, x: 0, duration: 0.28, ease: _eIn }}, {t_in + 0.20 + _oi * 0.12:.4f});')
                        lines.append(f'  tl.to(\'{_w1_opt_sel}\', {{ boxShadow: "0 0 12px {_esc_js(p["accent"])}30", duration: 0.20 }}, {t_in + 0.35 + _oi * 0.12:.4f});')
            elif content_style == "myth_vs_fact":
                _w1_myth_sel = f'.card[data-card-id="{card_id}"] #{card_id}-mvf-myth'
                _w1_strike_sel = f'.card[data-card-id="{card_id}"] #{card_id}-mvf-strike'
                _w1_fw_sel = f'.card[data-card-id="{card_id}"] #{card_id}-mvf-fact-wrap'
                _w1_t_sk = t_in + 0.40
                _w1_t_fact = t_in + 0.90
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w1_myth_sel}\', {{ opacity: 0.4 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w1_strike_sel}\', {{ width: "100%" }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w1_fw_sel}\', {{ opacity: 1 }}, {t_in + 0.10:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w1_myth_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w1_myth_sel}\', {{ scale: 0.8, opacity: 0.3, duration: 0.25, ease: "power2.in" }}, {_w1_t_sk:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_fw_sel}\', {{ opacity: 0, scale: 0.7 }}, {{ opacity: 1, scale: 1.05, duration: 0.30, ease: _eIn }}, {_w1_t_fact:.4f});')
                    lines.append(f'  tl.to(\'{_w1_fw_sel}\', {{ scale: 1, duration: 0.18, ease: _eOut }}, {_w1_t_fact + 0.30:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w1_myth_sel}\', {{ opacity: 0 }}, {{ opacity: 0.6, duration: 0.60, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w1_myth_sel}\', {{ opacity: 0.25, duration: 0.40, ease: _eIn }}, {t_in + 0.80:.4f});')
                    lines.append(f'  tl.to(\'{_w1_strike_sel}\', {{ width: "100%", duration: 0.45, ease: "power1.inOut" }}, {t_in + 0.90:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_fw_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.60, ease: _eIn }}, {t_in + 1.20:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w1_myth_sel}\', {{ opacity: 0 }}, {{ opacity: 0.5, duration: 0.25, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_fw_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.30:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w1_myth_sel}\', {{ opacity: 0, rotation: 1 }}, {{ opacity: 1, rotation: 0, duration: 0.30, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w1_myth_sel}\', {{ opacity: 0.4, duration: 0.20 }}, {_w1_t_sk:.4f});')
                    lines.append(f'  tl.to(\'{_w1_strike_sel}\', {{ width: "100%", duration: 0.45, ease: "elastic.out(1, 0.4)" }}, {_w1_t_sk:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_fw_sel}\', {{ opacity: 0, rotation: -1 }}, {{ opacity: 1, rotation: 0, duration: 0.35, ease: _eIn }}, {_w1_t_fact:.4f});')
                else:  # glass
                    lines.append(f'  tl.fromTo(\'{_w1_myth_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w1_myth_sel}\', {{ opacity: 0.4, duration: 0.15 }}, {_w1_t_sk:.4f});')
                    lines.append(f'  tl.to(\'{_w1_strike_sel}\', {{ width: "100%", duration: 0.35, ease: "power2.inOut" }}, {_w1_t_sk:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w1_fw_sel}\', {{ opacity: 0, y: 10 }}, {{ opacity: 1, y: 0, duration: 0.35, ease: _eIn }}, {_w1_t_fact:.4f});')
            elif content_style == "step_number":
                _w2_sn_sel = f'.card[data-card-id="{card_id}"] #{card_id}-sn-num'
                _w2_sl_sel = f'.card[data-card-id="{card_id}"] #{card_id}-sn-label'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w2_sn_sel}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w2_sl_sel}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w2_sn_sel}\', {{ opacity: 0, scale: 0.5 }}, {{ opacity: 1, scale: 1.1, duration: 0.30, ease: "back.out(2)" }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w2_sn_sel}\', {{ scale: 1, duration: 0.15, ease: "power2.out" }}, {t_in + 0.30:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_sl_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.35:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w2_sn_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.70, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_sl_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.50, ease: _eIn }}, {t_in + 0.40:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w2_sn_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_sl_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.25:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w2_sn_sel}\', {{ opacity: 0, scale: 1.4 }}, {{ opacity: 1, scale: 1, duration: 0.35, ease: "power3.out" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_sl_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.25:.4f});')
                else:  # glass
                    lines.append(f'  tl.fromTo(\'{_w2_sn_sel}\', {{ opacity: 0, scale: 0.7 }}, {{ opacity: 1, scale: 1, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    if p["title_glow"]:
                        lines.append(f'  tl.to(\'{_w2_sn_sel}\', {{ textShadow: "{_esc_js(p["title_glow"])}", duration: 0.20 }}, {t_in + 0.35:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_sl_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.30:.4f});')
            elif content_style == "quote_carousel":
                _w2_qc_h = card.get("contentHints", {})
                _w2_qc_quotes = _w2_qc_h.get("quotes", [])
                _w2_qc_n = min(len(_w2_qc_quotes), 5)
                if _w2_qc_n > 0:
                    _w2_slot = max(1.5, (dur - 0.5) / _w2_qc_n)
                    for _qi in range(_w2_qc_n):
                        _w2_qc_sel = f'.card[data-card-id="{card_id}"] #{card_id}-qc-{_qi}'
                        _w2_t0 = t_in + _qi * _w2_slot
                        _w2_t1 = _w2_t0 + _w2_slot - 0.30
                        if is_ledger:
                            lines.append(f'  tl.set(\'{_w2_qc_sel}\', {{ opacity: 1 }}, {_w2_t0:.4f});')
                            if _qi < _w2_qc_n - 1:
                                lines.append(f'  tl.set(\'{_w2_qc_sel}\', {{ opacity: 0 }}, {_w2_t1:.4f});')
                        elif is_cinema:
                            lines.append(f'  tl.fromTo(\'{_w2_qc_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.60, ease: _eIn }}, {_w2_t0:.4f});')
                            if _qi < _w2_qc_n - 1:
                                lines.append(f'  tl.to(\'{_w2_qc_sel}\', {{ opacity: 0, duration: 0.50, ease: _eOut }}, {_w2_t1:.4f});')
                        elif is_vibe:
                            lines.append(f'  tl.fromTo(\'{_w2_qc_sel}\', {{ opacity: 0, scale: 0.9 }}, {{ opacity: 1, scale: 1, duration: 0.25, ease: _eIn }}, {_w2_t0:.4f});')
                            if _qi < _w2_qc_n - 1:
                                lines.append(f'  tl.to(\'{_w2_qc_sel}\', {{ opacity: 0, scale: 1.1, duration: 0.20, ease: _eOut }}, {_w2_t1:.4f});')
                        elif is_paper or is_craft:
                            _w2_fi = 0.35 if is_craft else 0.25
                            lines.append(f'  tl.fromTo(\'{_w2_qc_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: {_w2_fi:.2f}, ease: _eIn }}, {_w2_t0:.4f});')
                            if _qi < _w2_qc_n - 1:
                                lines.append(f'  tl.to(\'{_w2_qc_sel}\', {{ opacity: 0, duration: 0.25, ease: _eOut }}, {_w2_t1:.4f});')
                        else:  # glass: blur transition
                            lines.append(f'  tl.fromTo(\'{_w2_qc_sel}\', {{ opacity: 0, filter: "blur(4px)" }}, {{ opacity: 1, filter: "blur(0px)", duration: 0.30, ease: _eIn }}, {_w2_t0:.4f});')
                            if _qi < _w2_qc_n - 1:
                                lines.append(f'  tl.to(\'{_w2_qc_sel}\', {{ opacity: 0, filter: "blur(4px)", duration: 0.25, ease: _eOut }}, {_w2_t1:.4f});')
            elif content_style == "emoji_reaction":
                _w2_el_sel = f'.card[data-card-id="{card_id}"] #{card_id}-er-label'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w2_el_sel}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w2_el_sel}\', {{ opacity: 0, scale: 0.8 }}, {{ opacity: 1, scale: 1.08, duration: 0.22, ease: "back.out(1.8)" }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w2_el_sel}\', {{ scale: 1, duration: 0.15, ease: "power2.out" }}, {t_in + 0.22:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w2_el_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.70, ease: _eIn }}, {t_in:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w2_el_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w2_el_sel}\', {{ opacity: 0, rotation: -1 }}, {{ opacity: 1, rotation: 0, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                else:  # glass: scale pop + glow
                    lines.append(f'  tl.fromTo(\'{_w2_el_sel}\', {{ opacity: 0, scale: 0.85 }}, {{ opacity: 1, scale: 1, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    if p["title_glow"]:
                        _w2_er_tg = _esc_js(p["title_glow"])
                        lines.append(f'  tl.to(\'{_w2_el_sel}\', {{ textShadow: "{_w2_er_tg}", duration: 0.20 }}, {t_in + 0.25:.4f});')
            elif content_style == "price_tag":
                _w2_pp_sel = f'.card[data-card-id="{card_id}"] #{card_id}-pt-price'
                _w2_pc_sel = f'.card[data-card-id="{card_id}"] #{card_id}-pt-ctx'
                if is_ledger:
                    lines.append(f'  tl.fromTo(\'{_w2_pp_sel}\', {{ clipPath: "inset(0% 100% 0% 0%)" }}, {{ clipPath: "inset(0% 0% 0% 0%)", duration: 0.35, ease: "power2.out" }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w2_pc_sel}\', {{ opacity: 1 }}, {t_in + 0.35:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w2_pp_sel}\', {{ opacity: 0, scale: 0.6 }}, {{ opacity: 1, scale: 1.1, duration: 0.30, ease: "back.out(2)" }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w2_pp_sel}\', {{ scale: 1, duration: 0.15, ease: "power2.out" }}, {t_in + 0.30:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_pc_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.40:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w2_pp_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.70, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_pc_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.50, ease: _eIn }}, {t_in + 0.40:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w2_pp_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_pc_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.25:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w2_pp_sel}\', {{ opacity: 0, rotation: -2 }}, {{ opacity: 1, rotation: 0, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_pc_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.30:.4f});')
                else:  # glass
                    lines.append(f'  tl.fromTo(\'{_w2_pp_sel}\', {{ opacity: 0, scale: 0.85 }}, {{ opacity: 1, scale: 1, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w2_pp_sel}\', {{ boxShadow: "0 0 20px {_esc_js(p["accent"])}", duration: 0.20 }}, {t_in + 0.35:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_pc_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.40:.4f});')
            elif content_style == "warning_soft":
                _w2_wi_sel = f'.card[data-card-id="{card_id}"] #{card_id}-ws-icon'
                _w2_wt_sel = f'.card[data-card-id="{card_id}"] #{card_id}-ws-text'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w2_wi_sel}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w2_wt_sel}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w2_wi_sel}\', {{ opacity: 0, scale: 0.6 }}, {{ opacity: 1, scale: 1.1, duration: 0.30, ease: "back.out(2)" }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w2_wi_sel}\', {{ scale: 1, duration: 0.15, ease: "power2.out" }}, {t_in + 0.30:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_wt_sel}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.25, ease: _eIn }}, {t_in + 0.35:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w2_wi_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.60, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_wt_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.60, ease: _eIn }}, {t_in + 0.40:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w2_wi_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_wt_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.25:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w2_wi_sel}\', {{ opacity: 0, scale: 0.9, rotation: -2 }}, {{ opacity: 1, scale: 1, rotation: 0, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_wt_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.25:.4f});')
                else:  # glass: icon glow + text slide
                    lines.append(f'  tl.fromTo(\'{_w2_wi_sel}\', {{ opacity: 0, scale: 0.8 }}, {{ opacity: 1, scale: 1, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    if p["accent"]:
                        lines.append(f'  tl.to(\'{_w2_wi_sel}\', {{ filter: "drop-shadow(0 0 8px {_esc_js(p["accent"])})", duration: 0.20 }}, {t_in + 0.30:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_wt_sel}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.30, ease: _eIn }}, {t_in + 0.25:.4f});')
            elif content_style == "testimonial":
                _w2_tt_sel = f'.card[data-card-id="{card_id}"] #{card_id}-tm-text'
                _w2_tp_sel = f'.card[data-card-id="{card_id}"] #{card_id}-tm-person'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w2_tt_sel}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w2_tp_sel}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w2_tt_sel}\', {{ opacity: 0, y: 20 }}, {{ opacity: 1, y: 0, duration: 0.35, ease: "back.out(1.5)" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_tp_sel}\', {{ opacity: 0, scale: 0.8 }}, {{ opacity: 1, scale: 1, duration: 0.25, ease: "back.out(2)" }}, {t_in + 0.35:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w2_tt_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.70, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_tp_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.50, ease: _eIn }}, {t_in + 0.60:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w2_tt_sel}\', {{ opacity: 0, y: 12 }}, {{ opacity: 1, y: 0, duration: 0.30, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_tp_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.30:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w2_tt_sel}\', {{ opacity: 0, y: 12 }}, {{ opacity: 1, y: 0, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_tp_sel}\', {{ opacity: 0, rotation: -1 }}, {{ opacity: 1, rotation: 0, duration: 0.30, ease: _eIn }}, {t_in + 0.30:.4f});')
                else:  # glass
                    lines.append(f'  tl.fromTo(\'{_w2_tt_sel}\', {{ opacity: 0, y: 16 }}, {{ opacity: 1, y: 0, duration: 0.40, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_tp_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.35:.4f});')
            elif content_style == "versus_battle":
                _w2_va_sel = f'.card[data-card-id="{card_id}"] #{card_id}-vb-a'
                _w2_vb_sel = f'.card[data-card-id="{card_id}"] #{card_id}-vb-b'
                _w2_vv_sel = f'.card[data-card-id="{card_id}"] #{card_id}-vb-vs'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w2_va_sel}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w2_vv_sel}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w2_vb_sel}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w2_va_sel}\', {{ opacity: 0, x: -40 }}, {{ opacity: 1, x: 0, duration: 0.35, ease: "back.out(1.5)" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_vb_sel}\', {{ opacity: 0, x: 40 }}, {{ opacity: 1, x: 0, duration: 0.35, ease: "back.out(1.5)" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_vv_sel}\', {{ opacity: 0, scale: 0.5 }}, {{ opacity: 1, scale: 1.2, duration: 0.25, ease: "back.out(2)" }}, {t_in + 0.20:.4f});')
                    lines.append(f'  tl.to(\'{_w2_vv_sel}\', {{ scale: 1, duration: 0.15, ease: "power2.out" }}, {t_in + 0.45:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w2_va_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.60, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_vb_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.60, ease: _eIn }}, {t_in + 0.15:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_vv_sel}\', {{ opacity: 0, scale: 0.8 }}, {{ opacity: 1, scale: 1, duration: 0.50, ease: _eIn }}, {t_in + 0.40:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w2_va_sel}\', {{ opacity: 0, x: -20 }}, {{ opacity: 1, x: 0, duration: 0.30, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_vb_sel}\', {{ opacity: 0, x: 20 }}, {{ opacity: 1, x: 0, duration: 0.30, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_vv_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.20:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w2_va_sel}\', {{ opacity: 0, rotation: -1 }}, {{ opacity: 1, rotation: 0, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_vb_sel}\', {{ opacity: 0, rotation: 1 }}, {{ opacity: 1, rotation: 0, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_vv_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.25:.4f});')
                else:  # glass: sides slide in + VS badge pulses
                    lines.append(f'  tl.fromTo(\'{_w2_va_sel}\', {{ opacity: 0, x: -30 }}, {{ opacity: 1, x: 0, duration: 0.40, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_vb_sel}\', {{ opacity: 0, x: 30 }}, {{ opacity: 1, x: 0, duration: 0.40, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w2_vv_sel}\', {{ opacity: 0, scale: 0.6 }}, {{ opacity: 1, scale: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.25:.4f});')
                    lines.append(f'  tl.to(\'{_w2_vv_sel}\', {{ boxShadow: "0 0 20px {_esc_js(p["accent"])}", duration: 0.20 }}, {t_in + 0.55:.4f});')
                    if p["accent_line_glow"]:
                        lines.append(f'  tl.to(\'{_w2_vv_sel}\', {{ boxShadow: "{_esc_js(p["accent_line_glow"])}", duration: 0.20 }}, {t_in + 0.75:.4f});')
            elif content_style == "recap_summary":
                _rs_items = card.get("contentHints", {}).get("recap_items", [])
                _n_rs = min(len(_rs_items), 5)
                for _rs_i in range(_n_rs):
                    _w3_rs = f'.card[data-card-id="{card_id}"] #{card_id}-rs-{_rs_i}'
                    _rs_t = t_in + _rs_i * 0.15
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w3_rs}\', {{ opacity: 1 }}, {t_in:.4f});')
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w3_rs}\', {{ opacity: 0, x: -10, scale: 0.95 }}, {{ opacity: 1, x: 0, scale: 1, duration: 0.28, ease: "back.out(1.5)" }}, {_rs_t:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w3_rs}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.45, ease: _eIn }}, {_rs_t:.4f});')
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_w3_rs}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {_rs_t:.4f});')
                    elif is_craft:
                        lines.append(f'  tl.fromTo(\'{_w3_rs}\', {{ opacity: 0, rotation: 0.5, x: -6 }}, {{ opacity: 1, rotation: 0, x: 0, duration: 0.28, ease: _eIn }}, {_rs_t:.4f});')
                    else:
                        lines.append(f'  tl.fromTo(\'{_w3_rs}\', {{ opacity: 0, x: -12 }}, {{ opacity: 1, x: 0, duration: 0.30, ease: _eIn }}, {_rs_t:.4f});')
                        if p["title_glow"]:
                            _w3_rs_tg = _esc_js(p["title_glow"])
                            lines.append(f'  tl.to(\'{_w3_rs}\', {{ textShadow: "{_w3_rs_tg}", duration: 0.20 }}, {_rs_t + 0.30:.4f});')
            elif content_style == "location_journey":
                _lj_pts = card.get("contentHints", {}).get("journey_points", [])
                _n_lj = min(len(_lj_pts), 5)
                _lj_stride = 0.38 if is_vibe else 0.50 if is_cinema else 0.42
                for _lj_i in range(_n_lj):
                    _w3_lj = f'.card[data-card-id="{card_id}"] #{card_id}-lj-{_lj_i}'
                    _lj_t = t_in + _lj_i * _lj_stride
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w3_lj}\', {{ opacity: 1 }}, {t_in:.4f});')
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w3_lj}\', {{ opacity: 0, scale: 0.5, y: -8 }}, {{ opacity: 1, scale: 1, y: 0, duration: 0.25, ease: "back.out(1.8)" }}, {_lj_t:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w3_lj}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.50, ease: _eIn }}, {_lj_t:.4f});')
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_w3_lj}\', {{ opacity: 0, y: -6 }}, {{ opacity: 1, y: 0, duration: 0.25, ease: _eIn }}, {_lj_t:.4f});')
                    elif is_craft:
                        lines.append(f'  tl.fromTo(\'{_w3_lj}\', {{ opacity: 0, rotation: -2 }}, {{ opacity: 1, rotation: 0, duration: 0.28, ease: _eIn }}, {_lj_t:.4f});')
                    else:
                        lines.append(f'  tl.fromTo(\'{_w3_lj}\', {{ opacity: 0, y: -10 }}, {{ opacity: 1, y: 0, duration: 0.28, ease: _eIn }}, {_lj_t:.4f});')
                    if _lj_i < _n_lj - 1:
                        _w3_lj_cn = f'.card[data-card-id="{card_id}"] #{card_id}-lj-c{_lj_i}'
                        _lj_cn_t = _lj_t + 0.28
                        if is_ledger:
                            lines.append(f'  tl.set(\'{_w3_lj_cn}\', {{ scaleX: 1 }}, {t_in:.4f});')
                        else:
                            lines.append(f'  tl.fromTo(\'{_w3_lj_cn}\', {{ scaleX: 0 }}, {{ scaleX: 1, duration: 0.30, ease: _eIn }}, {_lj_cn_t:.4f});')
            elif content_style == "formula_equation":
                _fe_parts = card.get("contentHints", {}).get("formula_parts", [])
                _n_fe = min(len(_fe_parts), 8)
                for _fe_i in range(_n_fe):
                    _w3_fe = f'.card[data-card-id="{card_id}"] #{card_id}-fe-{_fe_i}'
                    _fe_t = t_in + _fe_i * 0.18
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w3_fe}\', {{ opacity: 1 }}, {t_in:.4f});')
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w3_fe}\', {{ opacity: 0, scale: 0.7, y: 8 }}, {{ opacity: 1, scale: 1, y: 0, duration: 0.25, ease: "back.out(1.8)" }}, {_fe_t:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w3_fe}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.50, ease: _eIn }}, {_fe_t:.4f});')
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_w3_fe}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {_fe_t:.4f});')
                    elif is_craft:
                        lines.append(f'  tl.fromTo(\'{_w3_fe}\', {{ opacity: 0, rotation: 1 }}, {{ opacity: 1, rotation: 0, duration: 0.28, ease: _eIn }}, {_fe_t:.4f});')
                    else:
                        lines.append(f'  tl.fromTo(\'{_w3_fe}\', {{ opacity: 0, scale: 0.8 }}, {{ opacity: 1, scale: 1, duration: 0.25, ease: _eIn }}, {_fe_t:.4f});')
            elif content_style == "roadmap_milestone":
                _w3_rm_icon = f'.card[data-card-id="{card_id}"] #{card_id}-rm-icon'
                _w3_rm_lbl = f'.card[data-card-id="{card_id}"] #{card_id}-rm-label'
                _w3_rm_ctx = f'.card[data-card-id="{card_id}"] #{card_id}-rm-ctx'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w3_rm_icon}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w3_rm_lbl}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w3_rm_ctx}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w3_rm_icon}\', {{ opacity: 0, scale: 0.3, rotation: -20 }}, {{ opacity: 1, scale: 1, rotation: 0, duration: 0.35, ease: "back.out(2)" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w3_rm_lbl}\', {{ opacity: 0, y: 10 }}, {{ opacity: 1, y: 0, duration: 0.30, ease: "back.out(1.5)" }}, {t_in + 0.28:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w3_rm_ctx}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.50:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w3_rm_icon}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.60, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w3_rm_lbl}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.50, ease: _eIn }}, {t_in + 0.45:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w3_rm_ctx}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.40, ease: _eIn }}, {t_in + 0.80:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w3_rm_icon}\', {{ opacity: 0, scale: 0.8 }}, {{ opacity: 1, scale: 1, duration: 0.30, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w3_rm_lbl}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.25, ease: _eIn }}, {t_in + 0.25:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w3_rm_ctx}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.20, ease: _eIn }}, {t_in + 0.42:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w3_rm_icon}\', {{ opacity: 0, scale: 0.6, rotation: -5 }}, {{ opacity: 1, scale: 1, rotation: 0, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w3_rm_lbl}\', {{ opacity: 0, x: -8 }}, {{ opacity: 1, x: 0, duration: 0.28, ease: _eIn }}, {t_in + 0.30:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w3_rm_ctx}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.50:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w3_rm_icon}\', {{ opacity: 0, scale: 0.5, y: -15 }}, {{ opacity: 1, scale: 1, y: 0, duration: 0.40, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w3_rm_lbl}\', {{ opacity: 0, y: 10 }}, {{ opacity: 1, y: 0, duration: 0.35, ease: _eIn }}, {t_in + 0.35:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w3_rm_ctx}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.60:.4f});')
            elif content_style == "pros_cons":
                _pc_pros = card.get("contentHints", {}).get("pros", [])
                _pc_cons = card.get("contentHints", {}).get("cons", [])
                _n_pc_p = min(len(_pc_pros), 4)
                _n_pc_c = min(len(_pc_cons), 4)
                _w3_pc_div = f'.card[data-card-id="{card_id}"] #{card_id}-pc-div'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w3_pc_div}\', {{ scaleY: 1 }}, {t_in:.4f});')
                    for _i in range(_n_pc_p):
                        _w3_pr = f'.card[data-card-id="{card_id}"] #{card_id}-pc-pro-{_i}'
                        lines.append(f'  tl.set(\'{_w3_pr}\', {{ opacity: 1 }}, {t_in:.4f});')
                    for _i in range(_n_pc_c):
                        _w3_cn = f'.card[data-card-id="{card_id}"] #{card_id}-pc-con-{_i}'
                        lines.append(f'  tl.set(\'{_w3_cn}\', {{ opacity: 1 }}, {t_in:.4f});')
                else:
                    _pc_div_dur = 0.25 if is_vibe else 0.40 if is_cinema else 0.30
                    lines.append(f'  tl.fromTo(\'{_w3_pc_div}\', {{ scaleY: 0 }}, {{ scaleY: 1, duration: {_pc_div_dur:.2f}, ease: _eIn }}, {t_in:.4f});')
                    _pc_stride = 0.13 if is_vibe else 0.18 if is_cinema else 0.15
                    _pc_t0 = t_in + _pc_div_dur
                    for _i in range(max(_n_pc_p, _n_pc_c)):
                        _pc_t = _pc_t0 + _i * _pc_stride
                        if _i < _n_pc_p:
                            _w3_pr = f'.card[data-card-id="{card_id}"] #{card_id}-pc-pro-{_i}'
                            if is_vibe:
                                lines.append(f'  tl.fromTo(\'{_w3_pr}\', {{ opacity: 0, x: -8 }}, {{ opacity: 1, x: 0, duration: 0.22, ease: "back.out(1.5)" }}, {_pc_t:.4f});')
                            elif is_cinema:
                                lines.append(f'  tl.fromTo(\'{_w3_pr}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.40, ease: _eIn }}, {_pc_t:.4f});')
                            elif is_craft:
                                lines.append(f'  tl.fromTo(\'{_w3_pr}\', {{ opacity: 0, rotation: 0.5 }}, {{ opacity: 1, rotation: 0, duration: 0.25, ease: _eIn }}, {_pc_t:.4f});')
                            else:
                                lines.append(f'  tl.fromTo(\'{_w3_pr}\', {{ opacity: 0, x: -10 }}, {{ opacity: 1, x: 0, duration: 0.28, ease: _eIn }}, {_pc_t:.4f});')
                        if _i < _n_pc_c:
                            _w3_cn = f'.card[data-card-id="{card_id}"] #{card_id}-pc-con-{_i}'
                            if is_vibe:
                                lines.append(f'  tl.fromTo(\'{_w3_cn}\', {{ opacity: 0, x: 8 }}, {{ opacity: 1, x: 0, duration: 0.22, ease: "back.out(1.5)" }}, {_pc_t:.4f});')
                            elif is_cinema:
                                lines.append(f'  tl.fromTo(\'{_w3_cn}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.40, ease: _eIn }}, {_pc_t:.4f});')
                            elif is_craft:
                                lines.append(f'  tl.fromTo(\'{_w3_cn}\', {{ opacity: 0, rotation: -0.5 }}, {{ opacity: 1, rotation: 0, duration: 0.25, ease: _eIn }}, {_pc_t:.4f});')
                            else:
                                lines.append(f'  tl.fromTo(\'{_w3_cn}\', {{ opacity: 0, x: 10 }}, {{ opacity: 1, x: 0, duration: 0.28, ease: _eIn }}, {_pc_t:.4f});')
            elif content_style == "star_rating_review":
                _sr_n_raw = card.get("contentHints", {}).get("stars", 5)
                try:
                    _sr_n = max(0, min(5, int(_sr_n_raw)))
                except (ValueError, TypeError):
                    _sr_n = 5
                _w3_sr_text = f'.card[data-card-id="{card_id}"] #{card_id}-sr-text'
                _w3_sr_name = f'.card[data-card-id="{card_id}"] #{card_id}-sr-name'
                for _sr_i in range(5):
                    _w3_sr = f'.card[data-card-id="{card_id}"] #{card_id}-sr-s{_sr_i}'
                    _sr_t = t_in + _sr_i * 0.12
                    _sr_tgt = 1.0 if _sr_i < _sr_n else 0.4
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w3_sr}\', {{ opacity: {_sr_tgt} }}, {t_in:.4f});')
                    elif is_vibe:
                        if _sr_i < _sr_n:
                            lines.append(f'  tl.fromTo(\'{_w3_sr}\', {{ opacity: 0, scale: 0.5, y: -10 }}, {{ opacity: 1, scale: 1, y: 0, duration: 0.22, ease: "back.out(2)" }}, {_sr_t:.4f});')
                        else:
                            lines.append(f'  tl.fromTo(\'{_w3_sr}\', {{ opacity: 0 }}, {{ opacity: 0.4, duration: 0.20, ease: _eIn }}, {_sr_t:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w3_sr}\', {{ opacity: 0 }}, {{ opacity: {_sr_tgt}, duration: 0.40, ease: _eIn }}, {_sr_t:.4f});')
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_w3_sr}\', {{ opacity: 0 }}, {{ opacity: {_sr_tgt}, duration: 0.20, ease: _eIn }}, {_sr_t:.4f});')
                    elif is_craft:
                        lines.append(f'  tl.fromTo(\'{_w3_sr}\', {{ opacity: 0, rotation: -5 }}, {{ opacity: {_sr_tgt}, rotation: 0, duration: 0.22, ease: _eIn }}, {_sr_t:.4f});')
                    else:
                        if _sr_i < _sr_n:
                            lines.append(f'  tl.fromTo(\'{_w3_sr}\', {{ opacity: 0, scale: 0.7 }}, {{ opacity: 1, scale: 1, duration: 0.22, ease: _eIn }}, {_sr_t:.4f});')
                        else:
                            lines.append(f'  tl.fromTo(\'{_w3_sr}\', {{ opacity: 0 }}, {{ opacity: 0.4, duration: 0.20, ease: _eIn }}, {_sr_t:.4f});')
                _sr_after = t_in + 5 * 0.12 + 0.15
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w3_sr_text}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w3_sr_name}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w3_sr_text}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.45, ease: _eIn }}, {_sr_after:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w3_sr_name}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.35, ease: _eIn }}, {_sr_after + 0.35:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w3_sr_text}\', {{ opacity: 0, y: 6 }}, {{ opacity: 1, y: 0, duration: 0.28, ease: _eIn }}, {_sr_after:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w3_sr_name}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.22, ease: _eIn }}, {_sr_after + 0.25:.4f});')
            elif content_style == "income_reveal":
                _w3_ir_val = f'.card[data-card-id="{card_id}"] #{card_id}-ir-value'
                _w3_ir_ctx = f'.card[data-card-id="{card_id}"] #{card_id}-ir-ctx'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w3_ir_val}\', {{ opacity: 1, filter: "blur(0px)" }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w3_ir_ctx}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w3_ir_val}\', {{ opacity: 0.15, filter: "blur(8px)", scale: 0.95 }}, {{ opacity: 1, filter: "blur(0px)", scale: 1.08, duration: 0.25, ease: "power3.in" }}, {t_in + 0.55:.4f});')
                    lines.append(f'  tl.to(\'{_w3_ir_val}\', {{ scale: 1, duration: 0.15, ease: "power2.out" }}, {t_in + 0.80:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w3_ir_ctx}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.20, ease: _eIn }}, {t_in + 0.85:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w3_ir_val}\', {{ opacity: 0, filter: "blur(12px)" }}, {{ opacity: 1, filter: "blur(0px)", duration: 1.20, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w3_ir_ctx}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.50, ease: _eIn }}, {t_in + 1.00:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w3_ir_val}\', {{ opacity: 0, filter: "blur(6px)" }}, {{ opacity: 1, filter: "blur(0px)", duration: 0.60, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w3_ir_ctx}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.50:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w3_ir_val}\', {{ opacity: 0, filter: "blur(4px)", rotation: -3 }}, {{ opacity: 1, filter: "blur(0px)", rotation: 0, duration: 0.50, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w3_ir_ctx}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.28, ease: _eIn }}, {t_in + 0.42:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w3_ir_val}\', {{ opacity: 0, filter: "blur(10px)" }}, {{ opacity: 1, filter: "blur(0px)", duration: 0.70, ease: _eIn }}, {t_in:.4f});')
                    if p["title_glow_intense"]:
                        _w3_ir_tgi = _esc_js(p["title_glow_intense"])
                        lines.append(f'  tl.to(\'{_w3_ir_val}\', {{ textShadow: "{_w3_ir_tgi}", duration: 0.30 }}, {t_in + 0.60:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w3_ir_ctx}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.55:.4f});')
            # ── Wave 4 GSAP ───────────────────────────────────────────────────
            elif content_style == "question_answer_pair":
                _w4_qap_q   = f'.card[data-card-id="{card_id}"] #{card_id}-qap-q'
                _w4_qap_div = f'.card[data-card-id="{card_id}"] #{card_id}-qap-div'
                _w4_qap_a   = f'.card[data-card-id="{card_id}"] #{card_id}-qap-a'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w4_qap_q}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w4_qap_div}\', {{ width: "100%" }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w4_qap_a}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w4_qap_q}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.50, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_qap_div}\', {{ width: "0%" }}, {{ width: "100%", duration: 0.80, ease: "power2.inOut" }}, {t_in + 0.40:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_qap_a}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.70, ease: _eIn }}, {t_in + 1.10:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w4_qap_q}\', {{ opacity: 0, y: -10 }}, {{ opacity: 1, y: 0, duration: 0.20, ease: "back.out(1.5)" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_qap_div}\', {{ width: "0%" }}, {{ width: "100%", duration: 0.25, ease: "power3.out" }}, {t_in + 0.18:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_qap_a}\', {{ opacity: 0, scale: 0.9 }}, {{ opacity: 1, scale: 1, duration: 0.22, ease: "back.out(1.4)" }}, {t_in + 0.38:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w4_qap_q}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_qap_div}\', {{ width: "0%" }}, {{ width: "100%", duration: 0.40, ease: "power1.out" }}, {t_in + 0.28:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_qap_a}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.35, ease: _eIn }}, {t_in + 0.60:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w4_qap_q}\', {{ opacity: 0, rotation: -1 }}, {{ opacity: 1, rotation: 0, duration: 0.28, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_qap_div}\', {{ width: "0%" }}, {{ width: "100%", duration: 0.45, ease: "elastic.out(1,0.5)" }}, {t_in + 0.22:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_qap_a}\', {{ opacity: 0, rotation: 1 }}, {{ opacity: 1, rotation: 0, duration: 0.30, ease: _eIn }}, {t_in + 0.55:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w4_qap_q}\', {{ opacity: 0, y: -8 }}, {{ opacity: 1, y: 0, duration: 0.28, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_qap_div}\', {{ width: "0%" }}, {{ width: "100%", duration: 0.35, ease: "power2.out" }}, {t_in + 0.25:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_qap_a}\', {{ opacity: 0, y: 10 }}, {{ opacity: 1, y: 0, duration: 0.30, ease: _eIn }}, {t_in + 0.52:.4f});')
                    if p["title_glow"]:
                        _w4_tg = _esc_js(p["title_glow"])
                        lines.append(f'  tl.to(\'{_w4_qap_a}\', {{ textShadow: "{_w4_tg}", duration: 0.20 }}, {t_in + 0.70:.4f});')
            elif content_style == "chapter_marker":
                _w4_cm_num = f'.card[data-card-id="{card_id}"] #{card_id}-cm-num'
                _w4_cm_ln  = f'.card[data-card-id="{card_id}"] #{card_id}-cm-line'
                _w4_cm_ttl = f'.card[data-card-id="{card_id}"] #{card_id}-cm-title'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w4_cm_num}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w4_cm_ln}\', {{ width: "80px" }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w4_cm_ttl}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w4_cm_num}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.80, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_cm_ln}\', {{ width: "0px" }}, {{ width: "80px", duration: 1.00, ease: "power2.inOut" }}, {t_in + 0.60:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_cm_ttl}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.80, ease: _eIn }}, {t_in + 1.20:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w4_cm_num}\', {{ opacity: 0, scale: 0.5 }}, {{ opacity: 1, scale: 1, duration: 0.30, ease: "back.out(2)" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_cm_ln}\', {{ width: "0px" }}, {{ width: "80px", duration: 0.30, ease: "power3.out" }}, {t_in + 0.25:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_cm_ttl}\', {{ opacity: 0, y: 12 }}, {{ opacity: 1, y: 0, duration: 0.25, ease: "back.out(1.4)" }}, {t_in + 0.40:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w4_cm_num}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.40, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_cm_ln}\', {{ width: "0px" }}, {{ width: "80px", duration: 0.50, ease: "power1.out" }}, {t_in + 0.35:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_cm_ttl}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.35, ease: _eIn }}, {t_in + 0.70:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w4_cm_num}\', {{ opacity: 0, rotation: -5 }}, {{ opacity: 1, rotation: 0, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_cm_ln}\', {{ width: "0px" }}, {{ width: "80px", duration: 0.50, ease: "elastic.out(1,0.5)" }}, {t_in + 0.28:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_cm_ttl}\', {{ opacity: 0, rotation: 1 }}, {{ opacity: 1, rotation: 0, duration: 0.32, ease: _eIn }}, {t_in + 0.65:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w4_cm_num}\', {{ opacity: 0, scale: 0.8 }}, {{ opacity: 1, scale: 1, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_cm_ln}\', {{ width: "0px" }}, {{ width: "80px", duration: 0.40, ease: "power2.out" }}, {t_in + 0.30:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_cm_ttl}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.30, ease: _eIn }}, {t_in + 0.55:.4f});')
                    if p["title_glow_intense"]:
                        _w4_cm_tgi = _esc_js(p["title_glow_intense"])
                        lines.append(f'  tl.to(\'{_w4_cm_num}\', {{ textShadow: "{_w4_cm_tgi}", duration: 0.25 }}, {t_in + 0.20:.4f});')
            elif content_style == "secret_reveal":
                _w4_sec_lbl  = f'.card[data-card-id="{card_id}"] #{card_id}-sec-label'
                _w4_sec_text = f'.card[data-card-id="{card_id}"] #{card_id}-sec-text'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w4_sec_lbl}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w4_sec_text}\', {{ opacity: 1, filter: "blur(0px)" }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w4_sec_lbl}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.50, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_sec_text}\', {{ opacity: 0, filter: "blur(16px)" }}, {{ opacity: 1, filter: "blur(0px)", duration: 1.50, ease: _eIn }}, {t_in + 0.50:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w4_sec_lbl}\', {{ opacity: 0, scale: 0.5 }}, {{ opacity: 1, scale: 1, duration: 0.18, ease: "back.out(2)" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_sec_text}\', {{ opacity: 0, filter: "blur(10px)", scale: 0.92 }}, {{ opacity: 1, filter: "blur(0px)", scale: 1, duration: 0.28, ease: "power2.out" }}, {t_in + 0.60:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w4_sec_lbl}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_sec_text}\', {{ opacity: 0, filter: "blur(8px)" }}, {{ opacity: 1, filter: "blur(0px)", duration: 0.80, ease: _eIn }}, {t_in + 0.40:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w4_sec_lbl}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.28, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_sec_text}\', {{ opacity: 0, filter: "blur(6px)", rotation: -1 }}, {{ opacity: 1, filter: "blur(0px)", rotation: 0, duration: 0.60, ease: _eIn }}, {t_in + 0.40:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w4_sec_lbl}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_sec_text}\', {{ opacity: 0, filter: "blur(12px)" }}, {{ opacity: 1, filter: "blur(0px)", duration: 0.80, ease: _eIn }}, {t_in + 0.45:.4f});')
                    if p["title_glow"]:
                        _w4_sec_tg = _esc_js(p["title_glow"])
                        lines.append(f'  tl.to(\'{_w4_sec_text}\', {{ textShadow: "{_w4_sec_tg}", duration: 0.25 }}, {t_in + 1.05:.4f});')
            elif content_style == "objection_response":
                _w4_or_oh   = f'.card[data-card-id="{card_id}"] #{card_id}-or-obj-hdr'
                _w4_or_obj  = f'.card[data-card-id="{card_id}"] #{card_id}-or-obj'
                _w4_or_div  = f'.card[data-card-id="{card_id}"] #{card_id}-or-div'
                _w4_or_rh   = f'.card[data-card-id="{card_id}"] #{card_id}-or-resp-hdr'
                _w4_or_resp = f'.card[data-card-id="{card_id}"] #{card_id}-or-resp'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w4_or_oh}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w4_or_obj}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w4_or_div}\', {{ scaleX: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w4_or_rh}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w4_or_resp}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w4_or_oh}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.40, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_or_obj}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.60, ease: _eIn }}, {t_in + 0.30:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_or_div}\', {{ scaleX: 0 }}, {{ scaleX: 1, duration: 0.70, ease: "power2.inOut" }}, {t_in + 0.80:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_or_rh}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.40, ease: _eIn }}, {t_in + 1.30:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_or_resp}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.70, ease: _eIn }}, {t_in + 1.60:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w4_or_oh}\', {{ opacity: 0, x: -10 }}, {{ opacity: 1, x: 0, duration: 0.18, ease: "power3.out" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_or_obj}\', {{ opacity: 0, x: -8 }}, {{ opacity: 1, x: 0, duration: 0.20, ease: _eIn }}, {t_in + 0.14:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_or_div}\', {{ scaleX: 0 }}, {{ scaleX: 1, duration: 0.22, ease: "power3.out" }}, {t_in + 0.30:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_or_rh}\', {{ opacity: 0, x: 10 }}, {{ opacity: 1, x: 0, duration: 0.18, ease: "back.out(1.5)" }}, {t_in + 0.48:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_or_resp}\', {{ opacity: 0, scale: 0.95 }}, {{ opacity: 1, scale: 1, duration: 0.22, ease: "back.out(1.4)" }}, {t_in + 0.60:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w4_or_oh}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_or_obj}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.22:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_or_div}\', {{ scaleX: 0 }}, {{ scaleX: 1, duration: 0.40, ease: "power1.out" }}, {t_in + 0.45:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_or_rh}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.75:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_or_resp}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.92:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w4_or_oh}\', {{ opacity: 0, rotation: -1 }}, {{ opacity: 1, rotation: 0, duration: 0.28, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_or_obj}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.28, ease: _eIn }}, {t_in + 0.20:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_or_div}\', {{ scaleX: 0 }}, {{ scaleX: 1, duration: 0.50, ease: "elastic.out(1,0.5)" }}, {t_in + 0.42:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_or_rh}\', {{ opacity: 0, rotation: 1 }}, {{ opacity: 1, rotation: 0, duration: 0.28, ease: _eIn }}, {t_in + 0.78:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_or_resp}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.95:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w4_or_oh}\', {{ opacity: 0, y: -6 }}, {{ opacity: 1, y: 0, duration: 0.25, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_or_obj}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.28, ease: _eIn }}, {t_in + 0.20:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_or_div}\', {{ scaleX: 0 }}, {{ scaleX: 1, duration: 0.35, ease: "power2.out" }}, {t_in + 0.42:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_or_rh}\', {{ opacity: 0, y: 6 }}, {{ opacity: 1, y: 0, duration: 0.25, ease: _eIn }}, {t_in + 0.70:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_or_resp}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.30, ease: _eIn }}, {t_in + 0.88:.4f});')
                    if p["title_glow"]:
                        _w4_or_tg = _esc_js(p["title_glow"])
                        lines.append(f'  tl.to(\'{_w4_or_resp}\', {{ textShadow: "{_w4_or_tg}", duration: 0.20 }}, {t_in + 1.10:.4f});')
            elif content_style == "data_bar_chart":
                _w4_dbc_h = card.get("contentHints", {})
                _w4_dbc_labels = _w4_dbc_h.get("bar_labels", [])
                _w4_dbc_values = _w4_dbc_h.get("bar_values", [])
                _w4_dbc_n = min(len(_w4_dbc_labels), len(_w4_dbc_values), 4)
                _w4_dbc_max = max((float(v) for v in _w4_dbc_values[:_w4_dbc_n] if v is not None), default=1.0) or 1.0
                for _w4_di in range(_w4_dbc_n):
                    _w4_dbc_row  = f'.card[data-card-id="{card_id}"] #{card_id}-dbc-{_w4_di}'
                    _w4_dbc_fill = f'.card[data-card-id="{card_id}"] #{card_id}-dbc-fill-{_w4_di}'
                    try:
                        _w4_dbc_pct = round(float(_w4_dbc_values[_w4_di]) / _w4_dbc_max * 100, 1)
                    except (TypeError, ValueError, ZeroDivisionError):
                        _w4_dbc_pct = 0.0
                    _w4_dbc_delay = t_in + _w4_di * 0.15
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w4_dbc_row}\', {{ opacity: 1 }}, {t_in:.4f});')
                        lines.append(f'  tl.set(\'{_w4_dbc_fill}\', {{ width: "{_w4_dbc_pct}%" }}, {t_in:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w4_dbc_row}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.50, ease: _eIn }}, {_w4_dbc_delay:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w4_dbc_fill}\', {{ width: "0%" }}, {{ width: "{_w4_dbc_pct}%", duration: 1.20, ease: "power1.out" }}, {_w4_dbc_delay + 0.30:.4f});')
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w4_dbc_row}\', {{ opacity: 0, x: -10 }}, {{ opacity: 1, x: 0, duration: 0.18, ease: "power3.out" }}, {_w4_dbc_delay:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w4_dbc_fill}\', {{ width: "0%" }}, {{ width: "{_w4_dbc_pct}%", duration: 0.35, ease: "back.out(1.2)" }}, {_w4_dbc_delay + 0.15:.4f});')
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_w4_dbc_row}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {_w4_dbc_delay:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w4_dbc_fill}\', {{ width: "0%" }}, {{ width: "{_w4_dbc_pct}%", duration: 0.60, ease: "power1.out" }}, {_w4_dbc_delay + 0.20:.4f});')
                    elif is_craft:
                        lines.append(f'  tl.fromTo(\'{_w4_dbc_row}\', {{ opacity: 0, rotation: -1 }}, {{ opacity: 1, rotation: 0, duration: 0.28, ease: _eIn }}, {_w4_dbc_delay:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w4_dbc_fill}\', {{ width: "0%" }}, {{ width: "{_w4_dbc_pct}%", duration: 0.55, ease: "elastic.out(1,0.6)" }}, {_w4_dbc_delay + 0.18:.4f});')
                    else:
                        lines.append(f'  tl.fromTo(\'{_w4_dbc_row}\', {{ opacity: 0, y: 6 }}, {{ opacity: 1, y: 0, duration: 0.25, ease: _eIn }}, {_w4_dbc_delay:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w4_dbc_fill}\', {{ width: "0%" }}, {{ width: "{_w4_dbc_pct}%", duration: 0.50, ease: "power2.out" }}, {_w4_dbc_delay + 0.18:.4f});')
            elif content_style == "cause_effect":
                _w4_ce_cause  = f'.card[data-card-id="{card_id}"] #{card_id}-ceff-cause'
                _w4_ce_arrow  = f'.card[data-card-id="{card_id}"] #{card_id}-ceff-arrow'
                _w4_ce_path   = f'.card[data-card-id="{card_id}"] #{card_id}-ceff-path'
                _w4_ce_head   = f'.card[data-card-id="{card_id}"] #{card_id}-ceff-head'
                _w4_ce_effect = f'.card[data-card-id="{card_id}"] #{card_id}-ceff-effect'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w4_ce_cause}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w4_ce_arrow}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w4_ce_path}\', {{ strokeDashoffset: 0 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w4_ce_head}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w4_ce_effect}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w4_ce_cause}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.60, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w4_ce_arrow}\', {{ opacity: 1 }}, {t_in + 0.50:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_ce_path}\', {{ strokeDashoffset: 100 }}, {{ strokeDashoffset: 0, duration: 0.80, ease: "power2.inOut" }}, {t_in + 0.50:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_ce_head}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 1.10:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_ce_effect}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.70, ease: _eIn }}, {t_in + 1.20:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w4_ce_cause}\', {{ opacity: 0, scale: 0.9 }}, {{ opacity: 1, scale: 1, duration: 0.22, ease: "back.out(1.5)" }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w4_ce_arrow}\', {{ opacity: 1 }}, {t_in + 0.18:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_ce_path}\', {{ strokeDashoffset: 100 }}, {{ strokeDashoffset: 0, duration: 0.20, ease: "power3.out" }}, {t_in + 0.18:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_ce_head}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.10, ease: _eIn }}, {t_in + 0.36:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_ce_effect}\', {{ opacity: 0, scale: 0.9 }}, {{ opacity: 1, scale: 1, duration: 0.22, ease: "back.out(1.5)" }}, {t_in + 0.42:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w4_ce_cause}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w4_ce_arrow}\', {{ opacity: 1 }}, {t_in + 0.25:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_ce_path}\', {{ strokeDashoffset: 100 }}, {{ strokeDashoffset: 0, duration: 0.35, ease: "power1.out" }}, {t_in + 0.25:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_ce_head}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.20, ease: _eIn }}, {t_in + 0.55:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_ce_effect}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.65:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w4_ce_cause}\', {{ opacity: 0, rotation: -2 }}, {{ opacity: 1, rotation: 0, duration: 0.30, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w4_ce_arrow}\', {{ opacity: 1 }}, {t_in + 0.25:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_ce_path}\', {{ strokeDashoffset: 100 }}, {{ strokeDashoffset: 0, duration: 0.40, ease: "elastic.out(1,0.6)" }}, {t_in + 0.25:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_ce_head}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.20, ease: _eIn }}, {t_in + 0.55:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_ce_effect}\', {{ opacity: 0, rotation: 2 }}, {{ opacity: 1, rotation: 0, duration: 0.30, ease: _eIn }}, {t_in + 0.65:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w4_ce_cause}\', {{ opacity: 0, x: -12 }}, {{ opacity: 1, x: 0, duration: 0.28, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w4_ce_arrow}\', {{ opacity: 1 }}, {t_in + 0.25:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_ce_path}\', {{ strokeDashoffset: 100 }}, {{ strokeDashoffset: 0, duration: 0.30, ease: "power2.out" }}, {t_in + 0.25:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_ce_head}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.15, ease: _eIn }}, {t_in + 0.50:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w4_ce_effect}\', {{ opacity: 0, x: 12 }}, {{ opacity: 1, x: 0, duration: 0.28, ease: _eIn }}, {t_in + 0.55:.4f});')
                    if p["accent_line_glow"]:
                        _w4_ce_alg = _esc_js(p["accent_line_glow"])
                        lines.append(f'  tl.to(\'{_w4_ce_arrow}\', {{ filter: "drop-shadow(0 0 4px {p["accent"]})" }}, {t_in + 0.50:.4f});')
            elif content_style == "number_ranking":
                _w4_nr_h = card.get("contentHints", {})
                _w4_nr_items = _w4_nr_h.get("rankings", [])
                _w4_nr_n = min(len(_w4_nr_items), 5)
                for _w4_ni in range(_w4_nr_n):
                    _w4_nr_item = f'.card[data-card-id="{card_id}"] #{card_id}-nr-{_w4_ni}'
                    _w4_nr_delay = t_in + _w4_ni * 0.18
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w4_nr_item}\', {{ opacity: 1 }}, {t_in:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w4_nr_item}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.55, ease: _eIn }}, {_w4_nr_delay:.4f});')
                    elif is_vibe:
                        _w4_nr_bounce = "back.out(2.0)" if _w4_ni == 0 else "back.out(1.5)"
                        lines.append(f'  tl.fromTo(\'{_w4_nr_item}\', {{ opacity: 0, scale: 0.7, y: -10 }}, {{ opacity: 1, scale: 1, y: 0, duration: 0.22, ease: "{_w4_nr_bounce}" }}, {_w4_nr_delay:.4f});')
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_w4_nr_item}\', {{ opacity: 0, x: -8 }}, {{ opacity: 1, x: 0, duration: 0.28, ease: _eIn }}, {_w4_nr_delay:.4f});')
                    elif is_craft:
                        lines.append(f'  tl.fromTo(\'{_w4_nr_item}\', {{ opacity: 0, rotation: -2 }}, {{ opacity: 1, rotation: 0, duration: 0.28, ease: _eIn }}, {_w4_nr_delay:.4f});')
                    else:
                        lines.append(f'  tl.fromTo(\'{_w4_nr_item}\', {{ opacity: 0, y: 10 }}, {{ opacity: 1, y: 0, duration: 0.28, ease: _eIn }}, {_w4_nr_delay:.4f});')
            # ── Wave 5 GSAP ───────────────────────────────────────────────────
            elif content_style == "hand_written_note":
                _w5_hwn_text = f'.card[data-card-id="{card_id}"] #{card_id}-hwn-text'
                _w5_hwn_line = f'.card[data-card-id="{card_id}"] #{card_id}-hwn-line'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w5_hwn_text}\', {{ opacity: 1, rotation: 0 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w5_hwn_line}\', {{ width: "80%" }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w5_hwn_text}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.70, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w5_hwn_line}\', {{ width: "0%" }}, {{ width: "80%", duration: 0.60, ease: "power2.out" }}, {t_in + 0.35:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w5_hwn_text}\', {{ opacity: 0, rotation: -3, scale: 0.9 }}, {{ opacity: 1, rotation: -1.5, scale: 1, duration: 0.25, ease: "back.out(2.0)" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w5_hwn_line}\', {{ width: "0%" }}, {{ width: "80%", duration: 0.35, ease: "back.out(1.5)" }}, {t_in + 0.18:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w5_hwn_text}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w5_hwn_line}\', {{ width: "0%" }}, {{ width: "80%", duration: 0.40, ease: "power2.out" }}, {t_in + 0.20:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w5_hwn_text}\', {{ opacity: 0, rotation: -4 }}, {{ opacity: 1, rotation: -1.5, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w5_hwn_line}\', {{ width: "0%" }}, {{ width: "80%", duration: 0.45, ease: "power1.inOut" }}, {t_in + 0.22:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w5_hwn_text}\', {{ opacity: 0, scale: 0.92, rotation: -3 }}, {{ opacity: 1, scale: 1, rotation: -1.5, duration: 0.32, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w5_hwn_line}\', {{ width: "0%" }}, {{ width: "80%", duration: 0.35, ease: "power2.out" }}, {t_in + 0.22:.4f});')
                    if p.get("accent_line_glow"):
                        lines.append(f'  tl.to(\'{_w5_hwn_line}\', {{ boxShadow: "{_esc_js(p["accent_line_glow"])}", duration: 0.20 }}, {t_in + 0.40:.4f});')
            elif content_style == "speech_bubble_thought":
                _w5_sbt_text = f'.card[data-card-id="{card_id}"] #{card_id}-sbt-text'
                for _w5_sdi in range(3):
                    _w5_dot = f'.card[data-card-id="{card_id}"] #{card_id}-sbt-dot-{_w5_sdi}'
                    _w5_dd = t_in + _w5_sdi * 0.12
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w5_dot}\', {{ opacity: 1 }}, {t_in:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w5_dot}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.40, ease: _eIn }}, {_w5_dd:.4f});')
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w5_dot}\', {{ opacity: 0, scale: 0, y: 8 }}, {{ opacity: 1, scale: 1, y: 0, duration: 0.20, ease: "back.out(2.5)" }}, {_w5_dd:.4f});')
                    else:
                        lines.append(f'  tl.fromTo(\'{_w5_dot}\', {{ opacity: 0, y: 6 }}, {{ opacity: 1, y: 0, duration: 0.18, ease: _eIn }}, {_w5_dd:.4f});')
                _w5_sbt_delay = t_in + 0.40
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w5_sbt_text}\', {{ opacity: 1 }}, {_w5_sbt_delay:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w5_sbt_text}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.60, ease: _eIn }}, {_w5_sbt_delay:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w5_sbt_text}\', {{ opacity: 0, scale: 0.85 }}, {{ opacity: 1, scale: 1, duration: 0.22, ease: "back.out(1.8)" }}, {_w5_sbt_delay:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w5_sbt_text}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.32, ease: _eIn }}, {_w5_sbt_delay:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w5_sbt_text}\', {{ opacity: 0, rotation: -1 }}, {{ opacity: 1, rotation: 0, duration: 0.32, ease: _eIn }}, {_w5_sbt_delay:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w5_sbt_text}\', {{ opacity: 0, scale: 0.88 }}, {{ opacity: 1, scale: 1, duration: 0.30, ease: _eIn }}, {_w5_sbt_delay:.4f});')
                    if p.get("title_glow"):
                        lines.append(f'  tl.to(\'{_w5_sbt_text}\', {{ textShadow: "{_esc_js(p["title_glow"])}", duration: 0.20 }}, {_w5_sbt_delay + 0.22:.4f});')
            elif content_style == "calendar_date_highlight":
                _w5_cal_cell = f'.card[data-card-id="{card_id}"] #{card_id}-cal-cell'
                _w5_cal_ctx  = f'.card[data-card-id="{card_id}"] #{card_id}-cal-ctx'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w5_cal_cell}\', {{ opacity: 1, scale: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w5_cal_ctx}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w5_cal_cell}\', {{ opacity: 0, scale: 0.90 }}, {{ opacity: 1, scale: 1, duration: 0.65, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w5_cal_ctx}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.45, ease: _eIn }}, {t_in + 0.40:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w5_cal_cell}\', {{ opacity: 0, scale: 0.7 }}, {{ opacity: 1, scale: 1, duration: 0.25, ease: "back.out(2.2)" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w5_cal_ctx}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.20, ease: "back.out(1.5)" }}, {t_in + 0.20:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w5_cal_cell}\', {{ opacity: 0, scale: 0.92 }}, {{ opacity: 1, scale: 1, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w5_cal_ctx}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.25:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w5_cal_cell}\', {{ opacity: 0, scale: 0.88, rotation: -2 }}, {{ opacity: 1, scale: 1, rotation: 0, duration: 0.38, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w5_cal_ctx}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.28, ease: _eIn }}, {t_in + 0.28:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w5_cal_cell}\', {{ opacity: 0, scale: 0.82 }}, {{ opacity: 1, scale: 1, duration: 0.32, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w5_cal_ctx}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.28, ease: _eIn }}, {t_in + 0.25:.4f});')
                    if p.get("accent_line_glow"):
                        lines.append(f'  tl.to(\'{_w5_cal_cell}\', {{ boxShadow: "{_esc_js(p["accent_line_glow"])}", duration: 0.20 }}, {t_in + 0.28:.4f});')
            elif content_style == "percentage_split":
                _w5_psp_h = card.get("contentHints", {})
                _w5_psp_labels = _w5_psp_h.get("split_labels", [])
                _w5_psp_values = _w5_psp_h.get("split_values", [])
                _w5_psp_n = min(len(_w5_psp_labels), len(_w5_psp_values), 5)
                _w5_psp_total = sum(float(v) for v in _w5_psp_values[:_w5_psp_n]) or 1.0
                _w5_psp_track = f'.card[data-card-id="{card_id}"] #{card_id}-psp-track'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w5_psp_track}\', {{ opacity: 1 }}, {t_in:.4f});')
                    for _w5_psp_i in range(_w5_psp_n):
                        _w5_psp_pct = float(_w5_psp_values[_w5_psp_i]) / _w5_psp_total * 100
                        _w5_seg = f'.card[data-card-id="{card_id}"] #{card_id}-psp-seg-{_w5_psp_i}'
                        _w5_lbl = f'.card[data-card-id="{card_id}"] #{card_id}-psp-lbl-{_w5_psp_i}'
                        lines.append(f'  tl.set(\'{_w5_seg}\', {{ width: "{_w5_psp_pct:.1f}%" }}, {t_in:.4f});')
                        lines.append(f'  tl.set(\'{_w5_lbl}\', {{ opacity: 1 }}, {t_in:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w5_psp_track}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in:.4f});')
                    for _w5_psp_i in range(_w5_psp_n):
                        _w5_psp_pct = float(_w5_psp_values[_w5_psp_i]) / _w5_psp_total * 100
                        _w5_seg = f'.card[data-card-id="{card_id}"] #{card_id}-psp-seg-{_w5_psp_i}'
                        _w5_lbl = f'.card[data-card-id="{card_id}"] #{card_id}-psp-lbl-{_w5_psp_i}'
                        _w5_psp_delay = t_in + 0.15 + _w5_psp_i * 0.12
                        lines.append(f'  tl.fromTo(\'{_w5_seg}\', {{ width: "0%" }}, {{ width: "{_w5_psp_pct:.1f}%", duration: 0.45, ease: "power2.out" }}, {_w5_psp_delay:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w5_lbl}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {_w5_psp_delay + 0.30:.4f});')
            elif content_style == "red_flag_list":
                _w5_rfl_h = card.get("contentHints", {})
                _w5_rfl_items = _w5_rfl_h.get("flags", [])
                _w5_rfl_n = min(len(_w5_rfl_items), 5)
                for _w5_rfl_i in range(_w5_rfl_n):
                    _w5_rfl_item = f'.card[data-card-id="{card_id}"] #{card_id}-rfl-{_w5_rfl_i}'
                    _w5_rfl_delay = t_in + _w5_rfl_i * 0.16
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w5_rfl_item}\', {{ opacity: 1 }}, {t_in:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w5_rfl_item}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.55, ease: _eIn }}, {_w5_rfl_delay:.4f});')
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w5_rfl_item}\', {{ opacity: 0, x: -12 }}, {{ opacity: 1, x: 0, duration: 0.22, ease: "back.out(1.8)" }}, {_w5_rfl_delay:.4f});')
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_w5_rfl_item}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {_w5_rfl_delay:.4f});')
                    elif is_craft:
                        lines.append(f'  tl.fromTo(\'{_w5_rfl_item}\', {{ opacity: 0, rotation: -1 }}, {{ opacity: 1, rotation: 0, duration: 0.28, ease: _eIn }}, {_w5_rfl_delay:.4f});')
                    else:
                        lines.append(f'  tl.fromTo(\'{_w5_rfl_item}\', {{ opacity: 0, x: -10 }}, {{ opacity: 1, x: 0, duration: 0.28, ease: _eIn }}, {_w5_rfl_delay:.4f});')
            elif content_style == "success_metric_badge":
                _w5_smb_badge = f'.card[data-card-id="{card_id}"] #{card_id}-smb-badge'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w5_smb_badge}\', {{ opacity: 1, scale: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w5_smb_badge}\', {{ opacity: 0, scale: 0.92 }}, {{ opacity: 1, scale: 1, duration: 0.70, ease: _eIn }}, {t_in:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w5_smb_badge}\', {{ opacity: 0, scale: 0.6 }}, {{ opacity: 1, scale: 1.06, duration: 0.22, ease: "back.out(2.2)" }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w5_smb_badge}\', {{ scale: 1, duration: 0.15, ease: "power2.out" }}, {t_in + 0.22:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w5_smb_badge}\', {{ opacity: 0, scale: 0.90 }}, {{ opacity: 1, scale: 1, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w5_smb_badge}\', {{ opacity: 0, scale: 0.88, rotation: -2 }}, {{ opacity: 1, scale: 1, rotation: 0, duration: 0.38, ease: _eIn }}, {t_in:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w5_smb_badge}\', {{ opacity: 0, scale: 0.80 }}, {{ opacity: 1, scale: 1, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    if p.get("accent_line_glow"):
                        lines.append(f'  tl.to(\'{_w5_smb_badge}\', {{ boxShadow: "{_esc_js(p["accent_line_glow"])}", duration: 0.20 }}, {t_in + 0.28:.4f});')
            elif content_style == "client_avatar_persona":
                _w5_cap_h = card.get("contentHints", {})
                _w5_cap_traits = _w5_cap_h.get("persona_traits", [])
                _w5_cap_n = min(len(_w5_cap_traits), 4)
                _w5_cap_avatar = f'.card[data-card-id="{card_id}"] #{card_id}-cap-avatar'
                _w5_cap_name   = f'.card[data-card-id="{card_id}"] #{card_id}-cap-name'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w5_cap_avatar}\', {{ opacity: 1, scale: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w5_cap_name}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w5_cap_avatar}\', {{ opacity: 0, scale: 0.92 }}, {{ opacity: 1, scale: 1, duration: 0.65, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w5_cap_name}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.50, ease: _eIn }}, {t_in + 0.35:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w5_cap_avatar}\', {{ opacity: 0, scale: 0.65 }}, {{ opacity: 1, scale: 1, duration: 0.25, ease: "back.out(2.5)" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w5_cap_name}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.20, ease: "back.out(1.5)" }}, {t_in + 0.20:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w5_cap_avatar}\', {{ opacity: 0, scale: 0.90 }}, {{ opacity: 1, scale: 1, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w5_cap_name}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.25:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w5_cap_avatar}\', {{ opacity: 0, scale: 0.85, rotation: -3 }}, {{ opacity: 1, scale: 1, rotation: 0, duration: 0.38, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w5_cap_name}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.28:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w5_cap_avatar}\', {{ opacity: 0, scale: 0.75 }}, {{ opacity: 1, scale: 1, duration: 0.32, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w5_cap_name}\', {{ opacity: 0, y: 6 }}, {{ opacity: 1, y: 0, duration: 0.28, ease: _eIn }}, {t_in + 0.22:.4f});')
                    if p.get("accent_line_glow"):
                        lines.append(f'  tl.to(\'{_w5_cap_avatar}\', {{ boxShadow: "{_esc_js(p["accent_line_glow"])}", duration: 0.20 }}, {t_in + 0.28:.4f});')
                for _w5_cap_ti in range(_w5_cap_n):
                    _w5_cap_trait = f'.card[data-card-id="{card_id}"] #{card_id}-cap-trait-{_w5_cap_ti}'
                    _w5_cap_td = t_in + 0.35 + _w5_cap_ti * 0.12
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w5_cap_trait}\', {{ opacity: 1 }}, {t_in:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w5_cap_trait}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.40, ease: _eIn }}, {_w5_cap_td:.4f});')
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w5_cap_trait}\', {{ opacity: 0, scale: 0.7 }}, {{ opacity: 1, scale: 1, duration: 0.18, ease: "back.out(2.0)" }}, {_w5_cap_td:.4f});')
                    else:
                        lines.append(f'  tl.fromTo(\'{_w5_cap_trait}\', {{ opacity: 0, y: 6 }}, {{ opacity: 1, y: 0, duration: 0.20, ease: _eIn }}, {_w5_cap_td:.4f});')
            # ── Wave 6 GSAP ───────────────────────────────────────────────────
            elif content_style == "book_recommendation":
                _w6_br_cover  = f'.card[data-card-id="{card_id}"] #{card_id}-br-cover'
                _w6_br_title  = f'.card[data-card-id="{card_id}"] #{card_id}-br-title'
                _w6_br_author = f'.card[data-card-id="{card_id}"] #{card_id}-br-author'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w6_br_cover}\', {{ opacity: 1, scale: 1, rotationY: 0 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w6_br_title}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w6_br_author}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w6_br_cover}\', {{ opacity: 0, scale: 0.92 }}, {{ opacity: 1, scale: 1, duration: 0.75, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_br_title}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.55, ease: _eIn }}, {t_in + 0.45:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_br_author}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.40, ease: _eIn }}, {t_in + 0.65:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w6_br_cover}\', {{ opacity: 0, scale: 0.65, rotationY: -20 }}, {{ opacity: 1, scale: 1, rotationY: 0, duration: 0.28, ease: "back.out(2.2)" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_br_title}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.20, ease: "back.out(1.5)" }}, {t_in + 0.22:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_br_author}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.18, ease: _eIn }}, {t_in + 0.35:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w6_br_cover}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.40, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_br_title}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.32, ease: _eIn }}, {t_in + 0.30:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_br_author}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.28, ease: _eIn }}, {t_in + 0.45:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w6_br_cover}\', {{ opacity: 0, scale: 0.85, rotation: -3 }}, {{ opacity: 1, scale: 1, rotation: -1, duration: 0.38, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_br_title}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.28:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_br_author}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.42:.4f});')
                else:  # glass
                    lines.append(f'  tl.fromTo(\'{_w6_br_cover}\', {{ opacity: 0, scale: 0.78, rotationY: -20, perspective: 400 }}, {{ opacity: 1, scale: 1, rotationY: 0, duration: 0.40, ease: _eIn }}, {t_in:.4f});')
                    if p.get("accent_line_glow"):
                        lines.append(f'  tl.to(\'{_w6_br_cover}\', {{ boxShadow: "{_esc_js(p["accent_line_glow"])}", duration: 0.20 }}, {t_in + 0.32:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_br_title}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.30, ease: _eIn }}, {t_in + 0.30:.4f});')
                    if p.get("title_glow"):
                        lines.append(f'  tl.to(\'{_w6_br_title}\', {{ textShadow: "{_esc_js(p["title_glow"])}", duration: 0.20 }}, {t_in + 0.48:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_br_author}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {t_in + 0.45:.4f});')
            elif content_style == "tool_stack":
                _w6_ts_h = card.get("contentHints", {})
                _w6_ts_tools = _w6_ts_h.get("tools", [])
                _w6_ts_n = min(len(_w6_ts_tools), 6)
                for _w6_ti in range(_w6_ts_n):
                    _w6_ts_item = f'.card[data-card-id="{card_id}"] #{card_id}-ts-{_w6_ti}'
                    _w6_ts_delay = t_in + _w6_ti * 0.14
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w6_ts_item}\', {{ opacity: 1 }}, {t_in:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w6_ts_item}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.50, ease: _eIn }}, {_w6_ts_delay:.4f});')
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w6_ts_item}\', {{ opacity: 0, scale: 0.7, y: -6 }}, {{ opacity: 1, scale: 1, y: 0, duration: 0.20, ease: "back.out(2.0)" }}, {_w6_ts_delay:.4f});')
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_w6_ts_item}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.28, ease: _eIn }}, {_w6_ts_delay:.4f});')
                    elif is_craft:
                        lines.append(f'  tl.fromTo(\'{_w6_ts_item}\', {{ opacity: 0, rotation: -2 }}, {{ opacity: 1, rotation: 0, duration: 0.28, ease: _eIn }}, {_w6_ts_delay:.4f});')
                    else:
                        lines.append(f'  tl.fromTo(\'{_w6_ts_item}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.25, ease: _eIn }}, {_w6_ts_delay:.4f});')
                        if p.get("accent_line_glow"):
                            lines.append(f'  tl.to(\'{_w6_ts_item}\', {{ boxShadow: "{_esc_js(p["accent_line_glow"])}", duration: 0.18 }}, {_w6_ts_delay + 0.18:.4f});')
            elif content_style == "revenue_breakdown":
                _w6_rb_h = card.get("contentHints", {})
                _w6_rb_sources = _w6_rb_h.get("revenue_sources", [])
                _w6_rb_values  = _w6_rb_h.get("revenue_values", [])
                _w6_rb_n = min(len(_w6_rb_sources), len(_w6_rb_values), 5)
                _w6_rb_max = max((float(v) for v in _w6_rb_values[:_w6_rb_n]), default=1.0) or 1.0
                for _w6_rbi in range(_w6_rb_n):
                    _w6_rb_row  = f'.card[data-card-id="{card_id}"] #{card_id}-rb-{_w6_rbi}'
                    _w6_rb_fill = f'.card[data-card-id="{card_id}"] #{card_id}-rb-fill-{_w6_rbi}'
                    _w6_rb_pct  = float(_w6_rb_values[_w6_rbi]) / _w6_rb_max * 100
                    _w6_rb_delay = t_in + _w6_rbi * 0.18
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w6_rb_row}\', {{ opacity: 1 }}, {t_in:.4f});')
                        lines.append(f'  tl.set(\'{_w6_rb_fill}\', {{ width: "{_w6_rb_pct:.1f}%" }}, {t_in:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w6_rb_row}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.55, ease: _eIn }}, {_w6_rb_delay:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w6_rb_fill}\', {{ width: "0%" }}, {{ width: "{_w6_rb_pct:.1f}%", duration: 0.70, ease: "power1.inOut" }}, {_w6_rb_delay + 0.30:.4f});')
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w6_rb_row}\', {{ opacity: 0, x: -10 }}, {{ opacity: 1, x: 0, duration: 0.22, ease: "back.out(1.5)" }}, {_w6_rb_delay:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w6_rb_fill}\', {{ width: "0%" }}, {{ width: "{_w6_rb_pct:.1f}%", duration: 0.40, ease: "back.out(1.2)" }}, {_w6_rb_delay + 0.15:.4f});')
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_w6_rb_row}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {_w6_rb_delay:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w6_rb_fill}\', {{ width: "0%" }}, {{ width: "{_w6_rb_pct:.1f}%", duration: 0.45, ease: "power2.out" }}, {_w6_rb_delay + 0.18:.4f});')
                    elif is_craft:
                        lines.append(f'  tl.fromTo(\'{_w6_rb_row}\', {{ opacity: 0, rotation: -1 }}, {{ opacity: 1, rotation: 0, duration: 0.30, ease: _eIn }}, {_w6_rb_delay:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w6_rb_fill}\', {{ width: "0%" }}, {{ width: "{_w6_rb_pct:.1f}%", duration: 0.45, ease: "power1.inOut" }}, {_w6_rb_delay + 0.20:.4f});')
                    else:
                        lines.append(f'  tl.fromTo(\'{_w6_rb_row}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.28, ease: _eIn }}, {_w6_rb_delay:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w6_rb_fill}\', {{ width: "0%" }}, {{ width: "{_w6_rb_pct:.1f}%", duration: 0.45, ease: "power2.out" }}, {_w6_rb_delay + 0.18:.4f});')
                        if p.get("accent_line_glow"):
                            lines.append(f'  tl.to(\'{_w6_rb_fill}\', {{ boxShadow: "{_esc_js(p["accent_line_glow"])}", duration: 0.18 }}, {_w6_rb_delay + 0.45:.4f});')
            elif content_style == "age_milestone":
                _w6_am_num = f'.card[data-card-id="{card_id}"] #{card_id}-am-number'
                _w6_am_ctx = f'.card[data-card-id="{card_id}"] #{card_id}-am-ctx'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w6_am_num}\', {{ opacity: 1, scale: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w6_am_ctx}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w6_am_num}\', {{ opacity: 0, scale: 0.88 }}, {{ opacity: 1, scale: 1, duration: 0.85, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_am_ctx}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.60, ease: _eIn }}, {t_in + 0.55:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w6_am_num}\', {{ opacity: 0, scale: 0.4 }}, {{ opacity: 1, scale: 1.08, duration: 0.30, ease: "back.out(2.5)" }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w6_am_num}\', {{ scale: 1, duration: 0.18, ease: "power2.out" }}, {t_in + 0.30:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_am_ctx}\', {{ opacity: 0, y: 10 }}, {{ opacity: 1, y: 0, duration: 0.22, ease: "back.out(1.5)" }}, {t_in + 0.28:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w6_am_num}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.40, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_am_ctx}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.32, ease: _eIn }}, {t_in + 0.28:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w6_am_num}\', {{ opacity: 0, scale: 0.80, rotation: -4 }}, {{ opacity: 1, scale: 1, rotation: 0, duration: 0.40, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_am_ctx}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.30:.4f});')
                else:  # glass
                    lines.append(f'  tl.fromTo(\'{_w6_am_num}\', {{ opacity: 0, scale: 0.70 }}, {{ opacity: 1, scale: 1, duration: 0.38, ease: _eIn }}, {t_in:.4f});')
                    if p.get("title_glow_intense"):
                        lines.append(f'  tl.to(\'{_w6_am_num}\', {{ textShadow: "{_esc_js(p["title_glow_intense"])}", duration: 0.22 }}, {t_in + 0.30:.4f});')
                    elif p.get("title_glow"):
                        lines.append(f'  tl.to(\'{_w6_am_num}\', {{ textShadow: "{_esc_js(p["title_glow"])}", duration: 0.22 }}, {t_in + 0.30:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_am_ctx}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.28, ease: _eIn }}, {t_in + 0.28:.4f});')
            elif content_style == "contrarian_take":
                _w6_ct_text = f'.card[data-card-id="{card_id}"] #{card_id}-ct-text'
                _w6_ct_rule = f'.card[data-card-id="{card_id}"] #{card_id}-ct-rule'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w6_ct_text}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w6_ct_rule}\', {{ width: "60%" }}, {t_in:.4f});')
                elif is_cinema:
                    # longer pre-appearance pause for suspense
                    lines.append(f'  tl.fromTo(\'{_w6_ct_text}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.80, ease: _eIn }}, {t_in + 0.25:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_ct_rule}\', {{ width: "0%" }}, {{ width: "60%", duration: 0.55, ease: "power2.out" }}, {t_in + 0.75:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w6_ct_text}\', {{ opacity: 0, scale: 0.80 }}, {{ opacity: 1, scale: 1.06, duration: 0.25, ease: "back.out(2.5)" }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w6_ct_text}\', {{ scale: 1, duration: 0.15, ease: "power2.out" }}, {t_in + 0.25:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_ct_rule}\', {{ width: "0%" }}, {{ width: "60%", duration: 0.30, ease: "back.out(1.5)" }}, {t_in + 0.22:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w6_ct_text}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.38, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_ct_rule}\', {{ width: "0%" }}, {{ width: "60%", duration: 0.45, ease: "power2.out" }}, {t_in + 0.25:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w6_ct_text}\', {{ opacity: 0, rotation: -1.5 }}, {{ opacity: 1, rotation: 0, duration: 0.38, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_ct_rule}\', {{ width: "0%" }}, {{ width: "60%", duration: 0.50, ease: "power1.inOut" }}, {t_in + 0.28:.4f});')
                else:  # glass: scale overshoot + glow
                    lines.append(f'  tl.fromTo(\'{_w6_ct_text}\', {{ opacity: 0, scale: 0.90 }}, {{ opacity: 1, scale: 1.03, duration: 0.28, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w6_ct_text}\', {{ scale: 1, duration: 0.18, ease: "power2.out" }}, {t_in + 0.28:.4f});')
                    if p.get("title_glow"):
                        lines.append(f'  tl.to(\'{_w6_ct_text}\', {{ textShadow: "{_esc_js(p["title_glow"])}", duration: 0.22 }}, {t_in + 0.32:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_ct_rule}\', {{ width: "0%" }}, {{ width: "60%", duration: 0.35, ease: "power2.out" }}, {t_in + 0.25:.4f});')
                    if p.get("accent_line_glow"):
                        lines.append(f'  tl.to(\'{_w6_ct_rule}\', {{ boxShadow: "{_esc_js(p["accent_line_glow"])}", duration: 0.20 }}, {t_in + 0.45:.4f});')
            elif content_style == "action_step_cta":
                _w6_asc_text = f'.card[data-card-id="{card_id}"] #{card_id}-asc-text'
                _w6_asc_rule = f'.card[data-card-id="{card_id}"] #{card_id}-asc-rule'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w6_asc_text}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w6_asc_rule}\', {{ width: "100%" }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w6_asc_text}\', {{ opacity: 0, scale: 0.94 }}, {{ opacity: 1, scale: 1, duration: 0.75, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_asc_rule}\', {{ width: "0%" }}, {{ width: "100%", duration: 0.60, ease: "power2.out" }}, {t_in + 0.45:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w6_asc_text}\', {{ opacity: 0, scale: 0.75 }}, {{ opacity: 1, scale: 1.08, duration: 0.25, ease: "back.out(2.5)" }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w6_asc_text}\', {{ scale: 1, duration: 0.15, ease: "power2.out" }}, {t_in + 0.25:.4f});')
                    # flash effect
                    lines.append(f'  tl.to(\'{_w6_asc_text}\', {{ opacity: 0.5, duration: 0.06 }}, {t_in + 0.30:.4f});')
                    lines.append(f'  tl.to(\'{_w6_asc_text}\', {{ opacity: 1, duration: 0.06 }}, {t_in + 0.36:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_asc_rule}\', {{ width: "0%" }}, {{ width: "100%", duration: 0.30, ease: "back.out(1.5)" }}, {t_in + 0.22:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w6_asc_text}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.38, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_asc_rule}\', {{ width: "0%" }}, {{ width: "100%", duration: 0.55, ease: "power2.out" }}, {t_in + 0.22:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w6_asc_text}\', {{ opacity: 0, rotation: -1 }}, {{ opacity: 1, rotation: 0, duration: 0.38, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_asc_rule}\', {{ width: "0%" }}, {{ width: "100%", duration: 0.50, ease: "power1.inOut" }}, {t_in + 0.25:.4f});')
                else:  # glass: pop + pronouned glow + continuous subtle pulse
                    lines.append(f'  tl.fromTo(\'{_w6_asc_text}\', {{ opacity: 0, scale: 0.82 }}, {{ opacity: 1, scale: 1, duration: 0.32, ease: _eIn }}, {t_in:.4f});')
                    if p.get("title_glow_intense"):
                        lines.append(f'  tl.to(\'{_w6_asc_text}\', {{ textShadow: "{_esc_js(p["title_glow_intense"])}", duration: 0.22 }}, {t_in + 0.25:.4f});')
                    elif p.get("title_glow"):
                        lines.append(f'  tl.to(\'{_w6_asc_text}\', {{ textShadow: "{_esc_js(p["title_glow"])}", duration: 0.22 }}, {t_in + 0.25:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_asc_rule}\', {{ width: "0%" }}, {{ width: "100%", duration: 0.40, ease: "power2.out" }}, {t_in + 0.22:.4f});')
                    if p.get("accent_line_glow"):
                        lines.append(f'  tl.to(\'{_w6_asc_rule}\', {{ boxShadow: "{_esc_js(p["accent_line_glow"])}", duration: 0.20 }}, {t_in + 0.45:.4f});')
                    # subtle pulse — yoyo opacity
                    lines.append(f'  tl.to(\'{_w6_asc_text}\', {{ opacity: 0.82, duration: 0.55, ease: "sine.inOut", yoyo: true, repeat: -1 }}, {t_in + 0.65:.4f});')
            elif content_style == "story_chapter_transition":
                _w6_sct_text  = f'.card[data-card-id="{card_id}"] #{card_id}-sct-text'
                _w6_sct_rulea = f'.card[data-card-id="{card_id}"] #{card_id}-sct-rule-a'
                _w6_sct_ruleb = f'.card[data-card-id="{card_id}"] #{card_id}-sct-rule-b'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w6_sct_text}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w6_sct_rulea}\', {{ width: "60%" }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w6_sct_ruleb}\', {{ width: "60%" }}, {t_in:.4f});')
                elif is_cinema:
                    # film scene-transition feel: rules first, then text slow dissolve
                    lines.append(f'  tl.fromTo(\'{_w6_sct_rulea}\', {{ width: "0%" }}, {{ width: "60%", duration: 0.50, ease: "power1.inOut" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_sct_ruleb}\', {{ width: "0%" }}, {{ width: "60%", duration: 0.50, ease: "power1.inOut" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_sct_text}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.90, ease: _eIn }}, {t_in + 0.30:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w6_sct_text}\', {{ opacity: 0, scale: 0.85 }}, {{ opacity: 1, scale: 1, duration: 0.28, ease: "back.out(2.0)" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_sct_rulea}\', {{ width: "0%" }}, {{ width: "60%", duration: 0.32, ease: "power2.out" }}, {t_in + 0.20:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_sct_ruleb}\', {{ width: "0%" }}, {{ width: "60%", duration: 0.32, ease: "power2.out" }}, {t_in + 0.20:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w6_sct_text}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.42, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_sct_rulea}\', {{ width: "0%" }}, {{ width: "60%", duration: 0.45, ease: "power2.out" }}, {t_in + 0.28:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_sct_ruleb}\', {{ width: "0%" }}, {{ width: "60%", duration: 0.45, ease: "power2.out" }}, {t_in + 0.28:.4f});')
                elif is_craft:
                    # notebook-page feel: slight rotation on text
                    lines.append(f'  tl.fromTo(\'{_w6_sct_text}\', {{ opacity: 0, rotation: -1 }}, {{ opacity: 1, rotation: 0, duration: 0.40, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_sct_rulea}\', {{ width: "0%" }}, {{ width: "60%", duration: 0.48, ease: "power1.inOut" }}, {t_in + 0.25:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_sct_ruleb}\', {{ width: "0%" }}, {{ width: "60%", duration: 0.48, ease: "power1.inOut" }}, {t_in + 0.25:.4f});')
                else:  # glass: soft dissolve with glow
                    lines.append(f'  tl.fromTo(\'{_w6_sct_rulea}\', {{ width: "0%" }}, {{ width: "60%", duration: 0.38, ease: "power2.out" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_sct_ruleb}\', {{ width: "0%" }}, {{ width: "60%", duration: 0.38, ease: "power2.out" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w6_sct_text}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.45, ease: _eIn }}, {t_in + 0.22:.4f});')
                    if p.get("title_glow"):
                        lines.append(f'  tl.to(\'{_w6_sct_text}\', {{ textShadow: "{_esc_js(p["title_glow"])}", duration: 0.25 }}, {t_in + 0.45:.4f});')
                    if p.get("accent_line_glow"):
                        lines.append(f'  tl.to(\'{_w6_sct_rulea}\', {{ boxShadow: "{_esc_js(p["accent_line_glow"])}", duration: 0.20 }}, {t_in + 0.35:.4f});')
                        lines.append(f'  tl.to(\'{_w6_sct_ruleb}\', {{ boxShadow: "{_esc_js(p["accent_line_glow"])}", duration: 0.20 }}, {t_in + 0.35:.4f});')
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

            # Per-pack accent-word highlight swipe (fires 0.40s after title animates in).
            # Mirror _split_title_accent(): the id="{card_id}-accent" span only exists
            # when accent_word is found in display_text (= number || title). Skip the
            # tween when the word is absent from the rendered text (avoids GSAP warning).
            _aw = card.get("contentHints", {}).get("accent_word", "")
            _ch_ref = card.get("contentHints", {})
            _display_ref = _ch_ref.get("number") or _ch_ref.get("title", "")
            _accent_in_dom = bool(_aw) and _aw.lower() in _display_ref.lower()
            if _accent_in_dom:
                _aw_sel = f'.card[data-card-id="{card_id}"] #{card_id}-accent'
                lines.extend(_accent_treatment(p, _aw_sel, t_in + 0.40))

            if card.get("contentHints", {}).get("kicker"):
                lines.append(
                    f'  tl.fromTo(\'{kicker_sel}\', '
                    f'{{ opacity: 0, y: -8 }}, '
                    f'{{ opacity: 1, y: 0, duration: 0.250, ease: _eIn }}, '
                    f'{start + 0.10:.4f});'
                )
            # Accent-line shows unless the accent_word swipe is actually rendered —
            # two competing emphasis elements would visually clash.
            if not _accent_in_dom:
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
            if content_style not in ("timeline", "__broll__"):
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
            if content_style not in ("timeline", "news_ticker"):
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

    # ── Per-pack scene transitions ───────────────────────────────────────────
    # Fire at the start of every non-caption graphic card that is spaced >8s
    # from the previous transition (prevents flash-spam on dense sequences).
    _graphic_starts = sorted({
        round(float(c.get("startSec", 0)), 3)
        for c in cards
        if c.get("type") != "caption"
    })
    _transition_times: list[float] = []
    _last_tr = -999.0
    for _ts in _graphic_starts:
        if _ts - _last_tr >= 8.0:
            _transition_times.append(_ts)
            _last_tr = _ts

    pack_id = p.get("id", "lean_glass")
    if _transition_times:
        lines.append("  // ── Scene transitions ──")
        for _tt in _transition_times:
            _t0 = round(_tt, 4)

            if pack_id == "lean_paper":
                # flash-through-white: white overlay flashes briefly
                lines += [
                    f"  tl.fromTo('#broll-transition-overlay',"
                    f"{{opacity:0,background:'#ffffff'}},"
                    f"{{opacity:0.85,duration:0.10,ease:'power2.in'}},"
                    f"{_t0:.4f});",
                    f"  tl.to('#broll-transition-overlay',"
                    f"{{opacity:0,duration:0.25,ease:'power2.out'}},"
                    f"{round(_t0+0.10,4):.4f});",
                ]

            elif pack_id == "lean_vibe":
                # whip-pan: fast x-translate on video + motion blur hack
                lines += [
                    f"  tl.to('#video-wrap',"
                    f"{{x:60,duration:0.08,ease:'power3.in',overwrite:'auto'}},"
                    f"{_t0:.4f});",
                    f"  tl.to('#video-wrap',"
                    f"{{x:0,duration:0.14,ease:'power3.out',overwrite:'auto'}},"
                    f"{round(_t0+0.08,4):.4f});",
                ]

            elif pack_id in ("lean_craft", "lean_cinema"):
                # light-leak: warm amber overlay pulses
                lines += [
                    f"  tl.fromTo('#broll-transition-overlay',"
                    f"{{opacity:0,background:'radial-gradient(ellipse at 30% 50%,"
                    f"rgba(255,180,60,0.70) 0%,transparent 70%)'}},"
                    f"{{opacity:1,duration:0.15,ease:'power1.in'}},"
                    f"{_t0:.4f});",
                    f"  tl.to('#broll-transition-overlay',"
                    f"{{opacity:0,duration:0.35,ease:'power2.out'}},"
                    f"{round(_t0+0.15,4):.4f});",
                ]

            elif pack_id == "lean_ledger":
                # cross-warp-morph: horizontal scan line sweep
                lines += [
                    f"  tl.fromTo('#broll-transition-overlay',"
                    f"{{opacity:0,"
                    f"background:'linear-gradient(180deg,transparent 0%,"
                    f"rgba(0,200,150,0.25) 50%,transparent 100%)',"
                    f"backgroundSize:'100% 6px',backgroundRepeat:'repeat'}},"
                    f"{{opacity:1,backgroundPositionY:'100%',"
                    f"duration:0.30,ease:'none'}},"
                    f"{_t0:.4f});",
                    f"  tl.to('#broll-transition-overlay',"
                    f"{{opacity:0,duration:0.20,ease:'power1.out'}},"
                    f"{round(_t0+0.30,4):.4f});",
                ]

            else:
                # lean_glass → sdf-iris: radial clip-path iris open
                lines += [
                    f"  tl.fromTo('#broll-transition-overlay',"
                    f"{{opacity:1,background:'rgba(0,0,0,0.65)',"
                    f"clipPath:'circle(0% at 50% 50%)'}},"
                    f"{{clipPath:'circle(75% at 50% 50%)',"
                    f"duration:0.35,ease:'power2.out'}},"
                    f"{_t0:.4f});",
                    f"  tl.to('#broll-transition-overlay',"
                    f"{{opacity:0,duration:0.20,ease:'power1.out'}},"
                    f"{round(_t0+0.35,4):.4f});",
                ]
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




def _grain_opacity(pack: dict) -> str:
    pid = pack.get("id", "lean_glass")
    return {"lean_cinema": "0.18", "lean_craft": "0.14", "lean_glass": "0.08",
            "lean_vibe": "0.06", "lean_ledger": "0.07", "lean_paper": "0.0"}.get(pid, "0.07")


def _grain_svg(pack: dict) -> str:
    """Return the grain SVG data-URI already defined for this pack's grain_type."""
    grain_type = pack.get("grain_type", "")
    return {
        "confetti": _CONFETTI_SVG,
        "grid": _GRID_SVG,
        "paper": _PAPER_GRAIN_SVG,
        "film": _FILM_GRAIN_SVG,
    }.get(grain_type, _GRAIN_SVG)


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

        # Portrait anti-collision: caption band is always 70–85%; data cards
        # must never share that band.  Force all portrait data cards to upper-data
        # (20–40%) regardless of what Claude requested — zone separation is the
        # anti-collision mechanism, no temporal scheduling needed.
        if layout == "portrait" and style in _DATA_PANEL_TYPES:
            if zone != "upper-data":
                print(
                    f"[COMPOSE] portrait-zone: {card.get('id', '?')} ({style})"
                    f" {zone!r} → 'upper-data' (anti-collision)",
                    flush=True,
                )
                return {**card, "zone": "upper-data"}
            return card

        # Landscape: remap center-zone data cards to face-aware side panel.
        if style not in _DATA_PANEL_TYPES or zone not in _CENTER_ZONES:
            return card
        if layout == "landscape":
            new_zone = "side-panel-left" if _face_cx > 50.0 else "side-panel-right"
        else:
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
    card_hosts: list[str] = []
    _rendered_cards: list[dict] = []
    for c in all_cards:
        _missing = [k for k in ("id", "startSec", "endSec") if k not in c]
        if _missing:
            print(
                f"[COMPOSE] WARNING: malformed card skipped — missing fields {_missing}: {c}",
                flush=True,
            )
            continue
        track = 3 if c.get("type") == "caption" else 2
        try:
            card_hosts.append(_build_card_host(c, layout, track_index=track, pack=pack))
            _rendered_cards.append(c)
        except Exception as _card_exc:
            print(
                f"[COMPOSE] WARNING: card render error — skipping id={c.get('id', '?')}: {_card_exc} | card={c}",
                flush=True,
            )
    all_cards = _rendered_cards

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
    <div id="broll-transition-overlay" style="position:absolute;inset:0;z-index:18;pointer-events:none;opacity:0;"></div>
    <div id="grain-overlay" style="position:absolute;inset:0;z-index:7;pointer-events:none;opacity:{_grain_opacity(pack)};background-image:url('{_grain_svg(pack)}');background-repeat:repeat;mix-blend-mode:overlay;"></div>

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
