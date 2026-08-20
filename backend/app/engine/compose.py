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
    # 5-position rotation zones for landscape (proportionally adapted for 16:9).
    # Standard data cards — compact mode.
    "landscape-tl":      {"left": 40,   "top": 40,  "width": 660, "height": 300},   # top-left
    "landscape-tr":      {"left": 1220, "top": 40,  "width": 660, "height": 300},   # top-right
    "landscape-cl":      {"left": 40,   "top": 380, "width": 660, "height": 280},   # center-left
    "landscape-cr":      {"left": 1220, "top": 380, "width": 660, "height": 280},   # center-right
    "landscape-cf":      {"left": 0,    "top": 380, "width": 1920, "height": 300},  # center-full (dimming)
    # Tall multi-item data cards in landscape — 2-position top cycle, extra height.
    "landscape-tl-tall": {"left": 40,   "top": 40,  "width": 660, "height": 500},
    "landscape-tr-tall": {"left": 1220, "top": 40,  "width": 660, "height": 500},
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
    "upper-data":           {"left": 270, "top": 340,  "width": 780,  "height": 480},   # alias
    # Positional-variety zones — standard data cards alternate left/right per card index
    # (face-biased when subject_position available) so consecutive cards never land in the
    # same corner.  upper-data (right) already exists above; upper-left-data-sm mirrors it.
    "upper-left-data-sm":   {"left": 20,  "top": 340,  "width": 660,  "height": 480},   # standard, left side
    # Multi-item data cards (4-6 rows each) need more height than upper-data's 320 px and
    # more left-margin than upper-data's 40 px right gap.  Two mirrors: left (default when
    # face is right) and right (for left-biased faces, safe because 28 px compact font
    # keeps 22-char items at 339 px < 372 px text area — no wrapping).
    "upper-left-data":      {"left": 30,  "top": 80,   "width": 540,  "height": 500},   # tall multi-item, left side
    "upper-right-data-tall": {"left": 540, "top": 80,  "width": 500,  "height": 500},   # tall multi-item, right side
    # 5-position rotation — center zones (face zone, 34–50% height). Backdrop-dim applied.
    "portrait-center-left":  {"left": 20,  "top": 660, "width": 600, "height": 420},
    "portrait-center-right": {"left": 480, "top": 660, "width": 580, "height": 420},
    "portrait-center-full":  {"left": 40,  "top": 640, "width": 1000, "height": 360},
    # Legacy bottom zones (kept for backward compat, not used in the rotation sequence).
    "portrait-bottom-left":  {"left": 30,  "top": 1070, "width": 500, "height": 250},
    "portrait-bottom-right": {"left": 540, "top": 1070, "width": 500, "height": 250},
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
_DATA_PANEL_TYPES = {"stat", "list", "comparison", "checklist", "score", "trend", "rating", "progress_bar", "countdown", "step_number", "price_tag", "recap_summary", "formula_equation", "pros_cons", "star_rating_review", "income_reveal", "data_bar_chart", "number_ranking", "question_answer_pair", "cause_effect", "percentage_split", "red_flag_list", "client_avatar_persona", "tool_stack", "revenue_breakdown", "hidden_cost_reveal", "social_proof_counter", "red_thread_connector", "day_in_life_schedule", "skill_tree_unlock", "audience_poll_result", "broken_promise_tracker", "ingredient_list", "resource_allocation",
    "streak_counter", "before_now_later", "platform_stats",
    "cost_comparison", "decision_matrix", "habit_tracker", "income_vs_expense",
    "milestone_recap", "content_calendar", "client_result_number",
    "mistake_lesson", "tool_comparison", "weekly_review", "audience_question"}
_CENTER_ZONES = {"fullscreen", "video-overlay"}
_SIDE_PANEL_ZONES = {"side-panel", "side-panel-left", "side-panel-right", "side-panel-top", "upper-data", "upper-right", "upper-left-data", "upper-left-data-sm", "upper-right-data-tall", "portrait-bottom-left", "portrait-bottom-right", "portrait-center-left", "portrait-center-right", "landscape-tl", "landscape-tr", "landscape-cl", "landscape-cr", "landscape-cf", "landscape-tl-tall", "landscape-tr-tall"}
# Zones where the backdrop-dim overlay fires — card overlaps the speaker face.
# Portrait centre zones added as safety net: backdrop-dim fires IN ADDITION to
# per-card portrait scrims, ensuring consistent dimming regardless of face detection.
# Upper landscape zones (landscape-tl/tr) intentionally excluded — they sit above the
# speaker and dimming would cover unrelated content.
_DIMMING_ZONES = frozenset({
    "fullscreen", "video-overlay",
    "landscape-cf",
    "portrait-center-full", "portrait-center-left", "portrait-center-right",
})

# Subset of _DATA_PANEL_TYPES that render 4-6 stacked rows.  In portrait these are
# remapped to upper-left-data (left:30, height:500) instead of the standard upper-data
# (left:540, height:320) which is too narrow (40 px right margin) and too short.
_TALL_DATA_PANEL_TYPES = frozenset({
    "day_in_life_schedule", "ingredient_list", "skill_tree_unlock",
    "audience_poll_result", "broken_promise_tracker", "resource_allocation",
    "milestone_recap", "content_calendar", "tool_comparison", "weekly_review",
})


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

    # Compact flag is based on the original zone (before any centering redirect).
    # This preserves compact styling for data-panel cards that were in side-panel
    # zones even when we redirect them to portrait-center-full for positioning.
    compact = (not is_caption) and (zone in _SIDE_PANEL_ZONES)

    print(
        f"[COMPOSE] card {card_id} type={card.get('type','?')} zone={zone}"
        f" compact={compact} layout={layout}",
        flush=True,
    )

    # Portrait centering: every zone → portrait-center-full, EXCEPT:
    # - portrait-center-left/right (set by face-aware _remap_zone rotation)
    # - portrait-center-full (already correct)
    # - fullscreen for full-cover primitives (prim_split_compare, prim_journey_map)
    #
    # CRITICAL: fullscreen and video-overlay must NOT pass through for hero cards —
    # their lean_glass background (85% opaque) would cover the entire canvas and hide
    # the video source. Only full-cover primitives that need the complete canvas are exempt.
    if layout == "portrait" and not is_caption:
        _is_portrait_full_cover = card.get("contentHints", {}).get("style", "") in (
            "prim_split_compare", "prim_journey_map", "prim_cinematic_reveal",
            "prim_ascension_reveal", "prim_shatter_truth", "prim_split_stage",
            "prim_confession_frame",
        )
        if not _is_portrait_full_cover and zone not in (
            "portrait-center-full", "portrait-center-left", "portrait-center-right"
        ):
            zone = "portrait-center-full"

    bounds = _zone_bounds(zone, layout)

    # Dynamic zone height for tall multi-item data cards in portrait.
    # Avoids fixed 500px that is either too short (8+ items) or wastes space.
    # Per-item estimate: 28px compact font × 1.4 line-height ≈ 39px + 6px gap = 45px/row.
    # Panel v-padding: 56px. Root v-padding: 64px. Title/kicker row: 40px.
    if not is_caption:
        _dyn_style = card.get("contentHints", {}).get("style", "")
        if _dyn_style in _TALL_DATA_PANEL_TYPES:
            _dyn_hints = card.get("contentHints", {})
            _items_key = {
                "day_in_life_schedule": "schedule_items",
                "ingredient_list": "ingredients",
                "skill_tree_unlock": "unlocked_milestones",
                "audience_poll_result": "poll_options",
                "broken_promise_tracker": "promises",
                "resource_allocation": "resource_labels",
                "milestone_recap": "milestones",
                "content_calendar": "calendar_items",
                "tool_comparison": "tool_features",
                "weekly_review": "review_categories",
            }.get(_dyn_style, "items")
            _n_items = len(_dyn_hints.get(_items_key, _dyn_hints.get("items", [])))
            _n_items = max(1, min(_n_items, 12))
            _dyn_h = _n_items * 45 + 160
            _dyn_h = max(160, min(_dyn_h, 700))
            bounds = {**bounds, "height": _dyn_h}
            print(
                f"[COMPOSE] tall-dyn-height {card.get('id', '?')} ({_dyn_style})"
                f" n={_n_items} -> {_dyn_h}px",
                flush=True,
            )
        # Dual-text hero types: portrait-center zones (height 360-420px) overflow
        # when two full-size text blocks together exceed the container height.
        # Dynamic height prevents symmetric clipping at both top and bottom.
        _DUAL_TEXT_HERO_TYPES = frozenset({"myth_vs_fact", "objection_response"})
        if _dyn_style in _DUAL_TEXT_HERO_TYPES:
            _dyn_hints = card.get("contentHints", {})
            if _dyn_style == "myth_vs_fact":
                _tc = (len(_dyn_hints.get("myth_text", ""))
                       + len(_dyn_hints.get("fact_text", "")))
            else:
                _tc = (len(_dyn_hints.get("objection_text", ""))
                       + len(_dyn_hints.get("response_text", "")))
            # ~5px per char at 64px font (2 blocks) + 300px base (padding + gaps + badge).
            # Base 300 ensures even very short combined texts (≤30 chars) have room.
            _dyn_h = max(360, min(_tc * 5 + 300, 680))
            bounds = {**bounds, "height": _dyn_h}
            print(
                f"[COMPOSE] dual-text-dyn-height {card.get('id', '?')} ({_dyn_style})"
                f" chars={_tc} -> {_dyn_h}px",
                flush=True,
            )
        # number_hero: centered spotlight + mirror lines — 420px for breathing room.
        if _dyn_style == "number_hero":
            bounds = {**bounds, "height": 420}
            print(
                f"[COMPOSE] number-hero-height {card.get('id', '?')} -> 420px",
                flush=True,
            )

    if is_caption:
        inner = _build_caption_card_html(card, pack=pack, layout=layout)
    else:
        inner = _build_graphic_card_html(card, pack=pack, compact=compact, layout=layout)

    # Portrait scrim: full-canvas dimming overlay per card, sibling to card-host.
    # Explicit != "caption" check: generative cards omit the type field entirely
    # so not is_caption (derived from == "caption") is equivalent, but this is clearer.
    scrim_html = ""
    if layout == "portrait" and card.get("type") != "caption":
        scrim_html = (
            f'<div class="portrait-scrim" id="{card_id}-scrim" '
            f'style="position:absolute;left:0;top:0;width:1080px;height:1920px;'
            f'background:rgba(0,0,0,0.45);opacity:0;z-index:5;pointer-events:none;"></div>\n'
        )

    card_host = (
        f'<div class="card-host clip" data-card-id="{card_id}" '
        f'data-start="{start:.3f}" data-duration="{duration:.3f}" '
        f'data-track-index="{track_index}" '
        f'style="left:{bounds["left"]}px;top:{bounds["top"]}px;'
        f'width:{bounds["width"]}px;height:{bounds["height"]}px;'
        f'visibility:hidden;opacity:0;z-index:{20 if is_caption else 10};">\n'
        f'{inner}\n'
        f'</div>'
    )
    return scrim_html + card_host


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
    # Climax primitives: dark canvas with accent radial bloom at upper-centre (Direction B — cold)
    "bg_full": "radial-gradient(ellipse 65% 52% at 50% 44%, rgba(76,201,240,0.16) 0%, rgba(76,201,240,0.05) 44%, #04040E 72%)",
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
    "border": "1px solid rgba(0,0,0,0.16)",
    "radius": "12px",
    "shadow": "0 0 60px rgba(79,107,255,0.10), 0 2px 8px rgba(0,0,0,0.08), 0 8px 24px rgba(0,0,0,0.12), 0 20px 60px rgba(0,0,0,0.07)",
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
    # Climax primitives: dark canvas with blue radial bloom at upper-centre (Direction B — cold)
    "bg_full": "radial-gradient(ellipse 65% 52% at 50% 44%, rgba(79,107,255,0.16) 0%, rgba(79,107,255,0.05) 44%, #04041A 72%)",
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
    "border": "3px solid rgba(255,255,255,0.18)",
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
    # Climax primitives: deep warm dark with orange-to-yellow sol vif from bottom (Direction C — warm)
    "bg_full": "radial-gradient(ellipse 80% 58% at 50% 90%, rgba(255,140,60,0.28) 0%, rgba(255,80,30,0.12) 42%, #080402 70%)",
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
    # Climax primitives: dark forest with green radial bloom at upper-centre (Direction B — cold)
    "bg_full": "radial-gradient(ellipse 65% 52% at 50% 44%, rgba(0,200,150,0.14) 0%, rgba(0,200,150,0.04) 44%, #030A08 72%)",
}

_LEAN_CRAFT = {
    "id": "lean_craft",
    "bg": "#E8D9C5",
    "text": "#3D2B1F",
    "text_secondary": "rgba(61,43,31,0.55)",
    "accent": "#D97757",
    "font": '"Montserrat", "Helvetica Neue", Arial, sans-serif',
    "font_detail": '"Inter", ui-sans-serif, system-ui, sans-serif',
    "font_weight": "700",
    "title_size": "64px",
    "number_size": "90px",
    "kicker_size": "20px",
    "detail_size": "22px",
    "border": "1.5px solid rgba(217,119,87,0.25)",
    "radius": "12px 8px 10px 14px",
    "shadow": "0 0 60px rgba(217,119,87,0.20), 0 3px 16px rgba(61,43,31,0.28), 0 8px 32px rgba(61,43,31,0.12)",
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
    # Climax primitives: deep warm dark with terracotta sol vif from bottom (Direction C — warm)
    "bg_full": "radial-gradient(ellipse 80% 58% at 50% 90%, rgba(217,119,87,0.24) 0%, rgba(170,60,20,0.09) 42%, #060302 70%)",
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
    # Climax primitives: deep cinematic dark with gold sol vif from bottom (Direction C — warm)
    "bg_full": "radial-gradient(ellipse 80% 58% at 50% 90%, rgba(201,168,106,0.22) 0%, rgba(140,90,20,0.08) 42%, #050402 70%)",
}

_PACKS = {"lean_glass": _LEAN_GLASS, "lean_paper": _LEAN_PAPER, "lean_vibe": _LEAN_VIBE, "lean_ledger": _LEAN_LEDGER, "lean_craft": _LEAN_CRAFT, "lean_cinema": _LEAN_CINEMA}

