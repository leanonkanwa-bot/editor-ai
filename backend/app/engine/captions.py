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
    "Anton", "Playfair Display Bold",
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
    "Poppins Bold":       "Montserrat Bold",   # Montserrat is installed; Poppins is not
    "Poppins ExtraBold":  "Montserrat Bold",
    "Poppins SemiBold":   "Montserrat Bold",
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


def hex_to_ass_bgr(hex_color: str) -> str:
    """Convert #RRGGBB to ASS BGR string (without &H00 prefix).

    Example: "#FF7751" → "5177FF"
    Full ASS tag: "&H005177FF&"
    """
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        hex_color = "FFFFFF"
    r = hex_color[0:2]
    g = hex_color[2:4]
    b = hex_color[4:6]
    return f"{b}{g}{r}".upper()


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


def apply_emphasis(text: str, emphasis_words: list[str], brand_color_ass: str) -> str:
    """Wrap emphasis_words in ASS inline color override tags.

    Uses word-boundary matching (case-insensitive). The {\r} tag resets
    back to the style default after each emphasized word.
    """
    for word in emphasis_words:
        pat = re.compile(r'(?<!\w)' + re.escape(word) + r'(?!\w)', re.IGNORECASE)
        repl = "{\\c" + brand_color_ass + "}" + word + "{\\r}"
        text = pat.sub(repl, text)
    return text


def _build_long_form_ass(
    moments: list[dict],
    output_path: Path,
    video_w: int = 1920,
    video_h: int = 1080,
    brand_color: str = "#FF7751",
) -> Path:
    """Build a long-form (16:9) selective strategic captions ASS file.

    Jordan Belfort philosophy: only caption KEY MOMENTS. Long sections between
    captions are intentional — let the speaker breathe.

    Styles:
      Hook        — Playfair Display Bold 88px, white, center-screen, \\fad(150,150)
      Concept     — Montserrat Bold 62px, white + brand emphasis, lower-third
      Stat        — Montserrat Bold 96px, brand color, center, scale pop
      StatContext — Montserrat Bold 48px, white, center, subtle
    """
    brand_ass  = _hex_to_ass_bgr(brand_color)   # &H00BBGGRR

    scale = video_h / 1080.0
    hook_sz        = round(88 * scale)
    concept_sz     = round(62 * scale)
    stat_sz        = round(96 * scale)
    stat_ctx_sz    = round(48 * scale)

    concept_mv     = round(video_h * 0.12)  # lower-third bottom margin
    stat_ctx_mv    = round(video_h * 0.12)  # below-center margin for context

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {video_w}\n"
        f"PlayResY: {video_h}\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        # Hook: Playfair Display Bold, white, center (Alignment=5), 3px black outline
        f"Style: Hook,Playfair Display Bold,{hook_sz},&H00FFFFFF,&H00FFFFFF,"
        f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,0,5,40,40,0,1\n"
        # Concept: Montserrat Bold, white, lower-third (Alignment=2), 2px outline
        f"Style: Concept,Montserrat Bold,{concept_sz},&H00FFFFFF,&H00FFFFFF,"
        f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,2,0,2,40,40,{concept_mv},1\n"
        # Stat: Montserrat Bold, brand color, center (Alignment=5), 3px outline
        f"Style: Stat,Montserrat Bold,{stat_sz},{brand_ass},{brand_ass},"
        f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,0,5,40,40,0,1\n"
        # StatContext: Montserrat Bold, white, center (Alignment=5), subtle 1px outline
        f"Style: StatContext,Montserrat Bold,{stat_ctx_sz},&H00FFFFFF,&H00FFFFFF,"
        f"&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,1,0,5,40,40,{stat_ctx_mv},1\n"
        "\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )

    _style_map: dict[str, tuple[str, str]] = {
        "hook":         ("Hook",        r"{\fad(150,150)}"),
        "concept":      ("Concept",     r"{\fad(120,120)}"),
        "stat":         ("Stat",        r"{\fad(80,80)\fscx90\fscy90\t(0,200,\fscx100\fscy100)}"),
        "stat_context": ("StatContext", r"{\fad(80,80)}"),
        "list":         ("Concept",     r"{\fad(100,100)\move(0,20,0,0)}"),
        "list_item":    ("Concept",     r"{\fad(100,100)\move(0,20,0,0)}"),
        "quote":        ("Concept",     r"{\fad(120,120)}"),
        "marker":       ("Concept",     r"{\fad(80,80)}"),
    }

    lines = [header]
    for moment in moments:
        start = float(moment.get("start", 0))
        end   = float(moment.get("end",   start + 3.0))
        text  = str(moment.get("text",   "")).strip()
        style = str(moment.get("style",  "concept"))
        emph  = moment.get("emphasis_words") or []

        if not text:
            continue

        ass_style, anim = _style_map.get(style, ("Concept", r"{\fad(120,120)}"))
        display = apply_emphasis(text, emph, brand_ass) if emph else text
        lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},{ass_style},,0,0,0,,{anim}{display}")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[CAPTIONS LONG] Written {len(lines) - 1} selective caption moments")
    return output_path


