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
    words: list,
    output_path,
    video_w: int = 1080,
    video_h: int = 1920,
    brand_color: str = "#FF7751",
    caption_font: str = "Inter Bold",
    caption_style_map: dict | None = None,
    video_duration: float | None = None,
) -> Path:
    """Render two-level Jordan Belfort phrase captions to an ASS file.

    Two-level system:
      Layer 1 (Large): current phrase — big, brand color, bounce animation
      Layer 0 (Small): previous phrase — small, white 70%, gentle fade-in
    Phrases are 3–4 words grouped on natural breath pauses.
    caption_style_map: {source_segment_start: "normal"|"emphasis"|"highlight"}
      Segments with "emphasis" render in ALL CAPS for extra impact.
    """
    output_path = Path(output_path)

    # Resolve font: UI name → installed font face
    font = _FONT_MAP.get(caption_font, caption_font)
    if font not in ALLOWED_FONTS:
        font = "DejaVu Sans Bold"

    play_res_x = video_w
    play_res_y = video_h

    # Font sizes — calibrated for 9:16 1080×1920 reference, scaled to actual height
    large_sz = round(80 * play_res_y / 1920)
    small_sz = round(34 * play_res_y / 1920)
    # Slightly larger for XL "emphasis" segment phrases
    xlarge_sz = round(96 * play_res_y / 1920)

    # Colors
    large_color = _hex_to_ass_bgr(brand_color) if brand_color else EMPHASIS_COLOR_ASS
    # White at ~70% opacity: ASS alpha 0x4D ≈ 30% transparent
    small_color = "&H4DFFFFFF"

    # Position from bottom edge (MarginV)
    large_mv = round(play_res_y * 0.15)
    small_mv = large_mv + large_sz + round(play_res_y * 0.015)

    header = (
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
        # Large: current phrase — bold, brand color, semi-transparent dark pill bg
        f"Style: Large,{font},{large_sz},{large_color},{large_color},"
        f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,0,1,2,60,60,{large_mv},1\n"
        # LargeBrand: emphasis segments — even bigger, same brand color
        f"Style: LargeBrand,{font},{xlarge_sz},{large_color},{large_color},"
        f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,0,1,2,60,60,{large_mv},1\n"
        # Small: previous phrase — lighter weight, white 70%, subtle pill bg
        f"Style: Small,{font},{small_sz},{small_color},{small_color},"
        f"&H00000000,&H70000000,0,0,0,0,100,100,0,0,3,5,0,2,60,60,{small_mv},1\n"
        "\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )

    # Phrase grouping: 3–4 words, split on natural breath pauses (≥ 250ms)
    word_list = [w for w in words if _strip_punct(w.text)]
    groups = _group_words(word_list, max_words=4, gap_s=WORD_GROUP_GAP_S)

    # Bounce animation: scale 80%→105%→100% over 300ms (Jordan Belfort pop)
    _bounce = r"{\fscx80\fscy80\t(0,200,\fscx105\fscy105)\t(200,300,\fscx100\fscy100)}"
    # Fade-in for previous phrase (already "been said", softer appearance)
    _fade_in = r"{\fad(120,0)}"

    # Build sorted list of (timestamp, style) pairs from caption_style_map
    _style_entries: list[tuple[float, str]] = []
    if caption_style_map:
        _style_entries = sorted(
            ((float(k), str(v)) for k, v in caption_style_map.items()),
            key=lambda x: x[0],
        )

    def _segment_style_for(t: float) -> str:
        """Return the caption style name for timestamp t based on caption_style_map."""
        if not _style_entries:
            return "normal"
        style_name = "normal"
        for seg_start, seg_style in _style_entries:
            if t >= seg_start:
                style_name = seg_style
            else:
                break
        return style_name

    lines = [header]
    prev_text = ""

    for gi, group in enumerate(groups):
        clean = [_strip_punct(w.text) for w in group]
        clean = [w for w in clean if w]
        if not clean:
            continue

        start = max(0.0, group[0].start + WHISPER_TIMESTAMP_CORRECTION + CAPTION_DELAY_S)
        end_base = group[-1].end + WHISPER_TIMESTAMP_CORRECTION

        # Skip phrases that start after the video ends
        if video_duration is not None and start > video_duration:
            break

        # Find next group's start for hold-until-next behaviour
        next_start: float | None = None
        for ngi in range(gi + 1, len(groups)):
            ng = groups[ngi]
            nc = [_strip_punct(w.text) for w in ng]
            if any(nc):
                next_start = ng[0].start + WHISPER_TIMESTAMP_CORRECTION
                break

        end = (
            next_start
            if (next_start is not None and next_start > end_base + 0.05)
            else end_base + 0.4
        )

        if video_duration is not None and end > video_duration:
            end = video_duration

        # Determine ASS style from caption_style_map
        seg_style = _segment_style_for(start)
        ass_style = "LargeBrand" if seg_style == "emphasis" else "Large"

        # Build phrase text: "Capitalize first word, lower rest" for readability.
        # ALL CAPS for "emphasis" segments (high-energy Jordan Belfort moments).
        if seg_style == "emphasis":
            phrase_text = " ".join(w.upper() for w in clean)
        else:
            phrase_text = " ".join(w.capitalize() if i == 0 else w.lower() for i, w in enumerate(clean))

        large_text = _bounce + phrase_text
        lines.append(f"Dialogue: 1,{_ts(start)},{_ts(end)},{ass_style},,0,0,0,,{large_text}")

        # Previous phrase shown above in small faded text
        if prev_text:
            small_text = _fade_in + prev_text
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Small,,0,0,0,,{small_text}")

        # Update prev_text for next iteration (always lowercase for "been said" feel)
        prev_text = " ".join(w.lower() for w in clean)

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path
