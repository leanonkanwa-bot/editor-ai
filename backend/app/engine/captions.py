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

try:
    from app.engine.font_manager import get_font_path as _fm_get_path, get_font_family as _fm_get_family
    _FONT_MANAGER_AVAILABLE = True
except ImportError:
    _FONT_MANAGER_AVAILABLE = False


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
        # Hook: Playfair Display Bold, white, bottom-center (Alignment=2), 3px outline
        # Alignment=2 keeps hook below the face — safe on all talking-head videos.
        f"Style: Hook,Playfair Display Bold,{hook_sz},&H00FFFFFF,&H00FFFFFF,"
        f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,0,2,40,40,{concept_mv},1\n"
        # Concept: Montserrat Bold, white, lower-third (Alignment=2), 2px outline
        f"Style: Concept,Montserrat Bold,{concept_sz},&H00FFFFFF,&H00FFFFFF,"
        f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,2,0,2,40,40,{concept_mv},1\n"
        # Stat: Montserrat Bold, brand color, center-screen (Alignment=5), 3px outline
        # Stats/numbers remain center-screen for maximum visual impact.
        f"Style: Stat,Montserrat Bold,{stat_sz},{brand_ass},{brand_ass},"
        f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,0,5,40,40,0,1\n"
        # StatContext: Montserrat Bold, white, center (Alignment=5), 1px outline
        f"Style: StatContext,Montserrat Bold,{stat_ctx_sz},&H00FFFFFF,&H00FFFFFF,"
        f"&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,1,0,5,40,40,{stat_ctx_mv},1\n"
        # Mantra/Quote: Playfair Display Bold, brand color, bottom-center (Alignment=2), 2px outline
        # Alignment=2 keeps mantra/quote below the face — safe on all talking-head videos.
        f"Style: Mantra,Playfair Display Bold,{mantra_sz},{brand_ass},{brand_ass},"
        f"&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,2,0,2,40,40,{concept_mv},1\n"
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

        # Per-moment position override (FIX 4 — dynamic positioning).
        # The planner can set a "position" field to override the style's default alignment.
        # ASS {\an} tag uses numpad layout: 1=bottom-left, 2=bottom-center, 3=bottom-right,
        # 4=mid-left, 5=center, 6=mid-right, 7=top-left, 8=top-center, 9=top-right.
        _pos_tag_map = {
            "center_bottom": "{\\an2}",
            "bottom_center": "{\\an2}",
            "center":        "{\\an5}",
            "bottom_left":   "{\\an1}",
            "bottom_right":  "{\\an3}",
        }
        _pos = str(moment.get("position", ""))
        _pos_tag = _pos_tag_map.get(_pos, "")

        # Hook and concept: emphasis words get 10% size boost + brand color
        scale_emph = ass_style in {"Hook", "Concept"}
        display = apply_emphasis(text, emph, brand_ass, with_scale=scale_emph) if emph else text
        lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},{ass_style},,0,0,0,,{_pos_tag}{anim}{display}")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[CAPTIONS LONG] Written {len(lines) - 1} selective caption moments")
    return output_path


