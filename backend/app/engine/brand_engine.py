"""Brand Kit Engine — Feature 2.

Loads the brand kit and injects brand elements into the render pipeline:
logo watermark, text watermark, lower thirds, intro/outro bumpers,
and brand color overrides on graphics/captions.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from app.core.config import BACKEND_DIR
from app.engine.transcribe import FFMPEG_PATH, FFPROBE_PATH

BRAND_DIR  = BACKEND_DIR / "storage" / "brand"
BRAND_FILE = BRAND_DIR / "brand.json"
BRAND_DIR.mkdir(parents=True, exist_ok=True)

_DEFAULT_BRAND: dict[str, Any] = {
    "id":   "default",
    "name": "",
    "colors": {
        "primary":    "#FF7751",
        "secondary":  "#FFFFFF",
        "background": "#0A0A0A",
        "accent":     "#FF7751",
    },
    "logo": {
        "path":      "",
        "position":  "top_right",
        "size_pct":  0.08,
        "opacity":   0.85,
    },
    "font": {
        "name":           "Poppins",
        "weight":         "Bold",
        "caption_color":  "#FFFFFF",
        "emphasis_color": "#FF7751",
    },
    "intro": {"path": "", "duration": 3.0, "enabled": False},
    "outro": {"path": "", "duration": 5.0, "enabled": False, "cta_text": "Follow for more"},
    "watermark": {
        "text":     "",
        "position": "bottom_left",
        "size_pct": 0.025,
        "opacity":  0.6,
        "color":    "#FFFFFF",
    },
    "lower_third": {
        "name_text":             "",
        "title_text":            "",
        "show_on_first_appearance": True,
        "style":                 "minimal",
        "accent_color":          "#FF7751",
    },
}


def load_brand() -> dict[str, Any]:
    if not BRAND_FILE.exists():
        return dict(_DEFAULT_BRAND)
    try:
        return json.loads(BRAND_FILE.read_text(encoding="utf-8"))
    except Exception:
        return dict(_DEFAULT_BRAND)


def save_brand(brand: dict[str, Any]) -> None:
    BRAND_FILE.write_text(json.dumps(brand, indent=2, ensure_ascii=False), encoding="utf-8")


def _position_to_xy(position: str, target_w: int, target_h: int,
                    elem_w: int, elem_h: int, margin: int = 20) -> tuple[str, str]:
    """Map a position name to FFmpeg x/y expressions."""
    positions: dict[str, tuple[str, str]] = {
        "top_right":    (f"{target_w}-{elem_w}-{margin}", str(margin)),
        "top_left":     (str(margin), str(margin)),
        "bottom_right": (f"{target_w}-{elem_w}-{margin}", f"{target_h}-{elem_h}-{margin}"),
        "bottom_left":  (str(margin), f"{target_h}-{elem_h}-{margin}"),
        "center":       (f"({target_w}-{elem_w})/2", f"({target_h}-{elem_h})/2"),
    }
    return positions.get(position, positions["bottom_left"])


class BrandEngine:
    """Injects brand elements into render configs and FFmpeg filter graphs."""

    def apply_brand(self, render_plan: dict[str, Any], brand: dict[str, Any]) -> dict[str, Any]:
        """Merge brand overrides into a render plan dict. Returns updated plan."""
        import copy
        plan = copy.deepcopy(render_plan)

        colors = brand.get("colors", {})
        font   = brand.get("font", {})

        primary = colors.get("primary", "#FF7751")
        if primary:
            plan["brand_color"] = primary

        cap_color = font.get("caption_color", "")
        if cap_color:
            plan["caption_color"] = _hex_to_name(cap_color)

        plan["_brand"] = brand
        return plan

    def build_watermark_filters(
        self,
        brand: dict[str, Any],
        target_w: int,
        target_h: int,
    ) -> str:
        """Return FFmpeg drawtext filter string for text watermark. Empty if disabled."""
        wm = brand.get("watermark", {})
        text = wm.get("text", "").strip()
        if not text:
            return ""

        pos    = wm.get("position", "bottom_left")
        opacity = float(wm.get("opacity", 0.6))
        size_pct = float(wm.get("size_pct", 0.025))
        color  = wm.get("color", "#FFFFFF")
        fs     = max(14, int(target_h * size_pct))

        hex_color = color.lstrip("#")
        alpha_hex  = format(int(opacity * 255), "02x")
        ffcolor    = f"0x{hex_color}{alpha_hex}" if len(hex_color) == 6 else "white@0.6"

        x_expr, y_expr = {
            "bottom_left":  (f"20", f"h-th-20"),
            "bottom_right": (f"w-tw-20", f"h-th-20"),
            "top_left":     (f"20", f"20"),
            "top_right":    (f"w-tw-20", f"20"),
        }.get(pos, (f"20", f"h-th-20"))

        esc_text = text.replace("'", "\\'").replace(":", "\\:")
        return (
            f"drawtext=text='{esc_text}':"
            f"fontsize={fs}:fontcolor={ffcolor}:"
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            f"x={x_expr}:y={y_expr}"
        )

    def build_lower_third_filter(
        self,
        brand: dict[str, Any],
        target_w: int,
        target_h: int,
        t0: float = 1.5,
        t1: float = 4.5,
    ) -> str:
        """Return lower-third drawtext/drawbox filters for first speaker appearance."""
        lt = brand.get("lower_third", {})
        name_text  = lt.get("name_text", "").strip()
        title_text = lt.get("title_text", "").strip()
        if not name_text and not title_text:
            return ""

        accent  = lt.get("accent_color", "#FF7751").lstrip("#")
        bar_h   = max(50, target_h // 16)
        bar_w   = max(240, target_w // 3)
        bar_x   = 24
        bar_y   = target_h - bar_h - max(30, target_h // 20)
        fs_name  = max(18, min(28, target_h // 28))
        fs_title = max(13, min(20, target_h // 40))
        enable   = f"between(t,{t0:.2f},{t1:.2f})"

        parts = [
            f"drawbox=x={bar_x}:y={bar_y}:w={bar_w}:h={bar_h}:"
            f"color=0x0A0A0A@0.90:t=fill:enable='{enable}'",
            f"drawbox=x={bar_x}:y={bar_y}:w=4:h={bar_h}:"
            f"color=0x{accent}@1.0:t=fill:enable='{enable}'",
        ]
        if name_text:
            esc = name_text.replace("'", "\\'").replace(":", "\\:")
            parts.append(
                f"drawtext=text='{esc}':"
                f"fontsize={fs_name}:fontcolor=white:"
                f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
                f"x={bar_x + 12}:y={bar_y + 6}:enable='{enable}'"
            )
        if title_text:
            esc = title_text.replace("'", "\\'").replace(":", "\\:")
            parts.append(
                f"drawtext=text='{esc}':"
                f"fontsize={fs_title}:fontcolor=0xFFFFFF@0.70:"
                f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
                f"x={bar_x + 12}:y={bar_y + fs_name + 10}:enable='{enable}'"
            )
        return ",".join(parts)

    def prepend_intro(self, output_path: Path, brand: dict[str, Any], work_dir: Path) -> Path:
        """
        If an intro clip exists and is enabled, prepend it to output_path.
        Returns the path of the final video (may be a new temp file).
        """
        intro = brand.get("intro", {})
        if not intro.get("enabled", False):
            return output_path
        intro_path = Path(intro.get("path", ""))
        if not intro_path.exists():
            return output_path

        concat_list = work_dir / "brand_concat.txt"
        branded_out = work_dir / f"branded_{output_path.name}"
        concat_list.write_text(
            f"file '{intro_path.as_posix()}'\nfile '{output_path.as_posix()}'\n",
            encoding="utf-8",
        )
        subprocess.run(
            [FFMPEG_PATH, "-y", "-loglevel", "error",
             "-f", "concat", "-safe", "0", "-i", str(concat_list),
             "-c", "copy", str(branded_out)],
            capture_output=True, timeout=120,
        )
        concat_list.unlink(missing_ok=True)
        return branded_out if branded_out.exists() else output_path

    def append_outro(self, output_path: Path, brand: dict[str, Any], work_dir: Path) -> Path:
        """
        If an outro clip exists and is enabled, append it to output_path.
        Returns the path of the final video.
        """
        outro = brand.get("outro", {})
        if not outro.get("enabled", False):
            return output_path
        outro_path = Path(outro.get("path", ""))
        if not outro_path.exists():
            return output_path

        concat_list = work_dir / "outro_concat.txt"
        branded_out = work_dir / f"outro_{output_path.name}"
        concat_list.write_text(
            f"file '{output_path.as_posix()}'\nfile '{outro_path.as_posix()}'\n",
            encoding="utf-8",
        )
        subprocess.run(
            [FFMPEG_PATH, "-y", "-loglevel", "error",
             "-f", "concat", "-safe", "0", "-i", str(concat_list),
             "-c", "copy", str(branded_out)],
            capture_output=True, timeout=120,
        )
        concat_list.unlink(missing_ok=True)
        return branded_out if branded_out.exists() else output_path


def _hex_to_name(hex_color: str) -> str:
    """Best-effort map of hex → FFmpeg color name for common brand colors."""
    mapping = {
        "#ffffff": "white", "#FFFFFF": "white",
        "#000000": "black", "#0A0A0A": "black",
        "#ff0000": "red",
        "#ffff00": "yellow",
        "#0000ff": "blue",
        "#ff7751": "white",  # salmon → keep captions white
    }
    return mapping.get(hex_color, "white")
