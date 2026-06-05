"""Build .ass subtitle files.

Caption system — professional short-form edutainment standard:
  - kinetic mode: 1 word per frame, pops on its exact syllable timestamp
  - impact mode: up to 4 words per frame (grouped on pauses ≥ 0.25s)
  - Every spoken word appears — no gaps in the caption track
  - Emphasis words: larger (1.25×) + salmon colour (#FF7751) — always Title Case
  - Normal words: white, soft 1px drop shadow (no outline)
  - Position: bottom 25% of frame, alignment=2 (bottom-center)
              MarginV=150px for 9:16 (1080×1920), 80px for 16:9 (1920×1080)
  - Entry animation: scale 80%→100%, opacity 0%→100% over ~6 frames (200ms)
  - Shadow only (no outline) — softer, more legible on mixed backgrounds.
  - Font: Inter Bold (installed) or DejaVu Sans Bold fallback
  - Color hierarchy: time/location=sky-blue, action=white, emotion=light-red, hook=salmon, normal=white
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ALLOWED_FONTS = {
    "Poppins Bold", "Poppins ExtraBold", "Poppins SemiBold",
    "Inter Bold", "Montserrat Bold", "Montserrat Black",
    "Roboto Bold", "Bebas Neue", "DM Sans Bold", "DM Sans",
    "Space Grotesk Bold", "DejaVu Sans Bold", "SF Compact Bold",
    "Quicksand Bold", "Quicksand SemiBold", "Quicksand Medium",
    "Anton",
}

ALLOWED_COLORS = {
    "white":  "FFFFFF",
    "yellow": "FFE500",
    "red":    "FF3B30",
    "blue":   "0A84FF",
    "orange": "FF6B00",
}

ALLOWED_POSITIONS = {"center", "bottom", "side-left", "side-right"}
ALLOWED_STYLES    = {"impact", "kinetic", "popup", "twolevel"}

# Reference font sizes at 1080p — scaled proportionally to PlayResY in _ass_header().
# Normal captions: 58px · Emphasis captions: 72px (per professional caption spec).
CAP_SIZE_REF      = 58
CAP_SIZE_REF_EMPH = 72

# Salmon accent colour for emphasis words (matches brand)
EMPHASIS_COLOR_ASS = "&H005177FF"  # BGR for #FF7751

# Word category color map — kinetic color hierarchy
# Key = category name from plan's word_categories dict
# Value = ASS &H00BBGGRR color string
CATEGORY_COLOR_ASS: dict[str, str] = {
    "time":     "&H00EBCE87",  # sky-blue  #87CEEB
    "location": "&H00EBCE87",  # sky-blue  #87CEEB
    "action":   "&H00FFFFFF",  # white     #FFFFFF (replaced harsh purple)
    "emotion":  "&H006B6BFF",  # light-red #FF6B6B
    "hook":     "&H005177FF",  # salmon    #FF7751 (same as emphasis)
}

PUNCT_RE = re.compile(r"[.,!?;:\"'()\[\]…–—]")

# Whisper word timestamps are systematically 50–150ms earlier than when words
# are actually spoken (known faster-whisper alignment bias). This constant
# shifts all captions forward so they match actual lip movement.
WHISPER_TIMESTAMP_CORRECTION: float = 0.05   # 50ms forward shift (reduced after remapping fix)

# Additional per-group delay on top of the Whisper correction (usually 0).
CAPTION_DELAY_S: float = 0.0

# Group words separated by less than this gap into one caption line.
WORD_GROUP_GAP_S: float = 0.25   # 250 ms — natural breath pause threshold
MAX_WORDS_PER_GROUP: int = 4     # max 4 words per line per professional spec


@dataclass
class WordTiming:
    text: str
    start: float
    end: float


# ── Font mapping ──────────────────────────────────────────────────────────────
# Railway ships Debian; Poppins/Montserrat/Inter are not installed by default.
# Map all UI font names to a font that is actually present on the server.
_FONT_MAP: dict[str, str] = {
    # Fonts installed in Dockerfile pass through unchanged (their face names match).
    # Fonts NOT installed are remapped to the DejaVu fallback.
    "Poppins Bold":       "DejaVu Sans Bold",
    "Poppins ExtraBold":  "DejaVu Sans Bold",
    "Poppins SemiBold":   "DejaVu Sans Bold",
    "Montserrat Black":   "Montserrat Bold",   # Black weight not installed → Bold
    "Roboto Bold":        "DejaVu Sans Bold",
    "DM Sans Bold":       "DM Sans",           # face registered as "DM Sans"
    "Space Grotesk Bold": "DejaVu Sans Bold",
    "SF Compact Bold":    "DejaVu Sans Bold",
    # Installed fonts — no remapping needed (listed in ALLOWED_FONTS):
    # Inter Bold, Montserrat Bold, Bebas Neue, Anton, Quicksand Bold/SemiBold/Medium
}


def _ts(t: float) -> str:
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - (h * 3600 + m * 60)
    return f"{h}:{m:02d}:{s:05.2f}"


def _strip_punct(word: str) -> str:
    return PUNCT_RE.sub("", word).strip()


def _hex_to_ass_bgr(hex6: str) -> str:
    """ASS uses &H00BBGGRR primary colour notation."""
    hex6 = hex6.lstrip("#").upper()
    if len(hex6) != 6:
        hex6 = "FFFFFF"
    r, g, b = hex6[0:2], hex6[2:4], hex6[4:6]
    return f"&H00{b}{g}{r}"


def _ass_header(
    short_form: bool,
    font_name: str,
    color_hex: str,
    position: str,
    style: str = "impact",
    brand_color: str | None = None,
) -> str:
    primary = _hex_to_ass_bgr(color_hex)

    play_res_y = 1920 if short_form else 1080
    play_res_x = 1080 if short_form else 1920

    # Font sizes: 58px normal / 72px emphasis at 1080p, scaled to PlayResY.
    cap_size      = round(CAP_SIZE_REF      * play_res_y / 1080)
    cap_size_emph = round(CAP_SIZE_REF_EMPH * play_res_y / 1080)

    # Always bottom-center (alignment=2) — never covers the face.
    # MarginV is measured from the bottom edge of the frame.
    alignment = 2
    margin_v  = round(play_res_y * 0.18)  # 346px for 9:16, 194px for 16:9

    # Emphasis color: brand primary overrides the default salmon
    _emph_ass = _hex_to_ass_bgr(brand_color) if brand_color else EMPHASIS_COLOR_ASS

    # Both styles use the same positioning; style only affects grouping / animations.
    default_line = (
        f"Style: Default,{font_name},{cap_size},{primary},{primary},"
        f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,0,1,{alignment},60,60,{margin_v},1"
    )
    emphasis_line = (
        f"Style: Emphasis,{font_name},{cap_size_emph},{_emph_ass},{_emph_ass},"
        f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,0,1,{alignment},60,60,{margin_v},1"
    )

    # ── Two-level kinetic typography styles ──────────────────────────────────
    # TL_Large: large emphasis line at the bottom — bold, gold/cream, dark pill bg
    # TL_Small: small context line above — lighter, white 70% opacity, subtle bg
    # BorderStyle=3 → opaque box (BackColour fills the area behind each text block)
    # Outline= sets padding inside the box; Shadow=0 (box provides contrast)
    tl_extra = ""
    if style == "twolevel":
        tl_large_sz  = round(78  * play_res_y / 1080)
        tl_small_sz  = round(32  * play_res_y / 1080)
        tl_large_mv  = round(play_res_y * 0.13)
        # Small line sits directly above the large line with ~12px clearance
        tl_small_mv  = tl_large_mv + tl_large_sz + round(play_res_y * 0.012)
        # TL_Large color: use brand primary if provided, else gold/cream (#F5E6C8)
        tl_large_color = _hex_to_ass_bgr(brand_color) if brand_color else "&H00C8E6F5"
        # White at ~70% opacity: ASS alpha 0x4D ≈ 30% transparent → 70% visible
        tl_small_color = "&H4DFFFFFF"
        tl_large_line = (
            f"Style: TL_Large,{font_name},{tl_large_sz},"
            f"{tl_large_color},{tl_large_color},"
            f"&H00000000,&H80000000,"   # OutlineColour, BackColour (50% black box)
            f"1,0,0,0,100,100,0,0,3,10,0,{alignment},60,60,{tl_large_mv},1"
        )
        tl_small_line = (
            f"Style: TL_Small,{font_name},{tl_small_sz},"
            f"{tl_small_color},{tl_small_color},"
            f"&H00000000,&H70000000,"   # OutlineColour, BackColour (56% black box)
            f"0,0,0,0,100,100,0,0,3,6,0,{alignment},60,60,{tl_small_mv},1"
        )
        tl_extra = f"{tl_large_line}\n{tl_small_line}\n"

    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {play_res_x}\n"
        f"PlayResY: {play_res_y}\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{default_line}\n"
        f"{emphasis_line}\n"
        f"{tl_extra}"
        "\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )


def _in_window(t: float, windows: Iterable[tuple[float, float]]) -> bool:
    return any(s <= t <= e for s, e in windows)


def _group_words(
    words: list[WordTiming],
    max_words: int = MAX_WORDS_PER_GROUP,
    gap_s: float = WORD_GROUP_GAP_S,
) -> list[list[WordTiming]]:
    """Group consecutive words into caption frames.

    A new group starts when either:
      - The gap to the next word is ≥ gap_s (natural pause / breath)
      - The current group already has max_words words
    This produces 2–3 word caption cards that feel natural and readable.
    """
    groups: list[list[WordTiming]] = []
    current: list[WordTiming] = []
    for w in words:
        if current:
            gap = w.start - current[-1].end
            if gap >= gap_s or len(current) >= max_words:
                groups.append(current)
                current = [w]
            else:
                current.append(w)
        else:
            current = [w]
    if current:
        groups.append(current)
    return groups


def build_ass(
    words: Iterable[WordTiming],
    out_path: Path,
    short_form: bool,
    emphasis_words: set[str] | None = None,
    font: str = "Inter Bold",
    color: str = "white",
    position: str = "center",
    style: str = "kinetic",
    broll_windows: Iterable[tuple[float, float]] = (),
    emphasis_only: bool = False,
    word_colors: dict[str, str] | None = None,
    word_categories: dict[str, str] | None = None,
    brand_color: str | None = None,
) -> Path:
    """Render captions to an ASS file.

    kinetic style (default): 1 word per frame, pops on its exact spoken timestamp.
      Each word is independently timed. Color hierarchy: categories > word_colors >
      emphasis > white.
    impact style: 2–3-word caption frames grouped on natural pauses.

    word_categories: {"word": "time"|"location"|"action"|"emotion"|"hook"} —
      category-based color overrides applied per word.
    """
    # Font mapping: UI name → installed system font
    font = _FONT_MAP.get(font, font)
    if font not in ALLOWED_FONTS:
        font = "DejaVu Sans Bold"

    color_hex = ALLOWED_COLORS.get(color.lower(), color if color.startswith("#") else "FFFFFF")
    if position not in ALLOWED_POSITIONS:
        position = "bottom"
    if style not in ALLOWED_STYLES:
        style = "kinetic"

    # Derive emphasis color from brand primary (or fall back to default salmon)
    _emphasis_color_ass = _hex_to_ass_bgr(brand_color) if brand_color else EMPHASIS_COLOR_ASS

    emphasis_set = {_strip_punct(w).lower() for w in (emphasis_words or set())}
    broll_list   = list(broll_windows)
    word_list    = [w for w in words if _strip_punct(w.text)]

    lines = [_ass_header(short_form, font, color_hex, position, style, brand_color=brand_color)]

    word_color_map = {k.strip().lower(): v for k, v in (word_colors or {}).items()}
    # word_categories: key is the ORIGINAL word (case-preserved from the plan).
    # Normalise to lowercase for lookup.
    word_cat_map = {k.strip().lower(): v.strip().lower()
                    for k, v in (word_categories or {}).items()}

    # Kinetic: 1 word per group so each pops independently on its exact timestamp.
    # Impact: 2–3 words per group separated by natural breath pauses.
    # Twolevel: same grouping as impact (2–4 words), rendered as two simultaneous
    #   layers — large emphasis phrase (bottom) + small context phrase (top).
    if style in ("kinetic", "popup"):
        groups = _group_words(word_list, max_words=1)
    else:
        groups = _group_words(word_list)

    play_res_y    = 1920 if short_form else 1080
    cap_size_emph = round(CAP_SIZE_REF_EMPH * play_res_y / 1080)

    # ── Two-level kinetic typography rendering ────────────────────────────────
    # Each phrase group produces two simultaneous Dialogue lines:
    #   Layer 1 (TL_Large): current phrase — large, gold/cream, bounce animation
    #   Layer 0 (TL_Small): previous phrase — small, white 70%, gentle fade-in
    # This creates the reference-image look: small context above, bold focus below.
    if style == "twolevel":
        # Bounce animation for the large emphasis phrase
        _tl_anim = r"{\fscx80\fscy80\t(0,200,\fscx105\fscy105)\t(200,300,\fscx100\fscy100)}"
        # Soft fade-in for the small context phrase (already "been said")
        _tl_fade = r"{\fad(120,0)}"
        # Inline size for category/emphasis overrides within the large line
        tl_emph_sz = round(88 * play_res_y / 1080)

        prev_words: list[str] = []   # clean words of the last rendered group

        for gi, group in enumerate(groups):
            mid = (group[0].start + group[-1].end) / 2
            if _in_window(mid, broll_list):
                prev_words = []   # reset context at b-roll boundary
                continue

            clean = [_strip_punct(w.text) for w in group]
            clean = [w for w in clean if w]
            if not clean:
                continue

            has_emphasis = any(w.lower() in emphasis_set for w in clean)
            if emphasis_only and not has_emphasis:
                continue

            # Timing: hold until the next non-broll phrase starts
            start    = max(0.0, group[0].start + WHISPER_TIMESTAMP_CORRECTION + CAPTION_DELAY_S)
            end_base = group[-1].end + WHISPER_TIMESTAMP_CORRECTION
            next_start: float | None = None
            for ngi in range(gi + 1, len(groups)):
                ng = groups[ngi]
                if not _in_window((ng[0].start + ng[-1].end) / 2, broll_list):
                    next_start = ng[0].start + WHISPER_TIMESTAMP_CORRECTION
                    break
            end = next_start if (next_start is not None and next_start > end_base + 0.05) \
                else end_base + 0.4

            # ── TL_Large: current phrase with per-word color overrides ────────
            parts: list[str] = []
            for i, w in enumerate(clean):
                wl = w.lower()
                cat  = word_cat_map.get(wl)
                cust = word_color_map.get(wl)
                label = w.capitalize() if i == 0 else w.lower()
                if cat and cat in CATEGORY_COLOR_ASS:
                    parts.append(
                        f"{{\\c{CATEGORY_COLOR_ASS[cat]}\\fs{tl_emph_sz}}}{label}{{\\r}}"
                    )
                elif cust:
                    parts.append(f"{{\\c{_hex_to_ass_bgr(cust)}}}{label}{{\\r}}")
                elif wl in emphasis_set:
                    parts.append(
                        f"{{\\c{_emphasis_color_ass}\\fs{tl_emph_sz}}}{label}{{\\r}}"
                    )
                else:
                    parts.append(label)

            large_text = _tl_anim + " ".join(parts)
            lines.append(f"Dialogue: 1,{_ts(start)},{_ts(end)},TL_Large,,0,0,0,,{large_text}")

            # ── TL_Small: previous phrase as faded context ────────────────────
            if prev_words:
                small_text = _tl_fade + " ".join(w.lower() for w in prev_words)
                lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},TL_Small,,0,0,0,,{small_text}")

            prev_words = clean

        out_path.write_text("\n".join(lines), encoding="utf-8")
        return out_path

    # Entry animation tags — prepended to every Dialogue line text.
    # kinetic/popup: overshoot bounce — scale 0→112%→100% in 300ms, no pre-fade.
    #   Emphasis words overshoot further: 0→120%→100% in 350ms.
    # impact: subtle scale 97%→100% + fade over 400ms.
    if style in ("kinetic", "popup"):
        _anim = r"{\fad(0,80)\fscx0\fscy0\t(0,180,\fscx112\fscy112)\t(180,300,\fscx100\fscy100)}"
        _anim_emph = r"{\fad(0,80)\fscx0\fscy0\t(0,200,\fscx120\fscy120)\t(200,350,\fscx100\fscy100)}"
    else:
        _anim = r"{\fad(400,300)\fscx97\fscy97\t(0,400,\fscx100\fscy100)}"
        _anim_emph = _anim

    for gi, group in enumerate(groups):
        # Skip groups entirely inside b-roll windows
        mid = (group[0].start + group[-1].end) / 2
        if _in_window(mid, broll_list):
            continue

        clean_words = [_strip_punct(w.text) for w in group]
        clean_words = [w for w in clean_words if w]
        if not clean_words:
            continue

        has_emphasis = any(w.lower() in emphasis_set for w in clean_words)

        if emphasis_only and not has_emphasis:
            continue

        # Color priority per word:
        #   1. word_categories (time/location/action/emotion/hook) → category color
        #   2. word_colors (direct hex override)
        #   3. emphasis_set → salmon + larger size
        #   4. default → white (no inline tag)
        display_parts: list[str] = []
        for i, w in enumerate(clean_words):
            w_lower = w.lower()
            cat = word_cat_map.get(w_lower)
            custom_color = word_color_map.get(w_lower)

            if cat and cat in CATEGORY_COLOR_ASS:
                # Category color — enlarged to emphasis size for visual pop.
                cat_ass = CATEGORY_COLOR_ASS[cat]
                label = w.upper() if style in ("kinetic", "popup") else (w.capitalize() if i == 0 else w.lower())
                display_parts.append(
                    f"{{\\c{cat_ass}\\fs{cap_size_emph}}}{label}{{\\r}}"
                )
            elif custom_color:
                color_ass = _hex_to_ass_bgr(custom_color)
                label = w.capitalize() if i == 0 else w.lower()
                display_parts.append(f"{{\\c{color_ass}}}{label}{{\\r}}")
            elif w_lower in emphasis_set:
                display_parts.append(
                    f"{{\\c{_emphasis_color_ass}\\fs{cap_size_emph}}}{w.title()}{{\\r}}"
                )
            elif style in ("kinetic", "popup"):
                # Kinetic/popup normal words: ALL CAPS for impact at the center.
                display_parts.append(w.upper())
            elif i == 0:
                display_parts.append(w.capitalize())
            else:
                display_parts.append(w.lower())

        # Pick animation: emphasis words get the bigger overshoot.
        word_anim = _anim_emph if (style in ("kinetic", "popup") and has_emphasis) else _anim
        display = word_anim + " ".join(display_parts)

        # Apply Whisper correction + any explicit delay.
        start    = max(0.0, group[0].start + WHISPER_TIMESTAMP_CORRECTION + CAPTION_DELAY_S)
        end_base = group[-1].end + WHISPER_TIMESTAMP_CORRECTION

        # kinetic/popup dim effect: extend line to next word start, then dim.
        # The word pops in full brightness, then fades to ~56% opacity + slight
        # shrink as it waits for the next caption — eyes naturally drift to the
        # next word rather than re-reading this one.
        if style in ("kinetic", "popup"):
            # Find the next non-b-roll group for look-ahead.
            next_start: float | None = None
            for ngi in range(gi + 1, len(groups)):
                ng = groups[ngi]
                ng_mid = (ng[0].start + ng[-1].end) / 2
                if not _in_window(ng_mid, broll_list):
                    next_start = ng[0].start + WHISPER_TIMESTAMP_CORRECTION
                    break

            if next_start is not None and next_start > end_base + 0.02:
                # spoken_ms: milliseconds from line start until word finishes.
                spoken_ms = max(50, round((end_base - start) * 1000))
                dim_tag = (
                    rf"{{\t({spoken_ms},{spoken_ms + 80},"
                    r"\alpha&H71&\fscx97\fscy97)}}"
                )
                display = word_anim + " ".join(display_parts) + dim_tag
                end = next_start
            else:
                end = end_base
        else:
            end = end_base

        lines.append(
            f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{display}"
        )

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