# Per-pack hero punch-in parameters ({scale, in_dur, in_ease, out_dur, out_ease}).
# lean_paper: None → no punch-in; clean/minimal aesthetic.
_PUNCH_IN_PARAMS: dict = {
    "lean_glass":  {"scale": 1.015, "in_dur": 0.40, "in_ease": "power2.in",          "out_dur": 0.40, "out_ease": "power2.out"},
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


def _build_graphic_card_html(card: dict, pack: dict | None = None, compact: bool = False, layout: str = "portrait") -> str:
    """Build inner HTML for a graphic overlay card using the given style pack."""
    card_id = card["id"]

    hints = card.get("contentHints", {})
    kicker = hints.get("kicker", "")
    title = hints.get("title", "")
    detail = hints.get("detail", "")
    number = hints.get("number", "")
    p = pack or _LEAN_GLASS

    # Compact variant for side-panel zones — scale typography and tighten padding so
    # content fits the container without awkward wrapping or overflow.
    # Tall multi-item types (4-6 stacked rows, upper-left-data zone) use a tighter
    # 0.45× scale so long items like "12h - Pause déjeuner" (22 chars) don't wrap
    # in the 540px-wide container (text area ≈ 374px; 22 chars @ 29px ≈ 352px → fits).
    content_style = hints.get("style", "")
    if compact:
        def _s(px_str: str, f: float) -> str:
            return f"{int(float(px_str.replace('px', '')) * f)}px"
        if layout == "portrait":
            _title_scale    = 0.55 if content_style in _TALL_DATA_PANEL_TYPES else 0.80
        else:
            _title_scale    = 0.52 if content_style in _TALL_DATA_PANEL_TYPES else 0.75
        title_size_eff  = _s(p["title_size"],  _title_scale)
        number_size_eff = _s(p["number_size"], 0.67)
        detail_size_eff = "25px" if layout == "portrait" else "23px"
        kicker_size_eff = "20px" if layout == "portrait" else "18px"
        list_item_size  = "21px"
        chk_item_size   = "20px"
        panel_padding   = "28px 32px"
        root_padding    = "32px"
        text_align      = "left"
        panel_align     = "flex-start"
        max_width_eff   = "92%"
        # before_after_image: adaptive ba-text size in compact landscape (zone 660×300px)
        if content_style == "before_after_image" and layout == "landscape":
            _tc = len(hints.get("before_label", "")) + len(hints.get("after_label", ""))
            if _tc > 50:
                title_size_eff = "20px"
            elif _tc > 30:
                title_size_eff = "24px"
            elif _tc > 15:
                title_size_eff = "28px"
            else:
                title_size_eff = "32px"
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
        # Adaptive title size — prevent vertical overflow for long texts.
        # key_phrase/quote are hero cards: their text must stay large (≥48px) so they
        # dominate the frame. Other card types use a tighter reduction curve.
        if title and not number:
            _tc = len(title)
            if content_style in ("key_phrase", "quote"):
                if _tc > 60:
                    title_size_eff = "48px"
                elif _tc > 40:
                    title_size_eff = "56px"
                # ≤40 chars: keep 64px default
            else:
                if _tc > 55:
                    title_size_eff = "32px"
                elif _tc > 35:
                    title_size_eff = "38px"
                elif _tc > 20:
                    title_size_eff = "56px"
        # Dual-text-block types: myth_vs_fact and objection_response render two
        # separate text blocks both using title_size_eff. Their combined length
        # drives vertical height, not the title field — apply separate reduction.
        if content_style == "myth_vs_fact":
            _tc = len(hints.get("myth_text", "")) + len(hints.get("fact_text", ""))
            if _tc > 80:
                title_size_eff = "38px"
            elif _tc > 50:
                title_size_eff = "48px"
        elif content_style == "objection_response":
            _tc = len(hints.get("objection_text", "")) + len(hints.get("response_text", ""))
            if _tc > 80:
                title_size_eff = "38px"
            elif _tc > 50:
                title_size_eff = "48px"
        elif content_style == "before_after_image":
            _tc = len(hints.get("before_label", "")) + len(hints.get("after_label", ""))
            if _tc > 60:
                title_size_eff = "32px"
            elif _tc > 40:
                title_size_eff = "38px"
            elif _tc > 20:
                title_size_eff = "48px"

    display_text = number if number else title
    title_size   = number_size_eff if number else title_size_eff

    shadow_val = f'{p["shadow"]}, {p["shadow_inset"]}' if p["shadow_inset"] else p["shadow"]
    parts = [f'<div class="card" data-card-id="{card_id}">']
    parts.append('<style>')
    # Apply border-radius to the .card element itself so the global overflow:hidden clips
    # content AND box-shadow at a rounded boundary (not a rectangle).
    # This fixes shadow-clipping inconsistency: compact cards with wide panels (e.g.
    # warning_soft, action_step_cta) have only ~21px clearance to the card-host edge,
    # so a 60px box-shadow would be cut sharply at a right angle without this.
    # Excluded: full-cover styles where .card fills the entire 1920×1080 canvas.
    _full_cover_styles = frozenset({
        "prim_split_stage", "prim_anecdote_frame", "prim_journey_map",
        "prim_cinematic_reveal", "prim_ascension_reveal", "prim_shatter_truth",
        "prim_numbered_rule",
        # prim_split_compare and prim_confession_frame removed: their .card-panel
        # fills the card area but the .card itself must still receive border-radius
        # so the card boundary is rounded like all other catalogue cards.
    })
    if p.get("radius") and p["radius"] not in ("0px", "0") and content_style not in _full_cover_styles:
        parts.append(f'.card[data-card-id="{card_id}"] {{ border-radius: {p["radius"]}; overflow: hidden; }}')
    parts.append(f'.card[data-card-id="{card_id}"] .root {{')
    parts.append('  width: 100%; height: 100%; display: flex; flex-direction: column;')
    _root_justify = "flex-start" if (compact and content_style in _TALL_DATA_PANEL_TYPES) else "center"
    parts.append(f'  justify-content: {_root_justify}; align-items: center;')
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
    if p.get("id") == "lean_cinema":
        # Radial-gradient dissolve: panel fades into the video — text emerges from the scene.
        # box-shadow suppressed; a hard shadow rectangle would outline the transparent edges.
        parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{')
        parts.append('  background: radial-gradient(ellipse 85% 80% at 50% 50%,')
        parts.append('    rgba(13,13,13,0.95) 22%,')
        parts.append('    rgba(13,13,13,0.78) 50%,')
        parts.append('    rgba(13,13,13,0.32) 75%,')
        parts.append('    transparent 100%);')
        parts.append('  box-shadow: none;')
        parts.append('}')
    if compact and p.get("id") == "lean_glass":
        # Compact zone (landscape-tl, 660px wide): panel right edge at ~639px leaves only 21px
        # before the card boundary. The default 60px blur spills 39px past → clips in a straight
        # line even with rounded card corners. Reduce to 16px so the glow fades within bounds.
        parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{')
        parts.append('  box-shadow: 0 0 16px rgba(76,201,240,0.22), 0 4px 12px rgba(0,0,0,0.45);')
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
    parts.append(f'  border-radius: 999px; box-shadow: {p["accent_line_glow"]};')
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
        parts.append(f'  flex: 1; text-align: center; min-width: 0; max-width: 50%; overflow: hidden; overflow-wrap: break-word;')
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
        parts.append(f'  border-radius: 999px; flex-shrink: 0;')
        if p["title_glow"]:
            parts.append(f'  box-shadow: {p["accent_line_glow"]};')
        parts.append('}')
        # Force definite width on .card-panel so flex children can resolve width:100%.
        # Without this, align-items:center in .root causes the panel to intrinsically size
        # to text max-content, making overflow-wrap:break-word never trigger on .cmp-value.
        parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{ width: 100%; box-sizing: border-box; }}')
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
            parts.append(f'  display: flex; flex-direction: column; gap: 28px; width: 100%;')
            parts.append(f'  position: relative; padding: 20px 0;')
            parts.append('}')
            parts.append(f'.card[data-card-id="{card_id}"] .tl-line {{')
            parts.append(f'  position: absolute; left: 9px; top: 0; width: 3px; height: 0;')
            parts.append(f'  background: {p["accent"]};')
            if is_paper_tl:
                parts.append(f'  border-left: 2px dashed {p["accent"]};')
                parts.append(f'  background: transparent; width: 0;')
            parts.append('}')
            parts.append(f'.card[data-card-id="{card_id}"] .tl-step {{')
            parts.append(f'  display: flex; align-items: center; gap: 20px; z-index: 1;')
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
        parts.append(f'  font-family: {p["font"]}; font-size: 20px; line-height: 1.4;')
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
    # News-ticker: full-width horizontal crawl bar
    if content_style == "news_ticker":
        bg_solid = p.get("bg", "#0f0f13") if "gradient" not in p.get("bg","") else "#0f0f13"
        parts.append(f'.card[data-card-id="{card_id}"] .ticker-wrap {{')
        parts.append(f'  width:100%; height:100%; display:flex; align-items:center;')
        parts.append(f'  background:{bg_solid}; overflow:hidden; position:relative;')
        parts.append('}')
        _ticker_br = p["radius"].split()[0]
        parts.append(f'.card[data-card-id="{card_id}"] .ticker-label {{')
        parts.append(f'  flex-shrink:0; padding:0 20px; font-family:{p["font"]};')
        parts.append(f'  font-size:{("14px" if compact else "16px")}; font-weight:800;')
        parts.append(f'  color:{p["bg"] if "gradient" not in p.get("bg","") else "#0f0f13"};')
        parts.append(f'  background:{p["accent"]}; height:100%;')
        parts.append(f'  display:flex; align-items:center;')
        parts.append(f'  white-space:nowrap; letter-spacing:0.10em; text-transform:uppercase;')
        parts.append(f'  z-index:2; border-radius:0 {_ticker_br} {_ticker_br} 0;')
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
        parts.append('  justify-content:center; gap:10px; padding:18px; opacity:0; overflow:hidden; min-width:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ba-badge {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:800; letter-spacing:0.15em; text-transform:uppercase; color:{p["accent"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ba-text {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center;')
        parts.append('  overflow-wrap:break-word; word-break:break-word; width:100%;')
        parts.append('}')
        # Force definite width on .card-panel so flex children can resolve width:100%.
        # Without this, align-items:center in .root causes the panel to intrinsically size
        # to text max-content, making overflow-wrap:break-word never trigger on .ba-text.
        parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{ width: 100%; box-sizing: border-box; }}')
        if p["id"] == "lean_craft":
            parts.append(f'.card[data-card-id="{card_id}"] .ba-div {{')
            parts.append('  width:24px; flex-shrink:0; display:flex; align-items:center; justify-content:center;')
            parts.append('}')
        else:
            parts.append(f'.card[data-card-id="{card_id}"] .ba-div {{')
            parts.append(f'  width:3px; background:{p["accent"]}; align-self:stretch; flex-shrink:0;')
            parts.append('  border-radius:999px; margin:4px 0;')
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
        _ws_gap = "10px" if compact else "16px"
        parts.append(f'  display:flex; flex-direction:column; align-items:center; gap:{_ws_gap};')
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
        parts.append(f'  display:block; font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]};')
        parts.append('  text-align:center; max-width:90px; overflow-wrap:break-word;')
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
        parts.append('  border-radius:999px; margin:4px 0;')
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
        parts.append(f'  width:0; height:2px; background:{p["accent"]}; border-radius:999px;')
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
        parts.append(f'  width:0; height:3px; background:{p["accent"]}; border-radius:999px;')
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
        parts.append(f'  width:100%; height:2px; background:{p["accent"]}; border-radius:999px; transform:scaleX(0); transform-origin:left;')
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
    if content_style in ("data_bar_chart", "data_chart"):
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
        parts.append(f'  width:0; height:2px; background:{p["accent"]}; margin-top:8px; border-radius:999px;')
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
        _am_sz = "52px" if (compact and layout == "landscape") else "72px" if compact else "96px"
        parts.append(f'  font-family:{p["font"]}; font-size:{_am_sz};')
        parts.append(f'  font-weight:900; color:{p["accent"]}; line-height:1; text-align:center; opacity:0;')
        if p["title_glow_intense"]:
            parts.append(f'  text-shadow:{p["title_glow_intense"]};')
        elif p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        if compact and layout == "landscape":
            _am_ctx_tc = len(hints.get("age_context", hints.get("detail", "")))
            _am_ctx_sz = "20px" if _am_ctx_tc > 40 else "24px" if _am_ctx_tc > 25 else title_size_eff
        else:
            _am_ctx_sz = title_size_eff
        parts.append(f'.card[data-card-id="{card_id}"] .am-ctx {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_am_ctx_sz};')
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
        parts.append(f'  width:0; height:2px; background:{p["accent"]}; border-radius:999px;')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
    if content_style == "action_step_cta":
        parts.append(f'.card[data-card-id="{card_id}"] .asc-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:10px; width:100%;')
        parts.append('}')
        _asc_sz = title_size_eff if compact else number_size_eff
        parts.append(f'.card[data-card-id="{card_id}"] .asc-text {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_asc_sz};')
        parts.append(f'  font-weight:900; color:{p["text"]}; text-align:center; opacity:0;')
        if p["title_glow_intense"]:
            parts.append(f'  text-shadow:{p["title_glow_intense"]};')
        elif p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .asc-rule {{')
        parts.append(f'  width:0; height:3px; background:{p["accent"]}; border-radius:999px;')
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
        parts.append(f'  width:0; height:1px; background:{p["accent"]}; border-radius:999px;')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
    # ── Wave 7 CSS ────────────────────────────────────────────────────────────
    if content_style == "live_reaction_split":
        parts.append(f'.card[data-card-id="{card_id}"] .lrs-wrap {{')
        parts.append('  display:flex; align-items:stretch; gap:0; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .lrs-side {{')
        parts.append('  flex:1; display:flex; flex-direction:column; gap:6px; padding:14px; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .lrs-lbl {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text_secondary"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .lrs-txt {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]};')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .lrs-divider {{')
        parts.append(f'  width:2px; background:{p["accent"]}; align-self:stretch; opacity:0;')
        parts.append('  border-radius:999px; margin:4px 0;')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
    if content_style == "hidden_cost_reveal":
        parts.append(f'.card[data-card-id="{card_id}"] .hcr-wrap {{')
        parts.append('  display:flex; align-items:center; justify-content:center; gap:20px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .hcr-block {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:4px; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .hcr-lbl {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text_secondary"]};')
        parts.append('}')
        _hcr_sz = "36px" if compact else "44px"
        parts.append(f'.card[data-card-id="{card_id}"] .hcr-val {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_hcr_sz};')
        parts.append(f'  font-weight:900; color:{p["text"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .hcr-stk-val {{ text-decoration:line-through; opacity:0.55; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .hcr-real-val {{ color:{p["accent"]}; }}')
        _hcr_arr_sz = "22px" if compact else "28px"
        parts.append(f'.card[data-card-id="{card_id}"] .hcr-arrow {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_hcr_arr_sz};')
        parts.append(f'  color:{p["accent"]}; opacity:0;')
        parts.append('}')
    if content_style == "social_proof_counter":
        parts.append(f'.card[data-card-id="{card_id}"] .spc-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:10px; width:100%;')
        parts.append('}')
        _spc_sz = "64px" if compact else "88px"
        parts.append(f'.card[data-card-id="{card_id}"] .spc-num {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_spc_sz};')
        parts.append(f'  font-weight:900; color:{p["accent"]}; line-height:1; opacity:0;')
        if p["title_glow_intense"]:
            parts.append(f'  text-shadow:{p["title_glow_intense"]};')
        elif p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .spc-lbl {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; opacity:0;')
        parts.append('}')
    if content_style == "timeline_prediction":
        parts.append(f'.card[data-card-id="{card_id}"] .tp-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:8px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tp-sec-lbl {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:700; color:{p["text_secondary"]}; text-transform:uppercase; letter-spacing:0.05em;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tp-conf {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tp-div {{')
        parts.append(f'  height:0; border-top:2px dashed {p["accent"]}; opacity:0; width:100%; margin:4px 0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tp-pred {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text_secondary"]}; font-style:italic; opacity:0;')
        parts.append('}')
    if content_style == "red_thread_connector":
        parts.append(f'.card[data-card-id="{card_id}"] .rtc-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:6px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .rtc-pt {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]};')
        parts.append(f'  padding:8px 18px; border:1.5px solid {p["accent"]}; border-radius:{p["radius"]};')
        parts.append('  text-align:center; width:100%; box-sizing:border-box; opacity:0;')
        parts.append('}')
        _rtc_arr_sz = "18px" if compact else "22px"
        parts.append(f'.card[data-card-id="{card_id}"] .rtc-arr {{')
        parts.append(f'  font-size:{_rtc_arr_sz}; color:{p["accent"]}; opacity:0;')
        parts.append('}')
    if content_style == "silent_beat_pause":
        parts.append(f'.card[data-card-id="{card_id}"] .sbp-wrap {{')
        parts.append('  display:flex; align-items:center; justify-content:center; width:100%;')
        parts.append('}')
        _sbp_sz = "32px" if compact else "48px"
        parts.append(f'.card[data-card-id="{card_id}"] .sbp-sym {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_sbp_sz};')
        parts.append(f'  color:{p["text_secondary"]}; letter-spacing:0.3em; opacity:0;')
        parts.append('}')
    if content_style == "comment_reply_style":
        parts.append(f'.card[data-card-id="{card_id}"] .crs-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:10px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .crs-comment {{')
        parts.append(f'  display:flex; flex-direction:column; gap:4px;')
        parts.append(f'  padding:10px 14px; border-radius:{p["radius"]};')
        parts.append(f'  background:{p["bg"]}; border:1px solid rgba(255,255,255,0.08); opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .crs-reply {{')
        parts.append(f'  display:flex; flex-direction:column; gap:4px;')
        parts.append(f'  padding:10px 14px; border-radius:{p["radius"]};')
        parts.append(f'  background:{p["bg"]}; margin-left:24px;')
        parts.append(f'  border-left:3px solid {p["accent"]}; opacity:0;')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:-2px 0 8px {p["accent"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .crs-meta {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:700; color:{p["text_secondary"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .crs-txt {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .crs-rtxt {{ color:{p["accent"]}; }}')
    if content_style == "before_you_scroll":
        parts.append(f'.card[data-card-id="{card_id}"] .bys-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:10px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .bys-txt {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:900; color:{p["text"]}; text-align:center; opacity:0;')
        if p["title_glow_intense"]:
            parts.append(f'  text-shadow:{p["title_glow_intense"]};')
        elif p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .bys-rule {{')
        parts.append(f'  width:0; height:3px; background:{p["accent"]}; border-radius:999px;')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
    # ── Wave 8 CSS ────────────────────────────────────────────────────────────
    if content_style == "traffic_light_status":
        parts.append(f'.card[data-card-id="{card_id}"] .tls-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:12px; width:100%;')
        parts.append('}')
        _tls_sz = "52px" if compact else "72px"
        parts.append(f'.card[data-card-id="{card_id}"] .tls-light {{')
        parts.append(f'  width:{_tls_sz}; height:{_tls_sz}; border-radius:50%;')
        parts.append(f'  opacity:0; transform:scale(0.7);')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tls-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center; opacity:0;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
    if content_style == "day_in_life_schedule":
        parts.append(f'.card[data-card-id="{card_id}"] .dls-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:6px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .dls-item {{')
        parts.append(f'  display:flex; align-items:center; gap:10px;')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; opacity:0;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .dls-dot {{')
        parts.append(f'  width:8px; height:8px; border-radius:50%; background:{p["accent"]}; flex-shrink:0;')
        parts.append('}')
    if content_style == "skill_tree_unlock":
        parts.append(f'.card[data-card-id="{card_id}"] .stu-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:8px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .stu-item {{')
        parts.append(f'  display:flex; align-items:center; gap:10px;')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]};')
        parts.append(f'  padding:6px 12px; border:1.5px solid {p["accent"]}; border-radius:{p["radius"]};')
        parts.append(f'  opacity:0;')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .stu-icon {{')
        parts.append(f'  font-size:{kicker_size_eff}; color:{p["accent"]}; flex-shrink:0;')
        parts.append('}')
    if content_style == "audience_poll_result":
        parts.append(f'.card[data-card-id="{card_id}"] .apr-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:8px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .apr-row {{')
        parts.append(f'  display:flex; flex-direction:column; gap:3px; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .apr-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .apr-bar-track {{')
        parts.append('  height:12px; background:rgba(255,255,255,0.08); border-radius:6px; overflow:hidden;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .apr-bar-fill {{')
        parts.append(f'  height:100%; width:0; border-radius:6px; background:{p["accent"]};')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .apr-row.apr-winner .apr-label {{')
        parts.append(f'  color:{p["accent"]}; font-weight:700;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .apr-pct {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  color:{p["text_secondary"]}; font-weight:700;')
        parts.append('}')
    if content_style == "broken_promise_tracker":
        parts.append(f'.card[data-card-id="{card_id}"] .bpt-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:6px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .bpt-item {{')
        parts.append(f'  display:flex; align-items:center; gap:10px;')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .bpt-icon-kept {{')
        parts.append(f'  font-size:{kicker_size_eff}; color:{p["accent"]}; flex-shrink:0; line-height:1;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .bpt-icon-broken {{')
        parts.append(f'  font-size:{kicker_size_eff}; color:{p["text_secondary"]}; flex-shrink:0; line-height:1; opacity:0.5;')
        parts.append('}')
    if content_style == "ingredient_list":
        parts.append(f'.card[data-card-id="{card_id}"] .igl-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:6px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .igl-item {{')
        parts.append(f'  display:flex; align-items:center; gap:10px;')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .igl-bullet {{')
        parts.append(f'  width:6px; height:6px; border-radius:50%; background:{p["accent"]}; flex-shrink:0;')
        parts.append('}')
    if content_style == "resource_allocation":
        parts.append(f'.card[data-card-id="{card_id}"] .ral-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:8px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ral-row {{')
        parts.append('  display:flex; flex-direction:column; gap:3px; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ral-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ral-track {{')
        parts.append('  height:14px; background:rgba(255,255,255,0.08); border-radius:7px; overflow:hidden;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ral-fill {{')
        parts.append(f'  height:100%; width:0; border-radius:7px; background:{p["accent"]};')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
    if content_style == "fill_in_the_blank":
        parts.append(f'.card[data-card-id="{card_id}"] .fitb-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:14px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .fitb-sentence {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; text-align:center; opacity:0;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .fitb-word {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:900; color:{p["accent"]}; text-align:center; opacity:0;')
        parts.append(f'  padding:4px 18px; border-bottom:3px solid {p["accent"]};')
        if p["title_glow_intense"]:
            parts.append(f'  text-shadow:{p["title_glow_intense"]};')
        elif p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
    # ── Wave 9 ────────────────────────────────────────────────────────────────
    if content_style == "streak_counter":
        _sk_num_sz = "56px" if compact else "80px"
        parts.append(f'.card[data-card-id="{card_id}"] .sk-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:8px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .sk-row {{')
        parts.append('  display:flex; align-items:baseline; gap:10px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .sk-count {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_sk_num_sz};')
        parts.append(f'  font-weight:900; color:{p["accent"]}; line-height:1; opacity:0;')
        if p["title_glow_intense"]:
            parts.append(f'  text-shadow:{p["title_glow_intense"]};')
        elif p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .sk-unit {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .sk-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:700; color:{p["text_secondary"]}; letter-spacing:0.10em; text-transform:uppercase; opacity:0;')
        parts.append('}')
    if content_style == "before_now_later":
        parts.append(f'.card[data-card-id="{card_id}"] .bnl-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:8px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .bnl-slot {{')
        parts.append(f'  display:flex; flex-direction:column; gap:4px;')
        parts.append(f'  padding:8px 14px; border-radius:{p["radius"]};')
        parts.append(f'  background:{p["bg"]}; border:1px solid rgba(255,255,255,0.08); opacity:0;')
        parts.append(f'  min-width:0; overflow:hidden;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .bnl-tag {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:700; color:{p["accent"]}; letter-spacing:0.10em; text-transform:uppercase;')
        parts.append('}')
        _bnl_text_sz = detail_size_eff if compact else title_size_eff
        parts.append(f'.card[data-card-id="{card_id}"] .bnl-text {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_bnl_text_sz};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]};')
        parts.append(f'  overflow-wrap:break-word; word-break:break-word;')
        parts.append('}')
    if content_style == "platform_stats":
        parts.append(f'.card[data-card-id="{card_id}"] .pst-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:8px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .pst-row {{')
        parts.append('  display:flex; justify-content:space-between; align-items:center;')
        parts.append('  padding:6px 0; border-bottom:1px solid rgba(255,255,255,0.08); opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .pst-name {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .pst-val {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  font-weight:900; color:{p["accent"]};')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
    if content_style == "cost_comparison":
        _cco_price_sz = "28px" if compact else "36px"
        parts.append(f'.card[data-card-id="{card_id}"] .cco-wrap {{')
        parts.append('  display:flex; flex-direction:row; gap:10px; width:100%; align-items:stretch;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cco-col {{')
        parts.append(f'  flex:1; display:flex; flex-direction:column; gap:6px; align-items:center;')
        parts.append(f'  padding:10px 8px; border-radius:{p["radius"]};')
        parts.append(f'  background:{p["bg"]}; border:1px solid rgba(255,255,255,0.08); opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cco-best {{')
        parts.append(f'  border-color:{p["accent"]};')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:0 0 12px {p["accent"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cco-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:700; color:{p["text_secondary"]}; letter-spacing:0.08em; text-transform:uppercase; text-align:center;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cco-price {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_cco_price_sz};')
        parts.append(f'  font-weight:900; color:{p["text"]}; text-align:center;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cco-best .cco-price {{')
        parts.append(f'  color:{p["accent"]};')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
    if content_style == "decision_matrix":
        parts.append(f'.card[data-card-id="{card_id}"] .dmx-wrap {{')
        parts.append('  display:grid; grid-template-columns:1fr 1fr; gap:8px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .dmx-q {{')
        parts.append(f'  display:flex; align-items:center; justify-content:center;')
        parts.append(f'  padding:12px 8px; border-radius:{p["radius"]};')
        parts.append(f'  background:{p["bg"]}; border:1px solid rgba(255,255,255,0.08);')
        parts.append('  text-align:center; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .dmx-q-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .accent-line {{ display:none; }}')
    if content_style == "habit_tracker":
        _ht_day_sz = "28px" if compact else "36px"
        parts.append(f'.card[data-card-id="{card_id}"] .ht-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:12px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ht-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{title_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ht-days {{')
        parts.append('  display:flex; flex-direction:row; gap:8px; flex-wrap:wrap;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ht-day {{')
        parts.append(f'  width:{_ht_day_sz}; height:{_ht_day_sz}; border-radius:50%;')
        parts.append(f'  border:2px solid rgba(255,255,255,0.20); background:rgba(255,255,255,0.06);')
        parts.append('  opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ht-done {{')
        parts.append(f'  background:{p["accent"]}; border-color:{p["accent"]};')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:0 0 8px {p["accent"]};')
        parts.append('}')
    if content_style == "income_vs_expense":
        parts.append(f'.card[data-card-id="{card_id}"] .ive-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:12px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ive-row {{')
        parts.append('  display:flex; flex-direction:column; gap:4px; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ive-meta {{')
        parts.append('  display:flex; justify-content:space-between; align-items:baseline;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ive-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ive-val-income {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  font-weight:900; color:{p["accent"]};')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ive-val-expense {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{detail_size_eff};')
        parts.append(f'  font-weight:900; color:{p["text_secondary"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ive-track {{')
        parts.append(f'  width:100%; height:12px; border-radius:{p["radius"]}; overflow:hidden;')
        parts.append(f'  background:{p["bg"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ive-fill-income {{')
        parts.append(f'  height:100%; width:0; border-radius:{p["radius"]}; background:{p["accent"]};')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ive-fill-expense {{')
        parts.append(f'  height:100%; width:0; border-radius:{p["radius"]}; background:{p["text_secondary"]}; opacity:0.6;')
        parts.append('}')
    # ── Wave 10 ───────────────────────────────────────────────────────────────
    if content_style == "milestone_recap":
        _mr_sz = "24px"
        parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{ width: {max_width_eff}; box-sizing: border-box; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .mr-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:6px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .mr-item {{')
        parts.append('  display:flex; align-items:center; gap:10px; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .mr-dot {{')
        parts.append(f'  width:8px; height:8px; border-radius:50%; flex-shrink:0; background:{p["accent"]};')
        if p.get("accent_line_glow"):
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .mr-text {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_mr_sz};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; line-height:1.3;')
        parts.append('}')
    if content_style == "content_calendar":
        _cal_day_sz = "24px"
        _cal_cnt_sz = "18px"
        parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{ width: {max_width_eff}; box-sizing: border-box; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .cal-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:5px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cal-item {{')
        parts.append('  display:flex; gap:8px; align-items:flex-start; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cal-day {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_cal_day_sz};')
        parts.append(f'  font-weight:900; color:{p["accent"]}; min-width:80px; flex-shrink:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .cal-content {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_cal_cnt_sz};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; line-height:1.3;')
        parts.append('}')
    if content_style == "client_result_number":
        _crn_val_sz = "52px" if compact else "72px"
        _crn_ctx_sz = "16px" if compact else "20px"
        _crn_lbl_sz = "13px" if compact else "15px"
        parts.append(f'.card[data-card-id="{card_id}"] .crn-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center; gap:6px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .crn-value {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_crn_val_sz};')
        parts.append(f'  font-weight:900; color:{p["accent"]}; line-height:1.0; opacity:0;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .crn-context {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_crn_ctx_sz};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .crn-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_crn_lbl_sz};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text_secondary"]}; opacity:0;')
        parts.append('}')
    if content_style == "mistake_lesson":
        _ml_tag_sz = "14px" if compact else "16px"
        _ml_txt_sz = "18px" if compact else "22px"
        parts.append(f'.card[data-card-id="{card_id}"] .ml-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:10px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ml-block {{')
        parts.append(f'  display:flex; flex-direction:column; gap:4px; padding:10px;')
        parts.append(f'  border-radius:{p["radius"]}; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ml-mistake {{')
        parts.append(f'  border-left:3px solid {p["text_secondary"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ml-lesson {{')
        parts.append(f'  border-left:3px solid {p["accent"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ml-tag {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_ml_tag_sz};')
        parts.append(f'  font-weight:900; text-transform:uppercase; letter-spacing:0.08em;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ml-tag-err {{ color:{p["text_secondary"]}; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .ml-tag-lsn {{ color:{p["accent"]};')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .ml-text {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_ml_txt_sz};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; line-height:1.3;')
        parts.append('}')
    if content_style == "tool_comparison":
        _tc_head_sz = "24px"
        _tc_feat_sz = "18px"
        parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{ width: {max_width_eff}; box-sizing: border-box; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .tc-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:8px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tc-heads {{')
        parts.append('  display:flex; gap:6px; width:100%; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tc-head {{')
        parts.append(f'  flex:1; text-align:center; font-family:{p["font"]}; font-size:{_tc_head_sz};')
        parts.append(f'  font-weight:900; color:{p["accent"]};')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tc-feats {{')
        parts.append('  display:flex; flex-direction:column; gap:4px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .tc-feat {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_tc_feat_sz};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]}; opacity:0;')
        parts.append(f'  padding:3px 0; border-bottom:1px solid {p["text_secondary"]}22;')
        parts.append('}')
    if content_style == "weekly_review":
        _wr_sz = "24px"
        parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{ width: {max_width_eff}; box-sizing: border-box; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .wr-wrap {{')
        parts.append('  display:flex; flex-direction:column; gap:6px; width:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .wr-item {{')
        parts.append(f'  display:flex; justify-content:space-between; align-items:center; opacity:0;')
        parts.append(f'  padding:5px 0; border-bottom:1px solid {p["text_secondary"]}22;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .wr-cat {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_wr_sz};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .wr-score {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_wr_sz};')
        parts.append(f'  font-weight:900; color:{p["accent"]};')
        if p.get("title_glow"):
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
    if content_style == "audience_question":
        _aq_sz = "22px" if compact else "28px"
        parts.append(f'.card[data-card-id="{card_id}"] .aq-wrap {{')
        parts.append('  display:flex; flex-direction:column; align-items:center;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .aq-q {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_aq_sz};')
        parts.append(f'  font-weight:900; color:{p["text"]}; text-align:center;')
        parts.append(f'  line-height:1.3; opacity:0;')
        parts.append('}')
    # ── Catalogue primitives CSS (Wave 11) ───────────────────────────────────
    if content_style == "prim_stat_counter":
        # Scale relative to zone so it reads well in both portrait upper-right (500px)
        # and landscape landscape-tr (660px). compact=True applies to both via
        # _SIDE_PANEL_ZONES membership, so use zone width for finer calibration.
        _psc_zone_w = _zone_bounds(card.get("zone", "upper-right"), layout).get("width", 500)
        _psc_num_sz  = "46px" if _psc_zone_w < 400 else "62px" if _psc_zone_w < 560 else "80px"
        _psc_side_sz = "22px" if _psc_zone_w < 400 else "28px" if _psc_zone_w < 560 else "36px"
        # In compact landscape (660×300px) zone-width gives 80px/36px which overflows vertically
        if compact and layout == "landscape":
            _psc_num_sz  = "54px"
            _psc_side_sz = "24px"
        # Glassmorphism panel: genuine blur on dark packs (lean_glass, lean_ledger, lean_cinema)
        if p.get("id") in ("lean_glass", "lean_ledger", "lean_cinema", "lean_vibe"):
            parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{')
            parts.append('  backdrop-filter:blur(22px) saturate(180%);')
            parts.append('  -webkit-backdrop-filter:blur(22px) saturate(180%);')
            parts.append(f'  background:rgba(10,10,20,0.62);')
            parts.append(f'  border:1px solid {p["accent"]}26;')
            parts.append(f'  border-top:1px solid rgba(255,255,255,0.09);')
            parts.append('  box-sizing:border-box;')
            parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .psc-row {{')
        parts.append('  display:flex; align-items:baseline; gap:6px; justify-content:center;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .psc-number {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_psc_num_sz};')
        parts.append(f'  font-weight:900; color:{p["text"]}; line-height:1.0;')
        parts.append(f'  font-variant-numeric:tabular-nums; opacity:0;')
        # Start with zero glow; GSAP charges it during count-up
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .psc-side {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_psc_side_sz};')
        parts.append(f'  font-weight:700; color:{p["accent"]}; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .psc-kicker {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["text_secondary"]};')
        parts.append('  text-align:center; opacity:0; margin-top:6px;')
        parts.append('}')
    if content_style == "prim_numbered_rule":
        parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{')
        parts.append('  width:100%; height:100%; max-width:none; padding:0;')
        parts.append('  display:flex; flex-direction:column; align-items:center;')
        parts.append('  justify-content:center; gap:20px; box-sizing:border-box;')
        parts.append('  background:#000;')  # intentionally dark — not a climax primitive
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .pnr-number {{')
        parts.append(f'  font-family:{p["font"]}; font-size:180px;')
        parts.append(f'  font-weight:900; color:{p["accent"]}; line-height:1.0;')
        parts.append('  font-variant-numeric:tabular-nums; opacity:0;')
        parts.append('  transform-origin:center center;')
        if p.get("title_glow"):
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        # Rule text: always on a near-black bg_full background.
        # Light packs (craft, paper) have dark p["text"] → use white override.
        _pnr_rule_color = (
            "rgba(255,255,255,0.88)"
            if p.get("id") in ("lean_craft", "lean_paper")
            else p["text"]
        )
        parts.append(f'.card[data-card-id="{card_id}"] .pnr-rule {{')
        parts.append(f'  font-family:{p["font"]}; font-size:38px;')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{_pnr_rule_color};')
        parts.append('  text-align:center; opacity:0; max-width:80%; line-height:1.3;')
        parts.append('}')
    if content_style == "prim_anecdote_frame":
        _af_grain = (
            "url(\"data:image/svg+xml,"
            "<svg xmlns='http://www.w3.org/2000/svg' width='256' height='256'>"
            "<filter id='g'>"
            "<feTurbulence type='fractalNoise' baseFrequency='0.80' numOctaves='4' stitchTiles='stitch'/>"
            "<feColorMatrix type='saturate' values='0'/>"
            "</filter>"
            "<rect width='256' height='256' filter='url(%23g)' opacity='0.5'/>"
            "</svg>\")"
        )
        parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{')
        parts.append('  width:100%; height:100%; max-width:none; padding:0;')
        parts.append('  position:relative; background:transparent; overflow:hidden;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .af-tint {{')
        parts.append('  position:absolute; inset:0; opacity:0;')
        parts.append('  background:rgba(20,10,5,0.35);')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .af-vignette {{')
        parts.append('  position:absolute; inset:0; opacity:0;')
        parts.append('  background:radial-gradient(ellipse at center, transparent 25%, rgba(0,0,0,0.88) 100%);')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .af-grain {{')
        parts.append('  position:absolute; inset:0; opacity:0;')
        parts.append(f'  background-image:{_af_grain};')
        parts.append('  background-size:256px 256px; mix-blend-mode:overlay;')
        parts.append('}')
    if content_style == "prim_split_compare":
        # Root: zero padding for full-bleed canvas (same fix as prim_journey_map)
        parts.append(f'.card[data-card-id="{card_id}"] .root {{ padding:0; gap:0; justify-content:flex-start; align-items:stretch; }}')
        # Card-panel: base CSS sets flex-direction:column — must override to row for left/right split
        parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{')
        parts.append('  width:100%; height:100%; max-width:none; padding:0;')
        parts.append('  display:flex; flex-direction:row; align-items:stretch;')
        parts.append('  position:relative; overflow:hidden; background:#000;')
        parts.append('}')
        # Generic kicker is inside card-panel as first flex item — wrong position with flex-direction:row
        parts.append(f'.card[data-card-id="{card_id}"] .kicker {{ display:none; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .spc-half {{')
        parts.append('  flex:1; display:flex; align-items:center; justify-content:center;')
        parts.append('  overflow:hidden;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .spc-left {{ background:{p["accent"]}1a; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .spc-right {{ background:{p["text"]}0d; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .spc-label {{')
        parts.append(f'  font-family:{p["font"]}; font-size:42px;')
        parts.append(f'  font-weight:900; color:{p["text"]}; text-align:center;')
        parts.append('  padding:24px; line-height:1.2; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .spc-divider {{')
        parts.append('  position:absolute; left:50%; top:0; bottom:0; width:3px;')
        parts.append(f'  background:{p["accent"]}; transform:translateX(-50%) scaleY(0);')
        parts.append('  transform-origin:top center; border-radius:999px;')
        if p.get("accent_line_glow"):
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
        if kicker:
            parts.append(f'.card[data-card-id="{card_id}"] .spc-kicker {{')
            parts.append(f'  position:absolute; top:32px; left:50%; transform:translateX(-50%);')
            parts.append(f'  font-family:{p["font"]}; font-size:{kicker_size_eff};')
            parts.append(f'  font-weight:700; letter-spacing:0.15em; text-transform:uppercase;')
            parts.append(f'  color:{p["accent"]}; white-space:nowrap; opacity:0; z-index:10;')
            parts.append('}')
    elif content_style == "prim_journey_map":
        # ── prim_journey_map — flight-tracker overlay (prototype) ──────────
        # Root must be full-bleed: root_padding=48px would shrink the available height,
        # causing flex:1 on .jmt-map to collapse to 0 and hide the SVG entirely.
        parts.append(f'.card[data-card-id="{card_id}"] .root {{ padding:0; gap:0; justify-content:flex-start; align-items:stretch; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{')
        parts.append('  background:transparent; padding:0; overflow:hidden;')
        parts.append('  display:flex; flex-direction:column; width:100%; height:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .jmt-header {{')
        parts.append('  padding:16px 14px 8px; display:flex; flex-direction:column;')
        parts.append('  align-items:center; gap:3px; opacity:0; flex-shrink:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .jmt-route {{')
        parts.append(f'  font-family:{p["font"]}; font-size:20px; font-weight:800;')
        parts.append(f'  color:{p["text"]}; letter-spacing:.03em;')
        parts.append('  display:flex; align-items:center; gap:9px;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .jmt-arrow {{ color:{p["accent"]}; font-size:16px; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .jmt-sub {{')
        parts.append(f'  font-family:{p["font"]}; font-size:9px; font-weight:500;')
        parts.append('  color:rgba(255,255,255,0.38); letter-spacing:.08em; text-transform:uppercase;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .jmt-sep {{')
        parts.append(f'  height:1px; background:{p["accent"]}18; margin:0 12px; flex-shrink:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .jmt-map {{')
        parts.append('  flex:1; position:relative; overflow:hidden; min-height:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .jmt-map svg {{')
        parts.append('  position:absolute; inset:0; width:100%; height:100%;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .jmt-footer {{')
        parts.append('  padding:7px 14px 13px; display:flex; justify-content:space-between;')
        parts.append('  align-items:flex-end; opacity:0; flex-shrink:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .jmt-city {{')
        parts.append(f'  font-family:{p["font"]}; font-size:11px; font-weight:700; color:{p["text"]};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .jmt-ctry {{')
        parts.append(f'  font-family:{p["font"]}; font-size:8px; font-weight:500;')
        parts.append('  color:rgba(255,255,255,0.35); text-transform:uppercase; letter-spacing:.05em;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .kicker {{ text-shadow:0 1px 10px rgba(0,0,0,0.9); }}')
        parts.append(f'.card[data-card-id="{card_id}"] .accent-line {{ display:none; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .shimmer-mask {{ display:none; }}')
    # ── number_hero CSS ──────────────────────────────────────────────────────
    if content_style == "number_hero":
        # Tighten panel padding — scene needs vertical room for 3-act layout
        parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{ padding:20px 40px; }}')
        # Suppress standard kicker (nh-kicker is inside .nh-scene instead)
        parts.append(f'.card[data-card-id="{card_id}"] .kicker {{ display:none; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .accent-line {{ display:none; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .shimmer-mask {{ display:none; }}')
        # Scene: flex column, fills card-panel, centers the 6-element stack
        parts.append(f'.card[data-card-id="{card_id}"] .nh-scene {{')
        parts.append('  display:flex; flex-direction:column; align-items:center;')
        parts.append('  justify-content:center; position:relative; width:100%; flex:1; gap:0;')
        parts.append('}')
        # Spotlight: radial-gradient circle absolutely centered behind content
        _nh_is_glow = p["id"] in ("lean_glass", "lean_vibe")
        _nh_spot_alpha = "24" if _nh_is_glow else "10"
        _nh_is_compact_ls = compact and layout == "landscape"
        _nh_spot_dim = "180px" if _nh_is_compact_ls else "460px"
        parts.append(f'.card[data-card-id="{card_id}"] .nh-spotlight {{')
        parts.append(f'  position:absolute; width:{_nh_spot_dim}; height:{_nh_spot_dim}; border-radius:50%;')
        parts.append('  top:50%; left:50%; transform:translate(-50%,-50%);')
        parts.append(f'  background:radial-gradient(circle, {p["accent"]}{_nh_spot_alpha} 0%, transparent 70%);')
        parts.append('  pointer-events:none; opacity:0; z-index:0;')
        parts.append('}')
        # Kicker: small caps above the lines
        _nh_kicker_sz = kicker_size_eff if _nh_is_compact_ls else p["kicker_size"]
        _nh_kicker_mb = "6px" if _nh_is_compact_ls else "14px"
        parts.append(f'.card[data-card-id="{card_id}"] .nh-kicker {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_nh_kicker_sz}; font-weight:600;')
        parts.append(f'  color:{p["text_secondary"]}; letter-spacing:0.16em; text-transform:uppercase;')
        parts.append(f'  text-align:center; opacity:0; margin-bottom:{_nh_kicker_mb}; position:relative; z-index:1;')
        parts.append('}')
        # Mirror accent lines — block-level + resolved width so scaleX works (HF rule 7)
        parts.append(f'.card[data-card-id="{card_id}"] .nh-line {{')
        parts.append('  display:block; width:100%; height:2px; border-radius:1px;')
        parts.append(f'  background:{p["accent"]}; transform-origin:center; transform:scaleX(0);')
        parts.append('  position:relative; z-index:1;')
        if p["accent_line_glow"]:
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
        # Hero number: scale font-size to prevent overflow.
        # Portrait: content area ≈ 770px (850px panel − 80px padding). At 160px bold,
        # each char ≈ 90px → safe up to ~8 chars.
        # Compact landscape (660×300px): content width ≈ 580px, height budget ≈ 120px.
        _nh_raw_str = hints.get("nh_number", "")
        if _nh_is_compact_ls:
            _nh_fsize = max(40, min(88, int(560 * 88 // (78 * max(6, len(_nh_raw_str))))))
        else:
            _nh_fsize = max(60, min(160, int(770 * 160 // (90 * max(8, len(_nh_raw_str))))))
        _nh_num_margin = "8px 0" if _nh_is_compact_ls else "16px 0"
        parts.append(f'.card[data-card-id="{card_id}"] .nh-number {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_nh_fsize}px; font-weight:900;')
        parts.append(f'  line-height:1; letter-spacing:-0.02em; color:{p["text"]};')
        parts.append(f'  text-align:center; white-space:nowrap; max-width:100%; overflow:hidden; margin:{_nh_num_margin}; opacity:0; position:relative; z-index:1;')
        if p["title_glow"]:
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        # Detail: muted context label below lines
        _nh_detail_sz = detail_size_eff if _nh_is_compact_ls else p["detail_size"]
        _nh_detail_mt = "6px" if _nh_is_compact_ls else "14px"
        parts.append(f'.card[data-card-id="{card_id}"] .nh-detail {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_nh_detail_sz}; font-weight:500;')
        parts.append(f'  color:{p["text_secondary"]}; text-align:center; opacity:0;')
        parts.append(f'  margin-top:{_nh_detail_mt}; position:relative; z-index:1;')
        parts.append('}')
    # ── prim_cinematic_reveal CSS ────────────────────────────────────────────
    if content_style == "prim_cinematic_reveal":
        # Light-pack text override: bg_full is a dark gradient; craft/paper pack text is dark.
        _pcr_text = (
            "rgba(255,255,255,0.92)"
            if p["id"] in ("lean_craft", "lean_paper")
            else p["text"]
        )
        _pcr_secondary = (
            "rgba(255,255,255,0.55)"
            if p["id"] in ("lean_craft", "lean_paper")
            else p["text_secondary"]
        )
        parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{')
        parts.append('  width:100%; height:100%; max-width:none; padding:0;')
        parts.append('  display:flex; align-items:center; justify-content:center;')
        parts.append(f'  background:{p.get("bg_full", "#000")};')
        parts.append('}')
        # Suppress generic kicker / accent-line / shimmer (own pcr-* elements take over)
        parts.append(f'.card[data-card-id="{card_id}"] .kicker {{ display:none; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .accent-line {{ display:none; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .shimmer-mask {{ display:none; }}')
        # Scene: perspective wrapper, full width, centered flex column
        parts.append(f'.card[data-card-id="{card_id}"] .pcr-scene {{')
        parts.append('  position:relative; display:flex; flex-direction:column;')
        parts.append('  align-items:center; justify-content:center; gap:0;')
        parts.append('  width:100%; padding:72px 80px; box-sizing:border-box;')
        parts.append('}')
        # Background geometric: diamond ring (square rotated 45°) — GSAP owns transform
        parts.append(f'.card[data-card-id="{card_id}"] .pcr-bg {{')
        parts.append(f'  position:absolute; width:360px; height:360px;')
        parts.append(f'  border:1.5px solid {p["accent"]}; border-radius:3px;')
        parts.append('  opacity:0; pointer-events:none; transform-origin:center center;')
        parts.append('}')
        # Eyebrow kicker: small-caps label, perspective-flip entry via GSAP
        parts.append(f'.card[data-card-id="{card_id}"] .pcr-kicker {{')
        parts.append(f'  font-family:{p["font"]}; font-size:18px;')
        parts.append('  font-weight:600; letter-spacing:0.18em; text-transform:uppercase;')
        parts.append(f'  color:{_pcr_secondary}; text-align:center; opacity:0;')
        parts.append('  position:relative; z-index:1; margin-bottom:18px;')
        parts.append('}')
        # Main title: scale+rotateY approach via GSAP (expo.out)
        parts.append(f'.card[data-card-id="{card_id}"] .pcr-title {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{p["title_size"]};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{_pcr_text};')
        parts.append('  text-align:center; line-height:1.15; opacity:0;')
        parts.append('  position:relative; z-index:1; max-width:100%;')
        if p.get("title_glow"):
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        # Accent line: left-anchored scaleX growth via GSAP (power2.inOut)
        # Block-level with explicit width required so scaleX is not a no-op (HF rule 7).
        parts.append(f'.card[data-card-id="{card_id}"] .pcr-line {{')
        parts.append('  display:block; height:3px; border-radius:999px;')
        parts.append(f'  background:{p["accent"]}; width:calc(100% - 120px); max-width:480px;')
        parts.append('  opacity:0; position:relative; z-index:1;')
        parts.append('  transform-origin:left center; margin-top:22px;')
        if p.get("accent_line_glow"):
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
        # Optional detail: muted sub-text, y-rise entry via GSAP
        parts.append(f'.card[data-card-id="{card_id}"] .pcr-detail {{')
        parts.append(f'  font-family:{p.get("font_detail", p["font"])}; font-size:{p["detail_size"]};')
        parts.append(f'  font-weight:400; color:{_pcr_secondary};')
        parts.append('  text-align:center; opacity:0; position:relative; z-index:1;')
        parts.append('  margin-top:16px; max-width:80%; line-height:1.4;')
        parts.append('}')
    # ── prim_ascension_reveal CSS ─────────────────────────────────────────────
    if content_style == "prim_ascension_reveal":
        # Light-pack text override: bg_full is a dark gradient; craft/paper pack text is dark.
        _par_text = (
            "rgba(255,255,255,0.92)"
            if p["id"] in ("lean_craft", "lean_paper")
            else p["text"]
        )
        _par_secondary = (
            "rgba(255,255,255,0.55)"
            if p["id"] in ("lean_craft", "lean_paper")
            else p["text_secondary"]
        )
        parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{')
        parts.append('  width:100%; height:100%; max-width:none; padding:0;')
        parts.append('  display:flex; align-items:center; justify-content:center;')
        parts.append(f'  background:{p.get("bg_full", "#000")};')
        parts.append('}')
        # Suppress generic elements (par-* take over)
        parts.append(f'.card[data-card-id="{card_id}"] .kicker {{ display:none; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .accent-line {{ display:none; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .shimmer-mask {{ display:none; }}')
        # Scene: perspective on parent enables rotateX on .par-title (L2 layer)
        parts.append(f'.card[data-card-id="{card_id}"] .par-scene {{')
        parts.append('  position:relative; display:flex; flex-direction:column;')
        parts.append('  align-items:center; justify-content:center; gap:0;')
        parts.append('  width:100%; padding:64px 80px; box-sizing:border-box;')
        parts.append('  perspective:1400px;')
        parts.append('}')
        # L0 — Halo: radial gradient glow, no filter:blur (SwiftShader constraint)
        _par_halo_alpha = "26" if p["id"] in ("lean_glass", "lean_vibe") else "1A"
        parts.append(f'.card[data-card-id="{card_id}"] .par-halo {{')
        parts.append('  position:absolute; width:640px; height:640px; border-radius:50%;')
        parts.append('  top:50%; left:50%; transform:translate(-50%,-50%);')
        parts.append(f'  background:radial-gradient(circle, {p["accent"]}{_par_halo_alpha} 0%, transparent 65%);')
        parts.append('  pointer-events:none; opacity:0; z-index:0;')
        parts.append('}')
        # L1 — Horizon: HF rule 7 — display:block + explicit width so scaleX is non-trivial
        parts.append(f'.card[data-card-id="{card_id}"] .par-horizon {{')
        parts.append('  display:block; height:2px; border-radius:999px;')
        parts.append('  width:calc(100% - 160px); max-width:440px;')
        parts.append(f'  background:{p["accent"]}; transform-origin:center; transform:scaleX(0);')
        parts.append('  opacity:0; position:relative; z-index:1; margin-bottom:24px;')
        if p.get("accent_line_glow"):
            parts.append(f'  box-shadow:{p["accent_line_glow"]};')
        parts.append('}')
        # L2 — Main title: adaptive font size by character count (chiffres/résultats cibles)
        _par_raw   = hints.get("title", "")
        _par_chars = len(_par_raw)
        _par_fsize = (
            "120px" if _par_chars <= 8  else
            "88px"  if _par_chars <= 18 else
            "64px"  if _par_chars <= 35 else
            "48px"
        )
        parts.append(f'.card[data-card-id="{card_id}"] .par-title {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_par_fsize};')
        parts.append(f'  font-weight:900; color:{_par_text};')
        parts.append('  text-align:center; line-height:1.1; opacity:0;')
        parts.append('  position:relative; z-index:2; max-width:100%;')
        parts.append('  overflow-wrap:break-word; word-break:break-word;')
        if p.get("title_glow"):
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        # L3 — Ring pulse: border ellipse around title area, single-cycle expand-fade
        parts.append(f'.card[data-card-id="{card_id}"] .par-ring {{')
        parts.append('  position:absolute; border-radius:50%;')
        parts.append('  width:360px; height:160px;')
        parts.append(f'  border:1.5px solid {p["accent"]}66;')
        parts.append('  opacity:0; pointer-events:none; z-index:1;')
        parts.append('}')
        # L4 — Kicker: sub-text context, fade-rise entry via GSAP
        parts.append(f'.card[data-card-id="{card_id}"] .par-kicker {{')
        parts.append(f'  font-family:{p.get("font_detail", p["font"])}; font-size:{p["kicker_size"]};')
        parts.append('  font-weight:600; letter-spacing:0.12em; text-transform:uppercase;')
        parts.append(f'  color:{_par_secondary}; text-align:center; opacity:0;')
        parts.append('  position:relative; z-index:2; margin-top:20px;')
        parts.append('}')
    # ── prim_shatter_truth CSS ───────────────────────────────────────────────
    if content_style == "prim_shatter_truth":
        # Light-pack override: bg_full is a dark gradient; craft/paper text is dark.
        _pst_text = (
            "rgba(255,255,255,0.95)"
            if p["id"] in ("lean_craft", "lean_paper")
            else p["text"]
        )
        # Card-panel: full-cover black canvas, relative for absolute children
        parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{')
        parts.append('  width:100%; height:100%; max-width:none; padding:0;')
        parts.append('  position:relative;')
        parts.append(f'  background:{p.get("bg_full", "#000")};')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .kicker {{ display:none; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .accent-line {{ display:none; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .shimmer-mask {{ display:none; }}')
        # Scene: absolute overlay; all children positioned within it
        parts.append(f'.card[data-card-id="{card_id}"] .pst-scene {{')
        parts.append('  position:absolute; inset:0; overflow:hidden;')
        parts.append('}')
        # Layer wrappers: each covers full scene, flex-centered — avoids GSAP/CSS transform conflict
        parts.append(f'.card[data-card-id="{card_id}"] .pst-layer {{')
        parts.append('  position:absolute; inset:0;')
        parts.append('  display:flex; align-items:center; justify-content:center;')
        parts.append('}')
        # Myth-wrap: auto-sized to text, establishes positioning context for frags
        parts.append(f'.card[data-card-id="{card_id}"] .pst-myth-wrap {{')
        parts.append('  position:relative; width:84%; max-width:900px;')
        parts.append('}')
        # Font size for myth: adaptive by char count (max 50 chars)
        _pst_myth_raw = hints.get("myth_text", hints.get("title", ""))
        _pst_mchars   = len(_pst_myth_raw)
        _pst_mfsize   = (
            "72px" if _pst_mchars <= 15 else
            "60px" if _pst_mchars <= 28 else
            "50px" if _pst_mchars <= 40 else
            "42px"
        )
        # Shared font rules — myth text and each fragment must be pixel-identical
        _pst_font_common = [
            f'  font-family:{p["font"]}; font-size:{_pst_mfsize};',
            f'  font-weight:{p["font_weight"]}; color:{_pst_text};',
            '  text-align:center; line-height:1.2;',
            '  overflow-wrap:break-word; word-break:break-word;',
        ]
        # L0 — Myth text (in-flow, sizes myth-wrap)
        parts.append(f'.card[data-card-id="{card_id}"] .pst-myth {{')
        parts.extend(_pst_font_common)
        parts.append('  opacity:0; position:relative; z-index:1;')
        if p.get("title_glow"):
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        # L1 — Fragment container: absolute overlay matching myth-wrap bounds
        parts.append(f'.card[data-card-id="{card_id}"] .pst-frags {{')
        parts.append('  position:absolute; inset:0; pointer-events:none; z-index:2;')
        parts.append('}')
        # L1 — Individual fragments: identical style + position → clip-path cuts horizontal bands
        # HF rule 7: position:absolute + inset:0 → block-level + sized → transforms non-trivial.
        # display:flex + center mirrors myth text layout for pixel-accurate clip alignment.
        parts.append(f'.card[data-card-id="{card_id}"] .pst-frag {{')
        parts.extend(_pst_font_common)
        parts.append('  position:absolute; inset:0;')
        parts.append('  display:flex; align-items:center; justify-content:center;')
        parts.append('  opacity:0;')
        if p.get("title_glow"):
            parts.append(f'  text-shadow:{p["title_glow"]};')
        parts.append('}')
        # L2 — Flash: white punch above frags (z:10), invisible by default
        parts.append(f'.card[data-card-id="{card_id}"] .pst-flash {{')
        parts.append('  position:absolute; inset:0; background:#FFFFFF;')
        parts.append('  opacity:0; pointer-events:none; z-index:10;')
        parts.append('}')
        # L3 — Truth text: accent-colored, back.out(1.3) entry — no existing transform
        _pst_truth_raw = hints.get("truth_text", "")
        _pst_tchars    = len(_pst_truth_raw)
        _pst_tfsize    = (
            "68px" if _pst_tchars <= 20 else
            "54px" if _pst_tchars <= 40 else
            "44px"
        )
        parts.append(f'.card[data-card-id="{card_id}"] .pst-truth {{')
        parts.append(f'  font-family:{p["font"]}; font-size:{_pst_tfsize};')
        parts.append(f'  font-weight:{p["font_weight"]}; color:{p["accent"]};')
        parts.append('  text-align:center; line-height:1.2; opacity:0;')
        parts.append('  width:84%; max-width:900px;')
        parts.append('  overflow-wrap:break-word; word-break:break-word;')
        if p.get("title_glow_intense"):
            parts.append(f'  text-shadow:{p["title_glow_intense"]};')
        parts.append('}')
    # ── prim_split_stage CSS ─────────────────────────────────────────────────
    if content_style == "prim_split_stage":
        # Root must be full-bleed — same fix as prim_split_compare / prim_journey_map.
        # root_padding:48px confines .card-panel to an inset area and prevents
        # height:100% on .sst-panel from reaching the card edges.
        parts.append(f'.card[data-card-id="{card_id}"] .root {{ padding:0; gap:0; justify-content:flex-start; align-items:stretch; }}')
        _sst_side = hints.get("side", "right")    # "left"=video left / "right"=video right
        _sst_mode = hints.get("mode", "steps")    # "steps" or "diagram"
        _sst_is_light = p["id"] in ("lean_paper", "lean_craft")
        # Fully opaque panel — #video-stage stays at scale:1/x:0 (no SwiftShader
        # re-rasterization of the video texture). Panel covers its half completely.
        _sst_panel_bg = (
            "#FAFAF8" if p["id"] == "lean_paper" else
            "#E8D9C5" if p["id"] == "lean_craft" else
            p.get("bg_full", "linear-gradient(160deg, #12121C, #06060E)")
        )
        # panel sits on the side OPPOSITE the video
        _sst_panel_edge = "right:0" if _sst_side == "left" else "left:0"
        # divider line faces the video (accent-tinted, same opacity as prim_cinematic_reveal border)
        _sst_border_side = "border-left" if _sst_side == "left" else "border-right"

        # Card-panel: transparent — video shows through on the video side
        parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{')
        parts.append('  width:100%; height:100%; max-width:none; padding:0;')
        parts.append('  position:relative; background:none; overflow:hidden;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .kicker {{ display:none; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .accent-line {{ display:none; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .shimmer-mask {{ display:none; }}')

        # Content panel: occupies 62% of frame — speaker window is 38% (matches ref ratio)
        # Round only the speaker-facing edge — screen-touching edges stay flush
        _sst_radius = "0 14px 14px 0" if _sst_side == "right" else "14px 0 0 14px"
        parts.append(f'.card[data-card-id="{card_id}"] .sst-panel {{')
        parts.append(f'  position:absolute; top:0; {_sst_panel_edge}; width:62%;')
        parts.append('  height:100%; display:flex; flex-direction:column;')
        parts.append('  align-items:flex-start; justify-content:center;')
        parts.append('  padding:0 52px; box-sizing:border-box;')
        parts.append(f'  background:{_sst_panel_bg};')
        parts.append(f'  border-radius:{_sst_radius};')
        parts.append(f'  {_sst_border_side}:2px solid {p["accent"]}45;')
        parts.append('}')

        # Kicker — flex-column so ::before accent bar stacks above the text
        parts.append(f'.card[data-card-id="{card_id}"] .sst-kicker {{')
        parts.append(f'  display:flex; flex-direction:column; gap:12px;')
        parts.append(f'  font-family:{p["font"]}; font-size:18px;')
        parts.append(f'  font-weight:700; letter-spacing:0.18em; text-transform:uppercase;')
        parts.append(f'  color:{p["accent"]}; margin-bottom:36px; opacity:0;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .sst-kicker::before {{')
        parts.append(f'  content:""; display:block; width:28px; height:3px;')
        parts.append(f'  background:{p["accent"]}; border-radius:2px; flex-shrink:0;')
        parts.append('}')

        if _sst_mode == "steps":
            # Numbered step rows
            parts.append(f'.card[data-card-id="{card_id}"] .sst-step {{')
            parts.append('  display:flex; align-items:flex-start; gap:22px;')
            parts.append('  margin-bottom:30px; opacity:0;')
            parts.append('}')
            parts.append(f'.card[data-card-id="{card_id}"] .sst-num {{')
            parts.append(f'  font-family:{p["font"]}; font-size:36px;')
            parts.append(f'  font-weight:{p["font_weight"]}; color:{p["accent"]};')
            parts.append('  line-height:1.1; min-width:46px; flex-shrink:0;')
            parts.append('}')
            parts.append(f'.card[data-card-id="{card_id}"] .sst-label {{')
            parts.append(f'  font-family:{p["font"]}; font-size:30px;')
            parts.append(f'  font-weight:{"600" if p["font_weight"] == "800" else p["font_weight"]};')
            parts.append(f'  color:{p["text"]}; line-height:1.35;')
            parts.append('}')
        elif _sst_mode == "caption":
            # Word-by-word container — flex-wrap so words flow naturally
            parts.append(f'.card[data-card-id="{card_id}"] .sst-caption {{')
            parts.append('  display:flex; flex-wrap:wrap; align-content:flex-start;')
            parts.append('  gap:0.22em 0.28em; max-width:100%;')
            parts.append('}')
            parts.append(f'.card[data-card-id="{card_id}"] .sst-cap-word {{')
            parts.append(f'  font-family:{p["font"]}; font-size:44px;')
            parts.append(f'  font-weight:{p["font_weight"]};')
            parts.append(f'  color:{p["text"]}; line-height:1.3;')
            parts.append('  opacity:0;')
            parts.append('}')
        else:
            # Vertical diagram: text-only nodes with accent dash via ::before (no icons, no arrows)
            parts.append(f'.card[data-card-id="{card_id}"] .sst-diagram {{')
            parts.append('  display:flex; flex-direction:column; align-items:flex-start; gap:32px;')
            parts.append('}')
            parts.append(f'.card[data-card-id="{card_id}"] .sst-node {{')
            parts.append('  display:flex; align-items:center; gap:16px; opacity:0;')
            parts.append('}')
            parts.append(f'.card[data-card-id="{card_id}"] .sst-node::before {{')
            parts.append(f'  content:""; display:block; width:26px; height:2px; flex-shrink:0;')
            parts.append(f'  background:{p["accent"]}; border-radius:1px;')
            parts.append('}')
            parts.append(f'.card[data-card-id="{card_id}"] .sst-dlabel {{')
            parts.append(f'  font-family:{p["font"]}; font-size:30px;')
            parts.append(f'  font-weight:{"600" if p["font_weight"] == "800" else p["font_weight"]};')
            parts.append(f'  color:{p["text"]}; line-height:1.35;')
            parts.append('}')
    # ── prim_confession_frame CSS ─────────────────────────────────────────────
    if content_style == "prim_confession_frame":
        # Light-pack text override: bg_full is always dark; craft/paper native text is dark.
        _pcf_text_color = (
            "rgba(255,255,255,0.92)"
            if p["id"] in ("lean_craft", "lean_paper")
            else p["text"]
        )
        # Card-panel: transparent — the video source (speaker) shows through underneath.
        # pcf-desat and pcf-vignette apply their moody treatment ON the live video.
        # overflow:hidden clips absolute children to the panel's border-radius.
        # bg_full is intentionally NOT used here: it is an opaque dark gradient that
        # would fully cover the speaker (same bug as other full-cover primitives).
        parts.append(f'.card[data-card-id="{card_id}"] .card-panel {{')
        parts.append('  width:100%; height:100%; max-width:none; padding:0;')
        parts.append('  position:relative; overflow:hidden; background:transparent;')
        parts.append('}')
        parts.append(f'.card[data-card-id="{card_id}"] .kicker {{ display:none; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .accent-line {{ display:none; }}')
        parts.append(f'.card[data-card-id="{card_id}"] .shimmer-mask {{ display:none; }}')
        # L0 — Desaturation overlay: mix-blend-mode:saturation drains colour from the
        # video source showing through the transparent card-panel.
        # #808080 neutral grey → saturation collapses, no hue shift.
        # filter:saturate() banned (unconfirmed SwiftShader); blend-mode is safe.
        parts.append(f'.card[data-card-id="{card_id}"] .pcf-desat {{')
        parts.append('  position:absolute; inset:0; background:#808080;')
        parts.append('  mix-blend-mode:saturation; opacity:0; pointer-events:none; z-index:1;')
        parts.append('}')
        # L1 — Vignette: gradient starts at 50% (centre stays clear), edges dim to 0.40 black.
        # Wider transparent zone prevents the halo-over-face effect seen at 30%.
        parts.append(f'.card[data-card-id="{card_id}"] .pcf-vignette {{')
        parts.append('  position:absolute; inset:0; pointer-events:none; z-index:2;')
        parts.append('  background:radial-gradient(ellipse at center, transparent 50%, rgba(0,0,0,0.40) 100%);')
        parts.append('  opacity:0;')
        parts.append('}')
        # L2/L3 — Scene: stacks line then text bottom-left.
        parts.append(f'.card[data-card-id="{card_id}"] .pcf-scene {{')
        parts.append('  position:absolute; inset:0; z-index:3;')
        parts.append('  display:flex; flex-direction:column;')
        parts.append('  justify-content:flex-end; align-items:flex-start;')
        parts.append('  padding:56px 64px;')
        parts.append('}')
        # L3 — Accent line: HF rule 7 — display:block + explicit width → scaleX non-trivial.
        # border-radius:999px = stadium-shape (mandatory). transform-origin:left → left→right reveal.
        parts.append(f'.card[data-card-id="{card_id}"] .pcf-line {{')
        parts.append('  display:block; height:2px; border-radius:999px;')
        parts.append(f'  background:{p["accent"]}; width:100px;')
        parts.append('  transform:scaleX(0); transform-origin:left center;')
        parts.append('  opacity:0; margin-bottom:18px;')
        parts.append('}')
        # L2 — Text: intimate weight (600), moderate size, bottom-left.
        parts.append(f'.card[data-card-id="{card_id}"] .pcf-text {{')
        parts.append(f'  font-family:{p["font"]}; font-size:34px; font-weight:600;')
        parts.append(f'  color:{_pcf_text_color}; line-height:1.35; opacity:0;')
        parts.append('  max-width:72%; overflow-wrap:break-word; word-break:break-word;')
        parts.append('}')
    parts.append('</style>')
    # Timeline: full-screen overlay, no card-panel wrapper
    if content_style == "timeline":
        steps = hints.get("steps", [])
        n_steps = min(len(steps), 6)
        avg_label_len = sum(len(str(s)) for s in steps[:n_steps]) / max(n_steps, 1)
        total_label_chars = sum(len(str(s)) for s in steps[:n_steps])
        use_vertical = total_label_chars > 60 or avg_label_len > 18 or n_steps > 4
        parts.append('<div class="root">')
        parts.append('  <div class="card-panel">')
        if kicker:
            parts.append(f'    <div class="kicker" id="{card_id}-kicker">{_esc(kicker)}</div>')
        parts.append(f'    <div class="tl-track" data-layout="{"vertical" if use_vertical else "horizontal"}">')
        parts.append(f'      <div class="tl-line" id="{card_id}-tl-line"></div>')
        for i, step in enumerate(steps[:n_steps]):
            parts.append(f'      <div class="tl-step" id="{card_id}-step-{i}">')
            parts.append(f'        <div class="tl-dot" id="{card_id}-dot-{i}"></div>')
            parts.append(f'        <div class="tl-label">{_esc(str(step))}</div>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
        parts.append(f'    <div class="accent-line" id="{card_id}-line"></div>')
        parts.append('  </div>')
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
        _ws_sz = 28 if compact else 44
        if p["id"] == "lean_craft":
            _ws_svg = (
                f'<svg viewBox="0 0 48 48" width="{_ws_sz}" height="{_ws_sz}">'
                f'<path d="M24 5 L43 41 H5 Z" fill="none" stroke="{_ws_acc}" stroke-width="2.5"'
                f' stroke-linejoin="round" stroke-linecap="round"/>'
                f'<path d="M24 19 L24 30" stroke="{_ws_acc}" stroke-width="2.5" stroke-linecap="round"/>'
                f'<circle cx="24" cy="35.5" r="1.8" fill="{_ws_acc}"/>'
                f'</svg>'
            )
        else:
            _ws_svg = (
                f'<svg viewBox="0 0 48 48" width="{_ws_sz}" height="{_ws_sz}">'
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
        # Normalise: LLM sometimes sends a string instead of a list
        if isinstance(_lj_pts, str):
            _lj_pts = [x.strip() for x in _lj_pts.replace(" / ", "\n").replace(" • ", "\n").split("\n") if x.strip()] or [_lj_pts]
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
    elif content_style in ("data_bar_chart", "data_chart"):
        _dbc_labels = hints.get("bar_labels", [])
        _dbc_values = hints.get("bar_values", [])
        # data_chart compat: legacy "items" format ["Label: value", ...]
        if not _dbc_labels and hints.get("items"):
            for _dc_raw in hints.get("items", [])[:4]:
                _dc_ps = str(_dc_raw).split(":", 1)
                _dbc_labels.append(_dc_ps[0].strip() if len(_dc_ps) == 2 else str(_dc_raw))
                if len(_dc_ps) == 2:
                    try:
                        _dbc_values.append(float(_dc_ps[1].strip().replace("%", "").replace(",", ".")))
                    except ValueError:
                        _dbc_values.append(float(len(_dbc_labels)))
                else:
                    _dbc_values.append(float(len(_dbc_labels)))
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
    # ── Wave 7 HTML ───────────────────────────────────────────────────────────
    elif content_style == "live_reaction_split":
        _lrs_exp  = _esc(hints.get("expected_text", hints.get("title", "")))
        _lrs_real = _esc(hints.get("reality_text",  hints.get("detail", "")))
        parts.append(f'    <div class="lrs-wrap">')
        parts.append(f'      <div class="lrs-side" id="{card_id}-lrs-expected">')
        parts.append(f'        <div class="lrs-lbl">Ce qu\'on pensait</div>')
        parts.append(f'        <div class="lrs-txt">{_lrs_exp}</div>')
        parts.append(f'      </div>')
        parts.append(f'      <div class="lrs-divider" id="{card_id}-lrs-divider"></div>')
        parts.append(f'      <div class="lrs-side" id="{card_id}-lrs-reality">')
        parts.append(f'        <div class="lrs-lbl">La réalité</div>')
        parts.append(f'        <div class="lrs-txt">{_lrs_real}</div>')
        parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "hidden_cost_reveal":
        _hcr_stk  = _esc(hints.get("sticker_price", hints.get("number", "")))
        _hcr_real = _esc(hints.get("real_cost",     hints.get("detail", "")))
        parts.append(f'    <div class="hcr-wrap">')
        parts.append(f'      <div class="hcr-block" id="{card_id}-hcr-sticker">')
        parts.append(f'        <div class="hcr-lbl">Prix affiché</div>')
        parts.append(f'        <div class="hcr-val hcr-stk-val">{_hcr_stk}</div>')
        parts.append(f'      </div>')
        parts.append(f'      <div class="hcr-arrow" id="{card_id}-hcr-arrow">→</div>')
        parts.append(f'      <div class="hcr-block" id="{card_id}-hcr-real">')
        parts.append(f'        <div class="hcr-lbl">Coût réel</div>')
        parts.append(f'        <div class="hcr-val hcr-real-val">{_hcr_real}</div>')
        parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "social_proof_counter":
        _spc_num = _esc(hints.get("counter_final_value", hints.get("number", "0")))
        _spc_lbl = _esc(hints.get("counter_label", hints.get("title", "")))
        parts.append(f'    <div class="spc-wrap">')
        parts.append(f'      <div class="spc-num" id="{card_id}-spc-num">{_spc_num}</div>')
        if _spc_lbl:
            parts.append(f'      <div class="spc-lbl" id="{card_id}-spc-lbl">{_spc_lbl}</div>')
        parts.append(f'    </div>')
    elif content_style == "timeline_prediction":
        _tp_conf = hints.get("confirmed_steps", [])
        _tp_pred = hints.get("predicted_steps", [])
        parts.append(f'    <div class="tp-wrap">')
        if _tp_conf:
            parts.append(f'      <div class="tp-sec-lbl">Confirmé</div>')
            for _tp_i, _tp_s in enumerate(_tp_conf[:4]):
                parts.append(f'      <div class="tp-conf" id="{card_id}-tp-conf-{_tp_i}">{_esc(str(_tp_s))}</div>')
        parts.append(f'      <div class="tp-div" id="{card_id}-tp-div"></div>')
        if _tp_pred:
            parts.append(f'      <div class="tp-sec-lbl">Prévu</div>')
            for _tp_j, _tp_p in enumerate(_tp_pred[:4]):
                parts.append(f'      <div class="tp-pred" id="{card_id}-tp-pred-{_tp_j}">{_esc(str(_tp_p))}</div>')
        parts.append(f'    </div>')
    elif content_style == "red_thread_connector":
        _rtc_pts = hints.get("connector_points", [])
        parts.append(f'    <div class="rtc-wrap">')
        for _rtc_i, _rtc_p in enumerate(_rtc_pts[:3]):
            if _rtc_i > 0:
                parts.append(f'      <div class="rtc-arr" id="{card_id}-rtc-arr-{_rtc_i - 1}">↓</div>')
            parts.append(f'      <div class="rtc-pt" id="{card_id}-rtc-pt-{_rtc_i}">{_esc(str(_rtc_p))}</div>')
        parts.append(f'    </div>')
    elif content_style == "silent_beat_pause":
        _sbp_sym = _esc(hints.get("pause_symbol", "…"))
        parts.append(f'    <div class="sbp-wrap">')
        parts.append(f'      <div class="sbp-sym" id="{card_id}-sbp-sym">{_sbp_sym}</div>')
        parts.append(f'    </div>')
    elif content_style == "comment_reply_style":
        _crs_com = _esc(hints.get("comment_text", hints.get("title", "")))
        _crs_rep = _esc(hints.get("reply_text",   hints.get("detail", "")))
        parts.append(f'    <div class="crs-wrap">')
        parts.append(f'      <div class="crs-comment" id="{card_id}-crs-comment">')
        parts.append(f'        <div class="crs-meta">💬 Commentaire</div>')
        parts.append(f'        <div class="crs-txt">{_crs_com}</div>')
        parts.append(f'      </div>')
        parts.append(f'      <div class="crs-reply" id="{card_id}-crs-reply">')
        parts.append(f'        <div class="crs-meta">↳ Réponse</div>')
        parts.append(f'        <div class="crs-txt crs-rtxt">{_crs_rep}</div>')
        parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "before_you_scroll":
        _bys_txt = _esc(hints.get("hook_text", hints.get("title", "")))
        parts.append(f'    <div class="bys-wrap">')
        parts.append(f'      <div class="bys-txt" id="{card_id}-bys-txt">{_bys_txt}</div>')
        parts.append(f'      <div class="bys-rule" id="{card_id}-bys-rule"></div>')
        parts.append(f'    </div>')
    # ── Wave 8 HTML ───────────────────────────────────────────────────────────
    elif content_style == "traffic_light_status":
        _tls_color = hints.get("status_color", "green").lower()
        _tls_lbl   = _esc(hints.get("status_label", hints.get("title", "")))
        _tls_hex   = {"red": "#ef4444", "yellow": "#facc15", "green": "#22c55e"}.get(_tls_color, "#22c55e")
        parts.append(f'    <div class="tls-wrap">')
        parts.append(f'      <div class="tls-light" id="{card_id}-tls-light" style="background:{_tls_hex};"></div>')
        parts.append(f'      <div class="tls-label" id="{card_id}-tls-label">{_tls_lbl}</div>')
        parts.append(f'    </div>')
    elif content_style == "day_in_life_schedule":
        _dls_items = hints.get("schedule_items", hints.get("items", []))
        parts.append(f'    <div class="dls-wrap">')
        for _dls_i, _dls_s in enumerate(_dls_items[:6]):
            parts.append(f'      <div class="dls-item" id="{card_id}-dls-item-{_dls_i}">')
            parts.append(f'        <div class="dls-dot"></div>')
            parts.append(f'        <span>{_esc(str(_dls_s))}</span>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "skill_tree_unlock":
        _stu_items = hints.get("unlocked_milestones", hints.get("items", []))
        parts.append(f'    <div class="stu-wrap">')
        for _stu_i, _stu_m in enumerate(_stu_items[:5]):
            parts.append(f'      <div class="stu-item" id="{card_id}-stu-item-{_stu_i}">')
            parts.append(f'        <span class="stu-icon">&#9733;</span>')
            parts.append(f'        <span>{_esc(str(_stu_m))}</span>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "audience_poll_result":
        _apr_opts = hints.get("poll_options", hints.get("items", []))
        _apr_pcts_raw = hints.get("poll_percentages", [])
        if not _apr_pcts_raw:
            _n = max(len(_apr_opts), 1)
            _apr_pcts_raw = [round(100.0 / _n, 1)] * len(_apr_opts)
        _apr_winner_idx = _apr_pcts_raw.index(max(_apr_pcts_raw)) if _apr_pcts_raw else 0
        parts.append(f'    <div class="apr-wrap">')
        for _apr_i, _apr_opt in enumerate(_apr_opts[:4]):
            _apr_pct = float(_apr_pcts_raw[_apr_i]) if _apr_i < len(_apr_pcts_raw) else 0.0
            _apr_wcls = " apr-winner" if _apr_i == _apr_winner_idx else ""
            parts.append(f'      <div class="apr-row{_apr_wcls}" id="{card_id}-apr-row-{_apr_i}">')
            parts.append(f'        <div class="apr-label">{_esc(str(_apr_opt))}</div>')
            parts.append(f'        <div class="apr-bar-track">')
            parts.append(f'          <div class="apr-bar-fill" id="{card_id}-apr-fill-{_apr_i}" data-pct="{_apr_pct}"></div>')
            parts.append(f'        </div>')
            parts.append(f'        <div class="apr-pct">{_apr_pct:.0f}%</div>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "broken_promise_tracker":
        _bpt_promises = hints.get("promises", hints.get("items", []))
        _bpt_kept     = hints.get("kept_status", [True] * len(_bpt_promises))
        parts.append(f'    <div class="bpt-wrap">')
        for _bpt_i, _bpt_p in enumerate(_bpt_promises[:5]):
            _bpt_k = bool(_bpt_kept[_bpt_i]) if _bpt_i < len(_bpt_kept) else True
            _bpt_icon_cls = "bpt-icon-kept" if _bpt_k else "bpt-icon-broken"
            _bpt_icon     = "&#10003;" if _bpt_k else "&#10007;"
            parts.append(f'      <div class="bpt-item" id="{card_id}-bpt-item-{_bpt_i}">')
            parts.append(f'        <span class="{_bpt_icon_cls}">{_bpt_icon}</span>')
            parts.append(f'        <span>{_esc(str(_bpt_p))}</span>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "ingredient_list":
        _igl_items = hints.get("ingredients", hints.get("items", []))
        parts.append(f'    <div class="igl-wrap">')
        for _igl_i, _igl_s in enumerate(_igl_items[:6]):
            parts.append(f'      <div class="igl-item" id="{card_id}-igl-item-{_igl_i}">')
            parts.append(f'        <div class="igl-bullet"></div>')
            parts.append(f'        <span>{_esc(str(_igl_s))}</span>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "resource_allocation":
        _ral_labels = hints.get("resource_labels", [])
        _ral_values = hints.get("resource_values", [])
        _ral_max    = max((float(v) for v in _ral_values), default=1.0) or 1.0
        parts.append(f'    <div class="ral-wrap">')
        for _ral_i, _ral_lbl in enumerate(_ral_labels[:5]):
            _ral_pct = round((float(_ral_values[_ral_i]) / _ral_max) * 100, 1) if _ral_i < len(_ral_values) else 0.0
            parts.append(f'      <div class="ral-row" id="{card_id}-ral-seg-{_ral_i}">')
            parts.append(f'        <div class="ral-label">{_esc(str(_ral_lbl))}</div>')
            parts.append(f'        <div class="ral-track">')
            parts.append(f'          <div class="ral-fill" id="{card_id}-ral-fill-{_ral_i}" data-pct="{_ral_pct}"></div>')
            parts.append(f'        </div>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "fill_in_the_blank":
        _fitb_sent = _esc(hints.get("sentence_with_blank", hints.get("title", "")))
        _fitb_word = _esc(hints.get("blank_word", hints.get("detail", "?")))
        parts.append(f'    <div class="fitb-wrap">')
        parts.append(f'      <div class="fitb-sentence" id="{card_id}-fitb-sentence">{_fitb_sent}</div>')
        parts.append(f'      <div class="fitb-word" id="{card_id}-fitb-word">{_fitb_word}</div>')
        parts.append(f'    </div>')
    # ── Wave 9 ────────────────────────────────────────────────────────────────
    elif content_style == "streak_counter":
        _sk_count = _esc(hints.get("streak_count", hints.get("number", "0")))
        _sk_unit  = _esc(hints.get("streak_unit", ""))
        _sk_label = _esc(hints.get("streak_label", hints.get("title", "")))
        parts.append(f'    <div class="sk-wrap">')
        parts.append(f'      <div class="sk-row">')
        parts.append(f'        <div class="sk-count" id="{card_id}-sk-count">{_sk_count}</div>')
        if _sk_unit:
            parts.append(f'        <div class="sk-unit" id="{card_id}-sk-unit">{_sk_unit}</div>')
        parts.append(f'      </div>')
        if _sk_label:
            parts.append(f'      <div class="sk-label" id="{card_id}-sk-label">{_sk_label}</div>')
        parts.append(f'    </div>')
    elif content_style == "before_now_later":
        _bnl_labels = [
            _esc(hints.get("before_label", "Avant")),
            _esc(hints.get("now_label", "Maintenant")),
            _esc(hints.get("later_label", "Après")),
        ]
        _bnl_tags = ["AVANT", "MAINTENANT", "APRÈS"]
        parts.append(f'    <div class="bnl-wrap">')
        for _bnl_i, (_bnl_tag, _bnl_lbl) in enumerate(zip(_bnl_tags, _bnl_labels)):
            parts.append(f'      <div class="bnl-slot" id="{card_id}-bnl-{_bnl_i}">')
            parts.append(f'        <div class="bnl-tag">{_bnl_tag}</div>')
            parts.append(f'        <div class="bnl-text">{_bnl_lbl}</div>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "platform_stats":
        _pst_plats = hints.get("platforms", [])
        _pst_vals  = hints.get("values", [])
        parts.append(f'    <div class="pst-wrap">')
        for _pst_i, _pst_p in enumerate(_pst_plats[:5]):
            _pst_v = _pst_vals[_pst_i] if _pst_i < len(_pst_vals) else ""
            parts.append(f'      <div class="pst-row" id="{card_id}-pst-row-{_pst_i}">')
            parts.append(f'        <div class="pst-name">{_esc(str(_pst_p))}</div>')
            parts.append(f'        <div class="pst-val">{_esc(str(_pst_v))}</div>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "cost_comparison":
        _cco_labels = hints.get("option_labels", [])
        _cco_prices = hints.get("option_prices", [])
        _cco_n      = min(len(_cco_labels), 4)
        _cco_best   = hints.get("best_index", -1)
        if _cco_best < 0:
            _cco_best = _cco_n - 1
        parts.append(f'    <div class="cco-wrap">')
        for _cco_i, _cco_lbl in enumerate(_cco_labels[:4]):
            _cco_price = _cco_prices[_cco_i] if _cco_i < len(_cco_prices) else ""
            _cco_cls = "cco-col cco-best" if _cco_i == _cco_best else "cco-col"
            parts.append(f'      <div class="{_cco_cls}" id="{card_id}-cco-col-{_cco_i}">')
            parts.append(f'        <div class="cco-label">{_esc(str(_cco_lbl))}</div>')
            parts.append(f'        <div class="cco-price">{_esc(str(_cco_price))}</div>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "decision_matrix":
        if title:
            parts.append(f'    <div class="title" id="{card_id}-title">{_split_title_accent(display_text, accent_word_hint, card_id)}</div>')
        _dmx_quads = hints.get("quadrant_labels", ["", "", "", ""])
        parts.append(f'    <div class="dmx-wrap">')
        for _dmx_i, _dmx_q in enumerate(_dmx_quads[:4]):
            parts.append(f'      <div class="dmx-q" id="{card_id}-dmx-q-{_dmx_i}">')
            parts.append(f'        <div class="dmx-q-label">{_esc(str(_dmx_q))}</div>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "habit_tracker":
        _ht_label = _esc(hints.get("habit_label", hints.get("title", "")))
        _ht_days  = hints.get("days_completed", [])
        parts.append(f'    <div class="ht-wrap">')
        if _ht_label:
            parts.append(f'      <div class="ht-label" id="{card_id}-ht-label">{_ht_label}</div>')
        parts.append(f'      <div class="ht-days">')
        for _ht_i, _ht_done in enumerate(_ht_days[:14]):
            _ht_cls = "ht-day ht-done" if _ht_done else "ht-day"
            parts.append(f'        <div class="{_ht_cls}" id="{card_id}-ht-day-{_ht_i}"></div>')
        parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "income_vs_expense":
        _ive_inc_val = _esc(hints.get("income_value", ""))
        _ive_exp_val = _esc(hints.get("expense_value", ""))
        _ive_inc_lbl = _esc(hints.get("income_label", "Revenus"))
        _ive_exp_lbl = _esc(hints.get("expense_label", "Dépenses"))
        _ive_inc_raw = ''.join(c for c in str(hints.get("income_value", "0")) if c.isdigit() or c == '.')
        _ive_exp_raw = ''.join(c for c in str(hints.get("expense_value", "0")) if c.isdigit() or c == '.')
        try:
            _ive_inc_n = max(0.0, float(_ive_inc_raw or '0'))
        except (ValueError, TypeError):
            _ive_inc_n = 0.0
        try:
            _ive_exp_n = max(0.0, float(_ive_exp_raw or '0'))
        except (ValueError, TypeError):
            _ive_exp_n = 0.0
        _ive_max = max(_ive_inc_n, _ive_exp_n, 1.0)
        _ive_inc_pct = round((_ive_inc_n / _ive_max) * 100, 1)
        _ive_exp_pct = round((_ive_exp_n / _ive_max) * 100, 1)
        parts.append(f'    <div class="ive-wrap">')
        parts.append(f'      <div class="ive-row" id="{card_id}-ive-income">')
        parts.append(f'        <div class="ive-meta">')
        parts.append(f'          <div class="ive-label">{_ive_inc_lbl}</div>')
        parts.append(f'          <div class="ive-val-income">{_ive_inc_val}</div>')
        parts.append(f'        </div>')
        parts.append(f'        <div class="ive-track">')
        parts.append(f'          <div class="ive-fill-income" id="{card_id}-ive-fill-income" data-pct="{_ive_inc_pct}"></div>')
        parts.append(f'        </div>')
        parts.append(f'      </div>')
        parts.append(f'      <div class="ive-row" id="{card_id}-ive-expense">')
        parts.append(f'        <div class="ive-meta">')
        parts.append(f'          <div class="ive-label">{_ive_exp_lbl}</div>')
        parts.append(f'          <div class="ive-val-expense">{_ive_exp_val}</div>')
        parts.append(f'        </div>')
        parts.append(f'        <div class="ive-track">')
        parts.append(f'          <div class="ive-fill-expense" id="{card_id}-ive-fill-expense" data-pct="{_ive_exp_pct}"></div>')
        parts.append(f'        </div>')
        parts.append(f'      </div>')
        parts.append(f'    </div>')
    # ── Wave 10 ───────────────────────────────────────────────────────────────
    elif content_style == "milestone_recap":
        _mr_ms = hints.get("milestones", hints.get("items", []))
        parts.append(f'    <div class="mr-wrap">')
        for _i, _ms in enumerate(_mr_ms[:6]):
            parts.append(f'      <div class="mr-item" id="{card_id}-mr-item-{_i}">')
            parts.append(f'        <div class="mr-dot"></div>')
            parts.append(f'        <div class="mr-text">{_esc(str(_ms))}</div>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "content_calendar":
        _cal_entries = hints.get("calendar_items", hints.get("items", []))
        parts.append(f'    <div class="cal-wrap">')
        for _i, _cal_e in enumerate(_cal_entries[:7]):
            _cal_s = str(_cal_e)
            _sep = '—' if '—' in _cal_s else ('-' if '-' in _cal_s else None)
            if _sep:
                _cal_parts = _cal_s.split(_sep, 1)
                _cal_day = _esc(_cal_parts[0].strip())
                _cal_cnt = _esc(_cal_parts[1].strip()) if len(_cal_parts) > 1 else ""
            else:
                _cal_day, _cal_cnt = "", _esc(_cal_s)
            parts.append(f'      <div class="cal-item" id="{card_id}-cal-item-{_i}">')
            if _cal_day:
                parts.append(f'        <div class="cal-day">{_cal_day}</div>')
            parts.append(f'        <div class="cal-content">{_cal_cnt}</div>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "client_result_number":
        _crn_val = _esc(hints.get("result_value", hints.get("number", "")))
        _crn_ctx = _esc(hints.get("result_context", hints.get("detail", "")))
        _crn_lbl = hints.get("client_label", "")
        parts.append(f'    <div class="crn-wrap">')
        parts.append(f'      <div class="crn-value" id="{card_id}-crn-value">{_crn_val}</div>')
        parts.append(f'      <div class="crn-context" id="{card_id}-crn-context">{_crn_ctx}</div>')
        if _crn_lbl:
            parts.append(f'      <div class="crn-label" id="{card_id}-crn-label">{_esc(_crn_lbl)}</div>')
        parts.append(f'    </div>')
    elif content_style == "mistake_lesson":
        _ml_err = _esc(hints.get("mistake_text", ""))
        _ml_lsn = _esc(hints.get("lesson_text", ""))
        parts.append(f'    <div class="ml-wrap">')
        parts.append(f'      <div class="ml-block ml-mistake" id="{card_id}-ml-mistake">')
        parts.append(f'        <div class="ml-tag ml-tag-err">Erreur</div>')
        parts.append(f'        <div class="ml-text">{_ml_err}</div>')
        parts.append(f'      </div>')
        parts.append(f'      <div class="ml-block ml-lesson" id="{card_id}-ml-lesson">')
        parts.append(f'        <div class="ml-tag ml-tag-lsn">Leçon</div>')
        parts.append(f'        <div class="ml-text">{_ml_lsn}</div>')
        parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "tool_comparison":
        _tc_names = hints.get("tool_names", [])
        _tc_feats = hints.get("tool_features", hints.get("items", []))
        parts.append(f'    <div class="tc-wrap">')
        if _tc_names:
            parts.append(f'      <div class="tc-heads" id="{card_id}-tc-heads">')
            for _tc_n in _tc_names[:3]:
                parts.append(f'        <div class="tc-head">{_esc(str(_tc_n))}</div>')
            parts.append(f'      </div>')
        parts.append(f'      <div class="tc-feats">')
        for _i, _tc_f in enumerate(_tc_feats[:5]):
            parts.append(f'        <div class="tc-feat" id="{card_id}-tc-feat-{_i}">{_esc(str(_tc_f))}</div>')
        parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "weekly_review":
        _wr_cats = hints.get("review_categories", hints.get("items", []))
        _wr_scrs = hints.get("review_scores", [])
        parts.append(f'    <div class="wr-wrap">')
        for _i, _wr_c in enumerate(_wr_cats[:6]):
            _wr_s = _esc(str(_wr_scrs[_i])) if _i < len(_wr_scrs) else ""
            parts.append(f'      <div class="wr-item" id="{card_id}-wr-item-{_i}">')
            parts.append(f'        <div class="wr-cat">{_esc(str(_wr_c))}</div>')
            if _wr_s:
                parts.append(f'        <div class="wr-score">{_wr_s}</div>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "audience_question":
        _aq_q = _esc(hints.get("question_text", hints.get("title", "")))
        parts.append(f'    <div class="aq-wrap">')
        parts.append(f'      <div class="aq-q" id="{card_id}-aq-q">{_aq_q}</div>')
        parts.append(f'    </div>')
    # ── Catalogue primitives HTML (Wave 11) ──────────────────────────────────
    elif content_style == "prim_stat_counter":
        _psc_pfx = _esc(hints.get("prefix", ""))
        _psc_num = _esc(hints.get("number", "0"))
        _psc_sfx = _esc(hints.get("suffix", ""))
        _psc_kck = _esc(hints.get("title", hints.get("kicker", "")))
        parts.append(f'    <div class="psc-row">')
        if _psc_pfx:
            parts.append(f'      <span class="psc-side" id="{card_id}-psc-prefix">{_psc_pfx}</span>')
        parts.append(f'      <span class="psc-number" id="{card_id}-psc-number">{_psc_num}</span>')
        if _psc_sfx:
            parts.append(f'      <span class="psc-side" id="{card_id}-psc-suffix">{_psc_sfx}</span>')
        parts.append(f'    </div>')
        if _psc_kck:
            parts.append(f'    <div class="psc-kicker" id="{card_id}-psc-kicker">{_psc_kck}</div>')
    elif content_style == "prim_numbered_rule":
        _pnr_num  = _esc(hints.get("number", "1"))
        _pnr_rule = _esc(hints.get("title", ""))
        parts.append(f'    <div class="pnr-number" id="{card_id}-pnr-number">{_pnr_num}</div>')
        if _pnr_rule:
            parts.append(f'    <div class="pnr-rule" id="{card_id}-pnr-rule">{_pnr_rule}</div>')
    elif content_style == "prim_anecdote_frame":
        parts.append(f'    <div class="af-tint" id="{card_id}-af-tint"></div>')
        parts.append(f'    <div class="af-vignette" id="{card_id}-af-vignette"></div>')
        parts.append(f'    <div class="af-grain" id="{card_id}-af-grain"></div>')
    elif content_style == "prim_split_compare":
        _spc_l = _esc(hints.get("left_label", "A"))
        _spc_r = _esc(hints.get("right_label", "B"))
        if kicker:
            parts.append(f'    <div class="spc-kicker" id="{card_id}-spc-kicker">{_esc(kicker)}</div>')
        parts.append(f'    <div class="spc-half spc-left" id="{card_id}-spc-left">')
        parts.append(f'      <div class="spc-label" id="{card_id}-spc-label-l">{_spc_l}</div>')
        parts.append(f'    </div>')
        parts.append(f'    <div class="spc-half spc-right" id="{card_id}-spc-right">')
        parts.append(f'      <div class="spc-label" id="{card_id}-spc-label-r">{_spc_r}</div>')
        parts.append(f'    </div>')
        parts.append(f'    <div class="spc-divider" id="{card_id}-spc-divider"></div>')
    elif content_style == "prim_journey_map":
        # ── prim_journey_map — hardcoded France→Thailand prototype ──────────
        _jmt_from = _esc(hints.get("from_city", "Paris"))
        _jmt_to   = _esc(hints.get("to_city", "Bangkok"))
        _jmt_fc   = _esc(hints.get("from_country", "France"))
        _jmt_tc   = _esc(hints.get("to_country", "Thaïlande"))
        parts.append(f'  <div class="jmt-header" id="{card_id}-jmt-header">')
        parts.append(f'    <div class="jmt-route"><span>{_jmt_from}</span><span class="jmt-arrow">&#x203A;</span><span>{_jmt_to}</span></div>')
        parts.append(f'    <div class="jmt-sub">vol direct</div>')
        parts.append(f'  </div>')
        parts.append(f'  <div class="jmt-sep"></div>')
        parts.append(f'  <div class="jmt-map">')
        parts.append(f'    <svg id="{card_id}-jmt-svg" viewBox="0 0 400 260" xmlns="http://www.w3.org/2000/svg">')
        parts.append(f'      <defs>')
        parts.append(f'        <radialGradient id="{card_id}-jmt-gd" cx="50%" cy="50%" r="50%">')
        parts.append(f'          <stop offset="0%" stop-color="{p["accent"]}" stop-opacity=".9"/>')
        parts.append(f'          <stop offset="100%" stop-color="{p["accent"]}" stop-opacity="0"/>')
        parts.append(f'        </radialGradient>')
        parts.append(f'        <filter id="{card_id}-jmt-gw" x="-25%" y="-25%" width="150%" height="150%">')
        parts.append(f'          <feGaussianBlur stdDeviation="2.5" result="blur"/>')
        parts.append(f'          <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>')
        parts.append(f'        </filter>')
        parts.append(f'      </defs>')
        parts.append(f'      <rect width="400" height="260" fill="#0a1525" fill-opacity="0.75"/>')
        # Latitude ellipses (decorative, very faint — flight-tracker aesthetic)
        parts.append(f'      <ellipse cx="200" cy="260" rx="350" ry="140" fill="none" stroke="{p["accent"]}09" stroke-width="1"/>')
        parts.append(f'      <ellipse cx="200" cy="260" rx="260" ry="100" fill="none" stroke="{p["accent"]}09" stroke-width="1"/>')
        parts.append(f'      <ellipse cx="200" cy="260" rx="170" ry="62"  fill="none" stroke="{p["accent"]}09" stroke-width="1"/>')
        # Meridian lines
        for _gx in [100, 200, 300]:
            parts.append(f'      <line x1="{_gx}" y1="0" x2="{_gx}" y2="260" stroke="{p["accent"]}07" stroke-width="1"/>')
        # Arc background (dashed guide, always visible)
        parts.append(f'      <path d="M 71,79 C 140,28 215,40 289,152" fill="none" stroke="{p["accent"]}18" stroke-width="1.5" stroke-dasharray="5 4"/>')
        # Trail glow layer (wide+blurred) — GSAP animates dashoffset in sync with trail
        parts.append(f'      <path id="{card_id}-jmt-trail-glow" d="M 71,79 C 140,28 215,40 289,152" fill="none" stroke="{p["accent"]}44" stroke-width="5" stroke-linecap="round" stroke-dasharray="265" stroke-dashoffset="265" filter="url(#{card_id}-jmt-gw)"/>')
        # Trail sharp — dasharray=265 dashoffset=265 → hidden; GSAP reduces to 0
        parts.append(f'      <path id="{card_id}-jmt-trail" d="M 71,79 C 140,28 215,40 289,152" fill="none" stroke="{p["accent"]}" stroke-width="2" stroke-linecap="round" stroke-dasharray="265" stroke-dashoffset="265"/>')
        # Departure dot (Paris — hardcoded at 71,79 in 400×260 map viewBox)
        parts.append(f'      <circle id="{card_id}-jmt-df" cx="71" cy="79" r="4.5" fill="none" stroke="{p["accent"]}" stroke-width="1.5" opacity="0"/>')
        parts.append(f'      <circle id="{card_id}-jmt-dfi" cx="71" cy="79" r="2" fill="{p["accent"]}" opacity="0"/>')
        parts.append(f'      <circle id="{card_id}-jmt-gf" cx="71" cy="79" r="14" fill="url(#{card_id}-jmt-gd)" opacity="0"/>')
        # Arrival dot (Bangkok — hardcoded at 289,152)
        parts.append(f'      <circle id="{card_id}-jmt-dt" cx="289" cy="152" r="4.5" fill="none" stroke="{p["accent"]}" stroke-width="1.5" opacity="0"/>')
        parts.append(f'      <circle id="{card_id}-jmt-dti" cx="289" cy="152" r="2" fill="{p["accent"]}" opacity="0"/>')
        parts.append(f'      <circle id="{card_id}-jmt-gt" cx="289" cy="152" r="14" fill="url(#{card_id}-jmt-gd)" opacity="0"/>')
        # City labels
        parts.append(f'      <text id="{card_id}-jmt-lf" x="78" y="76" font-family="system-ui,sans-serif" font-size="10" font-weight="700" fill="{p["text"]}" opacity="0">{_jmt_from}</text>')
        parts.append(f'      <text id="{card_id}-jmt-sf" x="78" y="87" font-family="system-ui,sans-serif" font-size="7.5" font-weight="400" fill="{p["text"]}99" opacity="0">{_jmt_fc}</text>')
        parts.append(f'      <text id="{card_id}-jmt-lt" x="294" y="150" font-family="system-ui,sans-serif" font-size="10" font-weight="700" fill="{p["text"]}" opacity="0">{_jmt_to}</text>')
        parts.append(f'      <text id="{card_id}-jmt-st" x="294" y="161" font-family="system-ui,sans-serif" font-size="7.5" font-weight="400" fill="{p["text"]}99" opacity="0">{_jmt_tc}</text>')
        # Plane (polygon chevron, GSAP will move via transform attribute)
        parts.append(f'      <g id="{card_id}-jmt-plane" transform="translate(71,79) rotate(-45)">')
        parts.append(f'        <polygon points="0,-6 4.5,5 0,2.5 -4.5,5" fill="{p["accent"]}"/>')
        parts.append(f'        <polygon points="-7,0 7,0 4.5,5 0,2.5 -4.5,5" fill="{p["accent"]}7a"/>')
        parts.append(f'      </g>')
        parts.append('    </svg>')
        parts.append('  </div>')
        parts.append('  <div class="jmt-sep"></div>')
        parts.append(f'  <div class="jmt-footer" id="{card_id}-jmt-footer">')
        parts.append(f'    <div><div class="jmt-city">{_jmt_from}</div><div class="jmt-ctry">{_jmt_fc}</div></div>')
        parts.append(f'    <div style="text-align:right"><div class="jmt-city">{_jmt_to}</div><div class="jmt-ctry">{_jmt_tc}</div></div>')
        parts.append(f'  </div>')
    elif content_style == "prim_cinematic_reveal":
        # ── prim_cinematic_reveal HTML — multi-layer depth reveal ─────────────
        # Generic .kicker is suppressed via CSS (display:none); pcr-kicker is explicit.
        _pcr_title_t  = _esc(hints.get("title", ""))
        _pcr_kicker_t = _esc(hints.get("kicker", ""))
        _pcr_detail_t = _esc(hints.get("detail", ""))
        parts.append(f'    <div class="pcr-scene">')
        parts.append(f'      <div class="pcr-bg" id="{card_id}-pcr-bg"></div>')
        if _pcr_kicker_t:
            parts.append(f'      <div class="pcr-kicker" id="{card_id}-pcr-kicker">{_pcr_kicker_t}</div>')
        parts.append(f'      <div class="pcr-title" id="{card_id}-pcr-title">{_pcr_title_t}</div>')
        parts.append(f'      <div class="pcr-line" id="{card_id}-pcr-line"></div>')
        if _pcr_detail_t:
            parts.append(f'      <div class="pcr-detail" id="{card_id}-pcr-detail">{_pcr_detail_t}</div>')
        parts.append(f'    </div>')
    elif content_style == "prim_ascension_reveal":
        # ── prim_ascension_reveal HTML — 5-layer ascension reveal ─────────────
        # Generic .kicker suppressed via CSS (display:none); par-kicker is explicit.
        _par_title_t  = _esc(hints.get("title", ""))
        _par_kicker_t = _esc(hints.get("kicker", ""))
        parts.append(f'    <div class="par-scene">')
        parts.append(f'      <div class="par-halo"  id="{card_id}-par-halo"></div>')
        parts.append(f'      <div class="par-ring"  id="{card_id}-par-ring"></div>')
        parts.append(f'      <div class="par-horizon" id="{card_id}-par-horizon"></div>')
        parts.append(f'      <div class="par-title" id="{card_id}-par-title">{_par_title_t}</div>')
        if _par_kicker_t:
            parts.append(f'      <div class="par-kicker" id="{card_id}-par-kicker">{_par_kicker_t}</div>')
        parts.append(f'    </div>')
    elif content_style == "prim_shatter_truth":
        # prim_shatter_truth HTML — myth + 5 fragment shards + flash + truth
        _pst_myth_t  = _esc(hints.get("myth_text", hints.get("title", "")))
        _pst_truth_t = _esc(hints.get("truth_text", ""))
        parts.append(f'    <div class="pst-scene">')
        # Layer A — myth text + clip-path fragment shards (stacked in myth-wrap)
        parts.append(f'      <div class="pst-layer">')
        parts.append(f'        <div class="pst-myth-wrap">')
        parts.append(f'          <div class="pst-myth" id="{card_id}-pst-myth">{_pst_myth_t}</div>')
        parts.append(f'          <div class="pst-frags">')
        for _pst_i in range(5):
            parts.append(f'            <div class="pst-frag" id="{card_id}-pst-frag-{_pst_i}">{_pst_myth_t}</div>')
        parts.append(f'          </div>')
        parts.append(f'        </div>')
        parts.append(f'      </div>')
        # Flash: scene-level, z-index:10, covers all layers at impact
        parts.append(f'      <div class="pst-flash" id="{card_id}-pst-flash"></div>')
        # Layer B — truth text (centered by flex parent, no transform conflict)
        parts.append(f'      <div class="pst-layer">')
        parts.append(f'        <div class="pst-truth" id="{card_id}-pst-truth">{_pst_truth_t}</div>')
        parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "prim_split_stage":
        # ── prim_split_stage HTML — content panel (video side has no HTML)
        _sst_mode_h     = hints.get("mode", "steps")
        _sst_kicker_t   = _esc(hints.get("kicker", "") or kicker or "")
        _sst_steps_data = hints.get("steps", [])
        _sst_nodes_data = hints.get("nodes", [])
        parts.append(f'    <div class="sst-panel" id="{card_id}-sst-panel">')
        if _sst_kicker_t:
            parts.append(f'      <div class="sst-kicker" id="{card_id}-sst-kicker">{_sst_kicker_t}</div>')
        if _sst_mode_h == "steps":
            for _si, _step in enumerate(_sst_steps_data[:5]):
                parts.append(f'      <div class="sst-step" id="{card_id}-sst-step-{_si}">')
                parts.append(f'        <div class="sst-num">{_si + 1}.</div>')
                parts.append(f'        <div class="sst-label">{_esc(str(_step))}</div>')
                parts.append(f'      </div>')
        elif _sst_mode_h == "caption":
            _sst_cap_words = hints.get("caption_words", [])
            if _sst_cap_words:
                parts.append(f'      <div class="sst-caption" id="{card_id}-sst-caption">')
                for _cwi, _cw in enumerate(_sst_cap_words):
                    _cw_text = _esc(str(_cw.get("text", "") if isinstance(_cw, dict) else _cw))
                    parts.append(f'        <span class="sst-cap-word" id="{card_id}-sst-cw-{_cwi}">{_cw_text}</span>')
                parts.append('      </div>')
        else:
            parts.append(f'      <div class="sst-diagram" id="{card_id}-sst-diagram">')
            for _ni, _node in enumerate(_sst_nodes_data[:4]):
                _sst_lbl = _esc(str(_node.get("label", _node))) if isinstance(_node, dict) else _esc(str(_node))
                parts.append(f'        <div class="sst-node" id="{card_id}-sst-node-{_ni}">')
                parts.append(f'          <div class="sst-dlabel">{_sst_lbl}</div>')
                parts.append(f'        </div>')
            parts.append(f'      </div>')
        parts.append(f'    </div>')
    elif content_style == "prim_confession_frame":
        # ── prim_confession_frame HTML — 4-layer fragility reveal ─────────────
        # L0 pcf-desat + L1 pcf-vignette: full-canvas overlays (absolute, no text).
        # L2 pcf-text + L3 pcf-line: stacked bottom-left inside pcf-scene.
        _pcf_confession_t = _esc(hints.get("confession_text", ""))
        parts.append(f'    <div class="pcf-desat" id="{card_id}-pcf-desat"></div>')
        parts.append(f'    <div class="pcf-vignette" id="{card_id}-pcf-vignette"></div>')
        parts.append(f'    <div class="pcf-scene">')
        parts.append(f'      <div class="pcf-line" id="{card_id}-pcf-line"></div>')
        parts.append(f'      <div class="pcf-text" id="{card_id}-pcf-text">{_pcf_confession_t}</div>')
        parts.append(f'    </div>')
    elif content_style == "number_hero":
        _nh_number_t  = _esc(hints.get("nh_number", ""))
        _nh_kicker_t  = _esc(hints.get("nh_kicker", "") or kicker or "")
        _nh_detail_t  = _esc(hints.get("nh_detail", "") or detail or "")
        parts.append(f'    <div class="nh-scene">')
        parts.append(f'      <div class="nh-spotlight" id="{card_id}-nh-spotlight"></div>')
        if _nh_kicker_t:
            parts.append(f'      <div class="nh-kicker" id="{card_id}-nh-kicker">{_nh_kicker_t}</div>')
        parts.append(f'      <div class="nh-line" id="{card_id}-nh-line-top"></div>')
        parts.append(f'      <div class="nh-number" id="{card_id}-nh-number">{_nh_number_t}</div>')
        parts.append(f'      <div class="nh-line" id="{card_id}-nh-line-bottom"></div>')
        if _nh_detail_t:
            parts.append(f'      <div class="nh-detail" id="{card_id}-nh-detail">{_nh_detail_t}</div>')
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


def _build_caption_card_html(card: dict, pack: dict | None = None, layout: str = "portrait") -> str:
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

    font_size   = "62px" if layout == "portrait" else "48px"
    font_weight = "700"
    padding     = "16px 24px"
    css_color   = "#FFFFFF"
    css_shadow  = "0 2px 8px rgba(0,0,0,0.8), 0 0 2px rgba(0,0,0,0.9)"
    css_scrim   = ""

    return (
        f'<div class="card caption-card" data-card-id="{card_id}">\n'
        f'<style>\n'
        f'.card[data-card-id="{card_id}"] .cap-line {{\n'
        f'  display: flex; flex-wrap: wrap; justify-content: center; align-items: center;\n'
        f'  gap: 0.3em; padding: {padding};\n'
        f'  font-family: {p["font"]};\n'
        f'  font-size: {font_size}; font-weight: {font_weight}; color: {css_color};\n'
        f'  text-shadow: {css_shadow};\n'
        f'  text-align: center; line-height: 1.4;\n'
        f'{css_scrim}'
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
    layout: str = "portrait",
    video_pos_x: float = 50.0,
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
        # Runtime guard: wrap tl.fromTo / tl.to / tl.set so that tweens targeting a
        # selector that doesn't exist in the rendered HTML are skipped with a console
        # warning rather than silently no-oping while the CSS-set opacity:0 persists.
        # This means a future HTML/JS drift (new pack or type) degrades to a visible
        # warning in the Puppeteer log instead of a blank card in production.
        '  (function(){',
        '    function _guard(orig){',
        '      return function(target,a,b,c){',
        '        if(typeof target==="string"){',
        '          var el=document.querySelector(target);',
        '          if(!el){console.warn("[GSAP-GUARD] target not found: "+target);return tl;}',
        '          return orig.call(tl,el,a,b,c);',
        '        }',
        '        return orig.call(tl,target,a,b,c);',
        '      };',
        '    }',
        '    tl.fromTo=_guard(tl.fromTo);',
        '    tl.to    =_guard(tl.to);',
        '    tl.set   =_guard(tl.set);',
        '  })();',
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
        # Seed the initial scale so tl.to() below always starts from the
        # authored baseline rather than the element's CSS default (scale:1).
        _first_ze = next((ze for ze in zoom_entries if ze.get("kind", "drift") != "jump_cut"), None)
        if _first_ze:
            _init_scale = float(_first_ze.get("from", 1.0))
            lines.append(f'  tl.set("#video-wrap", {{ scale: {_init_scale:.4f} }}, 0);')
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
                elif kind == "punch_in":
                    ease = '"power2.out"'
                elif kind == "pull_out":
                    ease = '"power2.in"'
                else:
                    ease = '"sine.inOut"'
                # tl.to() (not fromTo) — continues from current animated value.
                # tl.fromTo() forced the 'from' scale at the tween's start time,
                # creating a visible snap when adjacent entries had mismatched
                # boundary values (e.g. drift ending at 1.20, pull_out declaring
                # from:1.08 → 0.12 snap at t=32.54s in job 45bf7899).
                lines.append(
                    f'  tl.to("#video-wrap", '
                    f'{{ scale: {zto:.4f}, duration: {zdur:.4f}, ease: {ease}, '
                    f'transformOrigin: "{transform_origin}", overwrite: "auto" }}, '
                    f'{zs:.4f});'
                )
        lines.append("")

    # Pre-compute graphic windows so captions that start during a graphic card
    # don't run their fade-in animation (the post-loop to(opacity:0) suppression
    # reads current opacity=0 and is a no-op, letting fromTo(0→1) win).
    _graphic_windows_pre = [
        (round(float(c.get("startSec", 0)), 3), round(float(c.get("endSec", 0)), 3))
        for c in cards if c.get("type") != "caption"
    ]
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

        # Portrait per-card scrim: fade in with the card.
        if card.get("type") != "caption" and layout == "portrait":
            scrim_sel = f'#{card_id}-scrim'
            lines.append(
                f'  tl.to(\'{scrim_sel}\', {{opacity:1,duration:0.25,ease:_eIn}},{start:.4f});'
            )

        if is_caption:
            # Skip fade-in if caption starts during an active graphic card window.
            # The post-loop suppression tl.to(opacity:0) is a no-op in that case
            # because it reads current opacity=0 and animates 0→0, letting the
            # fromTo(0→1) win. Skipping the fromTo keeps opacity at 0 instead.
            _cap_in_gfx = any(gs <= start < ge for gs, ge in _graphic_windows_pre)
            if not _cap_in_gfx:
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
            # full_cover cinematic beat: backdrop fires first, card enters after _fc_offset.
            _fc_excl_style = card.get("contentHints", {}).get("style", "")
            _is_fc = (card.get("_family") == "full_cover"
                      and _fc_excl_style not in ("prim_anecdote_frame", "prim_journey_map", "prim_split_stage",
                                                  "prim_confession_frame"))
            if _is_fc and is_paper:              # Piste A — cut sec + fade power2.out
                _fc_offset, _fc_bd_dur = 0.20, 0.22
                _fc_host_from = '{ opacity: 0 }'
                _fc_host_to   = '{ opacity: 1, duration: 0.400, ease: "power2.out" }'
            elif _is_fc and (is_vibe or is_craft or is_cinema):  # Piste B — scale-through
                _fc_offset, _fc_bd_dur = 0.18, 0.25
                _fc_host_from = '{ opacity: 0, scale: 1.08, transformOrigin: "center center" }'
                _fc_host_to   = '{ opacity: 1, scale: 1, duration: 0.500, ease: "power2.out" }'
            elif _is_fc:                         # Piste C — iris vertical (glass + ledger)
                _fc_offset, _fc_bd_dur = 0.15, 0.20
                _fc_host_from = '{ opacity: 0, clipPath: "inset(45% 0 45% 0)" }'
                _fc_host_to   = '{ opacity: 1, clipPath: "inset(0% 0 0% 0)", duration: 0.550, ease: "power2.out" }'
            else:
                _fc_offset = 0
            ent_dur = 0.550 if is_cinema else 0.320
            if _is_fc:
                lines.append(
                    f'  tl.fromTo(\'{sel}\', '
                    f'{_fc_host_from}, '
                    f'{_fc_host_to}, '
                    f'{start + _fc_offset:.4f});'
                )
            else:
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
            _early_style = card.get("contentHints", {}).get("style", "")
            if _early_style not in ("timeline", "news_ticker"):
                _p_t = start + _fc_offset  # shifted to match host entry for full_cover beat
                if is_cinema:
                    pass  # cinema: slow opacity only, no panel movement
                elif is_ledger:
                    if not _is_fc:  # iris host covers the reveal — skip conflicting panel clip
                        # Scan down: clip from top (matches ledger's terminal aesthetic)
                        lines.append(
                            f'  tl.fromTo(\'{panel_sel}\', '
                            f'{{ clipPath: "inset(100% 0 0% 0)" }}, '
                            f'{{ clipPath: "inset(0% 0 0% 0)", duration: 0.350, ease: _eIn }}, '
                            f'{_p_t:.4f});'
                        )
                elif is_vibe:
                    # Bouncy: more scale, more y, slight tilt
                    lines.append(
                        f'  tl.fromTo(\'{panel_sel}\', '
                        f'{{ scale: 1.08, y: 20, rotation: -1.5 }}, '
                        f'{{ scale: 1, y: 0, rotation: 0, duration: 0.400, ease: _eIn }}, '
                        f'{_p_t:.4f});'
                    )
                elif is_craft:
                    # Handwritten tilt: slight rotation on entry
                    lines.append(
                        f'  tl.fromTo(\'{panel_sel}\', '
                        f'{{ scale: 1.05, y: 10, rotation: 1 }}, '
                        f'{{ scale: 1, y: 0, rotation: 0, duration: 0.450, ease: _eIn }}, '
                        f'{_p_t:.4f});'
                    )
                elif is_paper:
                    # Minimal: barely perceptible scale (clean aesthetic)
                    lines.append(
                        f'  tl.fromTo(\'{panel_sel}\', '
                        f'{{ scale: 1.01, y: 6 }}, '
                        f'{{ scale: 1, y: 0, duration: 0.300, ease: _eIn }}, '
                        f'{_p_t:.4f});'
                    )
                else:
                    # lean_glass (default) — restrained entry, premium/authoritative read
                    lines.append(
                        f'  tl.fromTo(\'{panel_sel}\', '
                        f'{{ scale: 1.015, y: 8 }}, '
                        f'{{ scale: 1, y: 0, duration: 0.350, ease: _eIn }}, '
                        f'{_p_t:.4f});'
                    )

            # Premium backdrop dim: cards that overlap the speaker face dim the video.
            # Uses a separate overlay div (not CSS filter) — filter: brightness()
            # is not composited by SwiftShader on Railway.
            content_style = card.get("contentHints", {}).get("style", "key_phrase")
            card_zone = card.get("zone", "")
            # For portrait, replicate the _build_card_host() remapping so that cards
            # the LLM assigned to upper/side zones (but that visually land in the
            # portrait-center-full slot) also trigger backdrop-dim as a safety net.
            if layout == "portrait" and card.get("type") != "caption":
                _pfc_style = card.get("contentHints", {}).get("style", "")
                _is_pfc = _pfc_style in ("prim_split_compare", "prim_journey_map", "prim_cinematic_reveal", "prim_ascension_reveal", "prim_shatter_truth", "prim_split_stage", "prim_confession_frame")
                if not _is_pfc and card_zone not in (
                    "portrait-center-full", "portrait-center-left", "portrait-center-right"
                ):
                    _effective_zone = "portrait-center-full"
                else:
                    _effective_zone = card_zone
            else:
                _effective_zone = card_zone
            center_zone = _effective_zone in _DIMMING_ZONES
            if _is_fc:
                # Full-cover blackout: backdrop fires at start (before card entry by _fc_offset).
                # prim_journey_map / prim_anecdote_frame excluded via _is_fc above.
                lines.append(
                    f'  tl.to("#backdrop-dim", '
                    f'{{ opacity: 1, backgroundColor: "#000", duration: {_fc_bd_dur:.2f}, ease: "power2.in" }}, {start:.4f});'
                )
                lines.append(
                    f'  tl.to("#backdrop-dim", '
                    f'{{ opacity: 0, backgroundColor: "rgba(0,0,0,0.45)", duration: 0.18, ease: _eOut }}, {end - 0.18:.4f});'
                )
                lines.append(
                    f'  tl.set("#backdrop-dim", {{ opacity: 0, backgroundColor: "rgba(0,0,0,0.45)" }}, {end:.4f});'
                )
            elif center_zone and content_style not in ("prim_split_stage", "prim_confession_frame"):
                # prim_split_stage: video speaker must stay fully visible on the
                # non-panel half — no dimming of any kind.
                lines.append(
                    f'  tl.to("#backdrop-dim", '
                    f'{{ opacity: 1, duration: 0.30, ease: _eIn }}, {start:.4f});'
                )
                lines.append(
                    f'  tl.to("#backdrop-dim", '
                    f'{{ opacity: 0, duration: 0.18, ease: _eOut }}, {end - 0.18:.4f});'
                )
                lines.append(
                    f'  tl.set("#backdrop-dim", {{ opacity: 0 }}, {end:.4f});'
                )
                # Punch-in is handled as independent zoom entries via
                # _build_punch_in_zoom_entries() — not wired to card entry events.
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
                    # Distribute available card time evenly from t_in.
                    # tl.set at t=0 (not t_in) pins the hidden state from timeline start —
                    # prevents slides from escaping opacity:0 during GSAP seek initialization
                    # before the card's own t_in fires (was the "all slides visible" bug).
                    avail = max(0.1, end - t_in)
                    each_dur = round(avail / n_slides, 3)
                    for si in range(n_slides):
                        sl_sel = f'.card[data-card-id="{card_id}"] #{card_id}-slide-{si}'
                        sl_in  = round(t_in + si * each_dur, 4)
                        if sl_in >= end:
                            break
                        sl_out = round(min(sl_in + each_dur - 0.22, end - 0.06), 4)
                        if is_paper:
                            lines.append(f'  tl.set(\'{sl_sel}\', {{ opacity: 0 }}, 0);')
                            lines.append(
                                f'  tl.to(\'{sl_sel}\', '
                                f'{{ opacity: 1, duration: 0.25, ease: _eIn }}, '
                                f'{sl_in:.4f});')
                            lines.append(
                                f'  tl.to(\'{sl_sel}\', '
                                f'{{ opacity: 0, duration: 0.20, ease: _eOut }}, '
                                f'{sl_out:.4f});')
                        elif is_vibe:
                            lines.append(f'  tl.set(\'{sl_sel}\', {{ opacity: 0, y: 10 }}, 0);')
                            lines.append(
                                f'  tl.to(\'{sl_sel}\', '
                                f'{{ opacity: 1, y: 0, duration: 0.25, ease: _eIn }}, '
                                f'{sl_in:.4f});')
                            lines.append(
                                f'  tl.to(\'{sl_sel}\', '
                                f'{{ opacity: 0, y: -10, duration: 0.20, ease: _eOut }}, '
                                f'{sl_out:.4f});')
                        else:
                            lines.append(f'  tl.set(\'{sl_sel}\', {{ opacity: 0, x: 12 }}, 0);')
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
            elif content_style in ("data_bar_chart", "data_chart"):
                _w4_dbc_h = card.get("contentHints", {})
                _w4_dbc_labels = list(_w4_dbc_h.get("bar_labels", []))
                _w4_dbc_values = list(_w4_dbc_h.get("bar_values", []))
                if not _w4_dbc_labels and _w4_dbc_h.get("items"):
                    for _w4_dc_raw in _w4_dbc_h.get("items", [])[:4]:
                        _w4_dc_ps = str(_w4_dc_raw).split(":", 1)
                        _w4_dbc_labels.append(_w4_dc_ps[0].strip() if len(_w4_dc_ps) == 2 else str(_w4_dc_raw))
                        if len(_w4_dc_ps) == 2:
                            try:
                                _w4_dbc_values.append(float(_w4_dc_ps[1].strip().replace("%", "").replace(",", ".")))
                            except ValueError:
                                _w4_dbc_values.append(float(len(_w4_dbc_labels)))
                        else:
                            _w4_dbc_values.append(float(len(_w4_dbc_labels)))
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
            # ── Wave 7 GSAP ───────────────────────────────────────────────────
            elif content_style == "live_reaction_split":
                _w7_lrs_exp = f'.card[data-card-id="{card_id}"] #{card_id}-lrs-expected'
                _w7_lrs_div = f'.card[data-card-id="{card_id}"] #{card_id}-lrs-divider'
                _w7_lrs_rea = f'.card[data-card-id="{card_id}"] #{card_id}-lrs-reality'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w7_lrs_exp}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w7_lrs_div}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w7_lrs_rea}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w7_lrs_exp}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.55, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_lrs_div}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.45:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_lrs_rea}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.70, ease: _eIn }}, {t_in + 0.65:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w7_lrs_exp}\', {{ opacity: 0, x: -12 }}, {{ opacity: 1, x: 0, duration: 0.22, ease: "back.out(1.5)" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_lrs_div}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.15, ease: _eIn }}, {t_in + 0.18:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_lrs_rea}\', {{ opacity: 0, x: 12 }}, {{ opacity: 1, x: 0, duration: 0.25, ease: "back.out(2.0)" }}, {t_in + 0.28:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w7_lrs_exp}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_lrs_div}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.20, ease: _eIn }}, {t_in + 0.25:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_lrs_rea}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.35, ease: _eIn }}, {t_in + 0.35:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w7_lrs_exp}\', {{ opacity: 0, rotation: -1 }}, {{ opacity: 1, rotation: 0, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_lrs_div}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.20, ease: _eIn }}, {t_in + 0.28:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_lrs_rea}\', {{ opacity: 0, rotation: 1 }}, {{ opacity: 1, rotation: 0, duration: 0.38, ease: _eIn }}, {t_in + 0.38:.4f});')
                else:  # glass
                    lines.append(f'  tl.fromTo(\'{_w7_lrs_exp}\', {{ opacity: 0, x: -10 }}, {{ opacity: 1, x: 0, duration: 0.30, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_lrs_div}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.20, ease: _eIn }}, {t_in + 0.24:.4f});')
                    if p.get("accent_line_glow"):
                        lines.append(f'  tl.to(\'{_w7_lrs_div}\', {{ boxShadow: "{_esc_js(p["accent_line_glow"])}", duration: 0.18 }}, {t_in + 0.32:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_lrs_rea}\', {{ opacity: 0, x: 10 }}, {{ opacity: 1, x: 0, duration: 0.35, ease: _eIn }}, {t_in + 0.32:.4f});')
            elif content_style == "hidden_cost_reveal":
                _w7_hcr_stk = f'.card[data-card-id="{card_id}"] #{card_id}-hcr-sticker'
                _w7_hcr_arr = f'.card[data-card-id="{card_id}"] #{card_id}-hcr-arrow'
                _w7_hcr_rea = f'.card[data-card-id="{card_id}"] #{card_id}-hcr-real'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w7_hcr_stk}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w7_hcr_arr}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w7_hcr_rea}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w7_hcr_stk}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.50, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_hcr_arr}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.35, ease: _eIn }}, {t_in + 0.45:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_hcr_rea}\', {{ opacity: 0, scale: 0.92 }}, {{ opacity: 1, scale: 1, duration: 0.75, ease: _eIn }}, {t_in + 0.70:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w7_hcr_stk}\', {{ opacity: 0, scale: 0.8 }}, {{ opacity: 1, scale: 1, duration: 0.20, ease: "back.out(1.5)" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_hcr_arr}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.15, ease: _eIn }}, {t_in + 0.18:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_hcr_rea}\', {{ opacity: 0, scale: 0.6 }}, {{ opacity: 1, scale: 1.08, duration: 0.25, ease: "back.out(2.5)" }}, {t_in + 0.28:.4f});')
                    lines.append(f'  tl.to(\'{_w7_hcr_rea}\', {{ scale: 1, duration: 0.15, ease: "power2.out" }}, {t_in + 0.53:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w7_hcr_stk}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.32, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_hcr_arr}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.22, ease: _eIn }}, {t_in + 0.25:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_hcr_rea}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.38, ease: _eIn }}, {t_in + 0.40:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w7_hcr_stk}\', {{ opacity: 0, rotation: -2 }}, {{ opacity: 1, rotation: 0, duration: 0.32, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_hcr_arr}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.20, ease: _eIn }}, {t_in + 0.28:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_hcr_rea}\', {{ opacity: 0, rotation: 2 }}, {{ opacity: 1, rotation: 0, duration: 0.38, ease: _eIn }}, {t_in + 0.42:.4f});')
                else:  # glass: sticker fades slightly, real cost pops with glow
                    lines.append(f'  tl.fromTo(\'{_w7_hcr_stk}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.28, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_hcr_arr}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.18, ease: _eIn }}, {t_in + 0.22:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_hcr_rea}\', {{ opacity: 0, scale: 0.78 }}, {{ opacity: 1, scale: 1.04, duration: 0.30, ease: _eIn }}, {t_in + 0.35:.4f});')
                    lines.append(f'  tl.to(\'{_w7_hcr_rea}\', {{ scale: 1, duration: 0.18, ease: "power2.out" }}, {t_in + 0.65:.4f});')
                    if p.get("title_glow"):
                        lines.append(f'  tl.to(\'{_w7_hcr_rea}\', {{ textShadow: "{_esc_js(p["title_glow"])}", duration: 0.22 }}, {t_in + 0.55:.4f});')
            elif content_style == "social_proof_counter":
                _w7_spc_num = f'.card[data-card-id="{card_id}"] #{card_id}-spc-num'
                _w7_spc_lbl = f'.card[data-card-id="{card_id}"] #{card_id}-spc-lbl'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w7_spc_num}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w7_spc_lbl}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w7_spc_num}\', {{ opacity: 0, scale: 0.85 }}, {{ opacity: 1, scale: 1, duration: 0.85, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_spc_lbl}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.55, ease: _eIn }}, {t_in + 0.60:.4f});')
                elif is_vibe:
                    # slot-machine settle: rapid scale oscillation then rest
                    lines.append(f'  tl.fromTo(\'{_w7_spc_num}\', {{ opacity: 0, scale: 0.5 }}, {{ opacity: 1, scale: 1.15, duration: 0.22, ease: "back.out(3.0)" }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w7_spc_num}\', {{ scale: 0.95, duration: 0.10, ease: "power2.in" }}, {t_in + 0.22:.4f});')
                    lines.append(f'  tl.to(\'{_w7_spc_num}\', {{ scale: 1, duration: 0.08, ease: "power2.out" }}, {t_in + 0.32:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_spc_lbl}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.20, ease: "back.out(1.5)" }}, {t_in + 0.28:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w7_spc_num}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.40, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_spc_lbl}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.28:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w7_spc_num}\', {{ opacity: 0, scale: 0.80, rotation: -3 }}, {{ opacity: 1, scale: 1, rotation: 0, duration: 0.40, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_spc_lbl}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.28, ease: _eIn }}, {t_in + 0.30:.4f});')
                else:  # glass: scale pop + glow flash
                    lines.append(f'  tl.fromTo(\'{_w7_spc_num}\', {{ opacity: 0, scale: 0.65 }}, {{ opacity: 1, scale: 1.06, duration: 0.30, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w7_spc_num}\', {{ scale: 1, duration: 0.18, ease: "power2.out" }}, {t_in + 0.30:.4f});')
                    if p.get("title_glow_intense"):
                        lines.append(f'  tl.to(\'{_w7_spc_num}\', {{ textShadow: "{_esc_js(p["title_glow_intense"])}", duration: 0.22 }}, {t_in + 0.25:.4f});')
                    elif p.get("title_glow"):
                        lines.append(f'  tl.to(\'{_w7_spc_num}\', {{ textShadow: "{_esc_js(p["title_glow"])}", duration: 0.22 }}, {t_in + 0.25:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_spc_lbl}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.25, ease: _eIn }}, {t_in + 0.28:.4f});')
            elif content_style == "timeline_prediction":
                _w7_tp_h    = card.get("contentHints", {})
                _w7_tp_conf = _w7_tp_h.get("confirmed_steps", [])
                _w7_tp_pred = _w7_tp_h.get("predicted_steps", [])
                _w7_tp_nc   = min(len(_w7_tp_conf), 4)
                _w7_tp_np   = min(len(_w7_tp_pred), 4)
                _w7_tp_div  = f'.card[data-card-id="{card_id}"] #{card_id}-tp-div'
                _w7_tp_t    = t_in
                for _w7_i in range(_w7_tp_nc):
                    _w7_tp_el = f'.card[data-card-id="{card_id}"] #{card_id}-tp-conf-{_w7_i}'
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w7_tp_el}\', {{ opacity: 1 }}, {t_in:.4f});')
                    else:
                        lines.append(f'  tl.fromTo(\'{_w7_tp_el}\', {{ opacity: 0, x: -8 }}, {{ opacity: 1, x: 0, duration: {"0.50" if is_cinema else "0.25"}, ease: _eIn }}, {_w7_tp_t:.4f});')
                        _w7_tp_t += 0.12 if not is_cinema else 0.20
                # dashed divider
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w7_tp_div}\', {{ opacity: 1 }}, {t_in:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w7_tp_div}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {_w7_tp_t:.4f});')
                    _w7_tp_t += 0.15
                for _w7_j in range(_w7_tp_np):
                    _w7_tp_el = f'.card[data-card-id="{card_id}"] #{card_id}-tp-pred-{_w7_j}'
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w7_tp_el}\', {{ opacity: 0.6 }}, {t_in:.4f});')
                    else:
                        lines.append(f'  tl.fromTo(\'{_w7_tp_el}\', {{ opacity: 0 }}, {{ opacity: {"0.65" if not is_cinema else "0.55"}, duration: {"0.50" if is_cinema else "0.25"}, ease: _eIn }}, {_w7_tp_t:.4f});')
                        _w7_tp_t += 0.12 if not is_cinema else 0.20
            elif content_style == "red_thread_connector":
                _w7_rtc_h  = card.get("contentHints", {})
                _w7_rtc_pts = _w7_rtc_h.get("connector_points", [])
                _w7_rtc_n  = min(len(_w7_rtc_pts), 3)
                _w7_rtc_t  = t_in
                for _w7_ri in range(_w7_rtc_n):
                    _w7_rtc_pt = f'.card[data-card-id="{card_id}"] #{card_id}-rtc-pt-{_w7_ri}'
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w7_rtc_pt}\', {{ opacity: 1 }}, {t_in:.4f});')
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w7_rtc_pt}\', {{ opacity: 0, scale: 0.8 }}, {{ opacity: 1, scale: 1, duration: 0.22, ease: "back.out(2.0)" }}, {_w7_rtc_t:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w7_rtc_pt}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.55, ease: _eIn }}, {_w7_rtc_t:.4f});')
                    else:
                        lines.append(f'  tl.fromTo(\'{_w7_rtc_pt}\', {{ opacity: 0, y: 6 }}, {{ opacity: 1, y: 0, duration: 0.28, ease: _eIn }}, {_w7_rtc_t:.4f});')
                    if _w7_ri < _w7_rtc_n - 1:
                        _w7_rtc_arr = f'.card[data-card-id="{card_id}"] #{card_id}-rtc-arr-{_w7_ri}'
                        _w7_rtc_t += 0.18 if not is_cinema else 0.30
                        if is_ledger:
                            lines.append(f'  tl.set(\'{_w7_rtc_arr}\', {{ opacity: 1 }}, {t_in:.4f});')
                        else:
                            lines.append(f'  tl.fromTo(\'{_w7_rtc_arr}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.15, ease: _eIn }}, {_w7_rtc_t:.4f});')
                        _w7_rtc_t += 0.10
                    else:
                        _w7_rtc_t += 0.18 if not is_cinema else 0.30
            elif content_style == "silent_beat_pause":
                _w7_sbp_sym = f'.card[data-card-id="{card_id}"] #{card_id}-sbp-sym'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w7_sbp_sym}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w7_sbp_sym}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 1.20, ease: "sine.inOut" }}, {t_in:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w7_sbp_sym}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.40, ease: _eIn }}, {t_in:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w7_sbp_sym}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.60, ease: "sine.inOut" }}, {t_in:.4f});')
            elif content_style == "comment_reply_style":
                _w7_crs_com = f'.card[data-card-id="{card_id}"] #{card_id}-crs-comment'
                _w7_crs_rep = f'.card[data-card-id="{card_id}"] #{card_id}-crs-reply'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w7_crs_com}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w7_crs_rep}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w7_crs_com}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.55, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_crs_rep}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.65, ease: _eIn }}, {t_in + 0.60:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w7_crs_com}\', {{ opacity: 0, y: -8 }}, {{ opacity: 1, y: 0, duration: 0.22, ease: "back.out(1.5)" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_crs_rep}\', {{ opacity: 0, x: 12 }}, {{ opacity: 1, x: 0, duration: 0.25, ease: "back.out(2.0)" }}, {t_in + 0.22:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w7_crs_com}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.32, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_crs_rep}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.35, ease: _eIn }}, {t_in + 0.28:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w7_crs_com}\', {{ opacity: 0, rotation: -1 }}, {{ opacity: 1, rotation: 0, duration: 0.35, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_crs_rep}\', {{ opacity: 0, x: 8 }}, {{ opacity: 1, x: 0, duration: 0.35, ease: _eIn }}, {t_in + 0.30:.4f});')
                else:  # glass
                    lines.append(f'  tl.fromTo(\'{_w7_crs_com}\', {{ opacity: 0, y: -6 }}, {{ opacity: 1, y: 0, duration: 0.30, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_crs_rep}\', {{ opacity: 0, x: 10 }}, {{ opacity: 1, x: 0, duration: 0.32, ease: _eIn }}, {t_in + 0.28:.4f});')
                    if p.get("accent_line_glow"):
                        lines.append(f'  tl.to(\'{_w7_crs_rep}\', {{ boxShadow: "-2px 0 12px {p["accent"]}", duration: 0.22 }}, {t_in + 0.45:.4f});')
            elif content_style == "before_you_scroll":
                _w7_bys_txt  = f'.card[data-card-id="{card_id}"] #{card_id}-bys-txt'
                _w7_bys_rule = f'.card[data-card-id="{card_id}"] #{card_id}-bys-rule'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w7_bys_txt}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w7_bys_rule}\', {{ width: "80%" }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w7_bys_txt}\', {{ opacity: 0, scale: 0.95 }}, {{ opacity: 1, scale: 1, duration: 0.60, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_bys_rule}\', {{ width: "0%" }}, {{ width: "80%", duration: 0.50, ease: "power2.out" }}, {t_in + 0.40:.4f});')
                elif is_vibe:
                    # hard bounce-pop — most natural fit per spec
                    lines.append(f'  tl.fromTo(\'{_w7_bys_txt}\', {{ opacity: 0, scale: 0.60 }}, {{ opacity: 1, scale: 1.12, duration: 0.22, ease: "back.out(3.0)" }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w7_bys_txt}\', {{ scale: 1, duration: 0.12, ease: "power2.out" }}, {t_in + 0.22:.4f});')
                    # flash
                    lines.append(f'  tl.to(\'{_w7_bys_txt}\', {{ opacity: 0.4, duration: 0.05 }}, {t_in + 0.30:.4f});')
                    lines.append(f'  tl.to(\'{_w7_bys_txt}\', {{ opacity: 1, duration: 0.05 }}, {t_in + 0.35:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_bys_rule}\', {{ width: "0%" }}, {{ width: "80%", duration: 0.28, ease: "back.out(1.5)" }}, {t_in + 0.22:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w7_bys_txt}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.40, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_bys_rule}\', {{ width: "0%" }}, {{ width: "80%", duration: 0.55, ease: "power2.out" }}, {t_in + 0.25:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w7_bys_txt}\', {{ opacity: 0, rotation: -1.5 }}, {{ opacity: 1, rotation: 0, duration: 0.40, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_bys_rule}\', {{ width: "0%" }}, {{ width: "80%", duration: 0.50, ease: "power1.inOut" }}, {t_in + 0.28:.4f});')
                else:  # glass: sharp pop + glow
                    lines.append(f'  tl.fromTo(\'{_w7_bys_txt}\', {{ opacity: 0, scale: 0.82 }}, {{ opacity: 1, scale: 1.04, duration: 0.28, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w7_bys_txt}\', {{ scale: 1, duration: 0.16, ease: "power2.out" }}, {t_in + 0.28:.4f});')
                    if p.get("title_glow_intense"):
                        lines.append(f'  tl.to(\'{_w7_bys_txt}\', {{ textShadow: "{_esc_js(p["title_glow_intense"])}", duration: 0.20 }}, {t_in + 0.22:.4f});')
                    elif p.get("title_glow"):
                        lines.append(f'  tl.to(\'{_w7_bys_txt}\', {{ textShadow: "{_esc_js(p["title_glow"])}", duration: 0.20 }}, {t_in + 0.22:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w7_bys_rule}\', {{ width: "0%" }}, {{ width: "80%", duration: 0.35, ease: "power2.out" }}, {t_in + 0.22:.4f});')
                    if p.get("accent_line_glow"):
                        lines.append(f'  tl.to(\'{_w7_bys_rule}\', {{ boxShadow: "{_esc_js(p["accent_line_glow"])}", duration: 0.20 }}, {t_in + 0.42:.4f});')
            # ── Wave 8 GSAP ───────────────────────────────────────────────────
            elif content_style == "traffic_light_status":
                _w8_tls_lgt = f'.card[data-card-id="{card_id}"] #{card_id}-tls-light'
                _w8_tls_lbl = f'.card[data-card-id="{card_id}"] #{card_id}-tls-label'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w8_tls_lgt}\', {{ opacity: 1, scale: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w8_tls_lbl}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w8_tls_lgt}\', {{ opacity: 0, scale: 0.7 }}, {{ opacity: 1, scale: 1, duration: 0.80, ease: "sine.inOut" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w8_tls_lbl}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.55, ease: _eIn }}, {t_in + 0.55:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w8_tls_lgt}\', {{ opacity: 0, scale: 0.5 }}, {{ opacity: 1, scale: 1.18, duration: 0.22, ease: "back.out(3.0)" }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w8_tls_lgt}\', {{ scale: 1, duration: 0.12, ease: "power2.out" }}, {t_in + 0.22:.4f});')
                    lines.append(f'  tl.to(\'{_w8_tls_lgt}\', {{ opacity: 0.5, duration: 0.06 }}, {t_in + 0.30:.4f});')
                    lines.append(f'  tl.to(\'{_w8_tls_lgt}\', {{ opacity: 1, duration: 0.06 }}, {t_in + 0.36:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w8_tls_lbl}\', {{ opacity: 0, y: 6 }}, {{ opacity: 1, y: 0, duration: 0.22, ease: "back.out(1.5)" }}, {t_in + 0.28:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w8_tls_lgt}\', {{ opacity: 0, scale: 0.7 }}, {{ opacity: 1, scale: 1, duration: 0.38, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w8_tls_lbl}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.32, ease: _eIn }}, {t_in + 0.28:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w8_tls_lgt}\', {{ opacity: 0, scale: 0.7, rotation: -5 }}, {{ opacity: 1, scale: 1, rotation: 0, duration: 0.42, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w8_tls_lbl}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.32:.4f});')
                else:  # glass: pop + glow matching status color
                    lines.append(f'  tl.fromTo(\'{_w8_tls_lgt}\', {{ opacity: 0, scale: 0.6 }}, {{ opacity: 1, scale: 1.08, duration: 0.28, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w8_tls_lgt}\', {{ scale: 1, duration: 0.18, ease: "power2.out" }}, {t_in + 0.28:.4f});')
                    if p.get("accent_line_glow"):
                        lines.append(f'  tl.to(\'{_w8_tls_lgt}\', {{ boxShadow: "0 0 28px currentColor", duration: 0.22 }}, {t_in + 0.22:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w8_tls_lbl}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.28, ease: _eIn }}, {t_in + 0.28:.4f});')
                    if p.get("title_glow"):
                        lines.append(f'  tl.to(\'{_w8_tls_lbl}\', {{ textShadow: "{_esc_js(p["title_glow"])}", duration: 0.22 }}, {t_in + 0.38:.4f});')
            elif content_style == "day_in_life_schedule":
                _w8_dls_h  = card.get("contentHints", {})
                _w8_dls_n  = min(len(_w8_dls_h.get("schedule_items", _w8_dls_h.get("items", []))), 6)
                _w8_dls_t  = t_in
                for _w8_di in range(_w8_dls_n):
                    _w8_dls_el = f'.card[data-card-id="{card_id}"] #{card_id}-dls-item-{_w8_di}'
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w8_dls_el}\', {{ opacity: 1 }}, {t_in:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w8_dls_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.50, ease: _eIn }}, {_w8_dls_t:.4f});')
                        _w8_dls_t += 0.22
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w8_dls_el}\', {{ opacity: 0, x: -10, scale: 0.9 }}, {{ opacity: 1, x: 0, scale: 1, duration: 0.22, ease: "back.out(2.0)" }}, {_w8_dls_t:.4f});')
                        _w8_dls_t += 0.12
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_w8_dls_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.32, ease: _eIn }}, {_w8_dls_t:.4f});')
                        _w8_dls_t += 0.14
                    elif is_craft:
                        lines.append(f'  tl.fromTo(\'{_w8_dls_el}\', {{ opacity: 0, x: -6, rotation: -1 }}, {{ opacity: 1, x: 0, rotation: 0, duration: 0.35, ease: _eIn }}, {_w8_dls_t:.4f});')
                        _w8_dls_t += 0.14
                    else:  # glass
                        lines.append(f'  tl.fromTo(\'{_w8_dls_el}\', {{ opacity: 0, x: -8 }}, {{ opacity: 1, x: 0, duration: 0.28, ease: _eIn }}, {_w8_dls_t:.4f});')
                        if p.get("title_glow") and _w8_di == 0:
                            lines.append(f'  tl.to(\'{_w8_dls_el}\', {{ textShadow: "{_esc_js(p["title_glow"])}", duration: 0.20 }}, {_w8_dls_t + 0.18:.4f});')
                        _w8_dls_t += 0.12
            elif content_style == "skill_tree_unlock":
                _w8_stu_h  = card.get("contentHints", {})
                _w8_stu_n  = min(len(_w8_stu_h.get("unlocked_milestones", _w8_stu_h.get("items", []))), 5)
                _w8_stu_t  = t_in
                for _w8_si in range(_w8_stu_n):
                    _w8_stu_el = f'.card[data-card-id="{card_id}"] #{card_id}-stu-item-{_w8_si}'
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w8_stu_el}\', {{ opacity: 1 }}, {t_in:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w8_stu_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.55, ease: _eIn }}, {_w8_stu_t:.4f});')
                        _w8_stu_t += 0.25
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w8_stu_el}\', {{ opacity: 0, scale: 0.7 }}, {{ opacity: 1, scale: 1.10, duration: 0.20, ease: "back.out(2.5)" }}, {_w8_stu_t:.4f});')
                        lines.append(f'  tl.to(\'{_w8_stu_el}\', {{ scale: 1, duration: 0.10, ease: "power2.out" }}, {_w8_stu_t + 0.20:.4f});')
                        lines.append(f'  tl.to(\'{_w8_stu_el}\', {{ opacity: 0.4, duration: 0.04 }}, {_w8_stu_t + 0.26:.4f});')
                        lines.append(f'  tl.to(\'{_w8_stu_el}\', {{ opacity: 1, duration: 0.04 }}, {_w8_stu_t + 0.30:.4f});')
                        _w8_stu_t += 0.18
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_w8_stu_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.35, ease: _eIn }}, {_w8_stu_t:.4f});')
                        _w8_stu_t += 0.16
                    elif is_craft:
                        lines.append(f'  tl.fromTo(\'{_w8_stu_el}\', {{ opacity: 0, scale: 0.85, rotation: -2 }}, {{ opacity: 1, scale: 1, rotation: 0, duration: 0.38, ease: _eIn }}, {_w8_stu_t:.4f});')
                        _w8_stu_t += 0.16
                    else:  # glass
                        lines.append(f'  tl.fromTo(\'{_w8_stu_el}\', {{ opacity: 0, scale: 0.78 }}, {{ opacity: 1, scale: 1.04, duration: 0.26, ease: _eIn }}, {_w8_stu_t:.4f});')
                        lines.append(f'  tl.to(\'{_w8_stu_el}\', {{ scale: 1, duration: 0.14, ease: "power2.out" }}, {_w8_stu_t + 0.26:.4f});')
                        if p.get("accent_line_glow"):
                            lines.append(f'  tl.to(\'{_w8_stu_el}\', {{ boxShadow: "{_esc_js(p["accent_line_glow"])}", duration: 0.20 }}, {_w8_stu_t + 0.18:.4f});')
                        _w8_stu_t += 0.14
            elif content_style == "audience_poll_result":
                _w8_apr_h    = card.get("contentHints", {})
                _w8_apr_opts = _w8_apr_h.get("poll_options", _w8_apr_h.get("items", []))
                _w8_apr_n    = min(len(_w8_apr_opts), 4)
                _w8_apr_pcts = _w8_apr_h.get("poll_percentages", [])
                if not _w8_apr_pcts:
                    _n = max(_w8_apr_n, 1)
                    _w8_apr_pcts = [round(100.0 / _n, 1)] * _w8_apr_n
                _w8_apr_t    = t_in
                for _w8_ai in range(_w8_apr_n):
                    _w8_apr_row  = f'.card[data-card-id="{card_id}"] #{card_id}-apr-row-{_w8_ai}'
                    _w8_apr_fill = f'.card[data-card-id="{card_id}"] #{card_id}-apr-fill-{_w8_ai}'
                    _w8_pct      = float(_w8_apr_pcts[_w8_ai]) if _w8_ai < len(_w8_apr_pcts) else 0.0
                    _w8_pct_str  = f'{_w8_pct:.1f}%'
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w8_apr_row}\', {{ opacity: 1 }}, {t_in:.4f});')
                        lines.append(f'  tl.set(\'{_w8_apr_fill}\', {{ width: "{_w8_pct_str}" }}, {t_in:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w8_apr_row}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.45, ease: _eIn }}, {_w8_apr_t:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w8_apr_fill}\', {{ width: "0%" }}, {{ width: "{_w8_pct_str}", duration: 0.80, ease: "power2.out" }}, {_w8_apr_t + 0.15:.4f});')
                        _w8_apr_t += 0.20
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w8_apr_row}\', {{ opacity: 0, y: 6 }}, {{ opacity: 1, y: 0, duration: 0.20, ease: "back.out(1.5)" }}, {_w8_apr_t:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w8_apr_fill}\', {{ width: "0%" }}, {{ width: "{_w8_pct_str}", duration: 0.35, ease: "back.out(1.2)" }}, {_w8_apr_t + 0.10:.4f});')
                        _w8_apr_t += 0.14
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_w8_apr_row}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {_w8_apr_t:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w8_apr_fill}\', {{ width: "0%" }}, {{ width: "{_w8_pct_str}", duration: 0.50, ease: "power2.out" }}, {_w8_apr_t + 0.12:.4f});')
                        _w8_apr_t += 0.14
                    elif is_craft:
                        lines.append(f'  tl.fromTo(\'{_w8_apr_row}\', {{ opacity: 0, rotation: -0.5 }}, {{ opacity: 1, rotation: 0, duration: 0.35, ease: _eIn }}, {_w8_apr_t:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w8_apr_fill}\', {{ width: "0%" }}, {{ width: "{_w8_pct_str}", duration: 0.55, ease: "power1.inOut" }}, {_w8_apr_t + 0.12:.4f});')
                        _w8_apr_t += 0.16
                    else:  # glass: bars fill with glow, winner highlighted
                        lines.append(f'  tl.fromTo(\'{_w8_apr_row}\', {{ opacity: 0, x: -6 }}, {{ opacity: 1, x: 0, duration: 0.26, ease: _eIn }}, {_w8_apr_t:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w8_apr_fill}\', {{ width: "0%" }}, {{ width: "{_w8_pct_str}", duration: 0.55, ease: "power2.out" }}, {_w8_apr_t + 0.10:.4f});')
                        if p.get("accent_line_glow"):
                            lines.append(f'  tl.to(\'{_w8_apr_fill}\', {{ boxShadow: "{_esc_js(p["accent_line_glow"])}", duration: 0.22 }}, {_w8_apr_t + 0.30:.4f});')
                        _w8_apr_t += 0.12
            elif content_style == "broken_promise_tracker":
                _w8_bpt_h  = card.get("contentHints", {})
                _w8_bpt_n  = min(len(_w8_bpt_h.get("promises", _w8_bpt_h.get("items", []))), 5)
                _w8_bpt_t  = t_in
                for _w8_bi in range(_w8_bpt_n):
                    _w8_bpt_el = f'.card[data-card-id="{card_id}"] #{card_id}-bpt-item-{_w8_bi}'
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w8_bpt_el}\', {{ opacity: 1 }}, {t_in:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w8_bpt_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.50, ease: _eIn }}, {_w8_bpt_t:.4f});')
                        _w8_bpt_t += 0.22
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w8_bpt_el}\', {{ opacity: 0, x: -8 }}, {{ opacity: 1, x: 0, duration: 0.22, ease: "back.out(2.0)" }}, {_w8_bpt_t:.4f});')
                        _w8_bpt_t += 0.12
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_w8_bpt_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.32, ease: _eIn }}, {_w8_bpt_t:.4f});')
                        _w8_bpt_t += 0.14
                    elif is_craft:
                        lines.append(f'  tl.fromTo(\'{_w8_bpt_el}\', {{ opacity: 0, rotation: -1 }}, {{ opacity: 1, rotation: 0, duration: 0.36, ease: _eIn }}, {_w8_bpt_t:.4f});')
                        _w8_bpt_t += 0.14
                    else:  # glass
                        lines.append(f'  tl.fromTo(\'{_w8_bpt_el}\', {{ opacity: 0, x: -6 }}, {{ opacity: 1, x: 0, duration: 0.26, ease: _eIn }}, {_w8_bpt_t:.4f});')
                        if p.get("title_glow"):
                            lines.append(f'  tl.to(\'{_w8_bpt_el}\', {{ textShadow: "{_esc_js(p["title_glow"])}", duration: 0.20 }}, {_w8_bpt_t + 0.16:.4f});')
                        _w8_bpt_t += 0.12
            elif content_style == "ingredient_list":
                _w8_igl_h  = card.get("contentHints", {})
                _w8_igl_n  = min(len(_w8_igl_h.get("ingredients", _w8_igl_h.get("items", []))), 6)
                _w8_igl_t  = t_in
                for _w8_ii in range(_w8_igl_n):
                    _w8_igl_el = f'.card[data-card-id="{card_id}"] #{card_id}-igl-item-{_w8_ii}'
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w8_igl_el}\', {{ opacity: 1 }}, {t_in:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w8_igl_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.50, ease: _eIn }}, {_w8_igl_t:.4f});')
                        _w8_igl_t += 0.22
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w8_igl_el}\', {{ opacity: 0, x: -10 }}, {{ opacity: 1, x: 0, duration: 0.20, ease: "back.out(1.8)" }}, {_w8_igl_t:.4f});')
                        _w8_igl_t += 0.11
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_w8_igl_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {_w8_igl_t:.4f});')
                        _w8_igl_t += 0.13
                    elif is_craft:
                        lines.append(f'  tl.fromTo(\'{_w8_igl_el}\', {{ opacity: 0, x: -5, rotation: -0.8 }}, {{ opacity: 1, x: 0, rotation: 0, duration: 0.35, ease: _eIn }}, {_w8_igl_t:.4f});')
                        _w8_igl_t += 0.14
                    else:  # glass
                        lines.append(f'  tl.fromTo(\'{_w8_igl_el}\', {{ opacity: 0, x: -8 }}, {{ opacity: 1, x: 0, duration: 0.26, ease: _eIn }}, {_w8_igl_t:.4f});')
                        _w8_igl_t += 0.12
            elif content_style == "resource_allocation":
                _w8_ral_h      = card.get("contentHints", {})
                _w8_ral_labels = _w8_ral_h.get("resource_labels", [])
                _w8_ral_values = _w8_ral_h.get("resource_values", [])
                _w8_ral_n      = min(len(_w8_ral_labels), 5)
                _w8_ral_max    = max((float(v) for v in _w8_ral_values), default=1.0) or 1.0
                _w8_ral_t      = t_in
                for _w8_ri in range(_w8_ral_n):
                    _w8_ral_row  = f'.card[data-card-id="{card_id}"] #{card_id}-ral-seg-{_w8_ri}'
                    _w8_ral_fill = f'.card[data-card-id="{card_id}"] #{card_id}-ral-fill-{_w8_ri}'
                    _w8_ral_pct  = round((float(_w8_ral_values[_w8_ri]) / _w8_ral_max) * 100, 1) if _w8_ri < len(_w8_ral_values) else 0.0
                    _w8_ral_pstr = f'{_w8_ral_pct:.1f}%'
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w8_ral_row}\', {{ opacity: 1 }}, {t_in:.4f});')
                        lines.append(f'  tl.set(\'{_w8_ral_fill}\', {{ width: "{_w8_ral_pstr}" }}, {t_in:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w8_ral_row}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.45, ease: _eIn }}, {_w8_ral_t:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w8_ral_fill}\', {{ width: "0%" }}, {{ width: "{_w8_ral_pstr}", duration: 0.90, ease: "power1.inOut" }}, {_w8_ral_t + 0.15:.4f});')
                        _w8_ral_t += 0.20
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w8_ral_row}\', {{ opacity: 0, y: 5 }}, {{ opacity: 1, y: 0, duration: 0.20, ease: "back.out(1.5)" }}, {_w8_ral_t:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w8_ral_fill}\', {{ width: "0%" }}, {{ width: "{_w8_ral_pstr}", duration: 0.38, ease: "back.out(1.2)" }}, {_w8_ral_t + 0.10:.4f});')
                        _w8_ral_t += 0.14
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_w8_ral_row}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {_w8_ral_t:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w8_ral_fill}\', {{ width: "0%" }}, {{ width: "{_w8_ral_pstr}", duration: 0.55, ease: "power2.out" }}, {_w8_ral_t + 0.12:.4f});')
                        _w8_ral_t += 0.14
                    elif is_craft:
                        lines.append(f'  tl.fromTo(\'{_w8_ral_row}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.35, ease: _eIn }}, {_w8_ral_t:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w8_ral_fill}\', {{ width: "0%" }}, {{ width: "{_w8_ral_pstr}", duration: 0.65, ease: "power1.inOut" }}, {_w8_ral_t + 0.12:.4f});')
                        _w8_ral_t += 0.16
                    else:  # glass
                        lines.append(f'  tl.fromTo(\'{_w8_ral_row}\', {{ opacity: 0, x: -6 }}, {{ opacity: 1, x: 0, duration: 0.26, ease: _eIn }}, {_w8_ral_t:.4f});')
                        lines.append(f'  tl.fromTo(\'{_w8_ral_fill}\', {{ width: "0%" }}, {{ width: "{_w8_ral_pstr}", duration: 0.60, ease: "power2.out" }}, {_w8_ral_t + 0.10:.4f});')
                        if p.get("accent_line_glow"):
                            lines.append(f'  tl.to(\'{_w8_ral_fill}\', {{ boxShadow: "{_esc_js(p["accent_line_glow"])}", duration: 0.22 }}, {_w8_ral_t + 0.30:.4f});')
                        _w8_ral_t += 0.12
            elif content_style == "fill_in_the_blank":
                _w8_fitb_sent = f'.card[data-card-id="{card_id}"] #{card_id}-fitb-sentence'
                _w8_fitb_word = f'.card[data-card-id="{card_id}"] #{card_id}-fitb-word'
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w8_fitb_sent}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w8_fitb_word}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w8_fitb_sent}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.70, ease: "sine.inOut" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w8_fitb_word}\', {{ opacity: 0, scale: 0.85 }}, {{ opacity: 1, scale: 1, duration: 0.90, ease: "sine.inOut" }}, {t_in + 0.90:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w8_fitb_sent}\', {{ opacity: 0, scale: 0.95 }}, {{ opacity: 1, scale: 1, duration: 0.25, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w8_fitb_word}\', {{ opacity: 0, scale: 0.60 }}, {{ opacity: 1, scale: 1.12, duration: 0.22, ease: "back.out(3.0)" }}, {t_in + 0.55:.4f});')
                    lines.append(f'  tl.to(\'{_w8_fitb_word}\', {{ scale: 1, duration: 0.12, ease: "power2.out" }}, {t_in + 0.77:.4f});')
                    if p.get("title_glow_intense") or p.get("title_glow"):
                        _glow = p.get("title_glow_intense") or p.get("title_glow")
                        lines.append(f'  tl.to(\'{_w8_fitb_word}\', {{ textShadow: "{_esc_js(_glow)}", duration: 0.18 }}, {t_in + 0.65:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w8_fitb_sent}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.38, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w8_fitb_word}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.40, ease: _eIn }}, {t_in + 0.60:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w8_fitb_sent}\', {{ opacity: 0, rotation: -1 }}, {{ opacity: 1, rotation: 0, duration: 0.40, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w8_fitb_word}\', {{ opacity: 0, rotation: 2 }}, {{ opacity: 1, rotation: 0, duration: 0.45, ease: _eIn }}, {t_in + 0.65:.4f});')
                else:  # glass: sentence in, blank pulses, word pops with glow
                    lines.append(f'  tl.fromTo(\'{_w8_fitb_sent}\', {{ opacity: 0, y: -6 }}, {{ opacity: 1, y: 0, duration: 0.32, ease: _eIn }}, {t_in:.4f});')
                    if p.get("title_glow"):
                        lines.append(f'  tl.to(\'{_w8_fitb_sent}\', {{ textShadow: "{_esc_js(p["title_glow"])}", duration: 0.22 }}, {t_in + 0.22:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w8_fitb_word}\', {{ opacity: 0, scale: 0.72 }}, {{ opacity: 1, scale: 1.06, duration: 0.28, ease: _eIn }}, {t_in + 0.65:.4f});')
                    lines.append(f'  tl.to(\'{_w8_fitb_word}\', {{ scale: 1, duration: 0.16, ease: "power2.out" }}, {t_in + 0.93:.4f});')
                    if p.get("title_glow_intense") or p.get("title_glow"):
                        _glow = p.get("title_glow_intense") or p.get("title_glow")
                        lines.append(f'  tl.to(\'{_w8_fitb_word}\', {{ textShadow: "{_esc_js(_glow)}", duration: 0.22 }}, {t_in + 0.78:.4f});')
            # ── Wave 9 ────────────────────────────────────────────────────────
            elif content_style == "streak_counter":
                _w9_sk_h      = card.get("contentHints", {})
                _w9_sk_count  = f'.card[data-card-id="{card_id}"] #{card_id}-sk-count'
                _w9_sk_unit   = f'.card[data-card-id="{card_id}"] #{card_id}-sk-unit'
                _w9_sk_label  = f'.card[data-card-id="{card_id}"] #{card_id}-sk-label'
                _w9_has_unit  = bool(_w9_sk_h.get("streak_unit"))
                _w9_has_label = bool(_w9_sk_h.get("streak_label") or _w9_sk_h.get("title"))
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w9_sk_count}\', {{ opacity: 1 }}, {t_in:.4f});')
                    if _w9_has_unit:
                        lines.append(f'  tl.set(\'{_w9_sk_unit}\', {{ opacity: 1 }}, {t_in:.4f});')
                    if _w9_has_label:
                        lines.append(f'  tl.set(\'{_w9_sk_label}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w9_sk_count}\', {{ opacity: 0, scale: 0.85 }}, {{ opacity: 1, scale: 1, duration: 0.85, ease: _eIn }}, {t_in:.4f});')
                    if _w9_has_unit:
                        lines.append(f'  tl.fromTo(\'{_w9_sk_unit}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.55, ease: _eIn }}, {t_in + 0.60:.4f});')
                    if _w9_has_label:
                        lines.append(f'  tl.fromTo(\'{_w9_sk_label}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.45, ease: _eIn }}, {t_in + 0.80:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w9_sk_count}\', {{ opacity: 0, scale: 0.50 }}, {{ opacity: 1, scale: 1.15, duration: 0.22, ease: "back.out(3.0)" }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w9_sk_count}\', {{ scale: 1, duration: 0.15, ease: "power2.out" }}, {t_in + 0.22:.4f});')
                    if _w9_has_unit:
                        lines.append(f'  tl.fromTo(\'{_w9_sk_unit}\', {{ opacity: 0, x: 8 }}, {{ opacity: 1, x: 0, duration: 0.18, ease: "back.out(1.5)" }}, {t_in + 0.20:.4f});')
                    if _w9_has_label:
                        lines.append(f'  tl.fromTo(\'{_w9_sk_label}\', {{ opacity: 0, y: 6 }}, {{ opacity: 1, y: 0, duration: 0.18, ease: "power2.out" }}, {t_in + 0.30:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w9_sk_count}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.40, ease: _eIn }}, {t_in:.4f});')
                    if _w9_has_unit:
                        lines.append(f'  tl.fromTo(\'{_w9_sk_unit}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.28:.4f});')
                    if _w9_has_label:
                        lines.append(f'  tl.fromTo(\'{_w9_sk_label}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.28, ease: _eIn }}, {t_in + 0.45:.4f});')
                elif is_craft:
                    lines.append(f'  tl.fromTo(\'{_w9_sk_count}\', {{ opacity: 0, scale: 0.80, rotation: -4 }}, {{ opacity: 1, scale: 1, rotation: 0, duration: 0.42, ease: _eIn }}, {t_in:.4f});')
                    if _w9_has_unit:
                        lines.append(f'  tl.fromTo(\'{_w9_sk_unit}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.28, ease: _eIn }}, {t_in + 0.30:.4f});')
                    if _w9_has_label:
                        lines.append(f'  tl.fromTo(\'{_w9_sk_label}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.28, ease: _eIn }}, {t_in + 0.50:.4f});')
                else:  # glass
                    lines.append(f'  tl.fromTo(\'{_w9_sk_count}\', {{ opacity: 0, scale: 0.65 }}, {{ opacity: 1, scale: 1.06, duration: 0.28, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.to(\'{_w9_sk_count}\', {{ scale: 1, duration: 0.16, ease: "power2.out" }}, {t_in + 0.28:.4f});')
                    if p.get("title_glow_intense"):
                        lines.append(f'  tl.to(\'{_w9_sk_count}\', {{ textShadow: "{_esc_js(p["title_glow_intense"])}", duration: 0.22 }}, {t_in + 0.22:.4f});')
                    elif p.get("title_glow"):
                        lines.append(f'  tl.to(\'{_w9_sk_count}\', {{ textShadow: "{_esc_js(p["title_glow"])}", duration: 0.22 }}, {t_in + 0.22:.4f});')
                    if _w9_has_unit:
                        lines.append(f'  tl.fromTo(\'{_w9_sk_unit}\', {{ opacity: 0, x: 8 }}, {{ opacity: 1, x: 0, duration: 0.22, ease: _eIn }}, {t_in + 0.22:.4f});')
                    if _w9_has_label:
                        lines.append(f'  tl.fromTo(\'{_w9_sk_label}\', {{ opacity: 0, y: 6 }}, {{ opacity: 1, y: 0, duration: 0.22, ease: _eIn }}, {t_in + 0.35:.4f});')
            elif content_style == "before_now_later":
                _w9_bnl_t = t_in
                for _w9_bi in range(3):
                    _w9_bnl_el = f'.card[data-card-id="{card_id}"] #{card_id}-bnl-{_w9_bi}'
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w9_bnl_el}\', {{ opacity: 1 }}, {t_in:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w9_bnl_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.60, ease: _eIn }}, {_w9_bnl_t:.4f});')
                        _w9_bnl_t += 0.30
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w9_bnl_el}\', {{ opacity: 0, y: 10 }}, {{ opacity: 1, y: 0, duration: 0.22, ease: "back.out(1.8)" }}, {_w9_bnl_t:.4f});')
                        _w9_bnl_t += 0.14
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_w9_bnl_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {_w9_bnl_t:.4f});')
                        _w9_bnl_t += 0.18
                    elif is_craft:
                        lines.append(f'  tl.fromTo(\'{_w9_bnl_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.35, ease: _eIn }}, {_w9_bnl_t:.4f});')
                        _w9_bnl_t += 0.18
                    else:  # glass
                        lines.append(f'  tl.fromTo(\'{_w9_bnl_el}\', {{ opacity: 0, x: -10 }}, {{ opacity: 1, x: 0, duration: 0.28, ease: _eIn }}, {_w9_bnl_t:.4f});')
                        _w9_bnl_t += 0.16
            elif content_style == "platform_stats":
                _w9_pst_h     = card.get("contentHints", {})
                _w9_pst_plats = _w9_pst_h.get("platforms", [])
                _w9_pst_n     = min(len(_w9_pst_plats), 5)
                _w9_pst_t     = t_in
                for _w9_pi in range(_w9_pst_n):
                    _w9_pst_el = f'.card[data-card-id="{card_id}"] #{card_id}-pst-row-{_w9_pi}'
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w9_pst_el}\', {{ opacity: 1 }}, {t_in:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w9_pst_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.50, ease: _eIn }}, {_w9_pst_t:.4f});')
                        _w9_pst_t += 0.22
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w9_pst_el}\', {{ opacity: 0, x: 12 }}, {{ opacity: 1, x: 0, duration: 0.20, ease: "back.out(1.5)" }}, {_w9_pst_t:.4f});')
                        _w9_pst_t += 0.12
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_w9_pst_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.28, ease: _eIn }}, {_w9_pst_t:.4f});')
                        _w9_pst_t += 0.15
                    elif is_craft:
                        lines.append(f'  tl.fromTo(\'{_w9_pst_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.32, ease: _eIn }}, {_w9_pst_t:.4f});')
                        _w9_pst_t += 0.16
                    else:  # glass
                        lines.append(f'  tl.fromTo(\'{_w9_pst_el}\', {{ opacity: 0, x: -8 }}, {{ opacity: 1, x: 0, duration: 0.26, ease: _eIn }}, {_w9_pst_t:.4f});')
                        _w9_pst_t += 0.14
            elif content_style == "cost_comparison":
                _w9_cco_h      = card.get("contentHints", {})
                _w9_cco_labels = _w9_cco_h.get("option_labels", [])
                _w9_cco_n      = min(len(_w9_cco_labels), 4)
                _w9_cco_t      = t_in
                for _w9_ci in range(_w9_cco_n):
                    _w9_cco_el = f'.card[data-card-id="{card_id}"] #{card_id}-cco-col-{_w9_ci}'
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w9_cco_el}\', {{ opacity: 1 }}, {t_in:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w9_cco_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.55, ease: _eIn }}, {_w9_cco_t:.4f});')
                        _w9_cco_t += 0.25
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w9_cco_el}\', {{ opacity: 0, y: 12 }}, {{ opacity: 1, y: 0, duration: 0.22, ease: "back.out(1.8)" }}, {_w9_cco_t:.4f});')
                        _w9_cco_t += 0.12
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_w9_cco_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {_w9_cco_t:.4f});')
                        _w9_cco_t += 0.16
                    elif is_craft:
                        lines.append(f'  tl.fromTo(\'{_w9_cco_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.35, ease: _eIn }}, {_w9_cco_t:.4f});')
                        _w9_cco_t += 0.16
                    else:  # glass
                        lines.append(f'  tl.fromTo(\'{_w9_cco_el}\', {{ opacity: 0, y: 10 }}, {{ opacity: 1, y: 0, duration: 0.28, ease: _eIn }}, {_w9_cco_t:.4f});')
                        _w9_cco_t += 0.14
            elif content_style == "decision_matrix":
                _w9_dmx_t = t_in
                for _w9_di in range(4):
                    _w9_dmx_el = f'.card[data-card-id="{card_id}"] #{card_id}-dmx-q-{_w9_di}'
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w9_dmx_el}\', {{ opacity: 1 }}, {t_in:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w9_dmx_el}\', {{ opacity: 0, scale: 0.92 }}, {{ opacity: 1, scale: 1, duration: 0.55, ease: _eIn }}, {_w9_dmx_t:.4f});')
                        _w9_dmx_t += 0.22
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w9_dmx_el}\', {{ opacity: 0, scale: 0.80 }}, {{ opacity: 1, scale: 1.04, duration: 0.18, ease: "back.out(2.0)" }}, {_w9_dmx_t:.4f});')
                        lines.append(f'  tl.to(\'{_w9_dmx_el}\', {{ scale: 1, duration: 0.10, ease: "power2.out" }}, {_w9_dmx_t + 0.18:.4f});')
                        _w9_dmx_t += 0.12
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_w9_dmx_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.28, ease: _eIn }}, {_w9_dmx_t:.4f});')
                        _w9_dmx_t += 0.14
                    elif is_craft:
                        lines.append(f'  tl.fromTo(\'{_w9_dmx_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.32, ease: _eIn }}, {_w9_dmx_t:.4f});')
                        _w9_dmx_t += 0.14
                    else:  # glass
                        lines.append(f'  tl.fromTo(\'{_w9_dmx_el}\', {{ opacity: 0, scale: 0.88 }}, {{ opacity: 1, scale: 1, duration: 0.26, ease: _eIn }}, {_w9_dmx_t:.4f});')
                        _w9_dmx_t += 0.14
            elif content_style == "habit_tracker":
                _w9_ht_h      = card.get("contentHints", {})
                _w9_ht_days   = _w9_ht_h.get("days_completed", [])
                _w9_ht_n      = min(len(_w9_ht_days), 14)
                _w9_ht_label  = _w9_ht_h.get("habit_label") or _w9_ht_h.get("title", "")
                _w9_ht_lbl_el = f'.card[data-card-id="{card_id}"] #{card_id}-ht-label'
                _w9_ht_t      = t_in
                if _w9_ht_label:
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w9_ht_lbl_el}\', {{ opacity: 1 }}, {t_in:.4f});')
                    else:
                        lines.append(f'  tl.fromTo(\'{_w9_ht_lbl_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: {"0.55" if is_cinema else "0.28"}, ease: _eIn }}, {t_in:.4f});')
                    _w9_ht_t += 0.45 if is_cinema else 0.22
                for _w9_hi in range(_w9_ht_n):
                    _w9_ht_el = f'.card[data-card-id="{card_id}"] #{card_id}-ht-day-{_w9_hi}'
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_w9_ht_el}\', {{ opacity: 1 }}, {t_in:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_w9_ht_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.40, ease: _eIn }}, {_w9_ht_t:.4f});')
                        _w9_ht_t += 0.18
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_w9_ht_el}\', {{ opacity: 0, scale: 0.60 }}, {{ opacity: 1, scale: 1.12, duration: 0.15, ease: "back.out(2.5)" }}, {_w9_ht_t:.4f});')
                        lines.append(f'  tl.to(\'{_w9_ht_el}\', {{ scale: 1, duration: 0.10, ease: "power2.out" }}, {_w9_ht_t + 0.15:.4f});')
                        _w9_ht_t += 0.10
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_w9_ht_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.22, ease: _eIn }}, {_w9_ht_t:.4f});')
                        _w9_ht_t += 0.10
                    elif is_craft:
                        lines.append(f'  tl.fromTo(\'{_w9_ht_el}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: _eIn }}, {_w9_ht_t:.4f});')
                        _w9_ht_t += 0.10
                    else:  # glass
                        lines.append(f'  tl.fromTo(\'{_w9_ht_el}\', {{ opacity: 0, scale: 0.70 }}, {{ opacity: 1, scale: 1, duration: 0.20, ease: _eIn }}, {_w9_ht_t:.4f});')
                        _w9_ht_t += 0.10
            elif content_style == "income_vs_expense":
                _w9_ive_h       = card.get("contentHints", {})
                _w9_ive_inc_sel = f'.card[data-card-id="{card_id}"] #{card_id}-ive-income'
                _w9_ive_exp_sel = f'.card[data-card-id="{card_id}"] #{card_id}-ive-expense'
                _w9_ive_fi_sel  = f'.card[data-card-id="{card_id}"] #{card_id}-ive-fill-income'
                _w9_ive_fe_sel  = f'.card[data-card-id="{card_id}"] #{card_id}-ive-fill-expense'
                _w9_inc_raw = ''.join(c for c in str(_w9_ive_h.get("income_value", "0")) if c.isdigit() or c == '.')
                _w9_exp_raw = ''.join(c for c in str(_w9_ive_h.get("expense_value", "0")) if c.isdigit() or c == '.')
                try:
                    _w9_ive_inc_n = max(0.0, float(_w9_inc_raw or '0'))
                except (ValueError, TypeError):
                    _w9_ive_inc_n = 0.0
                try:
                    _w9_ive_exp_n = max(0.0, float(_w9_exp_raw or '0'))
                except (ValueError, TypeError):
                    _w9_ive_exp_n = 0.0
                _w9_ive_max     = max(_w9_ive_inc_n, _w9_ive_exp_n, 1.0)
                _w9_ive_inc_pct = f'{round((_w9_ive_inc_n / _w9_ive_max) * 100, 1):.1f}%'
                _w9_ive_exp_pct = f'{round((_w9_ive_exp_n / _w9_ive_max) * 100, 1):.1f}%'
                _w9_ive_dur     = 1.8 if is_cinema else 0.35 if is_ledger else 0.70
                _w9_ive_ease    = '"none"' if is_ledger else '"power1.inOut"' if is_cinema else '"power2.out"'
                _w9_ive_t2      = t_in + (0.30 if is_cinema else 0.18)
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w9_ive_inc_sel}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w9_ive_fi_sel}\', {{ width: "{_w9_ive_inc_pct}" }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w9_ive_exp_sel}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w9_ive_fe_sel}\', {{ width: "{_w9_ive_exp_pct}" }}, {t_in:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w9_ive_inc_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: {"0.50" if is_cinema else "0.26"}, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w9_ive_fi_sel}\', {{ width: "0%" }}, {{ width: "{_w9_ive_inc_pct}", duration: {_w9_ive_dur:.3f}, ease: {_w9_ive_ease} }}, {t_in + 0.10:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w9_ive_exp_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: {"0.50" if is_cinema else "0.26"}, ease: _eIn }}, {_w9_ive_t2:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w9_ive_fe_sel}\', {{ width: "0%" }}, {{ width: "{_w9_ive_exp_pct}", duration: {_w9_ive_dur:.3f}, ease: {_w9_ive_ease} }}, {_w9_ive_t2 + 0.10:.4f});')
                    if not is_paper and p.get("accent_line_glow"):
                        lines.append(f'  tl.to(\'{_w9_ive_fi_sel}\', {{ boxShadow: "{_esc_js(p["accent_line_glow"])}", duration: 0.22 }}, {t_in + _w9_ive_dur:.4f});')
            # ── Wave 10 ───────────────────────────────────────────────────────
            elif content_style == "milestone_recap":
                _w10_mr_h     = card.get("contentHints", {})
                _w10_mr_items = _w10_mr_h.get("milestones", _w10_mr_h.get("items", []))
                _w10_mr_n     = min(len(_w10_mr_items), 6)
                _w10_mr_step  = 0.20 if is_cinema else 0.12
                _w10_mr_dur   = 0.50 if is_cinema else 0.20 if is_ledger else 0.28
                for _w10_i in range(_w10_mr_n):
                    _sel = f'.card[data-card-id="{card_id}"] #{card_id}-mr-item-{_w10_i}'
                    _t   = t_in + _w10_i * _w10_mr_step
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_sel}\', {{ opacity: 1 }}, {_t:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_sel}\', {{ opacity: 0, x: -10 }}, {{ opacity: 1, x: 0, duration: {_w10_mr_dur:.3f}, ease: _eIn }}, {_t:.4f});')
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_sel}\', {{ opacity: 0, x: -8, scale: 0.92 }}, {{ opacity: 1, x: 0, scale: 1, duration: {_w10_mr_dur:.3f}, ease: "back.out(1.4)" }}, {_t:.4f});')
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: {_w10_mr_dur:.3f}, ease: _eIn }}, {_t:.4f});')
                    else:
                        lines.append(f'  tl.fromTo(\'{_sel}\', {{ opacity: 0, y: 10 }}, {{ opacity: 1, y: 0, duration: {_w10_mr_dur:.3f}, ease: _eIn }}, {_t:.4f});')
                if not (is_ledger or is_paper or is_craft or is_vibe or is_cinema) and _w10_mr_n and p.get("accent_line_glow"):
                    lines.append(f'  tl.to(\'.card[data-card-id="{card_id}"] .mr-dot\', {{ boxShadow: "{_esc_js(p["accent_line_glow"])}", duration: 0.22 }}, {t_in + _w10_mr_n * _w10_mr_step:.4f});')
            elif content_style == "content_calendar":
                _w10_cal_h     = card.get("contentHints", {})
                _w10_cal_items = _w10_cal_h.get("calendar_items", _w10_cal_h.get("items", []))
                _w10_cal_n     = min(len(_w10_cal_items), 7)
                _w10_cal_step  = 0.20 if is_cinema else 0.12
                _w10_cal_dur   = 0.50 if is_cinema else 0.20 if is_ledger else 0.28
                for _w10_i in range(_w10_cal_n):
                    _sel = f'.card[data-card-id="{card_id}"] #{card_id}-cal-item-{_w10_i}'
                    _t   = t_in + _w10_i * _w10_cal_step
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_sel}\', {{ opacity: 1 }}, {_t:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_sel}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: {_w10_cal_dur:.3f}, ease: _eIn }}, {_t:.4f});')
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_sel}\', {{ opacity: 0, y: 8, scale: 0.94 }}, {{ opacity: 1, y: 0, scale: 1, duration: {_w10_cal_dur:.3f}, ease: "back.out(1.4)" }}, {_t:.4f});')
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: {_w10_cal_dur:.3f}, ease: _eIn }}, {_t:.4f});')
                    else:
                        lines.append(f'  tl.fromTo(\'{_sel}\', {{ opacity: 0, x: 10 }}, {{ opacity: 1, x: 0, duration: {_w10_cal_dur:.3f}, ease: _eIn }}, {_t:.4f});')
            elif content_style == "client_result_number":
                _w10_crn_val = f'.card[data-card-id="{card_id}"] #{card_id}-crn-value'
                _w10_crn_ctx = f'.card[data-card-id="{card_id}"] #{card_id}-crn-context'
                _w10_crn_dur = 0.60 if is_cinema else 0.20 if is_ledger else 0.35
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w10_crn_val}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w10_crn_ctx}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w10_crn_val}\', {{ opacity: 0, scale: 0.80 }}, {{ opacity: 1, scale: 1, duration: {_w10_crn_dur:.3f}, ease: "expo.out" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w10_crn_ctx}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.40, ease: _eIn }}, {t_in + 0.50:.4f});')
                    if p.get("title_glow"):
                        lines.append(f'  tl.to(\'{_w10_crn_val}\', {{ textShadow: "{_esc_js(p["title_glow"])}", duration: 0.30 }}, {t_in + _w10_crn_dur:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w10_crn_val}\', {{ opacity: 0, scale: 0.70 }}, {{ opacity: 1, scale: 1, duration: {_w10_crn_dur:.3f}, ease: "back.out(2.0)" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w10_crn_ctx}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.28, ease: _eIn }}, {t_in + 0.28:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w10_crn_val}\', {{ opacity: 0 }}, {{ opacity: 1, duration: {_w10_crn_dur:.3f}, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w10_crn_ctx}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.30, ease: _eIn }}, {t_in + 0.25:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w10_crn_val}\', {{ opacity: 0, scale: 0.85, y: 10 }}, {{ opacity: 1, scale: 1, y: 0, duration: {_w10_crn_dur:.3f}, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w10_crn_ctx}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.28, ease: _eIn }}, {t_in + 0.28:.4f});')
                    if not is_craft and p.get("title_glow"):
                        lines.append(f'  tl.to(\'{_w10_crn_val}\', {{ textShadow: "{_esc_js(p["title_glow"])}", duration: 0.22 }}, {t_in + _w10_crn_dur:.4f});')
            elif content_style == "mistake_lesson":
                _w10_ml_err = f'.card[data-card-id="{card_id}"] #{card_id}-ml-mistake'
                _w10_ml_lsn = f'.card[data-card-id="{card_id}"] #{card_id}-ml-lesson'
                _w10_ml_dur = 0.50 if is_cinema else 0.20 if is_ledger else 0.30
                _w10_ml_t2  = t_in + (0.45 if is_cinema else 0.32)
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w10_ml_err}\', {{ opacity: 1 }}, {t_in:.4f});')
                    lines.append(f'  tl.set(\'{_w10_ml_lsn}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w10_ml_err}\', {{ opacity: 0, y: 10 }}, {{ opacity: 1, y: 0, duration: {_w10_ml_dur:.3f}, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w10_ml_lsn}\', {{ opacity: 0, y: 10 }}, {{ opacity: 1, y: 0, duration: {_w10_ml_dur:.3f}, ease: _eIn }}, {_w10_ml_t2:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w10_ml_err}\', {{ opacity: 0, x: -10 }}, {{ opacity: 1, x: 0, duration: {_w10_ml_dur:.3f}, ease: "back.out(1.2)" }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w10_ml_lsn}\', {{ opacity: 0, x: -10 }}, {{ opacity: 1, x: 0, duration: {_w10_ml_dur:.3f}, ease: "back.out(1.2)" }}, {_w10_ml_t2:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w10_ml_err}\', {{ opacity: 0 }}, {{ opacity: 1, duration: {_w10_ml_dur:.3f}, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w10_ml_lsn}\', {{ opacity: 0 }}, {{ opacity: 1, duration: {_w10_ml_dur:.3f}, ease: _eIn }}, {_w10_ml_t2:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w10_ml_err}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: {_w10_ml_dur:.3f}, ease: _eIn }}, {t_in:.4f});')
                    lines.append(f'  tl.fromTo(\'{_w10_ml_lsn}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: {_w10_ml_dur:.3f}, ease: _eIn }}, {_w10_ml_t2:.4f});')
                    if not is_craft and p.get("title_glow"):
                        lines.append(f'  tl.to(\'{_w10_ml_lsn}\', {{ boxShadow: "0 0 14px {_esc_js(p["accent"])}", duration: 0.22 }}, {_w10_ml_t2 + _w10_ml_dur:.4f});')
            elif content_style == "tool_comparison":
                _w10_tc_h      = card.get("contentHints", {})
                _w10_tc_feats  = _w10_tc_h.get("tool_features", _w10_tc_h.get("items", []))
                _w10_tc_n      = min(len(_w10_tc_feats), 5)
                _w10_tc_heads  = f'.card[data-card-id="{card_id}"] #{card_id}-tc-heads'
                _w10_tc_dur    = 0.50 if is_cinema else 0.20 if is_ledger else 0.28
                _w10_tc_step   = 0.18 if is_cinema else 0.12
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w10_tc_heads}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w10_tc_heads}\', {{ opacity: 0, y: -8 }}, {{ opacity: 1, y: 0, duration: {_w10_tc_dur:.3f}, ease: _eIn }}, {t_in:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w10_tc_heads}\', {{ opacity: 0, scale: 0.90 }}, {{ opacity: 1, scale: 1, duration: {_w10_tc_dur:.3f}, ease: "back.out(1.4)" }}, {t_in:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w10_tc_heads}\', {{ opacity: 0 }}, {{ opacity: 1, duration: {_w10_tc_dur:.3f}, ease: _eIn }}, {t_in:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w10_tc_heads}\', {{ opacity: 0, y: -8 }}, {{ opacity: 1, y: 0, duration: {_w10_tc_dur:.3f}, ease: _eIn }}, {t_in:.4f});')
                    if not is_craft and p.get("title_glow"):
                        lines.append(f'  tl.to(\'{_w10_tc_heads}\', {{ textShadow: "{_esc_js(p["title_glow"])}", duration: 0.20 }}, {t_in + _w10_tc_dur:.4f});')
                for _w10_i in range(_w10_tc_n):
                    _sel = f'.card[data-card-id="{card_id}"] #{card_id}-tc-feat-{_w10_i}'
                    _t   = t_in + _w10_tc_dur + 0.08 + _w10_i * _w10_tc_step
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_sel}\', {{ opacity: 1 }}, {_t:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_sel}\', {{ opacity: 0, x: 10 }}, {{ opacity: 1, x: 0, duration: {_w10_tc_dur:.3f}, ease: _eIn }}, {_t:.4f});')
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_sel}\', {{ opacity: 0, scale: 0.92 }}, {{ opacity: 1, scale: 1, duration: {_w10_tc_dur:.3f}, ease: "back.out(1.4)" }}, {_t:.4f});')
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: {_w10_tc_dur:.3f}, ease: _eIn }}, {_t:.4f});')
                    else:
                        lines.append(f'  tl.fromTo(\'{_sel}\', {{ opacity: 0, y: 6 }}, {{ opacity: 1, y: 0, duration: {_w10_tc_dur:.3f}, ease: _eIn }}, {_t:.4f});')
            elif content_style == "weekly_review":
                _w10_wr_h    = card.get("contentHints", {})
                _w10_wr_cats = _w10_wr_h.get("review_categories", _w10_wr_h.get("items", []))
                _w10_wr_n    = min(len(_w10_wr_cats), 6)
                _w10_wr_step = 0.18 if is_cinema else 0.12
                _w10_wr_dur  = 0.50 if is_cinema else 0.20 if is_ledger else 0.28
                for _w10_i in range(_w10_wr_n):
                    _sel = f'.card[data-card-id="{card_id}"] #{card_id}-wr-item-{_w10_i}'
                    _t   = t_in + _w10_i * _w10_wr_step
                    if is_ledger:
                        lines.append(f'  tl.set(\'{_sel}\', {{ opacity: 1 }}, {_t:.4f});')
                    elif is_cinema:
                        lines.append(f'  tl.fromTo(\'{_sel}\', {{ opacity: 0, x: 12 }}, {{ opacity: 1, x: 0, duration: {_w10_wr_dur:.3f}, ease: _eIn }}, {_t:.4f});')
                    elif is_vibe:
                        lines.append(f'  tl.fromTo(\'{_sel}\', {{ opacity: 0, x: 8, scale: 0.94 }}, {{ opacity: 1, x: 0, scale: 1, duration: {_w10_wr_dur:.3f}, ease: "back.out(1.4)" }}, {_t:.4f});')
                    elif is_paper:
                        lines.append(f'  tl.fromTo(\'{_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: {_w10_wr_dur:.3f}, ease: _eIn }}, {_t:.4f});')
                    else:
                        lines.append(f'  tl.fromTo(\'{_sel}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: {_w10_wr_dur:.3f}, ease: _eIn }}, {_t:.4f});')
                if not (is_ledger or is_paper or is_craft or is_vibe or is_cinema) and _w10_wr_n and p.get("title_glow"):
                    lines.append(f'  tl.to(\'.card[data-card-id="{card_id}"] .wr-score\', {{ textShadow: "{_esc_js(p["title_glow"])}", duration: 0.22 }}, {t_in + _w10_wr_n * _w10_wr_step:.4f});')
            elif content_style == "audience_question":
                _w10_aq = f'.card[data-card-id="{card_id}"] #{card_id}-aq-q'
                _w10_aq_dur = 0.70 if is_cinema else 0.22 if is_ledger else 0.40
                if is_ledger:
                    lines.append(f'  tl.set(\'{_w10_aq}\', {{ opacity: 1 }}, {t_in:.4f});')
                elif is_cinema:
                    lines.append(f'  tl.fromTo(\'{_w10_aq}\', {{ opacity: 0, scale: 0.96 }}, {{ opacity: 1, scale: 1, duration: {_w10_aq_dur:.3f}, ease: _eIn }}, {t_in:.4f});')
                elif is_vibe:
                    lines.append(f'  tl.fromTo(\'{_w10_aq}\', {{ opacity: 0, y: 12, scale: 0.95 }}, {{ opacity: 1, y: 0, scale: 1, duration: {_w10_aq_dur:.3f}, ease: "back.out(1.2)" }}, {t_in:.4f});')
                elif is_paper:
                    lines.append(f'  tl.fromTo(\'{_w10_aq}\', {{ opacity: 0 }}, {{ opacity: 1, duration: {_w10_aq_dur:.3f}, ease: _eIn }}, {t_in:.4f});')
                else:
                    lines.append(f'  tl.fromTo(\'{_w10_aq}\', {{ opacity: 0, y: 16 }}, {{ opacity: 1, y: 0, duration: {_w10_aq_dur:.3f}, ease: _eIn }}, {t_in:.4f});')
            # ── Catalogue primitives GSAP (Wave 11) ──────────────────────────
            elif content_style == "prim_stat_counter":
                _psc_num_sel = f'.card[data-card-id="{card_id}"] #{card_id}-psc-number'
                _psc_pfx_sel = f'.card[data-card-id="{card_id}"] #{card_id}-psc-prefix'
                _psc_sfx_sel = f'.card[data-card-id="{card_id}"] #{card_id}-psc-suffix'
                _psc_kck_sel = f'.card[data-card-id="{card_id}"] #{card_id}-psc-kicker'
                _psc_raw     = card.get("contentHints", {}).get("number", "0")
                _psc_pfx_raw = _esc_js(card.get("contentHints", {}).get("prefix", ""))
                _psc_sfx_raw = _esc_js(card.get("contentHints", {}).get("suffix", ""))
                _psc_count_dur = min(0.90, max(0.40, dur * 0.55))
                _psc_val, _psc_auto_sfx = _safe_number(_psc_raw)
                _psc_final_sfx = _psc_sfx_raw or _esc_js(_psc_auto_sfx)
                # Prefix and suffix: slide up simultaneously (only when element exists in DOM)
                if _psc_pfx_raw:
                    lines.append(f'  tl.fromTo(\'{_psc_pfx_sel}\', {{ opacity: 0, y: -6 }}, {{ opacity: 1, y: 0, duration: 0.280, ease: _eIn }}, {t_in:.4f});')
                if _psc_sfx_raw:
                    lines.append(f'  tl.fromTo(\'{_psc_sfx_sel}\', {{ opacity: 0, y: -6 }}, {{ opacity: 1, y: 0, duration: 0.280, ease: _eIn }}, {t_in:.4f});')
                if _psc_val is not None:
                    _psc_dec = 1 if '.' in str(_psc_val) else 0
                    # Number span shows ONLY the number — suffix is in the separate .psc-side span
                    _psc_fmt = (
                        'o.v.toFixed(1)'
                        if _psc_dec else
                        'Math.round(o.v).toLocaleString()'
                    )
                    # Count-up with expo.out — rushes through low values, decelerates into target
                    lines.append(
                        f'  (function(){{ var o={{v:0}}; '
                        f'tl.to(o, {{v:{_psc_val}, duration:{_psc_count_dur:.3f}, ease:"expo.out", '
                        f'onUpdate:function(){{ var el=document.querySelector(\'{_psc_num_sel}\'); '
                        f'if(el) el.textContent={_psc_fmt}; }}}}, {t_in:.4f}); }})();'
                    )
                    lines.append(f'  tl.fromTo(\'{_psc_num_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.160, ease: _eIn }}, {t_in:.4f});')
                    # Glow charge: text-shadow builds from zero to full glow over count duration,
                    # then bursts to intense at pulse moment (only for packs with title_glow)
                    if p.get("title_glow"):
                        lines.append(
                            f'  tl.fromTo(\'{_psc_num_sel}\', '
                            f'{{ textShadow: "0 0 0 transparent" }}, '
                            f'{{ textShadow: "{_esc_js(p["title_glow"])}", duration: {_psc_count_dur:.3f}, ease: "power2.in" }}, '
                            f'{t_in:.4f});'
                        )
                        if p.get("title_glow_intense"):
                            lines.append(
                                f'  tl.fromTo(\'{_psc_num_sel}\', '
                                f'{{ textShadow: "{_esc_js(p["title_glow"])}" }}, '
                                f'{{ textShadow: "{_esc_js(p["title_glow_intense"])}", duration: 0.110, ease: "power2.out", yoyo: true, repeat: 1 }}, '
                                f'{t_in + _psc_count_dur:.4f});'
                            )
                    # Scale pulse at count arrival (after glow burst)
                    lines.append(
                        f'  tl.fromTo(\'{_psc_num_sel}\', {{ scale: 1 }}, '
                        f'{{ scale: 1.08, duration: 0.100, ease: "power2.out", yoyo: true, repeat: 1 }}, '
                        f'{t_in + _psc_count_dur:.4f});'
                    )
                else:
                    lines.append(f'  tl.fromTo(\'{_psc_num_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.380, ease: _eIn }}, {t_in:.4f});')
                # Kicker label — slides up after number lands
                lines.append(f'  tl.fromTo(\'{_psc_kck_sel}\', {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.260, ease: _eIn }}, {t_in + 0.52:.4f});')
            elif content_style == "prim_numbered_rule":
                _pnr_num_sel  = f'.card[data-card-id="{card_id}"] #{card_id}-pnr-number'
                _pnr_rule_sel = f'.card[data-card-id="{card_id}"] #{card_id}-pnr-rule'
                # Number: scale-bounce from small (0.30) → slight overshoot (1.10) → settle (1.0)
                lines.append(
                    f'  tl.fromTo(\'{_pnr_num_sel}\', '
                    f'{{ opacity: 0, scale: 0.30 }}, '
                    f'{{ opacity: 1, scale: 1.10, duration: 0.260, ease: "power3.out" }}, '
                    f'{start:.4f});'
                )
                lines.append(
                    f'  tl.to(\'{_pnr_num_sel}\', '
                    f'{{ scale: 1.0, duration: 0.140, ease: "power2.inOut" }}, '
                    f'{start + 0.260:.4f});'
                )
                # Rule text fades in after bounce settles
                lines.append(
                    f'  tl.fromTo(\'{_pnr_rule_sel}\', '
                    f'{{ opacity: 0, y: 14 }}, '
                    f'{{ opacity: 1, y: 0, duration: 0.320, ease: _eIn }}, '
                    f'{start + 0.460:.4f});'
                )
            elif content_style == "prim_anecdote_frame":
                _af_tint_sel  = f'.card[data-card-id="{card_id}"] #{card_id}-af-tint'
                _af_vig_sel   = f'.card[data-card-id="{card_id}"] #{card_id}-af-vignette'
                _af_grain_sel = f'.card[data-card-id="{card_id}"] #{card_id}-af-grain'
                _af_in_dur    = min(0.70, dur * 0.20)
                _af_out_t     = end - _af_in_dur
                # Fade in: warm tint + vignette + grain
                lines.append(f'  tl.to(\'{_af_tint_sel}\', {{ opacity: 1, duration: {_af_in_dur:.3f}, ease: "power2.out" }}, {start:.4f});')
                lines.append(f'  tl.to(\'{_af_vig_sel}\', {{ opacity: 1, duration: {_af_in_dur:.3f}, ease: "power2.out" }}, {start:.4f});')
                lines.append(f'  tl.to(\'{_af_grain_sel}\', {{ opacity: 0.80, duration: {_af_in_dur:.3f}, ease: "power2.out" }}, {start:.4f});')
                # Fade out
                lines.append(f'  tl.to(\'{_af_tint_sel}\', {{ opacity: 0, duration: {_af_in_dur:.3f}, ease: "power2.in" }}, {_af_out_t:.4f});')
                lines.append(f'  tl.to(\'{_af_vig_sel}\', {{ opacity: 0, duration: {_af_in_dur:.3f}, ease: "power2.in" }}, {_af_out_t:.4f});')
                lines.append(f'  tl.to(\'{_af_grain_sel}\', {{ opacity: 0, duration: {_af_in_dur:.3f}, ease: "power2.in" }}, {_af_out_t:.4f});')
            elif content_style == "prim_split_compare":
                _spc_l_sel   = f'.card[data-card-id="{card_id}"] #{card_id}-spc-left'
                _spc_r_sel   = f'.card[data-card-id="{card_id}"] #{card_id}-spc-right'
                _spc_ll_sel  = f'.card[data-card-id="{card_id}"] #{card_id}-spc-label-l'
                _spc_rl_sel  = f'.card[data-card-id="{card_id}"] #{card_id}-spc-label-r'
                _spc_div_sel = f'.card[data-card-id="{card_id}"] #{card_id}-spc-divider'
                # Panels slide from opposite edges simultaneously
                lines.append(f'  tl.fromTo(\'{_spc_l_sel}\', {{ xPercent: -100 }}, {{ xPercent: 0, duration: 0.480, ease: "power3.out" }}, {start:.4f});')
                lines.append(f'  tl.fromTo(\'{_spc_r_sel}\', {{ xPercent: 100 }}, {{ xPercent: 0, duration: 0.480, ease: "power3.out" }}, {start:.4f});')
                # Divider line grows down after panels land
                lines.append(f'  tl.fromTo(\'{_spc_div_sel}\', {{ scaleY: 0 }}, {{ scaleY: 1, duration: 0.180, ease: "power2.inOut" }}, {start + 0.430:.4f});')
                # Labels fade in after divider
                lines.append(f'  tl.fromTo(\'{_spc_ll_sel}\', {{ opacity: 0, scale: 0.88 }}, {{ opacity: 1, scale: 1, duration: 0.260, ease: _eIn }}, {start + 0.530:.4f});')
                lines.append(f'  tl.fromTo(\'{_spc_rl_sel}\', {{ opacity: 0, scale: 0.88 }}, {{ opacity: 1, scale: 1, duration: 0.260, ease: _eIn }}, {start + 0.580:.4f});')
                _spc_kicker_txt = card.get("contentHints", {}).get("kicker", "")
                if _spc_kicker_txt:
                    _spc_k_sel = f'.card[data-card-id="{card_id}"] #{card_id}-spc-kicker'
                    lines.append(f'  tl.fromTo(\'{_spc_k_sel}\', {{ opacity: 0, y: -8 }}, {{ opacity: 1, y: 0, duration: 0.200, ease: _eIn }}, {start + 0.600:.4f});')
            elif content_style == "prim_journey_map":
                # ── prim_journey_map GSAP — bezier flight, pure JS onUpdate ──────
                _jmt_hd_sel  = f'.card[data-card-id="{card_id}"] #{card_id}-jmt-header'
                _jmt_ft_sel  = f'.card[data-card-id="{card_id}"] #{card_id}-jmt-footer'
                _jmt_pid     = f'{card_id}-jmt-plane'
                _jmt_tid     = f'{card_id}-jmt-trail'
                _jmt_flight  = max(2.2, min(dur * 0.80, 5.0))
                _t_hd        = start
                _t_dot       = start + 0.25
                _t_fly       = start + 0.50
                _t_arrive    = _t_fly + _jmt_flight
                # Header fade in
                lines.append(f'  tl.fromTo(\'{_jmt_hd_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.350, ease: "power2.out" }}, {_t_hd:.4f});')
                # Departure dot
                for _sel in (f'#{card_id}-jmt-df', f'#{card_id}-jmt-dfi'):
                    lines.append(f'  tl.fromTo(\'{_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.250, ease: "power2.out" }}, {_t_dot:.4f});')
                lines.append(f'  tl.fromTo(\'#{card_id}-jmt-gf\', {{ opacity: 0 }}, {{ opacity: 0.55, duration: 0.350, ease: "power2.out" }}, {_t_dot:.4f});')
                lines.append(f'  tl.fromTo(\'#{card_id}-jmt-lf\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.200, ease: "power2.out" }}, {_t_dot + 0.05:.4f});')
                lines.append(f'  tl.fromTo(\'#{card_id}-jmt-sf\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.200, ease: "power2.out" }}, {_t_dot + 0.08:.4f});')
                # Bezier flight + trail via onUpdate IIFE — no external plugins needed
                lines.append('  (function() {')
                lines.append('    var _t = { p: 0 };')
                lines.append(f'    var _pid = "{_jmt_pid}", _tid = "{_jmt_tid}", _tgid = "{_jmt_tid}-glow";')
                lines.append('    function _bx(t) { var u=1-t; return u*u*u*71+3*u*u*t*140+3*u*t*t*215+t*t*t*289; }')
                lines.append('    function _by(t) { var u=1-t; return u*u*u*79+3*u*u*t*28+3*u*t*t*40+t*t*t*152; }')
                lines.append('    function _ag(t) { var t2=Math.min(1,t+.002); return Math.atan2(_by(t2)-_by(t),_bx(t2)-_bx(t))*180/Math.PI; }')
                lines.append(f'    tl.to(_t, {{ p: 1, duration: {_jmt_flight:.3f}, ease: "power1.inOut", onUpdate: function() {{')
                lines.append('      var tp=_t.p, px=_bx(tp).toFixed(2), py=_by(tp).toFixed(2), ag=(_ag(tp)+90).toFixed(1);')
                lines.append('      var pl=document.getElementById(_pid); if(pl) pl.setAttribute("transform","translate("+px+","+py+") rotate("+ag+")");')
                lines.append('      var off=String(265*(1-tp));')
                lines.append('      var tr=document.getElementById(_tid); if(tr) tr.style.strokeDashoffset=off;')
                lines.append('      var tg=document.getElementById(_tgid); if(tg) tg.style.strokeDashoffset=off;')
                lines.append(f'    }}}}, {_t_fly:.4f});')
                lines.append('  })();')
                # Arrival dot
                for _sel in (f'#{card_id}-jmt-dt', f'#{card_id}-jmt-dti'):
                    lines.append(f'  tl.fromTo(\'{_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.250, ease: "power2.out" }}, {_t_arrive:.4f});')
                lines.append(f'  tl.fromTo(\'#{card_id}-jmt-gt\', {{ opacity: 0 }}, {{ opacity: 0.55, duration: 0.350, ease: "power2.out" }}, {_t_arrive:.4f});')
                lines.append(f'  tl.fromTo(\'#{card_id}-jmt-lt\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.200, ease: "power2.out" }}, {_t_arrive + 0.05:.4f});')
                lines.append(f'  tl.fromTo(\'#{card_id}-jmt-st\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.200, ease: "power2.out" }}, {_t_arrive + 0.08:.4f});')
                # Footer fade in after arrival
                lines.append(f'  tl.fromTo(\'{_jmt_ft_sel}\', {{ opacity: 0 }}, {{ opacity: 1, duration: 0.350, ease: "power2.out" }}, {_t_arrive + 0.20:.4f});')
            # ── prim_cinematic_reveal GSAP — 4-layer depth reveal ───────────────
            elif content_style == "prim_cinematic_reveal":
                _pcr_bg_s   = f'.card[data-card-id="{card_id}"] #{card_id}-pcr-bg'
                _pcr_kck_s  = f'.card[data-card-id="{card_id}"] #{card_id}-pcr-kicker'
                _pcr_tit_s  = f'.card[data-card-id="{card_id}"] #{card_id}-pcr-title'
                _pcr_lin_s  = f'.card[data-card-id="{card_id}"] #{card_id}-pcr-line'
                _pcr_det_s  = f'.card[data-card-id="{card_id}"] #{card_id}-pcr-detail'
                _pcr_hints  = card.get("contentHints", {})
                _pcr_has_kicker = bool(_pcr_hints.get("kicker"))
                _pcr_has_detail = bool(_pcr_hints.get("detail"))
                # Layer 0 — diamond ring: slow atmospheric scale from 0.58, sine.inOut
                # rotation holds at 45° (from:50→to:45 — 5° settle over 0.9s, invisible)
                # Easing choice: sine.inOut = pure S-curve, starts imperceptibly → peak → ends imperceptibly.
                # Creates "world breathing open" sensation that contextualises the main reveal.
                lines.append(f'  tl.fromTo(\'{_pcr_bg_s}\', '
                             f'{{ rotation: 50, scale: 0.58, opacity: 0 }}, '
                             f'{{ rotation: 45, scale: 1.0, opacity: 0.22, duration: 0.900, ease: "sine.inOut" }}, '
                             f'{start:.4f});')
                # Layer 1 — kicker: perspective flip from rotateX(-52°)+y(-16px), power3.out
                # Easing choice: power3.out = 3rd-power deceleration, solid weighted arrival.
                # Like a title-card element gliding into position with physical mass.
                if _pcr_has_kicker:
                    lines.append(f'  tl.fromTo(\'{_pcr_kck_s}\', '
                                 f'{{ opacity: 0, rotateX: -52, y: -16, transformPerspective: 800 }}, '
                                 f'{{ opacity: 1, rotateX: 0, y: 0, transformPerspective: 800, duration: 0.360, ease: "power3.out" }}, '
                                 f'{start + 0.16:.4f});')
                # Layer 2 — title: scale(0.88)+rotateY(7°) → full, expo.out
                # Easing choice: expo.out = explosive onset that halts with surgical precision.
                # Mimics a camera snapping to subject and locking focus — zero overshoot,
                # zero spring. The abrupt deceleration signals intentionality.
                lines.append(f'  tl.fromTo(\'{_pcr_tit_s}\', '
                             f'{{ opacity: 0, scale: 0.88, rotateY: 7, transformPerspective: 1100 }}, '
                             f'{{ opacity: 1, scale: 1.0, rotateY: 0, transformPerspective: 1100, duration: 0.520, ease: "expo.out" }}, '
                             f'{start + 0.28:.4f});')
                # Glow burst on Glass/Vibe at title landing
                if p.get("title_glow_intense") and p.get("title_glow"):
                    lines.append(f'  tl.fromTo(\'{_pcr_tit_s}\', '
                                 f'{{ textShadow: "{_esc_js(p["title_glow"])}" }}, '
                                 f'{{ textShadow: "{_esc_js(p["title_glow_intense"])}", duration: 0.130, ease: "power2.out", yoyo: true, repeat: 1 }}, '
                                 f'{start + 0.72:.4f});')
                # Layer 3 — accent line: scaleX left→right, power2.inOut
                # Block-level + explicit width ensures scaleX is non-trivial (HF rule 7).
                lines.append(f'  tl.fromTo(\'{_pcr_lin_s}\', '
                             f'{{ scaleX: 0, opacity: 0 }}, '
                             f'{{ scaleX: 1, opacity: 1, duration: 0.260, ease: "power2.inOut" }}, '
                             f'{start + 0.58:.4f});')
                # Layer 4 — optional detail: y-rise fade, power2.out
                if _pcr_has_detail:
                    lines.append(f'  tl.fromTo(\'{_pcr_det_s}\', '
                                 f'{{ opacity: 0, y: 12 }}, '
                                 f'{{ opacity: 1, y: 0, duration: 0.300, ease: "power2.out" }}, '
                                 f'{start + 0.72:.4f});')
            # ── prim_ascension_reveal GSAP — 5-layer ascension reveal ────────
            elif content_style == "prim_ascension_reveal":
                _par_halo_s = f'.card[data-card-id="{card_id}"] #{card_id}-par-halo'
                _par_hor_s  = f'.card[data-card-id="{card_id}"] #{card_id}-par-horizon'
                _par_tit_s  = f'.card[data-card-id="{card_id}"] #{card_id}-par-title'
                _par_ring_s = f'.card[data-card-id="{card_id}"] #{card_id}-par-ring'
                _par_kck_s  = f'.card[data-card-id="{card_id}"] #{card_id}-par-kicker'
                _par_hints      = card.get("contentHints", {})
                _par_has_kicker = bool(_par_hints.get("kicker"))
                # L0 — Halo: organic respiration, two-phase (0→1.4s total)
                # Phase 1 (0→0.7s): scale 0.7→1.15, opacity 0→0.30, sine.inOut "world opening"
                lines.append(f'  tl.fromTo(\'{_par_halo_s}\', '
                             f'{{ opacity: 0, scale: 0.7 }}, '
                             f'{{ opacity: 0.30, scale: 1.15, duration: 0.700, ease: "sine.inOut" }}, '
                             f'{start:.4f});')
                # Phase 2 (0.7→1.4s): settle 1.15→1.0, opacity 0.30→0.22
                lines.append(f'  tl.to(\'{_par_halo_s}\', '
                             f'{{ opacity: 0.22, scale: 1.0, duration: 0.700, ease: "sine.inOut" }}, '
                             f'{start + 0.700:.4f});')
                # L1 — Horizon: scaleX(0)→(1), center-origin, power4.out (0.1→0.5s)
                # HF rule 7: display:block + explicit width set in CSS — scaleX non-trivial.
                lines.append(f'  tl.fromTo(\'{_par_hor_s}\', '
                             f'{{ scaleX: 0, opacity: 0 }}, '
                             f'{{ scaleX: 1, opacity: 1, duration: 0.400, ease: "power4.out" }}, '
                             f'{start + 0.100:.4f});')
                # L2 — Title: translateY(60)+rotateX(-25°)→rest, back.out(1.4) (0.35→1.1s)
                # Overshoot ≈4%: intentional "poids qui se pose" physical landing sensation.
                # transformPerspective on element mirrors perspective:1400px on .par-scene.
                lines.append(f'  tl.fromTo(\'{_par_tit_s}\', '
                             f'{{ opacity: 0, y: 60, rotateX: -25, transformPerspective: 1400 }}, '
                             f'{{ opacity: 1, y: 0, rotateX: 0, transformPerspective: 1400, duration: 0.750, ease: "back.out(1.4)" }}, '
                             f'{start + 0.350:.4f});')
                # Glow burst on Glass/Vibe packs at title landing (t = start+1.0s)
                if p.get("title_glow_intense") and p.get("title_glow"):
                    lines.append(f'  tl.fromTo(\'{_par_tit_s}\', '
                                 f'{{ textShadow: "{_esc_js(p["title_glow"])}" }}, '
                                 f'{{ textShadow: "{_esc_js(p["title_glow_intense"])}", duration: 0.120, ease: "power2.out", yoyo: true, repeat: 1 }}, '
                                 f'{start + 1.000:.4f});')
                # L3 — Ring pulse: scale(1)→(1.4), opacity(0.4)→(0), single cycle (0.85→1.35s)
                # expo.out: explosive onset stoping abruptly — sonar-ping residual after impact.
                lines.append(f'  tl.fromTo(\'{_par_ring_s}\', '
                             f'{{ scale: 1, opacity: 0.4 }}, '
                             f'{{ scale: 1.4, opacity: 0, duration: 0.500, ease: "expo.out" }}, '
                             f'{start + 0.850:.4f});')
                # L4 — Kicker: y(10)→(0), opacity 0→1, power2.out (1.1→1.5s)
                if _par_has_kicker:
                    lines.append(f'  tl.fromTo(\'{_par_kck_s}\', '
                                 f'{{ opacity: 0, y: 10 }}, '
                                 f'{{ opacity: 1, y: 0, duration: 0.400, ease: "power2.out" }}, '
                                 f'{start + 1.100:.4f});')
            # ── prim_confession_frame GSAP — 4-layer fragility reveal ────────
            elif content_style == "prim_confession_frame":
                _pcf_desat_s = f'.card[data-card-id="{card_id}"] #{card_id}-pcf-desat'
                _pcf_vig_s   = f'.card[data-card-id="{card_id}"] #{card_id}-pcf-vignette'
                _pcf_line_s  = f'.card[data-card-id="{card_id}"] #{card_id}-pcf-line'
                _pcf_text_s  = f'.card[data-card-id="{card_id}"] #{card_id}-pcf-text'
                # L0 — Desaturation: sine.inOut 1.2s. Opacity kept at 0.10 so the effect
                # remains subtle even if mix-blend-mode:saturation falls back to a plain
                # grey overlay in the render environment (SwiftShader / headless Chrome).
                lines.append(f'  tl.fromTo(\'{_pcf_desat_s}\', '
                             f'{{ opacity: 0 }}, '
                             f'{{ opacity: 0.10, duration: 1.200, ease: "sine.inOut" }}, '
                             f'{start:.4f});')
                # L1 — Vignette: opacity-only (gradient is static in CSS), power1.out, +0.1s.
                # Cap at 0.45 — gradient now spans from centre-clear (50%) to edges,
                # so the speaker's face stays readable at any vignette opacity.
                lines.append(f'  tl.fromTo(\'{_pcf_vig_s}\', '
                             f'{{ opacity: 0 }}, '
                             f'{{ opacity: 0.45, duration: 1.000, ease: "power1.out" }}, '
                             f'{start + 0.100:.4f});')
                # L2 — Text: y(8→0) + opacity, power2.out, +0.4s.
                # No overshoot — deliberate contrast with back.out(1.4) of climax primitives.
                # letterSpacing intentionally static (animating it reflows text and stutters at frame capture).
                lines.append(f'  tl.fromTo(\'{_pcf_text_s}\', '
                             f'{{ opacity: 0, y: 8 }}, '
                             f'{{ opacity: 1, y: 0, duration: 1.300, ease: "power2.out" }}, '
                             f'{start + 0.400:.4f});')
                # L3 — Accent line: scaleX(0→1), transform-origin:left, power1.inOut, +0.9s, 0.9s dur.
                # Deliberately slow — arrives after text anchors the confession.
                # HF rule 7: display:block + explicit width (100px in CSS) → scaleX non-trivial.
                lines.append(f'  tl.fromTo(\'{_pcf_line_s}\', '
                             f'{{ scaleX: 0, opacity: 0 }}, '
                             f'{{ scaleX: 1, opacity: 1, duration: 0.900, ease: "power1.inOut" }}, '
                             f'{start + 0.900:.4f});')
            # ── prim_shatter_truth GSAP — myth trembles → shatters → truth ──
            elif content_style == "prim_shatter_truth":
                _pst_myth_s  = f'.card[data-card-id="{card_id}"] #{card_id}-pst-myth'
                _pst_flash_s = f'.card[data-card-id="{card_id}"] #{card_id}-pst-flash'
                _pst_truth_s = f'.card[data-card-id="{card_id}"] #{card_id}-pst-truth'
                _pst_frag_sels = [
                    f'.card[data-card-id="{card_id}"] #{card_id}-pst-frag-{_pst_i}'
                    for _pst_i in range(5)
                ]
                # Irregular horizontal strip clips — non-uniform widths mimic organic fracture
                _pst_clips = [
                    "inset(0% 0% 75% 0%)",
                    "inset(25% 0% 55% 0%)",
                    "inset(45% 0% 32% 0%)",
                    "inset(68% 0% 15% 0%)",
                    "inset(85% 0% 0% 0%)",
                ]
                # Radial scatter vectors: (tx px, ty px, rotation deg) — each shard flies outward
                _pst_scatter = [
                    ( 28, -90, -8),
                    (-55, -52, 12),
                    ( 62,  18, -6),
                    (-42,  60, 10),
                    ( 24,  88, -5),
                ]
                # Phase 1 — stable entry: myth scales in quickly
                lines.append(f'  tl.fromTo(\'{_pst_myth_s}\', '
                             f'{{ opacity: 0, scale: 0.96 }}, '
                             f'{{ opacity: 1, scale: 1, duration: 0.250, ease: "power2.out" }}, '
                             f'{start:.4f});')
                # Phase 2 — micro-vibration: ±1px translateX, 6 steps sine.inOut
                lines.append(f'  tl.to(\'{_pst_myth_s}\', {{ x: 1, duration: 0.025, ease: "sine.inOut" }}, {start + 0.600:.4f});')
                lines.append(f'  tl.to(\'{_pst_myth_s}\', {{ x: -1, duration: 0.025, ease: "sine.inOut" }}, {start + 0.625:.4f});')
                lines.append(f'  tl.to(\'{_pst_myth_s}\', {{ x: 1, duration: 0.025, ease: "sine.inOut" }}, {start + 0.650:.4f});')
                lines.append(f'  tl.to(\'{_pst_myth_s}\', {{ x: -1, duration: 0.025, ease: "sine.inOut" }}, {start + 0.675:.4f});')
                lines.append(f'  tl.to(\'{_pst_myth_s}\', {{ x: 1, duration: 0.025, ease: "sine.inOut" }}, {start + 0.700:.4f});')
                lines.append(f'  tl.to(\'{_pst_myth_s}\', {{ x: 0, duration: 0.025, ease: "sine.inOut" }}, {start + 0.725:.4f});')
                # Phase 3 — flash: expo.out rise (0→0.15) then power2.in fall
                lines.append(f'  tl.fromTo(\'{_pst_flash_s}\', '
                             f'{{ opacity: 0 }}, '
                             f'{{ opacity: 0.15, duration: 0.065, ease: "expo.out" }}, '
                             f'{start + 0.720:.4f});')
                lines.append(f'  tl.to(\'{_pst_flash_s}\', '
                             f'{{ opacity: 0, duration: 0.065, ease: "power2.in" }}, '
                             f'{start + 0.785:.4f});')
                # Phase 4 — shatter: myth disappears, 5 fragment shards scatter radially
                lines.append(f'  tl.to(\'{_pst_myth_s}\', {{ opacity: 0, duration: 0.001 }}, {start + 0.750:.4f});')
                for _fi, (_frag_s, _clip, (_tx, _ty, _rot)) in enumerate(
                    zip(_pst_frag_sels, _pst_clips, _pst_scatter)
                ):
                    _fdelay = round(start + 0.750 + _fi * 0.020, 4)
                    lines.append(f'  tl.set(\'{_frag_s}\', {{ clipPath: "{_clip}" }}, {_fdelay:.4f});')
                    lines.append(f'  tl.fromTo(\'{_frag_s}\', '
                                 f'{{ x: 0, y: 0, rotation: 0, opacity: 1 }}, '
                                 f'{{ x: {_tx}, y: {_ty}, rotation: {_rot}, opacity: 0, duration: 0.400, ease: "power4.out" }}, '
                                 f'{_fdelay:.4f});')
                # Phase 5 — truth imposes itself with back.out(1.3) weight
                lines.append(f'  tl.fromTo(\'{_pst_truth_s}\', '
                             f'{{ opacity: 0, scale: 0.92 }}, '
                             f'{{ opacity: 1, scale: 1, duration: 0.500, ease: "back.out(1.3)" }}, '
                             f'{start + 0.850:.4f});')
            # ── prim_split_stage GSAP — video slides + content reveals ──────
            elif content_style == "prim_split_stage":
                _sst_ch_g    = card.get("contentHints", {})
                _sst_side_g  = _sst_ch_g.get("side", "right")
                _sst_mode_g  = _sst_ch_g.get("mode", "steps")
                _sst_steps_g = _sst_ch_g.get("steps", [])
                _sst_nodes_g = _sst_ch_g.get("nodes", [])
                _sst_kicker_g = _sst_ch_g.get("kicker", "")

                # #video-stage stays at scale:1/x:0 — panel covers its half opaquely.
                # Content panel slides in from outside the content side
                _sst_px_from = 70 if _sst_side_g == "left" else -70

                _sst_panel_s  = f'.card[data-card-id="{card_id}"] #{card_id}-sst-panel'
                _sst_kicker_s = f'.card[data-card-id="{card_id}"] #{card_id}-sst-kicker'

                # ── ENTRY ──
                # Phase 1: content panel slides in (start+0.20)
                lines.append(
                    f'  tl.fromTo(\'{_sst_panel_s}\', '
                    f'{{ opacity: 0, x: {_sst_px_from} }}, '
                    f'{{ opacity: 1, x: 0, duration: 0.38, ease: "power3.out" }}, '
                    f'{start + 0.20:.4f});'
                )
                # Phase 3: kicker fades in ahead of steps
                if _sst_kicker_g:
                    lines.append(
                        f'  tl.fromTo(\'{_sst_kicker_s}\', '
                        f'{{ opacity: 0 }}, {{ opacity: 1, duration: 0.22, ease: "power2.out" }}, '
                        f'{start + 0.22:.4f});'
                    )
                # Phase 4: steps, nodes, or caption fade in
                if _sst_mode_g == "steps":
                    for _si in range(min(len(_sst_steps_g), 5)):
                        _sst_step_s = f'.card[data-card-id="{card_id}"] #{card_id}-sst-step-{_si}'
                        _sst_from_x = _sst_px_from // 2
                        lines.append(
                            f'  tl.fromTo(\'{_sst_step_s}\', '
                            f'{{ opacity: 0, x: {_sst_from_x} }}, '
                            f'{{ opacity: 1, x: 0, duration: 0.240, ease: "power2.out" }}, '
                            f'{start + 0.38 + _si * 0.11:.4f});'
                        )
                elif _sst_mode_g == "caption":
                    # Word-by-word sync: each word flips to opacity:1 at its transcript timestamp.
                    # Panel entry (0.20s) completes before the first word is due — words appear
                    # on the panel as the speaker says them, like normal captions.
                    _sst_cap_words_g = _sst_ch_g.get("caption_words", [])
                    _sst_panel_ready = start + 0.60  # panel entry takes 0.38s from start+0.20
                    for _cwi, _cw in enumerate(_sst_cap_words_g):
                        _cw_t = float(_cw.get("start", 0)) if isinstance(_cw, dict) else start + 0.60
                        _cw_t = max(_sst_panel_ready, _cw_t)  # never before panel is visible
                        _sst_cw_s = f'.card[data-card-id="{card_id}"] #{card_id}-sst-cw-{_cwi}'
                        lines.append(f'  tl.set(\'{_sst_cw_s}\', {{ opacity: 1 }}, {_cw_t:.4f});')
                else:
                    _sst_nn_g = min(len(_sst_nodes_g), 4)
                    for _ni in range(_sst_nn_g):
                        _sst_node_s = f'.card[data-card-id="{card_id}"] #{card_id}-sst-node-{_ni}'
                        lines.append(
                            f'  tl.fromTo(\'{_sst_node_s}\', '
                            f'{{ opacity: 0, y: 14 }}, '
                            f'{{ opacity: 1, y: 0, duration: 0.240, ease: "power2.out" }}, '
                            f'{start + 0.38 + _ni * 0.14:.4f});'
                        )

                # ── VIDEO ENCADRÉ — clip-path masks video to 38% window, rounded on panel-facing edge ──
                # No transform on #video-stage → no SwiftShader re-rasterization.
                # clip-path is GPU-composited (zero extra memory pressure).
                # side="left" → video on LEFT → clip right 62% → show left 38%
                # side="right" → video on RIGHT → clip left 62% → show right 38%
                if _sst_side_g == "left":
                    _sst_cp_show  = "inset(0 62% 0 0 round 0 14px 14px 0)"
                    _sst_cp_hide  = "inset(0 100% 0 0 round 0 14px 14px 0)"
                else:
                    _sst_cp_show  = "inset(0 0 0 62% round 14px 0 0 14px)"
                    _sst_cp_hide  = "inset(0 0 0 100% round 14px 0 0 14px)"

                # Clip-path entry: reveal speaker window simultaneously with panel slide
                lines.append(
                    f'  tl.fromTo(\'.video-wrapper\', '
                    f'{{ clipPath: \'{_sst_cp_hide}\' }}, '
                    f'{{ clipPath: \'{_sst_cp_show}\', duration: 0.38, ease: "power3.out" }}, '
                    f'{start + 0.20:.4f});'
                )

                # Face centering via translateX — works for portrait 9:16 sources where
                # object-position has zero effect (no horizontal overflow under object-fit:cover).
                # X = window_center_pct - video_pos_x shifts the video element so the face
                # aligns with the center of the 38% clip-path window.
                _sst_win_ctr = 81.0 if _sst_side_g == "right" else 19.0
                _sst_tx = round(_sst_win_ctr - video_pos_x, 1)
                lines.append(
                    f'  tl.set(\'.video-wrapper video\', '
                    f'{{ x: \'{_sst_tx:.1f}%\' }}, {start + 0.20:.4f});'
                )

                # ── EXIT — panel fades out; clip-path reset (no animated hide) ──
                # Double safety prevents black screen when card.endSec is close to or beyond
                # segment_duration — in that case Safety B at end-0.23 would never fire.
                _sst_exit_t = round(end - 0.52, 4)
                lines.append(
                    f'  tl.to(\'{_sst_panel_s}\', '
                    f'{{ opacity: 0, duration: 0.28, ease: "power2.in" }}, '
                    f'{_sst_exit_t:.4f});'
                )
                # Safety A: immediate clip-path + translateX reset at panel fade start
                lines.append(
                    f'  tl.set(\'.video-wrapper\', {{ clipPath: "none" }}, {_sst_exit_t:.4f});'
                )
                lines.append(
                    f'  tl.set(\'.video-wrapper video\', {{ x: "0%" }}, {_sst_exit_t:.4f});'
                )
                # Safety B: second reset as fallback
                lines.append(
                    f'  tl.set(\'.video-wrapper\', {{ clipPath: "none" }}, {round(end - 0.23, 4):.4f});'
                )
            # ── number_hero GSAP — 3-act cinematic reveal ────────────────────
            elif content_style == "number_hero":
                _nh_spot_s = f'.card[data-card-id="{card_id}"] #{card_id}-nh-spotlight'
                _nh_kck_s  = f'.card[data-card-id="{card_id}"] #{card_id}-nh-kicker'
                _nh_lt_s   = f'.card[data-card-id="{card_id}"] #{card_id}-nh-line-top'
                _nh_lb_s   = f'.card[data-card-id="{card_id}"] #{card_id}-nh-line-bottom'
                _nh_num_s  = f'.card[data-card-id="{card_id}"] #{card_id}-nh-number'
                _nh_det_s  = f'.card[data-card-id="{card_id}"] #{card_id}-nh-detail'
                _nh_hints  = card.get("contentHints", {})
                _nh_has_kicker = bool(_nh_hints.get("nh_kicker") or kicker)
                _nh_has_detail = bool(_nh_hints.get("nh_detail") or detail)
                # Act 1: spotlight scale(0)→(1) + opacity, sine.out (t_in + 0.0–0.5)
                lines.append(f'  tl.fromTo(\'{_nh_spot_s}\', {{ opacity: 0, scale: 0 }}, {{ opacity: 1, scale: 1, duration: 0.500, ease: "sine.out" }}, {t_in:.4f});')
                # Act 2: number scale(2.0)→(1.0) + blur(20px)→(0), power4.out (t_in + 0.3–0.9)
                lines.append(f'  tl.fromTo(\'{_nh_num_s}\', {{ opacity: 0, scale: 2.0, filter: "blur(20px)" }}, {{ opacity: 1, scale: 1.0, filter: "blur(0px)", duration: 0.600, ease: "power4.out" }}, {t_in + 0.3:.4f});')
                # Act 3: mirror accent lines scaleX(0)→(1), power2.out, +0.1s offset (t_in + 0.8–1.3)
                lines.append(f'  tl.fromTo(\'{_nh_lt_s}\', {{ scaleX: 0 }}, {{ scaleX: 1, duration: 0.400, ease: "power2.out" }}, {t_in + 0.8:.4f});')
                lines.append(f'  tl.fromTo(\'{_nh_lb_s}\', {{ scaleX: 0 }}, {{ scaleX: 1, duration: 0.400, ease: "power2.out" }}, {t_in + 0.9:.4f});')
                # Act 4: kicker + detail fade-rise with y offset (t_in + 1.0–1.5)
                if _nh_has_kicker:
                    lines.append(f'  tl.fromTo(\'{_nh_kck_s}\', {{ opacity: 0, y: 20 }}, {{ opacity: 1, y: 0, duration: 0.400, ease: _eIn }}, {t_in + 1.0:.4f});')
                if _nh_has_detail:
                    lines.append(f'  tl.fromTo(\'{_nh_det_s}\', {{ opacity: 0, y: 15 }}, {{ opacity: 1, y: 0, duration: 0.400, ease: _eIn }}, {t_in + 1.1:.4f});')
                # Act 5: glow burst on Glass/Vibe packs only (t_in + 1.5–1.8)
                if p.get("title_glow_intense") and p.get("title_glow"):
                    lines.append(
                        f'  tl.fromTo(\'{_nh_num_s}\', '
                        f'{{ textShadow: "{_esc_js(p["title_glow"])}" }}, '
                        f'{{ textShadow: "{_esc_js(p["title_glow_intense"])}", duration: 0.150, ease: "power2.out", yoyo: true, repeat: 1 }}, '
                        f'{t_in + 1.5:.4f});'
                    )
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
            # Also skip for types with custom HTML structures that never call
            # _split_title_accent() and therefore never generate #card-id-accent
            # (prim_* primitives, number_hero use their own element IDs).
            _NO_ACCENT_SPAN_TYPES = frozenset({
                "prim_cinematic_reveal", "prim_ascension_reveal", "number_hero", "prim_stat_counter",
                "prim_split_compare", "prim_journey_map", "prim_anecdote_frame",
                "prim_numbered_rule", "prim_shatter_truth", "prim_split_stage", "prim_confession_frame",
            })
            _aw = card.get("contentHints", {}).get("accent_word", "")
            _ch_ref = card.get("contentHints", {})
            _display_ref = _ch_ref.get("number") or _ch_ref.get("title", "")
            _accent_in_dom = (
                bool(_aw)
                and _aw.lower() in _display_ref.lower()
                and content_style not in _NO_ACCENT_SPAN_TYPES
            )
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
            # Accent-line shows unless the accent_word swipe is actually rendered,
            # or the card type uses a grid layout where a bottom bar has no meaning.
            _no_line_types = {"decision_matrix"}
            if not _accent_in_dom and content_style not in _no_line_types:
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
            if content_style not in ("timeline",):
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

        # Portrait per-card scrim: fade out synchronized with card exit.
        if card.get("type") != "caption" and layout == "portrait":
            scrim_sel = f'#{card_id}-scrim'
            lines.append(
                f'  tl.to(\'{scrim_sel}\', {{opacity:0,duration:{exit_dur:.3f},ease:_eOut}},{exit_start:.4f});'
            )
            lines.append(f'  tl.set(\'{scrim_sel}\', {{opacity:0}},{end:.4f});')

        lines.append(f'  }} catch(_e) {{ console.warn("card {card_id} animation error:", _e); }}')
        lines.append("")

    # Caption suppression: fade captions out while graphic cards are visible.
    graphic_windows = [
        (round(float(c.get("startSec", 0)), 3), round(float(c.get("endSec", 0)), 3))
        for c in cards if c.get("type") != "caption"
    ]
    caption_ids = [
        _esc_js(str(cid))
        for c in cards
        if c.get("type") == "caption"
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
    segment_offset: float = 0.0,
    segment_end: float | None = None,
    segment_data_card_offset: int = 0,
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

    # ── Segmentation window ───────────────────────────────────────────────────
    # When rendering one segment of a long video, filter cards to the window
    # [segment_offset, _seg_end) and shift all GSAP times by -segment_offset so
    # the trimmed segment video (which starts at t=0) stays in sync.
    _seg_end = segment_end if segment_end is not None else duration
    if segment_offset > 0.0 or segment_end is not None:
        _seg_dur = _seg_end - segment_offset
        print(
            f"[COMPOSE] Segment window: {segment_offset:.3f}s → {_seg_end:.3f}s "
            f"({_seg_dur:.3f}s of {duration:.3f}s total)",
            flush=True,
        )

        def _in_seg(c: dict) -> bool:
            return (
                float(c.get("startSec", 0)) < _seg_end
                and float(c.get("endSec", 0)) > segment_offset
            )

        def _shift_card_times(c: dict) -> dict:
            s = round(max(0.0, float(c["startSec"]) - segment_offset), 3)
            e = round(min(_seg_dur, float(c["endSec"]) - segment_offset), 3)
            return {**c, "startSec": s, "endSec": e}

        graphic_cards = [_shift_card_times(c) for c in graphic_cards if _in_seg(c)]
        caption_cards = [_shift_card_times(c) for c in caption_cards if _in_seg(c)]

        if zoom_entries:
            _z_shifted: list[dict] = []
            for _ze in zoom_entries:
                _zs = float(_ze.get("start", 0))
                _ze_e = float(_ze.get("end", _zs))
                if _ze_e <= segment_offset or _zs >= _seg_end:
                    continue
                _z_shifted.append({
                    **_ze,
                    "start": round(max(0.0, _zs - segment_offset), 4),
                    "end": round(min(_seg_dur, _ze_e - segment_offset), 4),
                })
            zoom_entries = _z_shifted

        duration = _seg_dur

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
        _face_side_log = "left" if _face_cx < 44.0 else ("right" if _face_cx > 56.0 else "center")
        print(
            f"[COMPOSE] face_side={_face_side_log!r} cx={_face_cx:.1f}% cy={_face_cy:.1f}%"
            f" bbox=[{_fl:.0f},{_ft:.0f}->{_fr:.0f},{_fb:.0f}]"
            f" excluded_zones={'portrait-center-left,portrait-center-full' if _face_side_log=='left' else ('portrait-center-right,portrait-center-full' if _face_side_log=='right' else 'none (dimming only)')}",
            flush=True,
        )
    else:
        _face_cx, _face_cy = 50.0, 50.0
        print("[COMPOSE] face_side=None — using centered defaults (full rotation)", flush=True)

    # object-position X% to CENTER the face in the 9:16 crop window.
    # With object-fit: cover on a 16:9 source → 9:16 container, the browser
    # renders at r = (16/9)^2 ≈ 3.16× the container width; a plain cx% would
    # place the face at cx% of the container, not the center.
    # Correct formula: X = (cx * r - 50) / (r - 1), clamped to [0, 100].
    _r_16_9 = 256 / 81  # (16/9)^2 — exact for any 16:9 landscape source
    _video_pos_x = (_face_cx * _r_16_9 - 50.0) / (_r_16_9 - 1.0)
    _video_pos_x = max(0.0, min(100.0, _video_pos_x))

    def _remap_zone(card: dict, data_card_idx: int = 0) -> dict:
        style = card.get("contentHints", {}).get("style", "")
        zone = card.get("zone", "video-overlay")

        # 5-position sequential rotation for data-panel cards (both portrait and landscape).
        # Non-data-panel types (key_phrase, quote, etc.) are returned unchanged.
        # Index resets per-job (data_card_idx is initialised to 0 below).
        #
        # Portrait (9:16) — face-aware:
        #   Face centred (44 ≤ cx ≤ 56): full 5-position rotation; existing scrim
        #     dimming handles face overlap for center-zone cards.
        #   Face LEFT (cx < 44): portrait-center-left and portrait-center-full excluded
        #     (both overlap the left side). Rotation collapses to top corners +
        #     portrait-center-right only. No dimming needed — face zone is avoided.
        #   Face RIGHT (cx > 56): symmetric — portrait-center-right and
        #     portrait-center-full excluded; portrait-center-left is safe.
        #
        # Landscape (16:9):
        #   pos 0 top-left     -> landscape-tl   (top-left corner panel)
        #   pos 1 top-right    -> landscape-tr   (top-right corner panel)
        #   pos 2 center-left  -> landscape-cl   (center-left, beside face)
        #   pos 3 center-right -> landscape-cr   (center-right, beside face)
        #   pos 4 center-full  -> landscape-cf   (full-width center band, dimming applied)
        # Landscape tall types: 2-position top-only (landscape-tl-tall / landscape-tr-tall).
        _POS_NAMES = ("top-left", "top-right", "center-left", "center-right", "center-full")

        # Face-side derived from _face_cx (closure variable set in the outer compose() scope).
        _face_side = "left" if _face_cx < 44.0 else ("right" if _face_cx > 56.0 else "center")

        if _face_side == "left":
            # portrait-center-left (x:20-620) and portrait-center-full (x:40-1040)
            # both overlap a left-positioned face — exclude them entirely.
            _STD_PORTRAIT = (
                "upper-left-data-sm",    # pos 0 — top-left corner, always safe
                "upper-data",            # pos 1 — top-right corner, always safe
                "portrait-center-right", # pos 2 — right side only, face is left
                "upper-left-data-sm",    # pos 3 — wrap: no center-full available
                "upper-data",            # pos 4 — wrap
            )
        elif _face_side == "right":
            # portrait-center-right (x:480-1060) and portrait-center-full (x:40-1040)
            # both overlap a right-positioned face — exclude them entirely.
            _STD_PORTRAIT = (
                "upper-left-data-sm",    # pos 0
                "upper-data",            # pos 1
                "portrait-center-left",  # pos 2 — left side only, face is right
                "upper-data",            # pos 3 — wrap
                "upper-left-data-sm",    # pos 4 — wrap
            )
        else:
            # Face centred: full 5-position rotation; scrim+dimming handle overlap.
            _STD_PORTRAIT = (
                "upper-left-data-sm",    # pos 0
                "upper-data",            # pos 1
                "portrait-center-left",  # pos 2
                "portrait-center-right", # pos 3
                "portrait-center-full",  # pos 4
            )

        _STD_LANDSCAPE = (
            "landscape-tl",  # pos 0
            "landscape-tr",  # pos 1
            "landscape-cl",  # pos 2
            "landscape-cr",  # pos 3
            "landscape-cf",  # pos 4
        )

        if style in _DATA_PANEL_TYPES:
            _pos = data_card_idx % 5
            _pos_name = _POS_NAMES[_pos]
            is_tall = style in _TALL_DATA_PANEL_TYPES

            if layout == "portrait":
                if is_tall:
                    # Strict L/R alternation regardless of 5-slot position — use raw index.
                    target_zone = "upper-left-data" if data_card_idx % 2 == 0 else "upper-right-data-tall"
                else:
                    target_zone = _STD_PORTRAIT[_pos]
            else:  # landscape
                if is_tall:
                    target_zone = "landscape-tl-tall" if data_card_idx % 2 == 0 else "landscape-tr-tall"
                else:
                    target_zone = _STD_LANDSCAPE[_pos]

            print(
                f"[STORYBOARD] POSITION-ROTATE {card.get('id', '?')}"
                f" position={_pos} ({_pos_name}) layout={layout}"
                f" ({style}) {zone!r} -> {target_zone!r}",
                flush=True,
            )
            if zone != target_zone:
                return {**card, "zone": target_zone}
            return card

        # Hero cards in portrait: alternate portrait-center-left / portrait-center-right.
        # Prevents two consecutive hero cards (key_phrase, quote, etc.) from landing
        # on the same zone — which stacks them visually in one half of the frame.
        _PORTRAIT_HERO_STYLES = frozenset({
            "key_phrase", "quote", "attributed_quote", "question", "definition",
            "chapter_marker", "callout", "quote_carousel", "silent_beat_pause",
        })
        if layout == "portrait" and style in _PORTRAIT_HERO_STYLES:
            idx = _hero_card_idx[0]
            _hero_card_idx[0] += 1
            if _face_side == "left":
                target_zone = "portrait-center-right"
            elif _face_side == "right":
                target_zone = "portrait-center-left"
            else:
                target_zone = "portrait-center-left" if idx % 2 == 0 else "portrait-center-right"
            if zone != target_zone:
                print(
                    f"[COMPOSE] HERO-ZONE-ROTATE {card.get('id', '?')}"
                    f" idx={idx} face={_face_side!r} ({style}) {zone!r} -> {target_zone!r}",
                    flush=True,
                )
                return {**card, "zone": target_zone}
            return card

        # number_hero: spotlight layout is always portrait-center-full (centered).
        # Remapping to left/right would clip the 1000px-wide scene. Skip catch-all.
        if style == "number_hero":
            return card

        # prim_split_stage: the LLM alternates sides for visual variety but ignores face
        # position. For portrait sources object-position cannot reframe the video, so if
        # the opaque .sst-panel ends up on the speaker's side the face is hidden entirely.
        # Correct the side hint at render time: "right" puts the panel on the LEFT 0-46%;
        # "left" puts it on the RIGHT 54-100%. Flip if face_cx falls inside the panel band.
        if style == "prim_split_stage" and _has_face:
            _sst_side = card.get("contentHints", {}).get("side", "right")
            _face_in_panel = (
                (_sst_side == "right" and _face_cx < 46.0) or
                (_sst_side == "left"  and _face_cx > 54.0)
            )
            if _face_in_panel:
                _corrected = "left" if _sst_side == "right" else "right"
                print(
                    f"[COMPOSE] PSS-side-flip {card.get('id', '?')}: "
                    f"face_cx={_face_cx:.1f}% in panel band for side={_sst_side!r}"
                    f" → corrected to {_corrected!r}",
                    flush=True,
                )
                return {**card, "contentHints": {**card.get("contentHints", {}), "side": _corrected}}

        # Catch-all for all remaining types (timeline, versus_battle, dialogue,
        # testimonial, roadmap_milestone, secret_reveal, mindmap, etc.): apply
        # face-aware portrait-centre displacement. These types previously bypassed
        # remapping and could overlap the face when the LLM placed them in a centre zone.
        # video-overlay and fullscreen are intentional full-cover designs — skip them.
        if layout == "portrait" and _has_face and _face_side != "center" and zone in {
            "portrait-center-full", "portrait-center-left", "portrait-center-right",
        }:
            target_zone = "portrait-center-right" if _face_side == "left" else "portrait-center-left"
            if zone != target_zone:
                print(
                    f"[COMPOSE] BYPASS-ZONE-REMAP {card.get('id', '?')}"
                    f" face={_face_side!r} ({style}) {zone!r} -> {target_zone!r}",
                    flush=True,
                )
                return {**card, "zone": target_zone}
        return card

    # Separate counters: data cards and hero cards each have independent rotation indices.
    # Hero counter starts at 0 per composition (no segment offset needed — hero cards
    # are rare enough that stacking across segments is not a problem).
    _data_card_idx = segment_data_card_offset
    _hero_card_idx = [0]  # mutable for closure mutation inside _remap_zone
    _remapped_cards: list[dict] = []
    for _c in graphic_cards:
        _c_style = _c.get("contentHints", {}).get("style", "")
        _is_data = _c_style in _DATA_PANEL_TYPES
        _remapped_cards.append(_remap_zone(_c, _data_card_idx))
        if _is_data:
            _data_card_idx += 1
    graphic_cards = _remapped_cards

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
    timeline_js = _build_timeline_js(all_cards, zoom_entries=zoom_entries, subject_position=subject_position, pack=pack, layout=layout, video_pos_x=_video_pos_x)

    # CSS custom properties from theme
    accent_vars = "\n".join(
        f"    --accent-{i}: {color};" for i, color in enumerate(theme["accents"])
    )

    # Build Google Fonts import for pack-specific fonts
    _font_imports = {
        "lean_vibe": "Poppins:wght@400;800",
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
  #video-stage {{ position: absolute; inset: 0; }}
  .video-wrapper {{
    position: absolute; left: 0; top: 0;
    width: {width}px; height: {height}px;
    overflow: hidden; border-radius: 0; box-shadow: none;
    transform-origin: center center;
  }}
  .video-wrapper video {{ width: 100%; height: 100%; object-fit: cover; object-position: {_video_pos_x:.1f}% 50%; }}
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

    <div id="video-stage">
      <div class="video-wrapper" id="video-wrap">
        <video id="bg-video" src="input-video.mp4" muted playsinline
               data-start="0" data-duration="{duration:.3f}"
               data-track-index="1"></video>
      </div>
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
