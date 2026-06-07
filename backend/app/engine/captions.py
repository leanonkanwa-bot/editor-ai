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

import os
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

# Absolute path map — used by _get_font_path() for drawtext/fontfile lookups
# and for diagnostics. Separate from _FONT_MAP (which remaps ASS font names).
FONT_MAP: dict[str, str] = {
    "Inter Bold":            "/usr/local/share/fonts/leanlead/Inter-Bold.otf",
    "Inter":                 "/usr/local/share/fonts/leanlead/Inter-Bold.otf",
    "Montserrat Bold":       "/usr/local/share/fonts/leanlead/Montserrat-Bold.ttf",
    "Montserrat":            "/usr/local/share/fonts/leanlead/Montserrat-Bold.ttf",
    "Poppins Bold":          "/usr/local/share/fonts/leanlead/Poppins-Bold.ttf",
    "Poppins":               "/usr/local/share/fonts/leanlead/Poppins-Bold.ttf",
    "Bebas Neue":            "/usr/local/share/fonts/leanlead/BebasNeue-Regular.ttf",
    "Anton":                 "/usr/local/share/fonts/leanlead/Anton-Regular.ttf",
    "DM Sans":               "/usr/local/share/fonts/leanlead/DMSans-Bold.ttf",
    "DM Sans Bold":          "/usr/local/share/fonts/leanlead/DMSans-Bold.ttf",
    "Playfair Display Bold": "/usr/local/share/fonts/leanlead/PlayfairDisplay-Bold.ttf",
    "default":               "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
}


def _get_font_path(font_name: str) -> str:
    """Return the absolute path for a font, logging resolution for diagnostics.

    Tries the exact name, then '<name> Bold', then falls back to DejaVu.
    Used for ffmpeg drawtext fontfile= and other path-based font lookups.
    """
    for candidate in (font_name, font_name + " Bold"):
        path = FONT_MAP.get(candidate)
        if path and os.path.exists(path):
            print(f"[FONT] Using: {font_name} → {path}")
            return path
    fallback = FONT_MAP["default"]
    print(f"[FONT] NOT FOUND: {font_name} → falling back to {fallback}")
    return fallback


