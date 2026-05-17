"""Build .ass subtitle files.

Hard rules (Hormozi/Sanchez/MrBeast caption system):
  - ONE word per card. Always. No exceptions.
  - One single color + subtle shadow/outline for readability.
  - Font is Poppins Bold by default. User can pick from a small allowlist.
  - Emphasis-only mode (default): captions appear ONLY for emphasis words.
  - No punctuation in captions.
  - Words appear ONLY when they are spoken — start..end of each word.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# Allowed fonts — must be installed in the Docker image (see Dockerfile).
ALLOWED_FONTS = {
    "Poppins Bold",
    "Poppins ExtraBold",
    "Poppins SemiBold",
    "Inter Bold",
    "Montserrat Bold",
    "Montserrat Black",
    "Roboto Bold",
    "Bebas Neue",
    "DM Sans Bold",
    "Space Grotesk Bold",
}

ALLOWED_COLORS = {
    "white": "FFFFFF",
    "yellow": "FFE500",
    "red": "FF3B30",
    "blue": "0A84FF",
    "orange": "FF6B00",
}

ALLOWED_POSITIONS = {"center", "bottom", "side-left", "side-right"}

# Caption styles — each is a different visual treatment of the one-word card.
ALLOWED_STYLES = {"impact", "kinetic"}

# Single consistent caption size: ~7% of PlayResY.
# One size for all captions — no tier system, no inconsistency between renders.
# ASS PlayResY is 1920 for short form, 1080 for long form.
CAP_SIZE_SHORT = 134    # 7.0% of 1920
CAP_SIZE_LONG = 76      # 7.0% of 1080

PUNCT_RE = re.compile(r"[.,!?;:\"'()\[\]…–—]")


@dataclass
class WordTiming:
    text: str
    start: float
    end: float


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


def _alignment_for(position: str) -> tuple[int, int, int, int]:
    """Return (Alignment, MarginL, MarginR, MarginV) for ASS Default style.

    Alignment numbers:
       1 = bottom-left, 2 = bottom-center, 3 = bottom-right,
       4 = mid-left,    5 = mid-center,    6 = mid-right,
       7 = top-left,    8 = top-center,    9 = top-right.
    """
    if position == "bottom":
        return (2, 60, 60, 320)
    if position == "side-left":
        return (4, 80, 60, 0)
    if position == "side-right":
        return (6, 60, 80, 0)
    return (5, 60, 60, 0)  # center default


def _ass_header(
    short_form: bool,
    font_name: str,
    color_hex: str,
    position: str,
    style: str = "impact",
) -> str:
    primary = _hex_to_ass_bgr(color_hex)
    align, ml, mr, mv = _alignment_for(position)

    cap_size = CAP_SIZE_SHORT if short_form else CAP_SIZE_LONG

    play_res_y = 1920 if short_form else 1080
    play_res_x = 1080 if short_form else 1920

    if style == "kinetic":
        # Style 4 — Kinetic Bottom Bar: dark bar background, white text on top.
        # BorderStyle=4 makes BackColour fill a box behind the text.
        # &HE61A1A1A = ~90% opaque dark grey (alpha 0xE6, BGR 1A1A1A).
        back = "&HE61A1A1A"
        align_v = 2  # bottom-center
        margin_v = 200
        default_line = (
            f"Style: Default,{font_name},{cap_size},{primary},{primary},"
            f"&H00000000,{back},1,0,0,0,100,100,0,0,4,12,0,{align_v},80,80,{margin_v},1"
        )
        emphasis_line = (
            f"Style: Emphasis,{font_name},{cap_size},{primary},{primary},"
            f"&H00000000,{back},1,0,0,0,100,100,0,0,4,12,0,{align_v},80,80,{margin_v},1"
        )
    else:
        # Style 1 — Impact: single size, 2px outline + 2px drop shadow for
        # readability. OutlineColour=black, shadow from semi-transparent black.
        # BorderStyle=1: outline+shadow. Outline=2, Shadow=2.
        default_line = (
            f"Style: Default,{font_name},{cap_size},{primary},{primary},"
            f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,2,2,{align},{ml},{mr},{mv},1"
        )
        emphasis_line = (
            f"Style: Emphasis,{font_name},{cap_size},{primary},{primary},"
            f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,2,2,{align},{ml},{mr},{mv},1"
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


def build_ass(
    words: Iterable[WordTiming],
    out_path: Path,
    short_form: bool,
    emphasis_words: set[str] | None = None,
    font: str = "Poppins Bold",
    color: str = "white",
    position: str = "center",
    style: str = "impact",
    broll_windows: Iterable[tuple[float, float]] = (),
    emphasis_only: bool = True,
) -> Path:
    """Render captions.

    When emphasis_only=True (default), only words in emphasis_words get a
    caption card — sparse, punchy, high-retention. When False, every spoken
    word gets a card (legacy behavior).
    """
    if font not in ALLOWED_FONTS:
        font = "Poppins Bold"
    color_hex = ALLOWED_COLORS.get(color.lower(), color if color.startswith("#") else "FFFFFF")
    if position not in ALLOWED_POSITIONS:
        position = "center"
    if style not in ALLOWED_STYLES:
        style = "impact"
    # Kinetic bar always sits at the bottom — that's part of its identity.
    if style == "kinetic":
        position = "bottom"

    emphasis_set = {_strip_punct(w).lower() for w in (emphasis_words or set())}
    broll_list = list(broll_windows)

    lines = [_ass_header(short_form, font, color_hex, position, style)]

    for w in words:
        clean = _strip_punct(w.text).upper()
        if not clean:
            continue
        # Hard rule: no captions during B-roll windows.
        if _in_window((w.start + w.end) / 2, broll_list):
            continue
        is_emphasis = clean.lower() in emphasis_set
        if emphasis_only and not is_emphasis:
            continue
        style_name = "Emphasis" if is_emphasis else "Default"
        lines.append(
            f"Dialogue: 0,{_ts(w.start)},{_ts(w.end)},{style_name},,0,0,0,,{clean}"
        )

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
