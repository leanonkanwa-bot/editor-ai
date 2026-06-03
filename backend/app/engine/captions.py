"""Build .ass subtitle files.

Caption system — professional short-form edutainment standard:
  - 2–3 words per caption frame (grouped on pauses ≥ 0.25s)
  - Every spoken word appears — no gaps in the caption track
  - Emphasis words: larger (1.3×) + salmon colour (#FF7751) — always Title Case
  - Normal words: sentence case, white, clean 2px black outline
  - Position: bottom 15% of frame (MarginV = 15% of PlayResY), never covers face
  - No shadow. Outline only.
  - Font: DejaVu Sans Bold (maps from Poppins/Montserrat on Linux)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ALLOWED_FONTS = {
    "Poppins Bold", "Poppins ExtraBold", "Poppins SemiBold",
    "Inter Bold", "Montserrat Bold", "Montserrat Black",
    "Roboto Bold", "Bebas Neue", "DM Sans Bold", "Space Grotesk Bold",
    "DejaVu Sans Bold",
}

ALLOWED_COLORS = {
    "white":  "FFFFFF",
    "yellow": "FFE500",
    "red":    "FF3B30",
    "blue":   "0A84FF",
    "orange": "FF6B00",
}

ALLOWED_POSITIONS = {"center", "bottom", "side-left", "side-right"}
ALLOWED_STYLES    = {"impact", "kinetic"}

# 5% of PlayResY — bold and readable at full screen.
CAP_SIZE_SHORT      = 96   # 5% of 1920
CAP_SIZE_LONG       = 54   # 5% of 1080
# Emphasis words: 1.3× larger (Title Case, salmon #FF7751)
CAP_SIZE_SHORT_EMPH = 125  # ~6.5% of 1920
CAP_SIZE_LONG_EMPH  = 70   # ~6.5% of 1080

# Salmon accent colour for emphasis words (matches brand)
EMPHASIS_COLOR_ASS = "&H005177FF"  # BGR for #FF7751

PUNCT_RE = re.compile(r"[.,!?;:\"'()\[\]…–—]")

# 1-frame lead so captions feel synced (brain reads ~33ms ahead of audio).
CAPTION_LEAD_S: float = 1.0 / 30.0  # 33 ms

# Group words separated by less than this gap into one caption line.
WORD_GROUP_GAP_S: float = 0.25   # 250 ms — natural breath pause threshold
MAX_WORDS_PER_GROUP: int = 3


@dataclass
class WordTiming:
    text: str
    start: float
    end: float


# ── Font mapping ──────────────────────────────────────────────────────────────
# Railway ships Debian; Poppins/Montserrat/Inter are not installed by default.
# Map all UI font names to a font that is actually present on the server.
_FONT_MAP: dict[str, str] = {
    "Poppins Bold":       "DejaVu Sans Bold",
    "Poppins ExtraBold":  "DejaVu Sans Bold",
    "Poppins SemiBold":   "DejaVu Sans Bold",
    "Inter Bold":         "DejaVu Sans Bold",
    "Montserrat Bold":    "DejaVu Sans Bold",
    "Montserrat Black":   "DejaVu Sans Bold",
    "Roboto Bold":        "DejaVu Sans Bold",
    "DM Sans Bold":       "DejaVu Sans Bold",
    "Space Grotesk Bold": "DejaVu Sans Bold",
    "Bebas Neue":         "DejaVu Sans Bold",
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
) -> str:
    primary = _hex_to_ass_bgr(color_hex)

    play_res_y = 1920 if short_form else 1080
    play_res_x = 1080 if short_form else 1920
    cap_size      = CAP_SIZE_SHORT      if short_form else CAP_SIZE_LONG
    cap_size_emph = CAP_SIZE_SHORT_EMPH if short_form else CAP_SIZE_LONG_EMPH

    # MarginV = 15% of PlayResY from bottom → captions sit in bottom 20% zone
    margin_v = int(play_res_y * 0.15)

    if style == "kinetic":
        # Kinetic bar: dark semi-transparent background behind text
        back     = "&HE61A1A1A"
        # Default words: white on dark bar
        default_line = (
            f"Style: Default,{font_name},{cap_size},{primary},{primary},"
            f"&H00000000,{back},1,0,0,0,100,100,0,0,4,12,0,2,60,60,{margin_v},1"
        )
        # Emphasis: salmon on dark bar, slightly larger
        emphasis_line = (
            f"Style: Emphasis,{font_name},{cap_size_emph},{EMPHASIS_COLOR_ASS},{EMPHASIS_COLOR_ASS},"
            f"&H00000000,{back},1,0,0,0,100,100,0,0,4,12,0,2,60,60,{margin_v},1"
        )
    else:
        # Impact: clean 2px black outline, no shadow (Shadow=0)
        # Default words: white, outline only
        default_line = (
            f"Style: Default,{font_name},{cap_size},{primary},{primary},"
            f"&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,2,0,2,60,60,{margin_v},1"
        )
        # Emphasis: salmon colour, 1.3× size, bolder outline
        emphasis_line = (
            f"Style: Emphasis,{font_name},{cap_size_emph},{EMPHASIS_COLOR_ASS},{EMPHASIS_COLOR_ASS},"
            f"&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,3,0,2,60,60,{margin_v},1"
        )

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
        f"{emphasis_line}\n\n"
        "[Events]\n"
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
    font: str = "Poppins Bold",
    color: str = "white",
    position: str = "bottom",
    style: str = "impact",
    broll_windows: Iterable[tuple[float, float]] = (),
    emphasis_only: bool = False,
) -> Path:
    """Render captions to an ASS file.

    Groups words into 2–3-word caption frames. Every spoken word gets a
    caption (emphasis_only=False by default). Emphasis words are displayed
    larger and in salmon (#FF7751).
    """
    # Font mapping: UI name → installed system font
    font = _FONT_MAP.get(font, font)
    if font not in ALLOWED_FONTS:
        font = "DejaVu Sans Bold"

    color_hex = ALLOWED_COLORS.get(color.lower(), color if color.startswith("#") else "FFFFFF")
    if position not in ALLOWED_POSITIONS:
        position = "bottom"
    if style not in ALLOWED_STYLES:
        style = "impact"
    if style == "kinetic":
        position = "bottom"

    emphasis_set = {_strip_punct(w).lower() for w in (emphasis_words or set())}
    broll_list   = list(broll_windows)
    word_list    = [w for w in words if _strip_punct(w.text)]

    lines = [_ass_header(short_form, font, color_hex, position, style)]

    groups = _group_words(word_list)
    for group in groups:
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

        # Per-word inline ASS tags: emphasis words get salmon colour + larger size,
        # all other words stay in Default style at normal size.
        cap_size_emph = CAP_SIZE_SHORT_EMPH if short_form else CAP_SIZE_LONG_EMPH
        display_parts: list[str] = []
        for i, w in enumerate(clean_words):
            if w.lower() in emphasis_set:
                # Inline override: salmon colour + emphasis size, then reset
                display_parts.append(
                    f"{{\\c{EMPHASIS_COLOR_ASS}\\fs{cap_size_emph}}}{w.title()}{{\\r}}"
                )
            elif i == 0:
                display_parts.append(w.capitalize())
            else:
                display_parts.append(w.lower())
        display = " ".join(display_parts)

        # Always use Default style; per-word overrides handle emphasis inline
        start = max(0.0, group[0].start - CAPTION_LEAD_S)
        end   = group[-1].end
        lines.append(
            f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{display}"
        )

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
