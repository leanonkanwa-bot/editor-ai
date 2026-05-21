"""
Motion graphics renderer.

Each function renders ONE graphic to a PNG (RGBA, transparent background)
that the FFmpeg filter chain overlays onto the video. Animation (slide-in,
fade-in) is done by the FFmpeg overlay expressions, NOT here — that keeps
each graphic file small (one PNG) and keeps the renderer fast.

Today's library:
  - lower_third_title  — section title that slides in from the left
  - stat_circle        — donut chart with a big number in the middle
  - checklist          — stacked rounded pill buttons with X / ✓ icons

Style alignment is intentional: rounded corners, sans-serif Bold,
high-contrast accent colours pulled from the app's palette so the
graphics feel like one product. Match the polished references the user
shared (Hormozi/Codie/MrBeast tier).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageDraw, ImageFont


AESTHETIC_COLORS: dict[str, dict] = {
    "dark-pro":    {"accent": "#0A84FF", "bg": (10, 10, 10, 255),   "text": (255, 255, 255, 255), "secondary": (142, 142, 147, 255)},
    "high-energy": {"accent": "#FF3B30", "bg": (10, 10, 10, 255),   "text": (255, 255, 255, 255), "secondary": (255, 229,   0, 255)},
    "faith-gold":  {"accent": "#D4AF37", "bg": (27, 34, 56,  255),  "text": (255, 248, 231, 255), "secondary": (180, 160, 100, 255)},
}


# Brand palette — kept in sync with the front-end picker.
PALETTE = {
    "blue":      (10, 132, 255, 255),    # Electric Blue  #0A84FF
    "red":       (255, 59, 48, 255),     # Clean Red      #FF3B30
    "yellow":    (255, 229, 0, 255),     # Electric Yellow #FFE500
    "green":     (52, 199, 89, 255),     # System Green
    "orange":    (255, 107, 0, 255),     # Orange Flash   #FF6B00
    "white":     (255, 255, 255, 255),
    "black":     (10, 10, 10, 255),
    "panel":     (26, 26, 26, 230),      # ~90% opaque dark grey
    "panel_2":   (16, 16, 16, 235),
}


def _normalize_font_name(name: str) -> str:
    """Map a human-friendly font name ('Poppins Bold') to the file basename
    bundled in /usr/local/share/fonts/leanlead ('Poppins-Bold')."""
    return (name or "Poppins Bold").strip().replace(" ", "-")


def _load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Resolve a font we baked into the Docker image. Falls back to default
    if a system box has none of these — prevents render hard-fails."""
    candidates = [
        f"/usr/local/share/fonts/leanlead/{name}.ttf",
        f"/usr/share/fonts/truetype/{name.lower()}/{name}.ttf",
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _hex_to_rgba(hex6: str | None, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    if not hex6:
        return fallback
    h = hex6.lstrip("#")
    if len(h) != 6:
        return fallback
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
    except ValueError:
        return fallback


def _rounded_rect(
    draw: ImageDraw.ImageDraw,
    bbox: tuple[int, int, int, int],
    radius: int,
    fill: tuple[int, int, int, int] | None = None,
    outline: tuple[int, int, int, int] | None = None,
    outline_width: int = 0,
) -> None:
    draw.rounded_rectangle(
        bbox, radius=radius, fill=fill,
        outline=outline, width=outline_width,
    )


def _text_size(font: ImageFont.FreeTypeFont, text: str) -> tuple[int, int]:
    bbox = font.getbbox(text)
    return (bbox[2] - bbox[0], bbox[3] - bbox[1])


def _wrap_text_to_width(text: str, font: ImageFont.FreeTypeFont, max_px: int) -> list[str]:
    """Word-wrap `text` so each line fits within max_px. Single words wider
    than max_px are kept on their own line rather than broken mid-word."""
    words = (text or "").split()
    if not words:
        return [""]
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if _text_size(font, trial)[0] <= max_px or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


# ---------------------------------------------------------------------------
# Graphic 1 — Lower Third Title
# ---------------------------------------------------------------------------

def render_lower_third(
    title: str,
    accent_word: str | None,
    out_path: Path,
    *,
    width: int = 900,
    max_text_width: int | None = None,
    accent_hex: str = "#0A84FF",
    font_title: str = "Poppins-Bold",
    font_subtitle: str = "Poppins-SemiBold",
) -> Path:
    """Title with optional accent (coloured) second line.
    Text is word-wrapped to max_text_width so it never overflows the frame.
    Slide-in animation is handled by ffmpeg; this PNG is the final state."""
    if max_text_width is None:
        max_text_width = width

    # Auto-size: reduce from 92px until every word-wrapped line fits within
    # max_text_width. Without this, long single words clip at the PNG edge.
    font_size = 92
    title_font = _load_font(font_title, font_size)
    while font_size > 28:
        test_lines = _wrap_text_to_width(title, title_font, max_text_width)
        if all(_text_size(title_font, ln)[0] <= max_text_width for ln in test_lines):
            break
        font_size = max(28, int(font_size * 0.88))
        title_font = _load_font(font_title, font_size)

    accent_color = _hex_to_rgba(accent_hex, PALETTE["blue"])
    title_lines = _wrap_text_to_width(title, title_font, max_text_width)
    accent_lines = (
        _wrap_text_to_width(accent_word, title_font, max_text_width)
        if accent_word else []
    )
    all_lines = title_lines + accent_lines

    ascent, descent = title_font.getmetrics()
    line_h = ascent + descent
    line_gap = 10
    pad = 8

    height = len(all_lines) * line_h + max(0, len(all_lines) - 1) * line_gap + pad * 2
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    y = pad
    for ln in title_lines:
        draw.text((0, y), ln, font=title_font, fill=PALETTE["white"])
        y += line_h + line_gap
    for ln in accent_lines:
        draw.text((0, y), ln, font=title_font, fill=accent_color)
        y += line_h + line_gap

    img.save(out_path, "PNG")
    return out_path


# ---------------------------------------------------------------------------
# Graphic 2 — Stat Circle (donut + big number)
# ---------------------------------------------------------------------------

def render_stat_circle(
    percent: int,
    label: str,
    sub_label: str | None,
    out_path: Path,
    *,
    accent_hex: str = "#0A84FF",
    size: int = 700,
    stroke: int = 60,
    font_number: str = "Poppins-ExtraBold",
    font_label: str = "Poppins-SemiBold",
) -> Path:
    """Donut chart filled to `percent`, big '<N>%' in the centre, smaller
    label underneath. Matches the '80% of your time' reference."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    accent = _hex_to_rgba(accent_hex, PALETTE["blue"])
    percent = max(0, min(100, int(percent)))

    bbox = (stroke // 2, stroke // 2, size - stroke // 2, size - stroke // 2)
    # Track + filled arc — start at top (-90°), draw clockwise.
    end_angle = -90 + (360 * percent / 100)
    draw.arc(bbox, start=-90, end=270, fill=(40, 40, 40, 255), width=stroke)
    draw.arc(bbox, start=-90, end=end_angle, fill=accent, width=stroke)

    # Centre text — number then sub-label.
    number_font = _load_font(font_number, int(size * 0.22))
    label_font = _load_font(font_label, int(size * 0.07))

    number_text = f"{percent}%"
    nw, nh = _text_size(number_font, number_text)
    draw.text(
        ((size - nw) / 2, (size - nh) / 2 - (size * 0.02)),
        number_text, font=number_font, fill=PALETTE["white"],
    )

    if label:
        lw, lh = _text_size(label_font, label)
        draw.text(
            ((size - lw) / 2, (size + nh) / 2 + 4),
            label, font=label_font, fill=PALETTE["white"],
        )

    img.save(out_path, "PNG")
    return out_path


# ---------------------------------------------------------------------------
# Graphic 3 — Checklist Reveal (X / ✓ rounded pill buttons)
# ---------------------------------------------------------------------------

@dataclass
class ChecklistItem:
    text: str
    ok: bool   # True = green ✓, False = red ✗


def render_checklist(
    items: Sequence[ChecklistItem],
    out_path: Path,
    *,
    width: int = 1200,
    pill_height: int = 160,
    gap: int = 28,
    font_name: str = "Poppins-Bold",
) -> Path:
    """Stacked rounded pill buttons with red X or green ✓ on the left.
    Matches the 'Not a Demo / Not Theory / Real Automations' reference."""
    n = len(items)
    if n == 0:
        # Render a 1×1 transparent pixel so callers always get a valid file.
        Image.new("RGBA", (1, 1), (0, 0, 0, 0)).save(out_path, "PNG")
        return out_path

    height = n * pill_height + (n - 1) * gap
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    radius = pill_height // 2
    icon_size = pill_height - 28
    icon_x = 16
    text_x_base = icon_x + icon_size + 36
    text_area_w = width - text_x_base - 20  # 20px right pad

    # Auto-size: reduce from 84px until the longest item text fits inside pill.
    font_size_t = 84
    text_font = _load_font(font_name, font_size_t)
    if items:
        longest = max((it.text for it in items), key=len)
        while font_size_t > 24 and _text_size(text_font, longest)[0] > text_area_w:
            font_size_t = max(24, int(font_size_t * 0.88))
            text_font = _load_font(font_name, font_size_t)

    for i, item in enumerate(items):
        y0 = i * (pill_height + gap)
        y1 = y0 + pill_height
        outline = PALETTE["green"] if item.ok else PALETTE["red"]

        _rounded_rect(
            draw,
            (0, y0, width, y1),
            radius=radius,
            fill=PALETTE["panel"],
            outline=outline,
            outline_width=4,
        )

        # Icon — filled circle in red/green with a white X or ✓.
        icon_y = y0 + (pill_height - icon_size) // 2
        draw.ellipse(
            (icon_x, icon_y, icon_x + icon_size, icon_y + icon_size),
            fill=outline,
        )
        cx = icon_x + icon_size / 2
        cy = icon_y + icon_size / 2
        if item.ok:
            # Checkmark — three points, drawn as two thick lines.
            arm = icon_size * 0.25
            stem = icon_size * 0.4
            draw.line(
                [(cx - arm, cy + 0), (cx - arm * 0.2, cy + arm * 0.7), (cx + stem * 0.7, cy - stem * 0.5)],
                fill=PALETTE["white"], width=10, joint="curve",
            )
        else:
            arm = icon_size * 0.28
            draw.line([(cx - arm, cy - arm), (cx + arm, cy + arm)], fill=PALETTE["white"], width=10)
            draw.line([(cx + arm, cy - arm), (cx - arm, cy + arm)], fill=PALETTE["white"], width=10)

        # Label text — left-aligned with consistent padding after the icon.
        text_x = icon_x + icon_size + 36
        tw, th = _text_size(text_font, item.text)
        draw.text(
            (text_x, y0 + (pill_height - th) / 2 - 6),
            item.text, font=text_font, fill=PALETTE["white"],
        )

    img.save(out_path, "PNG")
    return out_path


# ---------------------------------------------------------------------------
# Graphic 4 — Text Overlay (the universal primitive)
# ---------------------------------------------------------------------------
# Free-form text the agent can place anywhere with full styling control.
# This is the workhorse — anything the templates don't cover, the agent
# composes from text_overlays. Multi-line, custom font/size/color/position,
# choice of slide-in direction.

def render_text_overlay(
    text: str,
    out_path: Path,
    *,
    font_name: str = "Poppins-Bold",
    font_size: int = 80,
    color_hex: str | None = None,
    align: str = "left",
    line_spacing: int = 12,
    max_width_px: int | None = None,
) -> Path:
    """Render `text` (\\n separated for multi-line) onto a transparent PNG
    sized to fit the text. Caller decides timing + on-screen position."""
    color = _hex_to_rgba(color_hex, PALETTE["white"])
    font = _load_font(font_name, font_size)

    raw_lines = [ln for ln in text.split("\n") if ln is not None]

    # Soft wrap each input line if max_width_px is given.
    lines: list[str] = []
    if max_width_px and max_width_px > 0:
        for ln in raw_lines:
            words = ln.split()
            cur = ""
            for w in words:
                trial = (cur + " " + w).strip()
                tw, _ = _text_size(font, trial)
                if tw <= max_width_px or not cur:
                    cur = trial
                else:
                    lines.append(cur)
                    cur = w
            if cur:
                lines.append(cur)
    else:
        lines = raw_lines or [""]

    # Use font metrics for line height — getbbox is glyph-tight and clips
    # descenders ("y", "g") on multi-line layouts.
    ascent, descent = font.getmetrics()
    line_h = ascent + descent

    widths = [_text_size(font, ln)[0] for ln in lines]
    width = max(widths) if widths else 1
    height = line_h * len(lines) + line_spacing * max(0, len(lines) - 1)
    # A few px of margin so anti-aliased edges aren't clipped.
    pad = 4

    img = Image.new("RGBA", (width + pad * 2, height + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    y = pad
    for ln, lw in zip(lines, widths):
        if align == "center":
            x = pad + (width - lw) / 2
        elif align == "right":
            x = pad + (width - lw)
        else:
            x = pad
        draw.text((x, y), ln, font=font, fill=color)
        y += line_h + line_spacing

    img.save(out_path, "PNG")
    return out_path


# ---------------------------------------------------------------------------
# Graphic 5 — Quote Card (full-frame inspirational quote)
# ---------------------------------------------------------------------------

def render_quote_card(
    quote: str,
    speaker: str | None,
    out_path: Path,
    *,
    target_w: int,
    target_h: int,
    accent_hex: str = "#0A84FF",
    bg_rgba: tuple[int, int, int, int] = (10, 10, 10, 255),
    text_rgba: tuple[int, int, int, int] = (255, 255, 255, 255),
) -> Path:
    img = Image.new("RGBA", (target_w, target_h), bg_rgba)
    draw = ImageDraw.Draw(img)
    accent = _hex_to_rgba(accent_hex, PALETTE["blue"])

    quote_font_size = int(target_h * 0.055)
    speaker_font_size = int(target_h * 0.035)
    deco_font_size = int(target_h * 0.15)

    deco_font = _load_font("Poppins-Bold", deco_font_size)
    quote_font = _load_font("Poppins-SemiBold", quote_font_size)
    speaker_font = _load_font("Poppins-Bold", speaker_font_size)

    pad = int(target_w * 0.08)
    deco_x, deco_y = pad, int(target_h * 0.08)
    draw.text((deco_x, deco_y), "“", font=deco_font, fill=accent)

    max_quote_w = int(target_w * 0.84)
    lines = _wrap_text_to_width(quote or "", quote_font, max_quote_w)
    ascent, descent = quote_font.getmetrics()
    line_h = ascent + descent
    block_h = line_h * len(lines) + 8 * max(0, len(lines) - 1)
    block_y = (target_h - block_h) // 2

    for ln in lines:
        lw, _ = _text_size(quote_font, ln)
        draw.text(((target_w - lw) // 2, block_y), ln, font=quote_font, fill=text_rgba)
        block_y += line_h + 8

    if speaker:
        sw, sh = _text_size(speaker_font, speaker)
        draw.text(
            ((target_w - sw) // 2, target_h - int(target_h * 0.12)),
            speaker, font=speaker_font, fill=accent,
        )

    img.save(out_path, "PNG")
    return out_path


# ---------------------------------------------------------------------------
# Graphic 6 — Split Screen (wrong/right, before/after comparison)
# ---------------------------------------------------------------------------

def render_split_screen(
    left_text: str,
    right_text: str,
    out_path: Path,
    *,
    target_w: int,
    target_h: int,
    left_label: str = "WRONG",
    right_label: str = "RIGHT",
    accent_hex: str = "#0A84FF",
    bg_rgba: tuple[int, int, int, int] = (10, 10, 10, 255),
    text_rgba: tuple[int, int, int, int] = (255, 255, 255, 255),
) -> Path:
    img = Image.new("RGBA", (target_w, target_h), bg_rgba)
    draw = ImageDraw.Draw(img)

    half = target_w // 2
    label_font_size = int(target_h * 0.08)
    body_font_size = int(target_h * 0.06)
    label_font = _load_font("Poppins-Bold", label_font_size)
    body_font = _load_font("Poppins-Bold", body_font_size)

    red_tint = Image.new("RGBA", (half, target_h), (200, 30, 30, 50))
    img.paste(Image.alpha_composite(img.crop((0, 0, half, target_h)), red_tint), (0, 0))

    green_tint = Image.new("RGBA", (half, target_h), (30, 180, 60, 50))
    img.paste(Image.alpha_composite(img.crop((half, 0, target_w, target_h)), green_tint), (half, 0))

    draw = ImageDraw.Draw(img)

    lw, _ = _text_size(label_font, left_label)
    draw.text(((half - lw) // 2, int(target_h * 0.08)), left_label, font=label_font, fill=(255, 80, 80, 255))

    rw, _ = _text_size(label_font, right_label)
    draw.text((half + (half - rw) // 2, int(target_h * 0.08)), right_label, font=label_font, fill=(60, 210, 80, 255))

    max_half_w = int(half * 0.8)
    for side_x_base, text in [(0, left_text), (half, right_text)]:
        lines = _wrap_text_to_width(text or "", body_font, max_half_w)
        ascent, descent = body_font.getmetrics()
        lh = ascent + descent
        block_h = lh * len(lines) + 8 * max(0, len(lines) - 1)
        by = (target_h - block_h) // 2
        for ln in lines:
            lw2, _ = _text_size(body_font, ln)
            draw.text((side_x_base + (half - lw2) // 2, by), ln, font=body_font, fill=text_rgba)
            by += lh + 8

    divider_x = target_w // 2
    draw.line([(divider_x, 0), (divider_x, target_h)], fill=(255, 255, 255, 180), width=3)

    vs_font = _load_font("Poppins-Bold", int(target_h * 0.045))
    vs_text = "VS"
    vsw, vsh = _text_size(vs_font, vs_text)
    pill_pad = 16
    pill_x = divider_x - vsw // 2 - pill_pad
    pill_y = target_h // 2 - vsh // 2 - pill_pad
    _rounded_rect(draw, (pill_x, pill_y, pill_x + vsw + pill_pad * 2, pill_y + vsh + pill_pad * 2),
                  radius=20, fill=(40, 40, 40, 240))
    draw.text((divider_x - vsw // 2, target_h // 2 - vsh // 2), vs_text, font=vs_font, fill=(255, 255, 255, 255))

    img.save(out_path, "PNG")
    return out_path


# ---------------------------------------------------------------------------
# Graphic 7 — Timeline (horizontal with dots and labels)
# ---------------------------------------------------------------------------

def render_timeline(
    events: list[dict],
    out_path: Path,
    *,
    target_w: int,
    target_h: int,
    accent_hex: str = "#0A84FF",
    text_rgba: tuple[int, int, int, int] = (255, 255, 255, 255),
) -> Path:
    img = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    accent = _hex_to_rgba(accent_hex, PALETTE["blue"])

    if not events:
        img.save(out_path, "PNG")
        return out_path

    label_font = _load_font("Poppins-Bold", int(target_h * 0.1))
    year_font = _load_font("Poppins-SemiBold", int(target_h * 0.09))

    pad_x = int(target_w * 0.08)
    line_y = target_h // 2
    usable_w = target_w - pad_x * 2
    n = len(events)
    dot_r = int(target_h * 0.06)
    active_r = int(target_h * 0.09)

    draw.line([(pad_x, line_y), (target_w - pad_x, line_y)], fill=(100, 100, 100, 200), width=4)

    for idx, ev in enumerate(events):
        x = pad_x + int(usable_w * idx / max(1, n - 1)) if n > 1 else target_w // 2
        is_last = idx == n - 1
        r = active_r if is_last else dot_r

        if is_last:
            for g in range(3, 0, -1):
                glow_r = r + g * 6
                glow_a = 40 - g * 10
                draw.ellipse((x - glow_r, line_y - glow_r, x + glow_r, line_y + glow_r),
                              fill=(*accent[:3], glow_a))
            draw.ellipse((x - r, line_y - r, x + r, line_y + r), fill=accent)
        else:
            draw.ellipse((x - r, line_y - r, x + r, line_y + r), fill=(255, 255, 255, 220))

        year_text = str(ev.get("year", ""))
        if year_text:
            yw, yh = _text_size(year_font, year_text)
            draw.text((x - yw // 2, line_y - r - yh - 8), year_text, font=year_font, fill=text_rgba)

        label_text = str(ev.get("label", ""))
        if label_text:
            lw2, lh = _text_size(label_font, label_text)
            draw.text((x - lw2 // 2, line_y + r + 8), label_text, font=label_font, fill=text_rgba)

    img.save(out_path, "PNG")
    return out_path


# ---------------------------------------------------------------------------
# Graphic 8 — Versus (two-card head-to-head)
# ---------------------------------------------------------------------------

def render_versus(
    left_name: str,
    right_name: str,
    out_path: Path,
    *,
    target_w: int,
    target_h: int,
    accent_hex: str = "#0A84FF",
    bg_rgba: tuple[int, int, int, int] = (10, 10, 10, 255),
    text_rgba: tuple[int, int, int, int] = (255, 255, 255, 255),
    left_icon: str = "",
    right_icon: str = "",
) -> Path:
    img = Image.new("RGBA", (target_w, target_h), bg_rgba)
    draw = ImageDraw.Draw(img)
    accent = _hex_to_rgba(accent_hex, PALETTE["blue"])

    card_w = int(target_w * 0.42)
    card_h = int(target_h * 0.55)
    card_y = (target_h - card_h) // 2
    left_x = int(target_w * 0.03)
    right_x = target_w - left_x - card_w
    radius = int(card_h * 0.06)

    _rounded_rect(draw, (left_x, card_y, left_x + card_w, card_y + card_h),
                  radius=radius, fill=(30, 30, 35, 240), outline=(80, 80, 90, 200), outline_width=2)
    _rounded_rect(draw, (right_x, card_y, right_x + card_w, card_y + card_h),
                  radius=radius, fill=(35, 35, 40, 240), outline=accent, outline_width=3)

    name_font = _load_font("Poppins-Bold", int(target_h * 0.065))
    for cx, name in [(left_x + card_w // 2, left_name), (right_x + card_w // 2, right_name)]:
        nw, nh = _text_size(name_font, name)
        draw.text((cx - nw // 2, card_y + card_h // 2 - nh // 2), name, font=name_font, fill=text_rgba)

    vs_font = _load_font("Poppins-Bold", int(target_h * 0.07))
    vs_cx = target_w // 2
    vs_text = "VS"
    vsw, vsh = _text_size(vs_font, vs_text)
    for off in [(2, 2), (-2, -2), (2, -2), (-2, 2)]:
        draw.text((vs_cx - vsw // 2 + off[0], target_h // 2 - vsh // 2 + off[1]),
                  vs_text, font=vs_font, fill=(*accent[:3], 80))
    draw.text((vs_cx - vsw // 2, target_h // 2 - vsh // 2), vs_text, font=vs_font, fill=accent)

    img.save(out_path, "PNG")
    return out_path


# ---------------------------------------------------------------------------
# Graphic 9 — Notification Banner (iPhone-style)
# ---------------------------------------------------------------------------

def render_notification(
    title: str,
    body: str,
    app_name: str,
    out_path: Path,
    *,
    target_w: int,
    target_h: int,
    accent_hex: str = "#0A84FF",
) -> Path:
    banner_w = int(target_w * 0.9)
    banner_h = int(target_h * 0.15)
    img = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    accent = _hex_to_rgba(accent_hex, PALETTE["blue"])

    bx = (target_w - banner_w) // 2
    by = 0
    _rounded_rect(draw, (bx, by, bx + banner_w, by + banner_h),
                  radius=int(banner_h * 0.15), fill=(28, 28, 30, 240))

    icon_r = int(banner_h * 0.28)
    icon_cx = bx + int(banner_h * 0.38)
    icon_cy = by + banner_h // 2
    draw.ellipse((icon_cx - icon_r, icon_cy - icon_r, icon_cx + icon_r, icon_cy + icon_r), fill=accent)
    icon_font = _load_font("Poppins-Bold", icon_r)
    letter = (app_name or "A")[:1].upper()
    lw, lh = _text_size(icon_font, letter)
    draw.text((icon_cx - lw // 2, icon_cy - lh // 2), letter, font=icon_font, fill=(255, 255, 255, 255))

    text_x = icon_cx + icon_r + 16
    app_font = _load_font("Poppins-Bold", int(banner_h * 0.18))
    title_font = _load_font("Poppins-Bold", int(banner_h * 0.2))
    body_font = _load_font("Poppins-SemiBold", int(banner_h * 0.17))

    aw, ah = _text_size(app_font, app_name or "")
    draw.text((text_x, by + int(banner_h * 0.1)), app_name or "", font=app_font, fill=(160, 160, 165, 255))
    draw.text((text_x, by + int(banner_h * 0.28)), title or "", font=title_font, fill=(255, 255, 255, 255))
    draw.text((text_x, by + int(banner_h * 0.55)), body or "", font=body_font, fill=(190, 190, 195, 255))

    img.save(out_path, "PNG")
    return out_path


# ---------------------------------------------------------------------------
# Graphic 10 — Typography Broll (big word + orbiting supporting words)
# ---------------------------------------------------------------------------

def render_typography_broll(
    word: str,
    supporting_words: list[str],
    out_path: Path,
    *,
    target_w: int,
    target_h: int,
    accent_hex: str = "#0A84FF",
    bg_rgba: tuple[int, int, int, int] = (10, 10, 10, 255),
) -> Path:
    img = Image.new("RGBA", (target_w, target_h), bg_rgba)
    draw = ImageDraw.Draw(img)
    accent = _hex_to_rgba(accent_hex, PALETTE["blue"])

    # Cap main word to 28% of frame width (not height). Long words like
    # CHARACTERISTICS were bleeding off-screen when sized by height.
    max_word_w = target_w - 80  # 40px padding each side
    main_font_size = int(target_w * 0.28)
    main_font = _load_font("Poppins-ExtraBold", main_font_size)
    while main_font_size > 40:
        mw, _ = _text_size(main_font, word or "")
        if mw <= max_word_w:
            break
        main_font_size = max(40, int(main_font_size * 0.85))
        main_font = _load_font("Poppins-ExtraBold", main_font_size)
    mw, mh = _text_size(main_font, word or "")
    draw.text(((target_w - mw) // 2, (target_h - mh) // 2), word or "", font=main_font, fill=accent)

    sup_font = _load_font("Poppins-SemiBold", int(target_h * 0.04))
    # Positions that stay clear of the center (where the main word is) and
    # avoid the face area (typically center or lower-center of frame).
    # Only corners and top/bottom zones are used — max 3 orbit words.
    positions = [
        (0.05, 0.08), (0.72, 0.07), (0.05, 0.78),
        (0.72, 0.80), (0.40, 0.05), (0.40, 0.88),
    ]
    rendered_orbit = 0
    for idx, sw_text in enumerate(supporting_words or []):
        if rendered_orbit >= 3 or idx >= len(positions):
            break
        px_pct, py_pct = positions[idx]
        sw_w, sw_h = _text_size(sup_font, sw_text)
        # Skip if this orbit word would land inside the main word's bounding box
        ox = int(target_w * px_pct)
        oy = int(target_h * py_pct)
        main_x0 = (target_w - mw) // 2
        main_y0 = (target_h - mh) // 2
        if (main_x0 - 40 <= ox <= main_x0 + mw + 40 and
                main_y0 - 20 <= oy <= main_y0 + mh + 20):
            continue
        draw.text((ox, oy), sw_text, font=sup_font, fill=(180, 180, 180, 200))
        rendered_orbit += 1

    img.save(out_path, "PNG")
    return out_path


# ---------------------------------------------------------------------------
# Graphic 11 — Money Counter (large formatted number display)
# ---------------------------------------------------------------------------

def render_money_counter(
    amount: float | int,
    currency_symbol: str,
    out_path: Path,
    *,
    target_w: int,
    target_h: int,
    positive: bool = True,
) -> Path:
    img = Image.new("RGBA", (target_w, target_h), (10, 10, 10, 255))
    draw = ImageDraw.Draw(img)
    color = (48, 209, 88, 255) if positive else (255, 59, 48, 255)

    try:
        formatted = f"{int(amount):,}"
    except (TypeError, ValueError):
        formatted = str(amount)

    num_font_size = int(target_h * 0.18)
    num_font = _load_font("Poppins-Bold", num_font_size)
    cur_font = _load_font("Poppins-Bold", int(num_font_size * 0.65))

    nw, nh = _text_size(num_font, formatted)
    cw, ch = _text_size(cur_font, currency_symbol or "")
    total_w = cw + 8 + nw
    start_x = (target_w - total_w) // 2
    baseline_y = (target_h - nh) // 2

    draw.text((start_x, baseline_y + nh - ch), currency_symbol or "", font=cur_font, fill=color)
    draw.text((start_x + cw + 8, baseline_y), formatted, font=num_font, fill=color)

    img.save(out_path, "PNG")
    return out_path


# ---------------------------------------------------------------------------
# Graphic 12 — Giant Text (Style 1: huge number + colored subtitle overlay)
# ---------------------------------------------------------------------------

def render_giant_text(
    number: str,
    subtitle: str,
    subtitle_color_hex: str,
    out_path: Path,
    *,
    target_w: int,
    target_h: int,
    face_top_pct: float = 15.0,
    face_bottom_pct: float = 65.0,
) -> Path:
    """Huge white number/stat (e.g. '65%') with a colored subtitle below.
    Sized to fill whichever safe zone (above/below face) is taller.
    Background is transparent — overlaid on the live video."""
    space_above_px = int(target_h * face_top_pct / 100)
    space_below_px = int(target_h * (1.0 - face_bottom_pct / 100))
    avail_h = max(space_above_px, space_below_px, 100)
    avail_w = target_w

    # Auto-size number font to fill ~70% of available height.
    num_size = max(40, int(avail_h * 0.65))
    num_font = _load_font("Poppins-ExtraBold", num_size)
    # If text is too wide, shrink.
    while num_size > 40 and _text_size(num_font, number)[0] > int(avail_w * 0.90):
        num_size = max(40, int(num_size * 0.88))
        num_font = _load_font("Poppins-ExtraBold", num_size)

    sub_color = _hex_to_rgba(subtitle_color_hex, PALETTE["red"])
    sub_size = max(20, int(num_size * 0.28))
    sub_font = _load_font("Poppins-Bold", sub_size)

    nw, nh = _text_size(num_font, number)
    sw, sh = (_text_size(sub_font, subtitle) if subtitle else (0, 0))
    gap = int(num_size * 0.08)
    total_h = nh + (gap + sh if subtitle else 0) + 8
    total_w = max(nw, sw) + 16

    img = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # White number centered.
    draw.text(((total_w - nw) // 2, 4), number, font=num_font, fill=PALETTE["white"])
    if subtitle:
        sub_lines = _wrap_text_to_width(subtitle, sub_font, total_w)
        y = 4 + nh + gap
        for ln in sub_lines:
            lw, lh = _text_size(sub_font, ln)
            draw.text(((total_w - lw) // 2, y), ln, font=sub_font, fill=sub_color)
            y += lh + 4

    img.save(out_path, "PNG")
    return out_path


# ---------------------------------------------------------------------------
# Graphic 13 — Vignette Mask (rounded-rect alpha mask for alphamerge)
# ---------------------------------------------------------------------------

def render_vignette_mask(
    width: int,
    height: int,
    corner_radius: int,
    out_path: Path,
) -> Path:
    """Grayscale PNG: white inside rounded rect, black outside.
    Used by FFmpeg alphamerge to give the person a rounded-corner vignette."""
    img = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, width, height), radius=corner_radius, fill=255)
    img.save(out_path, "PNG")
    return out_path


# ---------------------------------------------------------------------------
# Graphic 14 — Whiteboard Layout (Style 2: white bg + text left + glow ring)
# ---------------------------------------------------------------------------

def render_whiteboard_layout(
    content_text: str,
    out_path: Path,
    *,
    target_w: int,
    target_h: int,
    vign_x: int,
    vign_y: int,
    vign_w: int,
    vign_h: int,
    glow_color: str = "#0A84FF",
    bar_w: int = 30,
) -> Path:
    """Full-frame white PNG: text/content on the left, glow ring where the
    person vignette will be overlaid by FFmpeg at (vign_x, vign_y)."""
    img = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Black decorative side bars.
    draw.rectangle((0, 0, bar_w, target_h), fill=(10, 10, 10, 255))
    draw.rectangle((target_w - bar_w, 0, target_w, target_h), fill=(10, 10, 10, 255))

    # Glow ring around the vignette area (drawn BEFORE the person is overlaid).
    glow = _hex_to_rgba(glow_color, PALETTE["blue"])
    border = 10
    glow_alpha_ring = (*glow[:3], 220)
    draw.rounded_rectangle(
        (vign_x - border, vign_y - border,
         vign_x + vign_w + border, vign_y + vign_h + border),
        radius=50, outline=glow_alpha_ring, width=8, fill=None,
    )
    # Faint glow halo.
    for g in range(1, 4):
        ga = (*glow[:3], max(0, 60 - g * 18))
        draw.rounded_rectangle(
            (vign_x - border - g * 5, vign_y - border - g * 5,
             vign_x + vign_w + border + g * 5, vign_y + vign_h + border + g * 5),
            radius=55 + g * 4, outline=ga, width=4, fill=None,
        )

    # Content text on the left (safe zone: bar_w+20 to vign_x-40).
    max_content_w = vign_x - bar_w - 60
    font_size = max(24, min(int(target_h * 0.045), 80))
    font = _load_font("Poppins-Bold", font_size)
    lines = _wrap_text_to_width(content_text or "", font, max_content_w)
    ascent, descent = font.getmetrics()
    lh = ascent + descent
    block_h = len(lines) * lh + max(0, len(lines) - 1) * 12
    y = max(bar_w + 20, (target_h - block_h) // 3)
    for ln in lines:
        draw.text((bar_w + 30, y), ln, font=font, fill=(10, 10, 10, 255))
        y += lh + 12

    img.save(out_path, "PNG")
    return out_path


# ---------------------------------------------------------------------------
# Graphic 15 — Slide Layout (Style 3: white bg + title + bullets + glow ring)
# ---------------------------------------------------------------------------

def render_slide_layout(
    title: str,
    bullets: list[str],
    out_path: Path,
    *,
    target_w: int,
    target_h: int,
    vign_x: int,
    vign_y: int,
    vign_w: int,
    vign_h: int,
    glow_color: str = "#0A84FF",
    bar_w: int = 30,
) -> Path:
    """Full-frame white PNG: big title left + bullet list, glow ring where
    the person vignette will be overlaid by FFmpeg."""
    img = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Black side bars.
    draw.rectangle((0, 0, bar_w, target_h), fill=(10, 10, 10, 255))
    draw.rectangle((target_w - bar_w, 0, target_w, target_h), fill=(10, 10, 10, 255))

    # Glow ring.
    glow = _hex_to_rgba(glow_color, PALETTE["blue"])
    border = 10
    glow_alpha_ring = (*glow[:3], 220)
    draw.rounded_rectangle(
        (vign_x - border, vign_y - border,
         vign_x + vign_w + border, vign_y + vign_h + border),
        radius=50, outline=glow_alpha_ring, width=8, fill=None,
    )
    for g in range(1, 4):
        ga = (*glow[:3], max(0, 60 - g * 18))
        draw.rounded_rectangle(
            (vign_x - border - g * 5, vign_y - border - g * 5,
             vign_x + vign_w + border + g * 5, vign_y + vign_h + border + g * 5),
            radius=55 + g * 4, outline=ga, width=4, fill=None,
        )

    content_w = vign_x - bar_w - 60
    pad_x = bar_w + 30
    y = int(target_h * 0.08)

    # Title.
    title_size = max(32, min(int(content_w * 0.09), 100))
    title_font = _load_font("Poppins-ExtraBold", title_size)
    while title_size > 32 and _text_size(title_font, title or "")[0] > content_w:
        title_size = max(32, int(title_size * 0.88))
        title_font = _load_font("Poppins-ExtraBold", title_size)
    title_lines = _wrap_text_to_width(title or "", title_font, content_w)
    t_ascent, t_descent = title_font.getmetrics()
    for ln in title_lines:
        draw.text((pad_x, y), ln, font=title_font, fill=(10, 10, 10, 255))
        y += t_ascent + t_descent + 8
    y += int(title_size * 0.3)

    # Accent line under title.
    glow_rgb = glow[:3]
    draw.rectangle((pad_x, y, pad_x + min(content_w, 200), y + 5),
                   fill=(*glow_rgb, 255))
    y += 24

    # Bullet points.
    bull_size = max(22, int(title_size * 0.48))
    bull_font = _load_font("Poppins-Bold", bull_size)
    b_ascent, b_descent = bull_font.getmetrics()
    for bullet in (bullets or []):
        bullet_text = f"→  {bullet}"
        b_lines = _wrap_text_to_width(bullet_text, bull_font, content_w - 20)
        for i, ln in enumerate(b_lines):
            draw.text((pad_x + (20 if i > 0 else 0), y), ln,
                      font=bull_font, fill=(30, 30, 30, 255))
            y += b_ascent + b_descent + 6
        y += int(bull_size * 0.3)

    img.save(out_path, "PNG")
    return out_path


# ---------------------------------------------------------------------------
# Dispatcher — turn one motion_graphic JSON into a rendered PNG + position.
# ---------------------------------------------------------------------------

@dataclass
class RenderedGraphic:
    """Result of rendering one motion_graphic: PNG path + frame placement +
    timing the renderer should overlay it on."""
    png: Path
    at: float
    duration: float
    x_expr: str        # ffmpeg overlay x expression
    y_expr: str        # ffmpeg overlay y expression
    fade_in_s: float = 0.3
    kind: str = ""
    bg_card: str = ""  # "black" | "white" — solid card painted behind the graphic


def _apply_bg_card(png: Path, color: str) -> None:
    """Paint a solid-color card behind the graphic PNG (in-place)."""
    rgba = (0, 0, 0, 210) if color == "black" else (255, 255, 255, 210)
    with Image.open(png).convert("RGBA") as img:
        card = Image.new("RGBA", img.size, rgba)
        card.paste(img, (0, 0), img)
        card.save(png)


def render_motion_graphic(
    spec: dict,
    out_dir: Path,
    index: int,
    *,
    target_w: int,
    target_h: int,
    accent_hex: str = "#0A84FF",
    aesthetic: str = "dark-pro",
    subject_pos: dict | None = None,
) -> RenderedGraphic | None:
    """Render one motion_graphic spec to a PNG + return its placement.

    Returns None for kinds we don't yet execute (the agent still gets to
    plan them; the renderer just doesn't draw them today)."""
    kind = (spec.get("kind") or "").lower()
    try:
        at = float(spec.get("at", 0))
        duration = float(spec.get("duration", 2.0))
    except (TypeError, ValueError):
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / f"mg_{index:03d}_{kind}.png"
    bg_card = (spec.get("bg_card") or "").lower()
    if bg_card not in ("black", "white"):
        bg_card = ""

    preset_colors = AESTHETIC_COLORS.get(aesthetic, AESTHETIC_COLORS["dark-pro"])
    accent_hex = accent_hex or preset_colors["accent"]
    preset_bg = preset_colors["bg"]
    preset_text = preset_colors["text"]

    # Subject-position helpers derived from Claude Vision data.
    _sp = subject_pos or {}
    _fl = _sp.get("face_left_pct", 25.0)
    _fr = _sp.get("face_right_pct", 75.0)
    _ft = _sp.get("face_top_pct", 15.0)
    _fb = _sp.get("face_bottom_pct", 65.0)
    _fcx = (_fl + _fr) / 2   # face center x %
    _fcy = (_ft + _fb) / 2   # face center y %

    def _safe_x_expr(graphic_w_expr: str = "w") -> str:
        if _fcx < 40:    # subject left → graphic right
            return f"W-{graphic_w_expr}-W*0.04"
        if _fcx > 60:    # subject right → graphic left
            return f"W*0.04"
        return f"(W-{graphic_w_expr})/2"

    def _safe_y_expr() -> str:
        if _fcy < 40:    # face in top half → graphic at bottom
            return "H*0.62"
        return "H*0.04"

    if kind == "lower_third" or kind == "fly_in":
        # fly_in degrades into a lower-third title — same visual, same intent.
        title = str(spec.get("title") or spec.get("text") or "").strip()
        accent_word = (spec.get("accent_word") or "").strip() or None
        if not title:
            return None
        # PNG width = frame width minus left margin and a small right gutter.
        margin_l = int(target_w * 0.06)
        safe_w = target_w - margin_l - 32
        render_lower_third(title, accent_word, png,
                           width=safe_w, max_text_width=safe_w,
                           accent_hex=accent_hex)
        anchor_y = int(target_h * 0.15)
        x_expr = (
            f"if(lt(t-{at:.3f},0.3),"
            f"-w+({margin_l}-(-w))*((t-{at:.3f})/0.3)*((t-{at:.3f})/0.3)*(3-2*((t-{at:.3f})/0.3)),"
            f"{margin_l})"
        )
        y_expr = f"{anchor_y}"
        if bg_card:
            _apply_bg_card(png, bg_card)
        return RenderedGraphic(
            png=png, at=at, duration=duration,
            x_expr=x_expr, y_expr=y_expr, kind=kind, bg_card=bg_card,
        )

    if kind == "count_up" or kind == "stat_circle":
        try:
            value = int(round(float(spec.get("to") or spec.get("percent") or 50)))
        except (TypeError, ValueError):
            return None
        label = str(spec.get("label") or spec.get("text") or "").strip()
        render_stat_circle(value, label, None, png, accent_hex=accent_hex)
        x_expr = _safe_x_expr()
        y_expr = _safe_y_expr()
        if bg_card:
            _apply_bg_card(png, bg_card)
        return RenderedGraphic(
            png=png, at=at, duration=duration,
            x_expr=x_expr, y_expr=y_expr, kind=kind, bg_card=bg_card,
        )

    if kind == "text_overlay" or kind == "text" or kind == "annotation":
        text = str(spec.get("text") or "").strip()
        if not text:
            return None
        font = _normalize_font_name(spec.get("font") or "Poppins Bold")
        # size is a percentage of the frame's shorter edge (min of W and H).
        # size: 15 → 15% of min(target_w, target_h). Keeps text proportional
        # across portrait and landscape without the agent needing pixel math.
        size_pct = float(spec.get("size") or 15)
        font_size = max(14, int(min(target_w, target_h) * size_pct / 100))
        color = spec.get("color") or "#FFFFFF"
        align = (spec.get("align") or "left").lower()
        if align not in {"left", "center", "right"}:
            align = "left"

        # Position by percentage of the frame so the agent doesn't have to
        # know the resolution. Defaults: 6% left, 8% from top (upper safe zone).
        x_pct = float(spec.get("x_pct", 6)) / 100.0
        y_pct = float(spec.get("y_pct", 8)) / 100.0

        # Soft wrap at 25% of frame width by default — keeps text blocks
        # compact and away from the centre of the frame.
        max_w_pct = float(spec.get("max_width_pct", 25)) / 100.0
        max_width_px = int(target_w * max_w_pct) if max_w_pct > 0 else None

        render_text_overlay(
            text, png,
            font_name=font, font_size=font_size,
            color_hex=color, align=align,
            max_width_px=max_width_px,
        )

        # Slide direction. Default = left → in.
        slide = (spec.get("slide_in") or "left").lower()
        anchor_x = int(target_w * x_pct)
        anchor_y = int(target_h * y_pct)
        if slide == "left":
            x_expr = (
                f"if(lt(t-{at:.3f},0.3),"
                f"-w+({anchor_x}-(-w))*"
                f"((t-{at:.3f})/0.3)*((t-{at:.3f})/0.3)*(3-2*((t-{at:.3f})/0.3)),"
                f"{anchor_x})"
            )
        elif slide == "right":
            x_expr = (
                f"if(lt(t-{at:.3f},0.3),"
                f"W+({anchor_x}-W)*"
                f"((t-{at:.3f})/0.3)*((t-{at:.3f})/0.3)*(3-2*((t-{at:.3f})/0.3)),"
                f"{anchor_x})"
            )
        else:  # "none" — instant pop-in
            x_expr = f"{anchor_x}"
        y_expr = f"{anchor_y}"

        if bg_card:
            _apply_bg_card(png, bg_card)
        return RenderedGraphic(
            png=png, at=at, duration=duration,
            x_expr=x_expr, y_expr=y_expr, kind=kind, bg_card=bg_card,
        )

    if kind == "checklist":
        raw_items = spec.get("items") or []
        items: list[ChecklistItem] = []
        for raw in raw_items:
            if isinstance(raw, dict):
                items.append(ChecklistItem(
                    text=str(raw.get("text", "")).strip(),
                    ok=bool(raw.get("ok", raw.get("checked", False))),
                ))
            elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
                items.append(ChecklistItem(text=str(raw[0]), ok=bool(raw[1])))
        items = [it for it in items if it.text]
        if not items:
            return None
        checklist_w = min(int(target_w * 0.92), 1200)
        render_checklist(items, png, width=checklist_w)
        x_expr = _safe_x_expr()
        y_expr = _safe_y_expr()
        if bg_card:
            _apply_bg_card(png, bg_card)
        return RenderedGraphic(
            png=png, at=at, duration=duration,
            x_expr=x_expr, y_expr=y_expr, kind=kind, bg_card=bg_card,
        )

    if kind in ("quote_card", "quote"):
        quote = str(spec.get("text") or spec.get("quote") or "").strip()
        speaker = str(spec.get("speaker") or "").strip() or None
        if not quote:
            return None
        render_quote_card(quote, speaker, png,
                          target_w=target_w, target_h=target_h,
                          accent_hex=accent_hex, bg_rgba=preset_bg, text_rgba=preset_text)
        if bg_card:
            _apply_bg_card(png, bg_card)
        return RenderedGraphic(
            png=png, at=at, duration=duration,
            x_expr="0", y_expr="0", kind=kind, bg_card=bg_card,
        )

    if kind in ("split_screen", "split"):
        left = str(spec.get("left") or "").strip()
        right = str(spec.get("right") or "").strip()
        left_label = str(spec.get("left_label") or "WRONG").strip()
        right_label = str(spec.get("right_label") or "RIGHT").strip()
        render_split_screen(left, right, png,
                            target_w=target_w, target_h=target_h,
                            left_label=left_label, right_label=right_label,
                            accent_hex=accent_hex, bg_rgba=preset_bg, text_rgba=preset_text)
        if bg_card:
            _apply_bg_card(png, bg_card)
        return RenderedGraphic(
            png=png, at=at, duration=duration,
            x_expr="0", y_expr="0", kind=kind, bg_card=bg_card,
        )

    if kind == "timeline":
        events = spec.get("events") or []
        render_timeline(events, png,
                        target_w=target_w, target_h=int(target_h * 0.4),
                        accent_hex=accent_hex, text_rgba=preset_text)
        if bg_card:
            _apply_bg_card(png, bg_card)
        return RenderedGraphic(
            png=png, at=at, duration=duration,
            x_expr="0", y_expr="(H-h)/2", kind=kind, bg_card=bg_card,
        )

    if kind == "versus":
        left = str(spec.get("left") or "").strip()
        right = str(spec.get("right") or "").strip()
        render_versus(left, right, png,
                      target_w=target_w, target_h=target_h,
                      accent_hex=accent_hex, bg_rgba=preset_bg, text_rgba=preset_text)
        if bg_card:
            _apply_bg_card(png, bg_card)
        return RenderedGraphic(
            png=png, at=at, duration=duration,
            x_expr="0", y_expr="0", kind=kind, bg_card=bg_card,
        )

    if kind == "notification":
        title = str(spec.get("title") or "").strip()
        body = str(spec.get("body") or "").strip()
        app_name = str(spec.get("app_name") or "").strip()
        render_notification(title, body, app_name, png,
                            target_w=target_w, target_h=target_h,
                            accent_hex=accent_hex)
        if bg_card:
            _apply_bg_card(png, bg_card)
        return RenderedGraphic(
            png=png, at=at, duration=duration,
            x_expr="0", y_expr="H*0.06", kind=kind, bg_card=bg_card,
        )

    if kind in ("typography_broll", "typo_broll"):
        word = str(spec.get("text") or "").strip()
        words = spec.get("words") or []
        if not word:
            return None
        render_typography_broll(word, words, png,
                                target_w=target_w, target_h=target_h,
                                accent_hex=accent_hex, bg_rgba=preset_bg)
        if bg_card:
            _apply_bg_card(png, bg_card)
        return RenderedGraphic(
            png=png, at=at, duration=duration,
            x_expr="0", y_expr="0", kind=kind, bg_card=bg_card,
        )

    if kind in ("money_counter", "counter"):
        try:
            amount = float(spec.get("to") or spec.get("amount") or 0)
        except (TypeError, ValueError):
            return None
        currency = str(spec.get("currency") or "$")
        positive = bool(spec.get("positive", True))
        render_money_counter(amount, currency, png,
                             target_w=target_w, target_h=target_h,
                             positive=positive)
        if bg_card:
            _apply_bg_card(png, bg_card)
        return RenderedGraphic(
            png=png, at=at, duration=duration,
            x_expr="0", y_expr="0", kind=kind, bg_card=bg_card,
        )

    if kind == "giant_text":
        number = str(spec.get("number") or spec.get("text") or "").strip()
        subtitle = str(spec.get("subtitle") or "").strip()
        sub_color = spec.get("subtitle_color") or "#FF3B30"
        if not number:
            return None
        ft = _ft
        fb = _fb
        render_giant_text(
            number, subtitle, sub_color, png,
            target_w=target_w, target_h=target_h,
            face_top_pct=ft, face_bottom_pct=fb,
        )
        # Place in the safe zone with the most vertical space.
        space_above = ft
        space_below = 100.0 - fb
        if space_above >= space_below:
            y_expr = f"max(0,{int(target_h * ft / 100 / 2)}-h/2)"
        else:
            safe_start = int(target_h * fb / 100)
            safe_end = target_h
            y_expr = f"{safe_start + (safe_end - safe_start) // 2}-h/2"
        x_expr = "(W-w)/2"
        if bg_card:
            _apply_bg_card(png, bg_card)
        return RenderedGraphic(
            png=png, at=at, duration=duration,
            x_expr=x_expr, y_expr=y_expr, kind=kind, bg_card=bg_card,
        )

    return None