def build_ass(
    words: list,
    output_path,
    video_w: int = 1080,
    video_h: int = 1920,
    brand_color: str = "#FF7751",
    caption_font: str = "Inter Bold",
    caption_style_map: dict | None = None,
    video_duration: float | None = None,
    mode: str = "short",
    caption_moments: list[dict] | None = None,
) -> Path:
    """Render captions to an ASS subtitle file.

    Short-form (mode='short'): two-level Jordan Belfort style — Keyword + Context.
    Long-form  (mode='long'):  strategic moments only, 4 styles — Hook/Concept/Stat/ListItem.
                               Requires caption_moments list from the planner.
                               Falls back to short-form if caption_moments is empty.
    """
    output_path = Path(output_path)

    if mode == "long" and caption_moments:
        return _build_long_form_ass(
            caption_moments, output_path,
            video_w=video_w, video_h=video_h, brand_color=brand_color,
        )

    play_res_x = video_w
    play_res_y = video_h

    # Jordan Belfort two-level typography:
    # BOTTOM (Keyword): serif font, large, brand color — the current phrase
    # TOP    (Context): sans font, small, white       — the previous phrase
    keyword_font = "Playfair Display Bold"
    context_font = "Montserrat Bold"

    # Font sizes scaled from 1920px reference (PlayResY == video_h, so these
    # are already in the correct coordinate space).
    keyword_sz = round(88 * play_res_y / 1920)
    context_sz = round(48 * play_res_y / 1920)

    # Colors
    keyword_color = _hex_to_ass_bgr(brand_color) if brand_color else EMPHASIS_COLOR_ASS
    context_color = "&H00FFFFFF"  # white
    outline_color = "&H00000000"  # black
    back_color    = "&H00000000"  # transparent

    # Margins from bottom edge.
    # keyword_mv: 8% from bottom.
    # context_mv: sits directly above keyword — keyword margin + keyword height + 20px gap.
    keyword_mv = int(play_res_y * 0.08)
    context_mv = keyword_mv + keyword_sz + 20

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
        # Keyword: current phrase — serif, brand color, 88px, 3px black outline
        f"Style: Keyword,{keyword_font},{keyword_sz},{keyword_color},{keyword_color},"
        f"{outline_color},{back_color},1,0,0,0,100,100,0,0,1,3,0,2,60,60,{keyword_mv},1\n"
        # Context: previous phrase — sans, white, 48px, 2px black outline
        f"Style: Context,{context_font},{context_sz},{context_color},{context_color},"
        f"{outline_color},{back_color},1,0,0,0,100,100,0,0,1,2,0,2,60,60,{context_mv},1\n"
        "\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )

    # Step 1 — Group words into phrase units of up to 4 words, split on pauses ≥ 0.25s
    word_list = [w for w in words if _strip_punct(w.text)]
    groups = _group_words(word_list, max_words=4, gap_s=0.25)

    # Animation tags per spec
    _kw_anim  = r"{\fad(80,0)\t(\fscx97\fscy97,\fscx100\fscy100)}"
    _ctx_anim = r"{\fad(80,0)}"

    lines = [header]
    prev_text = ""

    for gi, group in enumerate(groups):
        # Step 2 — Build clean phrase text
        clean = [_strip_punct(w.text) for w in group]
        clean = [w for w in clean if w]
        if not clean:
            continue

        start = max(0.0, group[0].start + WHISPER_TIMESTAMP_CORRECTION)
        end_base = group[-1].end + WHISPER_TIMESTAMP_CORRECTION

        if video_duration is not None and start > video_duration:
            break

        # Find next phrase start for gap-filling
        next_start: float | None = None
        for ngi in range(gi + 1, len(groups)):
            ng = groups[ngi]
            nc = [_strip_punct(w.text) for w in ng]
            if any(nc):
                next_start = ng[0].start + WHISPER_TIMESTAMP_CORRECTION
                break

        # Step 3 — Hold until next phrase; NEVER overlap (cap at next_start - 0.01)
        if next_start is not None:
            end = next_start - 0.01
        else:
            end = end_base + 0.1

        if video_duration is not None and end > video_duration:
            end = video_duration

        # Capitalize first word only
        phrase_text = " ".join(
            w.capitalize() if i == 0 else w.lower()
            for i, w in enumerate(clean)
        )

        # Both events get IDENTICAL timestamps — they are always paired
        # Keyword (bottom, Layer 1): current phrase, animated serif
        lines.append(
            f"Dialogue: 1,{_ts(start)},{_ts(end)},Keyword,,0,0,0,,{_kw_anim}{phrase_text}"
        )
        # Context (top, Layer 0): previous phrase (empty string for phrase[0])
        lines.append(
            f"Dialogue: 0,{_ts(start)},{_ts(end)},Context,,0,0,0,,{_ctx_anim}{prev_text}"
        )

        prev_text = phrase_text

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path