def _ensure_font(font_name: str) -> str:
    """Ensure font is available locally, downloading from Google Fonts if needed.

    Returns the ASS font family name (weight suffixes stripped).
    Falls back to DejaVu Sans if download fails or font not found.
    """
    import urllib.request

    # Check pre-installed fonts first
    for candidate in (font_name, font_name + " Bold"):
        path = FONT_MAP.get(candidate)
        if path and os.path.exists(path):
            print(f"[FONT] Pre-installed: {font_name}")
            family = (
                font_name
                .replace(" ExtraBold", "")
                .replace(" SemiBold", "")
                .replace(" Bold", "")
                .strip()
            )
            return family

    # Try to download from Google Fonts CSS2 API (no API key needed)
    try:
        font_query = font_name.replace(" Bold", "").replace(" ", "+").strip()
        css_url = f"https://fonts.googleapis.com/css2?family={font_query}:wght@700&display=swap"
        req = urllib.request.Request(css_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            css = resp.read().decode("utf-8")

        # Extract the first font URL from the CSS
        urls = re.findall(r"src:\s*url\(([^)]+)\)", css)
        font_url = None
        for u in urls:
            u = u.strip("'\"")
            if ".ttf" in u or ".woff2" in u:
                font_url = u
                break
        if not font_url and urls:
            font_url = urls[0].strip("'\"")

        if font_url:
            safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", font_name)
            ext = ".woff2" if ".woff2" in font_url else ".ttf"
            dst = f"/usr/local/share/fonts/leanlead/{safe_name}{ext}"
            req2 = urllib.request.Request(font_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req2, timeout=15) as resp2:
                data = resp2.read()
            if len(data) > 1000:
                with open(dst, "wb") as f:
                    f.write(data)
                os.system("fc-cache -f /usr/local/share/fonts/leanlead/ 2>/dev/null")
                print(f"[FONT] Downloaded: {font_name} ({len(data)} bytes) → {dst}")
                family = (
                    font_name
                    .replace(" ExtraBold", "")
                    .replace(" SemiBold", "")
                    .replace(" Bold", "")
                    .strip()
                )
                return family
    except Exception as exc:
        print(f"[FONT] Download failed for {font_name}: {exc}")

    print(f"[FONT] Fallback to DejaVu Sans for: {font_name}")
    return "DejaVu Sans"


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


def apply_emphasis(
    text: str,
    emphasis_words: list[str],
    brand_color_ass: str,
    with_scale: bool = False,
) -> str:
    """Wrap emphasis_words in ASS inline color override tags.

    Uses str.replace() to avoid re.sub() interpreting \\c and \\r as
    regex escape sequences in the replacement string.
    with_scale=True adds \\fscx110\\fscy110 — makes the word 10% larger.
    """
    for word in (emphasis_words or []):
        if not word:
            continue
        if with_scale:
            replacement = "{\\c" + brand_color_ass + "\\fscx110\\fscy110}" + word + "{\\r}"
        else:
            replacement = "{\\c" + brand_color_ass + "}" + word + "{\\r}"
        text = text.replace(word, replacement)
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
    brand_ass = _hex_to_ass_bgr(brand_color)

    scale = video_h / 1080.0
    hook_sz     = round(88 * scale)
    concept_sz  = round(68 * scale)   # 68px per professional spec
    stat_sz     = round(96 * scale)
    stat_ctx_sz = round(48 * scale)
    mantra_sz   = round(78 * scale)
    list_sz     = round(62 * scale)

    concept_mv  = round(video_h * 0.12)  # lower-third bottom margin
    stat_ctx_mv = round(video_h * 0.12)

    # Concept slide-up: move from 15px below lower-third anchor to natural position
    _cx        = video_w // 2
    _cy_end    = video_h - concept_mv
    _cy_start  = _cy_end + 15
    _concept_anim = (
        "{\\fad(150,100)"
        f"\\move({_cx},{_cy_start},{_cx},{_cy_end},0,200)"
        "}"
    )

    # List slide-in: move from 20px left of natural left-margin anchor
    _lx_end   = 80 + list_sz // 2
    _lx_start = _lx_end - 20
    _ly       = video_h // 2
    _list_anim = (
        "{\\fad(100,50)"
        f"\\move({_lx_start},{_ly},{_lx_end},{_ly},0,200)"
        "}"
    )

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
        # Hook: Playfair Display Bold, white, center-screen (Alignment=5), 3px outline
        f"Style: Hook,Playfair Display Bold,{hook_sz},&H00FFFFFF,&H00FFFFFF,"
        f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,0,5,40,40,0,1\n"
        # Concept: Montserrat Bold, white, lower-third (Alignment=2), 2px outline
        f"Style: Concept,Montserrat Bold,{concept_sz},&H00FFFFFF,&H00FFFFFF,"
        f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,2,0,2,40,40,{concept_mv},1\n"
        # Stat: Montserrat Bold, brand color, center-screen (Alignment=5), 3px outline
        f"Style: Stat,Montserrat Bold,{stat_sz},{brand_ass},{brand_ass},"
        f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,0,5,40,40,0,1\n"
        # StatContext: Montserrat Bold, white, center (Alignment=5), 1px outline
        f"Style: StatContext,Montserrat Bold,{stat_ctx_sz},&H00FFFFFF,&H00FFFFFF,"
        f"&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,1,0,5,40,40,{stat_ctx_mv},1\n"
        # Mantra/Quote: Playfair Display Bold, brand color, center-screen (Alignment=5), 2px outline
        f"Style: Mantra,Playfair Display Bold,{mantra_sz},{brand_ass},{brand_ass},"
        f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,2,0,5,40,40,0,1\n"
        # List: Montserrat Bold, white, middle-left (Alignment=4), MarginL=80, 2px outline
        f"Style: List,Montserrat Bold,{list_sz},&H00FFFFFF,&H00FFFFFF,"
        f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,2,0,4,80,40,0,1\n"
        "\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )

    _style_map: dict[str, tuple[str, str]] = {
        "hook":         ("Hook",        "{\\fad(200,150)}"),
        "concept":      ("Concept",     _concept_anim),
        "stat":         ("Stat",        r"{\fad(80,80)\fscx90\fscy90\t(0,200,\fscx100\fscy100)}"),
        "stat_context": ("StatContext", r"{\fad(80,80)}"),
        "list":         ("List",        _list_anim),
        "list_item":    ("List",        _list_anim),
        "quote":        ("Mantra",      r"{\fad(300,200)}"),
        "mantra":       ("Mantra",      r"{\fad(300,200)}"),
        "marker":       ("Concept",     "{\\fad(80,80)}"),
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

        ass_style, anim = _style_map.get(style, ("Concept", "{\\fad(120,120)}"))
        # Hook and concept: emphasis words get 10% size boost + brand color
        scale_emph = ass_style in {"Hook", "Concept"}
        display = apply_emphasis(text, emph, brand_ass, with_scale=scale_emph) if emph else text
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

    # Long-form: selective moments only — NO fallback to word-by-word.
    if mode == "long":
        if caption_moments:
            return _build_long_form_ass(
                caption_moments, output_path,
                video_w=video_w, video_h=video_h, brand_color=brand_color,
            )
        # No moments → intentional silence; write empty ASS (no captions at all).
        output_path.write_text(
            "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n\n[Events]\n",
            encoding="utf-8",
        )
        print("[CAPTIONS LONG] No caption_moments — empty ASS (no captions)")
        return output_path

    play_res_x = video_w
    play_res_y = video_h

    # Font resolution: ensure font is available (pre-installed or downloaded),
    # then strip weight suffixes — bold is declared via Bold=1 flag in the Style.
    print(f"[FONT] User requested: {caption_font}")
    font_family = _ensure_font(caption_font or "Inter Bold")
    keyword_font = font_family
    context_font = font_family

    # Font sizes scaled from 1920px reference.
    keyword_sz = round(88 * play_res_y / 1920)
    context_sz = round(48 * play_res_y / 1920)

    # Colors
    keyword_color = _hex_to_ass_bgr(brand_color) if brand_color else EMPHASIS_COLOR_ASS
    context_color = "&H66FFFFFF"  # white at ~60% opacity (ASS alpha 0x66 ≈ 40% transparent)
    outline_color = "&H00000000"  # black
    back_color    = "&H00000000"  # transparent

    # Margins from bottom edge (Alignment=2 = bottom-center).
    # keyword_mv: 12% from bottom — bottom 18% of frame.
    # context_mv: stacked directly above keyword with a 15px gap.
    keyword_mv = int(play_res_y * 0.12)
    context_mv = keyword_mv + keyword_sz + 15

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
        # Keyword: current phrase — bold, brand color, 88px, 3px outline, bottom
        f"Style: Keyword,{keyword_font},{keyword_sz},{keyword_color},{keyword_color},"
        f"{outline_color},{back_color},1,0,0,0,100,100,0,0,1,3,0,2,60,60,{keyword_mv},1\n"
        # Context: previous phrase — same font, 60% white, 48px, 2px outline, above keyword
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