def _build_priestley_ass(
    words: list,
    output_path,
    video_w: int = 1920,
    video_h: int = 1080,
    video_duration: float | None = None,
    caption_moments: list[dict] | None = None,
) -> Path:
    """Daniel Priestley style ASS captions.

    Dialogue: white pill-box, gold karaoke highlight, Inter Bold 42px.
    Title cards: dark burgundy box, cream/gold text, scale animation.
    """
    output_path = Path(output_path)

    # Ensure Inter is available before building ASS referencing it
    if _FONT_MANAGER_AVAILABLE:
        _fm_get_path("Inter", 700)
        _fm_get_path("Inter", 900)

    scale = video_w / 1920.0
    font_size  = max(24, int(42  * scale))
    title_size = max(48, int(130 * scale))
    title_sub_size = max(20, int(52 * scale))
    margin_v   = int(video_h * 0.18)
    margin_lr  = int(150 * scale)

    def _p_hex_to_ass(hex_color: str, alpha: int = 0) -> str:
        h = hex_color.lstrip("#").upper()
        if len(h) != 6:
            h = "FFFFFF"
        r, g, b = h[0:2], h[2:4], h[4:6]
        return f"&H{alpha:02X}{b}{g}{r}"

    white_ass    = _p_hex_to_ass("FFFFFF", 0)
    gold_ass     = _p_hex_to_ass("FFDE4D", 0)
    black70_ass  = _p_hex_to_ass("000000", 0x4C)   # 70% opacity black box
    burgundy_ass = _p_hex_to_ass("2B080C", 0)
    cream_ass    = _p_hex_to_ass("FDFBF7", 0)

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
        # Dialogue: white text, 70% black box, BorderStyle=3 (opaque box)
        f"Style: Dialogue,Inter,{font_size},{white_ass},{gold_ass},"
        f"&H00000000,{black70_ass},-1,0,0,0,100,100,0,0,3,1,0,"
        f"2,{margin_lr},{margin_lr},{margin_v},1\n"
        # TitleCard: cream text, burgundy box, center (Alignment=5)
        f"Style: TitleCard,Inter,{title_size},{cream_ass},{gold_ass},"
        f"&H00000000,{burgundy_ass},-1,0,0,0,100,100,0,0,1,0,0,"
        f"5,60,60,60,1\n"
        "\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )

    lines = [header]

    # ── Title cards from caption_moments (hook / stat / mantra) ─────────────
    if caption_moments:
        for moment in caption_moments:
            style_type = str(moment.get("style", "concept"))
            if style_type in ("hook", "stat", "mantra"):
                text = str(moment.get("text", "")).strip().upper()
                if not text:
                    continue
                start = float(moment.get("start", 0))
                end   = float(moment.get("end", start + 2.5))
                anim = r"{\fscx100\fscy100\t(0,1500,\fscx105\fscy105)}"
                lines.append(
                    f"Dialogue: 1,{_ts(start)},{_ts(end)},TitleCard,,0,0,0,,{anim}{text}"
                )

    # ── Dialogue captions — karaoke highlight with gold active word ──────────
    PHRASE_SIZE = 4
    valid_words = [
        w for w in words
        if _strip_punct(w.text) and (video_duration is None or w.start < video_duration)
    ]

    i = 0
    while i < len(valid_words):
        phrase_words = valid_words[i: i + PHRASE_SIZE]
        if not phrase_words:
            break

        p_start = max(0.0, phrase_words[0].start - 0.050)  # 50ms early entry
        p_end   = phrase_words[-1].end

        # Hard cap at 3 seconds
        if p_end - p_start > 3.0:
            p_end = p_start + 3.0

        # Gap: disappear during silence > 250ms
        next_i = i + PHRASE_SIZE
        if next_i < len(valid_words):
            next_word_start = valid_words[next_i].start
            gap = next_word_start - phrase_words[-1].end
            if gap > 0.25:
                pass  # natural gap — keep p_end as is
            else:
                p_end = min(p_end, next_word_start - 0.02)

        if video_duration is not None and p_end > video_duration:
            p_end = video_duration
        if p_end <= p_start:
            i += PHRASE_SIZE
            continue

        # Build karaoke text: {\kf<cs>}word  (kf = fill from left)
        kara_parts = []
        for wi, word in enumerate(phrase_words):
            dur_cs = max(1, round((word.end - word.start) * 100))
            word_text = _strip_punct(word.text)
            if not word_text:
                continue
            display = word_text.capitalize() if wi == 0 else word_text.lower()
            kara_parts.append(f"{{\\kf{dur_cs}}}{display}")

        if kara_parts:
            kara_line = "".join(kara_parts)
            lines.append(
                f"Dialogue: 0,{_ts(p_start)},{_ts(p_end)},Dialogue,,0,0,0,,{kara_line}"
            )

        i += PHRASE_SIZE

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[CAPTIONS] Priestley: {len(lines) - 1} lines written")
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
    caption_style: str = "impact",
) -> Path:
    """Render captions to an ASS subtitle file.

    Short-form (mode='short'): two-level Jordan Belfort style — Keyword + Context.
    Long-form  (mode='long'):  strategic moments only, 4 styles — Hook/Concept/Stat/ListItem.
                               Requires caption_moments list from the planner.
                               Falls back to short-form if caption_moments is empty.
    """
    output_path = Path(output_path)

    # Priestley style: dedicated renderer — completely separate from other modes.
    if caption_style == "priestley":
        return _build_priestley_ass(
            words, output_path, video_w=video_w, video_h=video_h,
            video_duration=video_duration, caption_moments=caption_moments,
        )

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

    # Font resolution: use font_manager (download from Google Fonts on demand)
    # with fallback to the legacy _ensure_font() if font_manager is unavailable.
    _requested_font = caption_font or "Inter Bold"
    print(f"[FONT] User requested: {_requested_font}")
    if _FONT_MANAGER_AVAILABLE:
        _fm_get_path(_requested_font)          # ensure downloaded + fc-cache
        font_family = _fm_get_family(_requested_font)
    else:
        font_family = _ensure_font(_requested_font)
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
    # 10% from bottom keeps captions in the universal safe zone below the face
    # on any talking-head video (portrait or landscape).
    keyword_mv = int(play_res_y * 0.10)
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

    word_list = [w for w in words if _strip_punct(w.text)]

    # ── Caption style dispatch ───────────────────────────────────────────────
    _kw_anim  = r"{\fad(80,0)\t(\fscx97\fscy97,\fscx100\fscy100)}"
    _ctx_anim = r"{\fad(80,0)}"

    if caption_style in ("impact", "kinetic"):
        # One word at a time — very punchy
        groups = _group_words(word_list, max_words=1, gap_s=0.05)
        _word_anim = (
            r"{\fad(60,0)\t(\fscx95\fscy95,\fscx105\fscy105)\t(\fscx105\fscy105,\fscx100\fscy100)}"
            if caption_style == "kinetic"
            else r"{\fad(50,0)\t(\fscx95\fscy95,\fscx100\fscy100)}"
        )
        lines = [header]
        for gi, group in enumerate(groups):
            clean = [_strip_punct(w.text) for w in group]
            clean = [w for w in clean if w]
            if not clean:
                continue
            start = max(0.0, group[0].start + WHISPER_TIMESTAMP_CORRECTION)
            end_base = group[-1].end + WHISPER_TIMESTAMP_CORRECTION
            if video_duration is not None and start > video_duration:
                break
            next_start = None
            for ngi in range(gi + 1, len(groups)):
                ng = groups[ngi]
                nc = [_strip_punct(w.text) for w in ng]
                if any(nc):
                    next_start = ng[0].start + WHISPER_TIMESTAMP_CORRECTION
                    break
            end = (next_start - 0.01) if next_start is not None else (end_base + 0.08)
            if video_duration is not None and end > video_duration:
                end = video_duration
            word_text = clean[0].upper()
            lines.append(f"Dialogue: 1,{_ts(start)},{_ts(end)},Keyword,,0,0,0,,{_word_anim}{word_text}")
        output_path.write_text("\n".join(lines), encoding="utf-8")
        return output_path

    if caption_style == "karaoke":
        # Phrase groups with per-word progressive highlight using ASS karaoke tags
        groups = _group_words(word_list, max_words=6, gap_s=0.35)
        lines = [header]
        for gi, group in enumerate(groups):
            clean_words = [(w, _strip_punct(w.text)) for w in group if _strip_punct(w.text)]
            if not clean_words:
                continue
            start = max(0.0, clean_words[0][0].start + WHISPER_TIMESTAMP_CORRECTION)
            end_base = clean_words[-1][0].end + WHISPER_TIMESTAMP_CORRECTION
            if video_duration is not None and start > video_duration:
                break
            next_start = None
            for ngi in range(gi + 1, len(groups)):
                ng = groups[ngi]
                nc = [_strip_punct(w.text) for w in ng]
                if any(nc):
                    next_start = ng[0].start + WHISPER_TIMESTAMP_CORRECTION
                    break
            end = (next_start - 0.01) if next_start is not None else (end_base + 0.15)
            if video_duration is not None and end > video_duration:
                end = video_duration
            # Build karaoke text: {\k<centiseconds>}word for each word
            kara_parts = []
            for wi, (w_obj, w_text) in enumerate(clean_words):
                w_start = max(0.0, w_obj.start + WHISPER_TIMESTAMP_CORRECTION)
                w_end = w_obj.end + WHISPER_TIMESTAMP_CORRECTION
                dur_cs = max(1, round((w_end - w_start) * 100))
                kara_parts.append(f"{{\\k{dur_cs}}}{w_text.capitalize() if wi == 0 else w_text.lower()}")
            kara_text = r"{\fad(80,0)}" + "".join(kara_parts)
            lines.append(f"Dialogue: 1,{_ts(start)},{_ts(end)},Keyword,,0,0,0,,{kara_text}")
        output_path.write_text("\n".join(lines), encoding="utf-8")
        return output_path

    # ── Default: twolevel (Jordan Belfort — Keyword + Context) ──────────────
    groups = _group_words(word_list, max_words=4, gap_s=0.25)
    lines = [header]
    prev_text = ""

    for gi, group in enumerate(groups):
        clean = [_strip_punct(w.text) for w in group]
        clean = [w for w in clean if w]
        if not clean:
            continue

        start = max(0.0, group[0].start + WHISPER_TIMESTAMP_CORRECTION)
        end_base = group[-1].end + WHISPER_TIMESTAMP_CORRECTION

        if video_duration is not None and start > video_duration:
            break

        next_start: float | None = None
        for ngi in range(gi + 1, len(groups)):
            ng = groups[ngi]
            nc = [_strip_punct(w.text) for w in ng]
            if any(nc):
                next_start = ng[0].start + WHISPER_TIMESTAMP_CORRECTION
                break

        end = (next_start - 0.01) if next_start is not None else (end_base + 0.1)
        if video_duration is not None and end > video_duration:
            end = video_duration

        phrase_text = " ".join(
            w.capitalize() if i == 0 else w.lower()
            for i, w in enumerate(clean)
        )

        lines.append(f"Dialogue: 1,{_ts(start)},{_ts(end)},Keyword,,0,0,0,,{_kw_anim}{phrase_text}")
        lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Context,,0,0,0,,{_ctx_anim}{prev_text}")
        prev_text = phrase_text

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path
