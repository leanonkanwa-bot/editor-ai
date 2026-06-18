"""
HyperFrames HTML motion graphics engine.

Rendering priority:
  1. HyperFrames CLI  (npx hyperframes render — if installed)
  2. Chromium screenshot loop  (single headless-Chrome capture → FFmpeg video)
  3. Transparent black fallback  (no crash, graphic simply absent)

HTML compositions use GSAP for animations.  The --virtual-time-budget flag
advances the Chromium clock so animations settle before the screenshot.
"""
from __future__ import annotations

import html
import os
import re
import subprocess
from pathlib import Path

from app.engine.transcribe import FFMPEG_PATH

_NODE_BINS = ["/usr/bin/node", "/usr/local/bin/node"]
_NPX_BINS  = ["/usr/bin/npx",  "/usr/local/bin/npx"]
_CHROMIUM_CANDIDATES = [
    os.environ.get("CHROMIUM_PATH", ""),
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
]

# Pure green page background, forced onto every rendered composition so the
# render.py overlay pass can `colorkey` it out — gives real per-pixel
# transparency without requiring an alpha-capable video codec (libx264 has
# no yuva420p support).
CHROMA_KEY_HEX = "00FF00"

# ── Inline GSAP bundle (loaded once, embedded in every composition HTML) ────
_GSAP_JS: str | None = None

def _gsap_inline() -> str:
    """Return the contents of the local gsap.min.js for inline embedding."""
    global _GSAP_JS
    if _GSAP_JS is None:
        gsap_path = Path(__file__).parent / "node_modules" / "gsap" / "dist" / "gsap.min.js"
        if gsap_path.exists():
            _GSAP_JS = gsap_path.read_text(encoding="utf-8")
        else:
            _GSAP_JS = ""
            print(f"[HYPERFRAMES] WARNING: gsap.min.js not found at {gsap_path}")
    return _GSAP_JS

def _gsap_script_tag() -> str:
    """Return a <script> tag with GSAP inlined — no CDN dependency."""
    return f"<script>{_gsap_inline()}</script>"

# ─────────────────────────────────────────────────────────────────────────────


def _find(candidates: list[str]) -> str | None:
    return next((p for p in candidates if p and os.path.exists(p)), None)


def render_with_hyperframes(html_path: Path, output_path: Path, width: int, height: int, fps: int) -> bool:
    """Render a standalone HTML composition to a transparent MP4 via the HyperFrames CLI."""
    npx = _find(_NPX_BINS) or "npx"
    try:
        result = subprocess.run(
            [
                npx, "hyperframes", "render",
                str(html_path),
                "--output", str(output_path),
                "--width", str(width),
                "--height", str(height),
                "--fps", str(fps),
            ],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as e:
        print(f"[HF] CLI error: {e}")
        return False

    if result.returncode == 0 and Path(output_path).exists():
        print(f"[HF] Rendered: {output_path}")
        return True

    print(f"[HF] CLI failed: {result.stderr[:200]}")
    return False


# ── HTML generation (GSAP compositions) ──────────────────────────────────────

def _esc(t: str) -> str:
    return (
        t.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


def _style_palette(style: str, brand_color: str) -> dict:
    """Theme tokens for the motion-board graphic styles.

    momentum  → Anton, uppercase, lime accent on black (matches captions._build_momentum_ass)
    priestley → Inter, gold accent on burgundy/cream (matches captions._build_priestley_ass)
    """
    if style == "priestley":
        return {
            "font": "Inter", "weight": "800", "transform": "none",
            "text": "#FDFBF7", "accent": "#FFDE4D", "bg": "#2B080C",
        }
    return {
        "font": "Anton", "weight": "900", "transform": "uppercase",
        "text": "#FFFFFF", "accent": brand_color or "#FF7751", "bg": "#000000",
    }


def prompt_to_gsap_config(prompt: str) -> dict:
    """Parse a natural-language hf_prompt into GSAP/CSS animation parameters.

    Recognizes entry/exit animation keywords, position, background (color,
    opacity, frosted-glass blur), hex colors, font-size as % of frame height,
    and explicit cubic-bezier/ms timing — falling back to sane defaults for
    anything not mentioned in the prompt.
    """
    p = (prompt or "").lower()
    cfg: dict = {
        "entry_ease": "power2.out",
        "entry_from": {"opacity": 0},
        "exit_ease": "power2.in",
        "exit_to": {"opacity": 0},
        "position": "center",
        "bg_color": None,
        "blur": False,
        "text_color": None,
        "accent_color": None,
        "font_size_pct": None,
        "entry_duration": 0.3,
        "exit_duration": 0.2,
    }

    # ── Entry animation ──────────────────────────────────────────────────
    if "slam" in p or "pop" in p:
        cfg["entry_ease"] = "back.out(1.7)"
        cfg["entry_from"]["scale"] = 0.8
    if "bounce" in p:
        cfg["entry_ease"] = "elastic.out(1, 0.3)"
        cfg["entry_from"].setdefault("scale", 0.8)
    if re.search(r"slides?\s+(?:up\s+)?from\s+(?:the\s+)?bottom|slides?\s+up", p):
        cfg["entry_from"]["y"] = 50
    elif re.search(r"slides?\s+(?:down\s+)?from\s+(?:the\s+)?top|slides?\s+down", p):
        cfg["entry_from"]["y"] = -50
    if re.search(r"slides?\s+(?:in\s+)?from\s+(?:the\s+)?left", p):
        cfg["entry_from"]["x"] = -100
    elif re.search(r"slides?\s+(?:in\s+)?from\s+(?:the\s+)?right", p):
        cfg["entry_from"]["x"] = 100
    if "fade" in p:
        cfg["entry_from"].setdefault("opacity", 0)

    m = re.search(r"cubic-bezier\(\s*([\d.,\s]+?)\s*\)", prompt, re.I)
    if m:
        cfg["entry_ease"] = f"cubic-bezier({m.group(1)})"

    # ── Exit animation ───────────────────────────────────────────────────
    if "slide" in p and ("back down" in p or "down on exit" in p or "slides down" in p):
        cfg["exit_to"]["y"] = 50
    if "scale down" in p or "scales down" in p:
        cfg["exit_to"]["scale"] = 0.85
    if "instant" in p and "cut" in p:
        cfg["exit_duration"] = 0.0

    # ── Position ─────────────────────────────────────────────────────────
    if "top-left" in p or "top left" in p:
        cfg["position"] = "top_left"
    elif "top-right" in p or "top right" in p:
        cfg["position"] = "top_right"
    elif "bottom-left" in p or "bottom left" in p:
        cfg["position"] = "bottom_left"
    elif "bottom-right" in p or "bottom right" in p:
        cfg["position"] = "bottom_right"
    elif "bottom" in p:
        cfg["position"] = "bottom_center"
    elif "top" in p:
        cfg["position"] = "top_center"

    # ── Background ───────────────────────────────────────────────────────
    if "frosted glass" in p or "backdrop-filter" in p or "glass" in p:
        cfg["blur"] = True
    m = re.search(r"rgba?\([^)]+\)", prompt, re.I)
    if m:
        cfg["bg_color"] = m.group(0)

    # ── Colors ───────────────────────────────────────────────────────────
    hexes = re.findall(r"#[0-9a-fA-F]{6}", prompt)
    if hexes:
        cfg["text_color"] = hexes[0]
        if len(hexes) > 1:
            cfg["accent_color"] = hexes[1]

    # ── Typography size ──────────────────────────────────────────────────
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:of\s+(?:the\s+)?)?frame\s+height", prompt, re.I)
    if m:
        cfg["font_size_pct"] = float(m.group(1)) / 100.0

    # ── Timing (explicit ms overrides for entry/exit) ───────────────────
    durs_ms = re.findall(r"(\d+)\s*ms", prompt, re.I)
    if durs_ms:
        cfg["entry_duration"] = int(durs_ms[0]) / 1000.0
        if len(durs_ms) > 1:
            cfg["exit_duration"] = int(durs_ms[-1]) / 1000.0

    return cfg


def _render_from_prompt(content: dict, duration: float, width: int, height: int, brand_color: str) -> str:
    """Render a motion graphic driven by a rich `hf_prompt` description."""
    cfg = prompt_to_gsap_config(str(content.get("hf_prompt", "")))
    text = _esc(str(content.get("text", "")))
    subtext = _esc(str(content.get("subtext", "")))
    pal = _style_palette(str(content.get("style", "momentum")), brand_color)

    text_color = pal["text"]
    accent_color = cfg["accent_color"] or pal["accent"]
    font_size = max(1, int(height * (cfg["font_size_pct"] or 0.08)))
    entry_dur = cfg["entry_duration"]
    exit_dur = cfg["exit_duration"]

    align_map = {
        "top_left":      ("flex-start", "flex-start"),
        "top_center":    ("center", "flex-start"),
        "top_right":     ("flex-end", "flex-start"),
        "center":        ("center", "center"),
        "bottom_left":   ("flex-start", "flex-end"),
        "bottom_center": ("center", "flex-end"),
        "bottom_right":  ("flex-end", "flex-end"),
    }
    justify_content, align_items = align_map.get(cfg["position"], ("center", "center"))

    bg_color = cfg["bg_color"] or "transparent"
    blur_css = (
        "backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);"
        if cfg["blur"] else ""
    )

    entry_from = cfg["entry_from"]
    transforms = []
    if "x" in entry_from:
        transforms.append(f"translateX({entry_from['x']}px)")
    if "y" in entry_from:
        transforms.append(f"translateY({entry_from['y']}px)")
    if "scale" in entry_from:
        transforms.append(f"scale({entry_from['scale']})")
    initial_transform = " ".join(transforms) if transforms else "none"
    initial_opacity = entry_from.get("opacity", 0)

    entry_props = ["opacity:1"]
    if "x" in entry_from:
        entry_props.append("x:0")
    if "y" in entry_from:
        entry_props.append("y:0")
    if "scale" in entry_from:
        entry_props.append("scale:1")
    entry_props.append(f"duration:{entry_dur:.3f}")
    entry_props.append(f'ease:"{cfg["entry_ease"]}"')

    exit_to = dict(cfg["exit_to"])
    exit_to.setdefault("opacity", 0)
    exit_props = [f"{k}:{v}" for k, v in exit_to.items()]
    exit_props.append(f"duration:{exit_dur:.3f}")
    exit_props.append(f'ease:"{cfg["exit_ease"]}"')

    sub_html = f'<div class="hf-sub" id="hsub">{subtext}</div>' if subtext else ""
    sub_js = (
        f'gsap.to("#hsub",{{opacity:1,duration:{entry_dur:.3f},delay:0.1,ease:"power2.out"}});\n'
        f'gsap.to("#hsub",{{opacity:0,duration:{exit_dur:.3f},ease:"power2.in"}},"{duration:.3f}-{exit_dur:.3f}");'
        if subtext else ""
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
body {{
    width:{width}px;height:{height}px;background:transparent;overflow:hidden;
    display:flex;align-items:{align_items};justify-content:{justify_content};
    padding:{int(height*0.06)}px {int(width*0.06)}px;
}}
.hf-card {{
    background:{bg_color};{blur_css}
    border-radius:20px;padding:{int(height*0.025)}px {int(width*0.03)}px;
    opacity:{initial_opacity};transform:{initial_transform};
    text-align:center;
}}
.hf-text {{
    font-family:'{pal["font"]}',sans-serif;font-size:{font_size}px;font-weight:{pal["weight"]};
    color:{text_color};text-transform:{pal["transform"]};line-height:1.1;
}}
.hf-sub {{
    font-family:'{pal["font"]}',sans-serif;font-size:{int(font_size*0.35)}px;font-weight:600;
    color:{accent_color};margin-top:{int(height*0.012)}px;opacity:0;
}}
</style>
</head>
<body data-duration="{duration:.3f}">
<div class="hf-card" id="hf">
  <div class="hf-text" id="hft">{text}</div>
  {sub_html}
</div>
<script>
gsap.to("#hf",{{{",".join(entry_props)}}});
gsap.to("#hf",{{{",".join(exit_props)}}},"{duration:.3f}-{exit_dur:.3f}");
{sub_js}
</script>
</body>
</html>"""


def generate_composition_html(
    graphic_type: str,
    content: dict,
    duration: float,
    width: int,
    height: int,
    brand_color: str = "#FF7751",
    font: str = "Inter",
) -> str:
    """Return GSAP-animated HTML for a motion graphic overlay."""

    if content.get("hf_prompt"):
        return _render_from_prompt(content, duration, width, height, brand_color)

    if graphic_type == "kinetic_title":
        # Reference style: a persistent bold title badge sitting directly over
        # the footage (no dark scrim) — brand-coloured text with a black
        # outline for contrast, anchored near the top of frame.
        text = _esc(str(content.get("text", "")).upper())
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script>%%GSAP_INLINE%%</script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
html,body {{ width:{width}px;height:{height}px;overflow:hidden;background:transparent !important; }}
.wrap {{
    position:absolute;inset:0;top:{int(height*0.06)}px;
    display:flex;align-items:flex-start;justify-content:center;
}}
.title {{
    font-family:'Inter',sans-serif;font-size:{int(height*0.085)}px;font-weight:900;
    color:#FFFFFF;text-align:center;line-height:1.05;
    max-width:{int(width*0.9)}px;
    -webkit-text-stroke:2px #000000;
    text-shadow:2px 2px 8px rgba(0,0,0,0.8),0 0 18px rgba(0,0,0,0.6);
    opacity:0;transform:scale(0.9) translateY(20px);
}}
</style>
</head>
<body data-duration="{duration}">
<div class="wrap"><div class="title" id="kt">{text}</div></div>
<script>
window.__timelines = window.__timelines || {{}};
const tl = gsap.timeline({{ paused: true }});
tl.from("#kt", {{ opacity: 0, scale: 0.9, y: 20, duration: 0.4, ease: "power2.out" }}, 0.1);
tl.to("#kt", {{ opacity: 0, duration: 0.25, ease: "power2.in" }}, {duration} - 0.25);
window.__timelines["root"] = tl;
</script>
</body>
</html>"""

    if graphic_type == "chapter_marker":
        text = _esc(str(content.get("text", "")))
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script>%%GSAP_INLINE%%</script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
html,body {{ width:{width}px;height:{height}px;overflow:hidden;background:transparent !important; }}
.pill {{
    position:absolute;top:5%;left:50%;transform:translateX(-50%) translateY(-30px);
    background:{brand_color};padding:8px 20px;border-radius:30px;
    opacity:0;
}}
.text {{
    font-family:'Inter',sans-serif;font-size:{int(height*0.03)}px;font-weight:700;
    color:#FFFFFF;white-space:nowrap;
}}
</style>
</head>
<body data-duration="{duration}">
<div class="pill" id="cm"><div class="text">{text}</div></div>
<script>
window.__timelines = window.__timelines || {{}};
const tl = gsap.timeline({{ paused: true }});
tl.from("#cm", {{ opacity: 0, y: -30, duration: 0.4, ease: "power2.out" }}, 0.1);
tl.to("#cm", {{ opacity: 0, duration: 0.25, ease: "power2.in" }}, {duration} - 0.25);
window.__timelines["root"] = tl;
</script>
</body>
</html>"""

    if graphic_type == "stat_card":
        text = _esc(str(content.get("text", "")))
        subtext = _esc(str(content.get("subtext", "")).upper())
        sub_div = f'<div class="label">{subtext}</div>' if subtext else ""
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script>%%GSAP_INLINE%%</script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
html,body {{ width:{width}px;height:{height}px;overflow:hidden;background:transparent !important; }}
.card {{
    position:absolute;top:5%;left:5%;
    background:#1a1a1a;border-radius:20px;padding:24px 32px;
    display:inline-flex;flex-direction:column;align-items:flex-start;
    opacity:0;transform:scale(0.8);
}}
.number {{
    font-family:'Inter',sans-serif;font-size:{int(height*0.15)}px;font-weight:900;
    color:{brand_color};line-height:1;
}}
.label {{
    font-family:'Inter',sans-serif;font-size:{int(height*0.04)}px;font-weight:700;
    color:#FFFFFF;letter-spacing:0.08em;margin-top:{int(height*0.01)}px;
}}
</style>
</head>
<body data-duration="{duration}">
<div class="card" id="sc">
    <div class="number">{text}</div>
    {sub_div}
</div>
<script>
window.__timelines = window.__timelines || {{}};
const tl = gsap.timeline({{ paused: true }});
tl.from("#sc", {{ opacity: 0, scale: 0.8, duration: 0.4, ease: "back.out(1.7)" }}, 0.1);
tl.to("#sc", {{ opacity: 0, duration: 0.25, ease: "power2.in" }}, {duration} - 0.25);
window.__timelines["root"] = tl;
</script>
</body>
</html>"""

    if graphic_type == "title_card":
        text = _esc(str(content.get("text", "")).upper())
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script>%%GSAP_INLINE%%</script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
html,body {{ width:{width}px;height:{height}px;overflow:hidden;background:transparent !important; }}
body {{ display:flex;align-items:center;justify-content:center; }}
.title {{
    font-family:'{font}',Inter,sans-serif;
    font-size:{int(height*0.12)}px;
    font-weight:900;color:#FDFBF7;
    text-align:center;letter-spacing:0.04em;line-height:1.1;
    opacity:0;transform:scale(0.95);
}}
</style>
</head>
<body data-duration="{duration}">
<div class="title" id="t">{text}</div>
<script>
window.__timelines = window.__timelines || {{}};
const tl = gsap.timeline({{ paused: true }});
tl.fromTo("#t", {{ opacity: 0, scale: 0.95 }}, {{ opacity: 1, scale: 1, duration: 0.4, ease: "power2.out" }}, 0.1);
tl.to("#t", {{ scale: 1.05, duration: {duration:.2f} - 0.5, ease: "power1.inOut" }}, 0.5);
tl.to("#t", {{ opacity: 0, duration: 0.25, ease: "power2.in" }}, {duration} - 0.25);
window.__timelines["root"] = tl;
</script>
</body>
</html>"""

    if graphic_type == "stat":
        number  = _esc(str(content.get("number", "")))
        label   = _esc(str(content.get("label", "")))
        context = _esc(str(content.get("context", "")))
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script>%%GSAP_INLINE%%</script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
html,body {{ width:{width}px;height:{height}px;overflow:hidden;background:transparent !important; }}
body {{ display:flex;align-items:center;justify-content:center; }}
.card {{
    background:rgba(0,0,0,0.85);border-radius:24px;
    padding:40px 56px;text-align:center;
    border:1px solid rgba(255,255,255,0.1);
    opacity:0;transform:scale(0.8);
}}
.number {{ font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.20)}px;font-weight:900;color:{brand_color};line-height:1;text-shadow:0 0 40px {brand_color}80,0 0 80px {brand_color}40; }}
.label  {{ font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.035)}px;font-weight:700;color:#fff;text-transform:uppercase;letter-spacing:0.1em;margin-top:12px; }}
.ctx    {{ font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.025)}px;color:rgba(255,255,255,0.6);margin-top:8px; }}
</style>
</head>
<body data-duration="{duration}">
<div class="card" id="c">
    <div class="number">{number}</div>
    <div class="label">{label}</div>
    <div class="ctx">{context}</div>
</div>
<script>
window.__timelines = window.__timelines || {{}};
const tl = gsap.timeline({{ paused: true }});
tl.to("#c", {{ opacity: 1, scale: 1, duration: 0.4, ease: "back.out(1.7)" }}, 0.1);
tl.to("#c", {{ opacity: 0, scale: 0.9, duration: 0.25, ease: "power2.in" }}, {duration} - 0.25);
window.__timelines["root"] = tl;
</script>
</body>
</html>"""

    if graphic_type == "key_phrase":
        small = _esc(str(content.get("context", "")))
        large = _esc(str(content.get("phrase", "")))
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script>%%GSAP_INLINE%%</script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
html,body {{ width:{width}px;height:{height}px;overflow:hidden;background:transparent !important; }}
.bg {{ position:fixed;bottom:0;left:0;right:0;height:48%;
      background:linear-gradient(to top,rgba(0,0,0,0.92) 0%,rgba(0,0,0,0.6) 60%,transparent 100%); }}
body {{ display:flex;flex-direction:column;align-items:center;justify-content:flex-end;
       padding-bottom:{int(height*0.10)}px;position:relative; }}
.accent {{ width:56px;height:3px;background:{brand_color};border-radius:2px;
           margin-bottom:10px;opacity:0;transform:scaleX(0); }}
.small {{ font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.028)}px;
          color:rgba(255,255,255,0.75);margin-bottom:10px;opacity:0;
          letter-spacing:0.08em;text-transform:uppercase; }}
.large {{ font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.075)}px;
          font-weight:800;color:#FFFFFF;text-align:center;line-height:1.1;
          opacity:0;transform:translateY(20px);
          text-shadow:2px 2px 8px rgba(0,0,0,0.8),0 2px 30px rgba(0,0,0,0.8); }}
</style>
</head>
<body data-duration="{duration}">
<div class="bg"></div>
<div class="accent" id="a"></div>
<div class="small" id="s">{small}</div>
<div class="large" id="l">{large}</div>
<script>
window.__timelines = window.__timelines || {{}};
const tl = gsap.timeline({{ paused: true }});
tl.to("#a", {{ opacity: 1, scaleX: 1, duration: 0.25, ease: "power2.out" }}, 0.1);
tl.to("#s", {{ opacity: 1, duration: 0.3, ease: "power2.out" }}, 0.2);
tl.to("#l", {{ opacity: 1, y: 0, duration: 0.4, ease: "back.out(1.4)" }}, 0.25);
tl.to(["#a", "#s", "#l"], {{ opacity: 0, duration: 0.25, ease: "power2.in" }}, {duration} - 0.25);
window.__timelines["root"] = tl;
</script>
</body>
</html>"""

    if graphic_type == "checklist":
        title = _esc(str(content.get("title", "")))
        items = [_esc(str(it)) for it in (content.get("items") or [])[:5]]
        items_html = "".join(
            f'<div class="item" id="i{j}"><span class="dot" style="color:{brand_color}">▸</span>{it}</div>'
            for j, it in enumerate(items)
        )
        item_count = len(items)
        anims = "".join(
            f'tl.to("#i{j}", {{ opacity: 1, x: 0, duration: 0.3, ease: "power2.out" }}, {0.1 + j * 0.15:.2f});'
            for j in range(item_count)
        )
        item_selectors = ", ".join(f'"#i{j}"' for j in range(item_count))
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script>%%GSAP_INLINE%%</script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
html,body {{ width:{width}px;height:{height}px;overflow:hidden;background:transparent !important; }}
body {{ display:flex;align-items:center;padding-left:{int(width*0.06)}px; }}
.card {{ background:rgba(0,0,0,0.82);border-radius:20px;padding:28px 36px;
         border-left:4px solid {brand_color};max-width:{int(width*0.42)}px; }}
.title {{ font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.03)}px;color:{brand_color};
          font-weight:700;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:16px; }}
.item  {{ font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.025)}px;color:#fff;
          margin:10px 0;display:flex;gap:10px;align-items:center;
          opacity:0;transform:translateX(-20px); }}
.dot   {{ font-size:1.2em; }}
</style>
</head>
<body data-duration="{duration}">
<div class="card" id="cl"><div class="title">{title}</div>{items_html}</div>
<script>
window.__timelines = window.__timelines || {{}};
const tl = gsap.timeline({{ paused: true }});
{anims}
tl.to([{item_selectors}], {{ opacity: 0, duration: 0.25, ease: "power2.in" }}, {duration} - 0.25);
window.__timelines["root"] = tl;
</script>
</body>
</html>"""

    if graphic_type == "portrait_callout":
        name  = _esc(str(content.get("name", content.get("text", ""))))
        label = _esc(str(content.get("label", "")).upper())
        image_url = str(content.get("image_url", "") or "")
        bg_style = (
            f"background-image:url('{_esc(image_url)}');background-size:cover;"
            "background-position:center;filter:grayscale(100%) contrast(1.1);"
            if image_url else "background:#2A2A2A;"
        )
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script>%%GSAP_INLINE%%</script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
html,body {{ width:{width}px;height:{height}px;overflow:hidden;background:transparent !important; }}
.photo {{ position:absolute;inset:0;{bg_style} }}
.banner {{
    position:absolute;left:0;top:{int(height*0.62)}px;
    width:{int(width*0.7)}px;padding:{int(height*0.025)}px {int(width*0.06)}px;
    background:#E2241A;transform:rotate(-4deg);
    box-shadow:0 8px 24px rgba(0,0,0,0.4);
}}
.banner span {{
    font-family:'{font}',Inter,sans-serif;font-weight:900;color:#fff;
    text-transform:uppercase;letter-spacing:0.02em;display:block;
}}
.banner .name  {{ font-size:{int(height*0.045)}px; }}
.banner .label {{ font-size:{int(height*0.028)}px;opacity:0.85;margin-top:4px; }}
</style>
</head>
<body data-duration="{duration}">
<div class="photo"></div>
<div class="banner" id="b"><span class="name">{name}</span><span class="label">{label}</span></div>
<script>
window.__timelines = window.__timelines || {{}};
const tl = gsap.timeline({{ paused: true }});
tl.fromTo("#b", {{ x: -{width*1.2}, rotation: -4 }}, {{ x: 0, rotation: -4, duration: 0.5, ease: "power3.out" }}, 0.1);
tl.to("#b", {{ opacity: 0, x: -{width*0.5}, duration: 0.3, ease: "power2.in" }}, {duration} - 0.3);
window.__timelines["root"] = tl;
</script>
</body>
</html>"""

    if graphic_type == "step_diagram":
        raw_text = str(content.get("text", ""))
        subtext = _esc(str(content.get("subtext", "")))
        pal = _style_palette(str(content.get("style", "momentum")), brand_color)
        m = re.search(r"\d+", raw_text)
        step_num = m.group(0) if m else "•"
        title = _esc(raw_text.upper())
        sub_div = f'<div class="desc" id="d">{subtext}</div>' if subtext else ""
        sub_js = (
            f'tl.to("#d", {{ opacity: 1, y: 0, duration: 0.35, ease: "power2.out" }}, 0.35);\n'
            f'tl.to("#d", {{ opacity: 0, duration: 0.2, ease: "power2.in" }}, {duration} - 0.25);'
            if subtext else ""
        )
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script>%%GSAP_INLINE%%</script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
body {{ width:{width}px;height:{height}px;background:transparent !important;overflow:hidden;
        display:flex;flex-direction:column;align-items:center;justify-content:center;gap:{int(height*0.03)}px; }}
.badge {{
    width:{int(height*0.16)}px;height:{int(height*0.16)}px;border-radius:50%;
    background:{pal["accent"]};display:flex;align-items:center;justify-content:center;
    font-family:'{pal["font"]}',sans-serif;font-size:{int(height*0.07)}px;font-weight:{pal["weight"]};
    color:{pal["bg"]};opacity:0;transform:scale(0.6);
    box-shadow:0 0 30px {pal["accent"]}80;
}}
.title {{
    font-family:'{pal["font"]}',sans-serif;font-size:{int(height*0.05)}px;font-weight:{pal["weight"]};
    color:{pal["text"]};text-transform:{pal["transform"]};text-align:center;
    max-width:{int(width*0.8)}px;opacity:0;transform:translateY(20px);
}}
.desc {{
    font-family:'{pal["font"]}',sans-serif;font-size:{int(height*0.03)}px;font-weight:600;
    color:{pal["accent"]};text-align:center;max-width:{int(width*0.7)}px;
    opacity:0;transform:translateY(15px);
}}
</style>
</head>
<body data-duration="{duration:.3f}">
<div class="badge" id="b">{step_num}</div>
<div class="title" id="t">{title}</div>
{sub_div}
<script>
window.__timelines = window.__timelines || {{}};
const tl = gsap.timeline({{ paused: true }});
tl.to("#b", {{ opacity: 1, scale: 1, duration: 0.4, ease: "back.out(1.7)" }}, 0.1);
tl.to("#t", {{ opacity: 1, y: 0, duration: 0.35, ease: "power2.out" }}, 0.25);
{sub_js}
tl.to(["#b", "#t"], {{ opacity: 0, duration: 0.25, ease: "power2.in" }}, {duration} - 0.25);
window.__timelines["root"] = tl;
</script>
</body>
</html>"""

    if graphic_type == "scoreboard_stat":
        big_number   = _esc(str(content.get("big_number", content.get("number", ""))))
        small_number = _esc(str(content.get("small_number", "")))
        label        = _esc(str(content.get("label", "")).upper())
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
html,body {{ width:{width}px;height:{height}px;overflow:hidden;background:transparent; }}
body {{ display:flex;flex-direction:column;align-items:center;justify-content:center;gap:{int(height*0.02)}px; }}
.big {{
    font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.16)}px;font-weight:900;
    color:#fff;line-height:1;opacity:0;transform:scale(0.85);
}}
.divider {{
    width:{int(width*0.26)}px;height:2px;background:rgba(255,255,255,0.3);
    position:relative;margin:{int(height*0.015)}px 0;opacity:0;transform:scaleX(0);
}}
.divider::after {{
    content:'';position:absolute;left:50%;top:-4px;width:10px;height:10px;
    border-radius:50%;background:{brand_color};transform:translateX(-50%);
}}
.small {{
    font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.05)}px;font-weight:800;
    color:{brand_color};opacity:0;transform:translateY(15px);
}}
.label {{
    font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.024)}px;color:rgba(255,255,255,0.6);
    text-transform:uppercase;letter-spacing:0.15em;opacity:0;
}}
</style>
</head>
<body data-duration="{duration}">
<div class="big" id="bg">{big_number}</div>
<div class="divider" id="dv"></div>
<div class="small" id="sm">{small_number}</div>
<div class="label" id="lb">{label}</div>
<script>
gsap.to("#bg",{{opacity:1,scale:1,duration:0.4,ease:"back.out(1.7)"}});
gsap.to("#dv",{{opacity:1,scaleX:1,duration:0.35,delay:0.15,ease:"power2.out"}});
gsap.to("#sm",{{opacity:1,y:0,duration:0.3,delay:0.25,ease:"power2.out"}});
gsap.to("#lb",{{opacity:1,duration:0.3,delay:0.35,ease:"power2.out"}});
</script>
</body>
</html>"""

    if graphic_type == "big_number":
        raw = content.get("number", content.get("value", ""))
        try:
            num_val = float(str(raw).replace(",", "").replace("$", "").replace("%", ""))
            number_str = f"{num_val:,.0f}" if num_val == int(num_val) else f"{num_val:,.2f}"
        except (TypeError, ValueError):
            number_str = str(raw)
        prefix = _esc(str(content.get("prefix", "")))
        suffix = _esc(str(content.get("suffix", "")))
        label  = _esc(str(content.get("label", "")).upper())
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
html,body {{ width:{width}px;height:{height}px;overflow:hidden;background:transparent; }}
body {{ display:flex;flex-direction:column;align-items:center;justify-content:center; }}
.number {{
    font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.18)}px;font-weight:900;
    color:#FFFFFF;line-height:1;opacity:0;transform:scale(0.7);
    text-shadow:0 4px 24px rgba(0,0,0,0.8);
}}
.label {{
    font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.03)}px;font-weight:700;
    color:{brand_color};text-transform:uppercase;letter-spacing:0.12em;margin-top:{int(height*0.015)}px;
    opacity:0;
}}
</style>
</head>
<body data-duration="{duration}">
<div class="number" id="n">{prefix}{_esc(number_str)}{suffix}</div>
<div class="label" id="l">{label}</div>
<script>
gsap.to("#n",{{opacity:1,scale:1,duration:0.45,ease:"back.out(1.8)"}});
gsap.to("#l",{{opacity:1,duration:0.3,delay:0.2,ease:"power2.out"}});
</script>
</body>
</html>"""

    if graphic_type == "social_handle":
        handle   = _esc(str(content.get("handle", content.get("text", ""))))
        platform = _esc(str(content.get("platform", "")).upper())
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
html,body {{ width:{width}px;height:{height}px;overflow:hidden;background:transparent; }}
body {{ display:flex;align-items:flex-end;justify-content:flex-start;
       padding:0 {int(width*0.05)}px {int(height*0.08)}px; }}
.bar {{
    display:flex;align-items:center;gap:14px;background:rgba(0,0,0,0.78);
    border-radius:999px;padding:14px 28px;opacity:0;transform:translateX(-30px);
}}
.dot {{
    width:{int(height*0.025)}px;height:{int(height*0.025)}px;border-radius:50%;
    background:{brand_color};
}}
.handle {{
    font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.032)}px;font-weight:800;color:#fff;
}}
.platform {{
    font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.022)}px;font-weight:600;
    color:rgba(255,255,255,0.6);text-transform:uppercase;letter-spacing:0.15em;
}}
</style>
</head>
<body data-duration="{duration}">
<div class="bar" id="b">
    <div class="dot"></div>
    <div class="handle">{handle}</div>
    <div class="platform">{platform}</div>
</div>
<script>gsap.to("#b",{{opacity:1,x:0,duration:0.4,ease:"power2.out"}});</script>
</body>
</html>"""

    # ── Long-form full-screen slide templates ─────────────────────────────────

    if graphic_type == "concept_pill_slide":
        pill_text = _esc(str(content.get("pill_text", "Core Concept")))
        watermark_text = _esc(str(content.get("watermark_text", "")))
        acc = (content.get("accent_color") or brand_color or "#00C3FF").strip()
        if not acc.startswith("#"):
            acc = f"#{acc}"
        pill_top   = int(height * 0.115)
        pill_font  = int(height * 0.043)
        pill_pad_v = int(height * 0.022)
        pill_pad_h = int(width  * 0.027)
        pill_br    = int(height * 0.056)
        wm_size    = int(height * 0.34)
        # arc: quarter-circle from pill bottom-center curving down to the right
        ax0 = int(width * 0.500); ay0 = int(height * 0.175)
        ax1 = int(width * 0.727); ay1 = int(height * 0.574)
        arc_r = int(height * 0.40)
        glow_w = int(width * 0.55); glow_h = int(height * 0.65)
        return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
html,body{{width:{width}px;height:{height}px;overflow:hidden;background:#000000 !important;}}
body::before{{
  content:'';position:absolute;bottom:0;left:0;
  width:{glow_w}px;height:{glow_h}px;
  background:radial-gradient(ellipse at bottom left,rgba(0,110,170,0.38) 0%,rgba(0,60,110,0.15) 40%,transparent 70%);
  pointer-events:none;
}}
.watermark{{
  position:absolute;left:50%;top:50%;transform:translate(-50%,-52%);
  font-family:Georgia,'Times New Roman',serif;
  font-style:italic;font-weight:400;
  font-size:{wm_size}px;
  color:rgba(255,255,255,0.16);
  white-space:nowrap;pointer-events:none;letter-spacing:-0.02em;
}}
.pill{{
  position:absolute;top:{pill_top}px;left:50%;transform:translateX(-50%);
  background:{acc};
  border-radius:{pill_br}px;
  padding:{pill_pad_v}px {pill_pad_h}px;
  box-shadow:
    0 0 14px {acc},
    0 0 30px {acc}CC,
    0 0 60px {acc}88,
    0 0 110px {acc}44;
}}
.pill-text{{
  font-family:'Inter',Arial,sans-serif;font-size:{pill_font}px;font-weight:700;
  color:#FFFFFF;white-space:nowrap;text-align:center;
}}
.arc-svg{{position:absolute;top:0;left:0;width:{width}px;height:{height}px;pointer-events:none;overflow:visible;}}
</style></head>
<body>
<div class="watermark">{watermark_text}</div>
<div class="pill"><div class="pill-text">{pill_text}</div></div>
<svg class="arc-svg" viewBox="0 0 {width} {height}">
  <path d="M {ax0} {ay0} A {arc_r} {arc_r} 0 0 1 {ax1} {ay1}"
        stroke="rgba(255,255,255,0.32)" stroke-width="2.2" fill="none" stroke-linecap="round"/>
</svg>
</body></html>"""

    if graphic_type == "calculation_steps_slide":
        title  = _esc(str(content.get("title", "Calculate Your Rate")))
        steps  = content.get("steps") or []
        acc = (content.get("accent_color") or brand_color or "#00C3FF").strip()
        if not acc.startswith("#"):
            acc = f"#{acc}"

        title_size  = int(height * 0.098)
        step_lbl_sz = int(height * 0.028)
        formula_sz  = int(height * 0.063)
        result_sz   = int(height * 0.074)
        sublbl_sz   = int(height * 0.028)
        card_br     = int(height * 0.016)
        card_pad_v  = int(height * 0.033)
        card_pad_h  = int(width  * 0.037)
        card_maxw   = int(width  * 0.80)
        title_mt    = int(height * 0.040)
        cards_mt    = int(height * 0.026)
        gap         = int(height * 0.030)

        cards_html = ""
        for idx, step in enumerate(steps[:2]):
            lbl     = _esc(str(step.get("label",   f"STEP {idx+1}")))
            formula = _esc(str(step.get("formula",  "")))
            result  = _esc(str(step.get("result",   "")))
            sub_lbl = _esc(str(step.get("sub_label", "")))
            is_final = (idx == len(steps) - 1)
            result_color = acc if is_final else "#FFFFFF"
            result_font  = int(result_sz * 1.10) if is_final else result_sz
            sub_div = (
                f'<div style="font-size:{sublbl_sz}px;color:{acc};'
                f'font-style:italic;margin-top:4px;">{sub_lbl}</div>'
            ) if sub_lbl else ""
            card_bg = "#161622" if idx == 0 else "#131320"
            gap_style = f"margin-top:{gap}px;" if idx > 0 else ""
            cards_html += f"""
<div style="background:{card_bg};border-radius:{card_br}px;
  padding:{card_pad_v}px {card_pad_h}px;
  max-width:{card_maxw}px;margin:0 auto;{gap_style}">
  <div style="font-family:'Inter',Arial,sans-serif;font-size:{step_lbl_sz}px;
    font-weight:800;color:{acc};text-align:center;letter-spacing:0.12em;
    margin-bottom:{int(height*0.018)}px;">{lbl}</div>
  <div style="display:flex;align-items:center;justify-content:center;gap:{int(width*0.018)}px;flex-wrap:wrap;">
    <span style="font-family:'Inter',Arial,sans-serif;font-size:{formula_sz}px;
      font-weight:700;color:#FFFFFF;">{formula}</span>
    <div style="background:#111120;border-radius:{int(height*0.010)}px;
      padding:{int(height*0.012)}px {int(width*0.016)}px;display:flex;flex-direction:column;align-items:center;">
      <span style="font-family:'Inter',Arial,sans-serif;font-size:{result_font}px;
        font-weight:800;color:{result_color};">{result}</span>
      {sub_div}
    </div>
  </div>
</div>"""

        glow_w = int(width * 0.50); glow_h = int(height * 0.60)
        return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
html,body{{width:{width}px;height:{height}px;overflow:hidden;background:#070712 !important;}}
body::before{{
  content:'';position:absolute;bottom:0;left:0;
  width:{glow_w}px;height:{glow_h}px;
  background:radial-gradient(ellipse at bottom left,rgba(0,100,160,0.32) 0%,rgba(0,50,100,0.12) 40%,transparent 70%);
  pointer-events:none;
}}
</style></head>
<body>
<div style="padding:{title_mt}px {int(width*0.04)}px 0;text-align:center;">
  <div style="font-family:'Inter',Arial,sans-serif;font-size:{title_size}px;
    font-weight:900;color:#FFFFFF;line-height:1.05;">{title}</div>
</div>
<div style="margin-top:{cards_mt}px;padding:0 {int(width*0.04)}px;">
  {cards_html}
</div>
</body></html>"""

    if graphic_type == "timeline_arc_slide":
        title  = _esc(str(content.get("title", "Expand Your Time Horizons")))
        points = content.get("points") or [
            {"value": "1/4", "label": ""},
            {"value": "1",   "label": ""},
            {"value": "3",   "label": ""},
            {"value": "10",  "label": ""},
            {"value": "25",  "label": "Write Your 25-Year Vision"},
        ]
        labels = content.get("labels") or ["Write Your 25-Year Vision", "Build a Work-Backwards Plan"]
        acc = (content.get("accent_color") or brand_color or "#00C3FF").strip()
        if not acc.startswith("#"):
            acc = f"#{acc}"

        title_sz  = int(height * 0.082)
        num_sz    = int(height * 0.085)
        arrow_sz  = int(height * 0.055)
        label_sz  = int(height * 0.033)
        title_top = int(height * 0.038)

        # Arc geometry: circle centered at (960, 1418), radius 1018
        # passes through (0,1080) and (1920,1080), peaks at (960, 400)
        cx  = width  // 2
        cy  = int(height * 1.313)   # 1418 for 1080
        r   = int(height * 0.942)   # 1018 for 1080
        # SVG arc: M 0 {height} A {r} {r} 0 0 1 {width} {height}
        # sweep=1 → clockwise on screen → goes UP through peak

        # Tick marks: angle span from ~199° to ~341°, 30 ticks
        # Computed in JS to avoid huge string of numbers
        tick_js = f"""
const cx={cx},cy={cy},r={r};
const aStart=199.4*Math.PI/180, aEnd=340.6*Math.PI/180;
const N=30;
const svg=document.getElementById('arc-svg');
for(let i=0;i<=N;i++){{
  const a=aStart+i*(aEnd-aStart)/N;
  const px=cx+r*Math.cos(a), py=cy+r*Math.sin(a);
  const isMaj=(i%5===0);
  const tlen=isMaj?20:10;
  const tx=px-tlen*Math.cos(a), ty=py-tlen*Math.sin(a);
  const line=document.createElementNS('http://www.w3.org/2000/svg','line');
  line.setAttribute('x1',px.toFixed(1));line.setAttribute('y1',py.toFixed(1));
  line.setAttribute('x2',tx.toFixed(1));line.setAttribute('y2',ty.toFixed(1));
  line.setAttribute('stroke','{acc}');
  line.setAttribute('stroke-width',isMaj?'2.5':'1.5');
  line.setAttribute('opacity',isMaj?'0.9':'0.6');
  svg.appendChild(line);
}}"""

        # Evenly-spaced number positions
        num_xs = [int(width * f) for f in [0.16, 0.30, 0.46, 0.62, 0.78]]
        num_y  = int(height * 0.875)
        nums   = [_esc(str(p.get("value", ""))) for p in points[:5]]

        nums_html = ""
        for k, (nx, nv) in enumerate(zip(num_xs, nums)):
            nums_html += f'<text x="{nx}" y="{num_y}" text-anchor="middle" font-family="Inter,Arial,sans-serif" font-size="{num_sz}" font-weight="900" fill="#FFFFFF">{nv}</text>\n'
            if k < len(nums) - 1:
                arrow_x = (num_xs[k] + num_xs[k+1]) // 2
                nums_html += f'<text x="{arrow_x}" y="{num_y}" text-anchor="middle" font-family="Inter,Arial,sans-serif" font-size="{arrow_sz}" font-weight="700" fill="{acc}">&#8592;</text>\n'

        # Labels positioned above arc
        lbl1_x = int(width * 0.380); lbl1_y = int(height * 0.340)
        lbl2_x = int(width * 0.590); lbl2_y = int(height * 0.310)
        dia_x  = int(width * 0.378); dia_y  = int(height * 0.278)
        dot1_x = int(width * 0.560); dot2_x = int(width * 0.604)
        dot_y  = int(height * 0.228)
        lbl1   = _esc(str(labels[0]) if labels else "Write Your 25-Year Vision")
        lbl2   = _esc(str(labels[1]) if len(labels) > 1 else "Build a Work-Backwards Plan")

        glow_left_h = int(height * 0.70)
        return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
html,body{{width:{width}px;height:{height}px;overflow:hidden;background:#000000 !important;}}
</style></head>
<body>
<svg id="arc-svg" style="position:absolute;top:0;left:0;width:{width}px;height:{height}px;" viewBox="0 0 {width} {height}">
  <!-- bottom glow -->
  <defs>
    <radialGradient id="blglow" cx="0%" cy="100%" r="60%">
      <stop offset="0%" stop-color="{acc}" stop-opacity="0.28"/>
      <stop offset="100%" stop-color="{acc}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="brglow" cx="100%" cy="100%" r="55%">
      <stop offset="0%" stop-color="{acc}" stop-opacity="0.18"/>
      <stop offset="100%" stop-color="{acc}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <ellipse cx="0" cy="{height}" rx="{int(width*0.45)}" ry="{int(height*0.55)}" fill="url(#blglow)"/>
  <ellipse cx="{width}" cy="{height}" rx="{int(width*0.35)}" ry="{int(height*0.45)}" fill="url(#brglow)"/>
  <!-- main arc -->
  <path d="M 0 {height} A {r} {r} 0 0 1 {width} {height}"
        stroke="{acc}" stroke-width="3" fill="none" opacity="0.92"/>
  <!-- tick marks injected by JS -->
  <!-- numbers and arrows -->
  {nums_html}
  <!-- diamond marker at 25-year label position -->
  <ellipse cx="{dia_x}" cy="{dia_y}" rx="26" ry="16"
           fill="none" stroke="{acc}" stroke-width="2.2"/>
  <text x="{dia_x}" y="{dia_y + 5}" text-anchor="middle"
        font-family="Inter,Arial,sans-serif" font-size="16" font-weight="700"
        fill="#FFFFFF">25</text>
  <!-- label 1 -->
  <text x="{lbl1_x}" y="{lbl1_y}" text-anchor="middle"
        font-family="Inter,Arial,sans-serif" font-size="{label_sz}" font-weight="700"
        fill="#FFFFFF">{lbl1}</text>
  <!-- label 2 text (two lines) -->
  <text x="{lbl2_x}" y="{lbl2_y - int(label_sz*0.6)}" text-anchor="middle"
        font-family="Inter,Arial,sans-serif" font-size="{label_sz}" font-weight="700"
        fill="#FFFFFF">Build a</text>
  <text x="{lbl2_x}" y="{lbl2_y + int(label_sz*0.7)}" text-anchor="middle"
        font-family="Inter,Arial,sans-serif" font-size="{label_sz}" font-weight="700"
        fill="#FFFFFF">Work-Backwards Plan</text>
  <!-- two dots with arrow (animation indicator) -->
  <circle cx="{dot1_x}" cy="{dot_y}" r="6" fill="{acc}"/>
  <text x="{int((dot1_x+dot2_x)/2)}" y="{dot_y + 6}" text-anchor="middle"
        font-family="Inter,Arial,sans-serif" font-size="22" fill="{acc}">&#8592;</text>
  <circle cx="{dot2_x}" cy="{dot_y}" r="6" fill="{acc}" opacity="0.55"/>
  <!-- title -->
  <text x="{width//2}" y="{title_top + title_sz}" text-anchor="middle"
        font-family="Inter,Arial,sans-serif" font-size="{title_sz}" font-weight="900"
        fill="#FFFFFF">{title}</text>
</svg>
<script>{tick_js}</script>
</body></html>"""

    # lower_third (and fallback for unknown types) — text/subtext schema
    text = _esc(str(content.get("text", content.get("name", ""))))
    subtext = _esc(str(content.get("subtext", content.get("role", ""))))
    sub_div = f'<div class="subtext">{subtext}</div>' if subtext else ""
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
html,body {{ width:{width}px;height:{height}px;overflow:hidden;background:transparent !important; }}
.bar {{
    position:absolute;left:0;bottom:0;width:100%;height:20%;
    background:{brand_color}D9;
    display:flex;flex-direction:column;align-items:center;justify-content:center;
    text-align:center;
}}
.text {{
    font-family:'Inter',sans-serif;font-size:{int(height*0.05)}px;font-weight:800;color:#FFFFFF;
}}
.subtext {{
    font-family:'Inter',sans-serif;font-size:{int(height*0.03)}px;color:rgba(255,255,255,0.7);
    margin-top:{int(height*0.01)}px;
}}
</style>
</head>
<body>
<div class="bar"><div class="text">{text}</div>{sub_div}</div>
</body>
</html>"""


# ── AI-generated motion graphics ──────────────────────────────────────────────

_MG_SYSTEM_PROMPT = """\
You are a motion graphics engineer. You generate a SINGLE self-contained HTML
file that renders an animated composition using GSAP 3.

HARD TECHNICAL CONSTRAINTS — violating ANY of these makes the output unusable:

1. The file must be a complete <!DOCTYPE html> document.
2. GSAP is already loaded — a <script> tag with the full GSAP library precedes
   your code. Do NOT add any external <script src="..."> tags.
3. Set html,body to: width:{width}px; height:{height}px; overflow:hidden;
   background:transparent !important;
4. Register a PAUSED GSAP timeline on window.__timelines:
     window.__timelines = window.__timelines || {{}};
     const tl = gsap.timeline({{ paused: true }});
     // ... all your tweens on tl ...
     window.__timelines["root"] = tl;
5. ALL animation MUST be on the tl timeline (no standalone gsap.to() calls).
   The capture engine calls tl.seek(t) for each frame.
6. Entrance animation: 0.3–0.6s starting at t=0.1. Elements start invisible
   and animate in. CRITICAL: Always use tl.fromTo() — never tl.from().
   tl.from() reads the CSS value as end-state which breaks when CSS has
   opacity:0. fromTo() explicitly sets BOTH start and end values:
     tl.fromTo("#el", {{ opacity:0, y:30 }}, {{ opacity:1, y:0, duration:0.4 }}, 0.1);
7. Hold: elements stay fully visible and static for the middle of the duration.
8. Exit animation: 0.2–0.3s ending at t=DURATION. Elements fade out.
   Use: tl.to(selector, {{ opacity:0, duration:0.25 }}, DURATION - 0.25);
9a. When computing dynamic dimensions (height, width) ensure values are
   always POSITIVE. If using low/high or min/max, verify the subtraction
   order: height = Math.abs(top - bottom).
9. Font: 'Inter', Arial, sans-serif. Font-weight 700–900 for headlines.
10. Color palette: white (#FFFFFF) text, {accent_color} for accent elements,
    dark cards use rgba(0,0,0,0.85) or #1a1a1a. NO green (#00FF00).
11. No external assets (images, fonts, audio). SVG inline is fine.
12. No Math.random(), Date.now(), setTimeout, setInterval, fetch, or async code.
13. GSAP onUpdate callbacks DO NOT fire during tl.seek(). For animated counters,
    use CSS custom properties: set --val via gsap, then in an __afterSeek hook
    read getComputedStyle and update textContent.
    Pattern for animated numbers:
      el.style.setProperty('--val', 0);
      tl.to(el, {{ '--val': TARGET, duration: D, ease: 'power2.out' }}, T);
      window.__afterSeek = function() {{
        document.querySelectorAll('[data-counter]').forEach(function(e) {{
          var v = parseFloat(getComputedStyle(e).getPropertyValue('--val')) || 0;
          e.textContent = e.dataset.prefix + Math.round(v) + e.dataset.suffix;
        }});
      }};

DURATION = {duration} seconds.

Reply with ONLY the HTML code — no explanation, no markdown fencing.
"""

_MG_EXAMPLE_STAT = """
EXAMPLE — a stat card that scales in with a glow:
<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
*{margin:0;padding:0;box-sizing:border-box;}
html,body{width:1080px;height:1920px;overflow:hidden;background:transparent !important;}
body{display:flex;align-items:center;justify-content:center;}
.card{background:rgba(0,0,0,0.85);border-radius:24px;padding:40px 56px;
  text-align:center;border:1px solid rgba(255,255,255,0.1);opacity:0;transform:scale(0.8);}
.number{font-family:Inter,sans-serif;font-size:384px;font-weight:900;color:#FF7751;
  line-height:1;text-shadow:0 0 40px #FF775180;}
.label{font-family:Inter,sans-serif;font-size:67px;font-weight:700;color:#fff;
  text-transform:uppercase;letter-spacing:0.1em;margin-top:12px;}
</style></head>
<body>
<div class="card" id="c">
  <div class="number">73%</div>
  <div class="label">COMPLETION RATE</div>
</div>
<script>
window.__timelines = window.__timelines || {};
const tl = gsap.timeline({ paused: true });
tl.to("#c", { opacity:1, scale:1, duration:0.4, ease:"back.out(1.7)" }, 0.1);
tl.to("#c", { opacity:0, scale:0.9, duration:0.25, ease:"power2.in" }, 2.75);
window.__timelines["root"] = tl;
</script></body></html>
"""

_MG_EXAMPLE_CHECKLIST = """
EXAMPLE — a checklist with staggered reveals:
<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
*{margin:0;padding:0;box-sizing:border-box;}
html,body{width:1080px;height:1920px;overflow:hidden;background:transparent !important;}
body{display:flex;align-items:center;padding-left:65px;}
.card{background:rgba(0,0,0,0.82);border-radius:20px;padding:28px 36px;
  border-left:4px solid #FF7751;max-width:454px;}
.title{font-family:Inter,sans-serif;font-size:58px;color:#FF7751;font-weight:700;
  text-transform:uppercase;letter-spacing:0.1em;margin-bottom:16px;}
.item{font-family:Inter,sans-serif;font-size:48px;color:#fff;margin:10px 0;
  display:flex;gap:10px;align-items:center;opacity:0;transform:translateX(-20px);}
.dot{font-size:1.2em;color:#FF7751;}
</style></head>
<body>
<div class="card">
  <div class="title">KEY STEPS</div>
  <div class="item" id="i0"><span class="dot">▸</span>First item</div>
  <div class="item" id="i1"><span class="dot">▸</span>Second item</div>
  <div class="item" id="i2"><span class="dot">▸</span>Third item</div>
</div>
<script>
window.__timelines = window.__timelines || {};
const tl = gsap.timeline({ paused: true });
tl.to("#i0", {opacity:1,x:0,duration:0.3,ease:"power2.out"}, 0.1);
tl.to("#i1", {opacity:1,x:0,duration:0.3,ease:"power2.out"}, 0.35);
tl.to("#i2", {opacity:1,x:0,duration:0.3,ease:"power2.out"}, 0.60);
tl.to(["#i0","#i1","#i2"], {opacity:0,duration:0.25,ease:"power2.in"}, 2.75);
window.__timelines["root"] = tl;
</script></body></html>
"""


_TEMPLATE_KEYWORDS: dict[str, list[str]] = {
    "stat_card":    ["stat", "number", "percentage", "count", "metric"],
    "big_number":   ["big number", "counter", "score", "amount", "value"],
    "key_phrase":   ["phrase", "quote", "statement", "callout", "one-liner"],
    "checklist":    ["list", "checklist", "steps", "items", "features", "benefits"],
    "chapter_marker": ["chapter", "section", "marker", "label", "tag"],
    "kinetic_title": ["title", "headline", "heading", "intro"],
    "stat":         ["chart", "comparison", "growth", "data"],
    "scoreboard_stat": ["versus", "vs", "scoreboard", "ranking"],
    "social_handle": ["social", "handle", "follow", "username", "instagram"],
    "concept_pill_slide": ["concept", "idea", "principle", "framework"],
    "calculation_steps_slide": ["calculate", "formula", "math", "equation", "step-by-step"],
    "timeline_arc_slide": ["timeline", "phases", "progression", "sequence", "arc"],
}


def _match_fallback_template(concept: str) -> str:
    """Pick the closest existing template type by keyword overlap."""
    concept_lower = concept.lower()
    best, best_score = "stat_card", 0
    for tpl, keywords in _TEMPLATE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in concept_lower)
        if score > best_score:
            best, best_score = tpl, score
    return best


def _call_generation_api(
    system: str, user_msg: str, *, error_context: str | None = None,
) -> str | None:
    """Single API call to Claude for HTML generation. Returns raw text."""
    from anthropic import Anthropic
    from app.core.config import settings

    messages = [{"role": "user", "content": user_msg}]
    if error_context:
        messages = [
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": "(previous attempt produced an error)"},
            {"role": "user", "content": (
                f"The previous HTML had this problem:\n{error_context}\n\n"
                "Fix the issue and regenerate the FULL HTML. Reply with ONLY HTML."
            )},
        ]
    try:
        client = Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=16384,
            system=system,
            messages=messages,
        )
        text = resp.content[0].text.strip()
        if resp.stop_reason == "max_tokens":
            print("[MG-GEN] Response truncated (hit max_tokens)")
            return None
        return text
    except Exception as e:
        print(f"[MG-GEN] API call failed: {e}")
        return None


def _postprocess_generated_html(raw: str) -> str | None:
    """Strip markdown fencing, inject local GSAP, validate __timelines."""
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:])
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3].rstrip()

    gsap_js = _gsap_inline()
    if not gsap_js:
        return raw if "window.__timelines" in raw else None

    gsap_tag = f"<script>{gsap_js}</script>"

    # Strategy: always inject GSAP as a NEW script block before </head>.
    # Remove any CDN <script src="...gsap..."> tags Claude may have added.
    cdn_pattern = re.compile(r'<script\s+src="[^"]*gsap[^"]*"\s*>\s*</script>')
    raw = cdn_pattern.sub("", raw)

    if "</head>" in raw:
        raw = raw.replace("</head>", f"{gsap_tag}\n</head>", 1)
    else:
        raw = f"{gsap_tag}\n{raw}"

    if "window.__timelines" not in raw:
        return None
    return raw


def _validate_render(html: str, width: int, height: int, work_dir: Path) -> str | None:
    """Fast 1-second test render. Returns None on success, error string on failure."""
    work_dir.mkdir(parents=True, exist_ok=True)
    html_path = work_dir / "_validate.html"
    mp4_path = work_dir / "_validate.mp4"
    html_path.write_text(html, encoding="utf-8")
    try:
        script = Path(__file__).parent / "hf_render.mjs"
        env = os.environ.copy()
        env.setdefault("DISPLAY", ":99")
        r = subprocess.run(
            ["node", str(script), str(html_path), str(mp4_path),
             "1.0", str(width), str(height), "5"],
            capture_output=True, text=True, timeout=60, env=env,
        )
        if r.returncode != 0:
            return f"hf_render exit code {r.returncode}: {r.stderr[:300]}"
        if not mp4_path.exists() or mp4_path.stat().st_size < 500:
            return "Output file missing or too small"
        return None
    except subprocess.TimeoutExpired:
        return "Render timed out after 60s"
    except Exception as e:
        return str(e)
    finally:
        html_path.unlink(missing_ok=True)
        mp4_path.unlink(missing_ok=True)


def generate_custom_motion_graphic(
    concept_description: str,
    content: dict,
    duration: float,
    width: int,
    height: int,
    accent_color: str = "#FF7751",
    work_dir: Path | None = None,
) -> tuple[str | None, str]:
    """Generate a custom HTML/GSAP motion graphic with validation and fallback.

    Returns (html, path_used) where path_used is "generated", "retry", or
    "fallback:<template_type>".
    """
    import json as _json
    from app.core.config import settings

    if not settings.anthropic_api_key:
        print("[MG-GEN] No API key — using fallback template")
        fallback = _match_fallback_template(concept_description)
        return None, f"fallback:{fallback}"

    acc = accent_color.strip()
    if not acc.startswith("#"):
        acc = f"#{acc}"

    system = _MG_SYSTEM_PROMPT.format(
        width=width, height=height, duration=duration, accent_color=acc,
    )

    content_json = ""
    if content:
        content_json = f"\n\nContent data to incorporate:\n{_json.dumps(content, indent=2)}"

    user_msg = (
        f"Create a motion graphic for: {concept_description}\n"
        f"Canvas: {width}x{height}, duration: {duration}s, accent: {acc}"
        f"{content_json}\n\n"
        f"Here are two PATTERN EXAMPLES showing the exact technical structure "
        f"(window.__timelines registration, GSAP paused timeline, CSS styling). "
        f"Do NOT copy their visual design — create something ORIGINAL and SPECIFIC "
        f"to the concept described above.\n"
        f"{_MG_EXAMPLE_STAT}\n{_MG_EXAMPLE_CHECKLIST}"
    )

    _work = work_dir or Path(__file__).parent / "_mg_validate"

    # ── Attempt 1 ────────────────────────────────────────────────────────
    raw = _call_generation_api(system, user_msg)
    if raw:
        html = _postprocess_generated_html(raw)
        if html:
            err = _validate_render(html, width, height, _work)
            if err is None:
                print(f"[MG-GEN] Generated OK (attempt 1)")
                return html, "generated"
            print(f"[MG-GEN] Validation failed (attempt 1): {err}")

            # ── Attempt 2 — retry with error context ─────────────────────
            raw2 = _call_generation_api(system, user_msg, error_context=err)
            if raw2:
                html2 = _postprocess_generated_html(raw2)
                if html2:
                    err2 = _validate_render(html2, width, height, _work)
                    if err2 is None:
                        print(f"[MG-GEN] Generated OK (attempt 2 — retry)")
                        return html2, "retry"
                    print(f"[MG-GEN] Validation failed (attempt 2): {err2}")
        else:
            print("[MG-GEN] Post-processing failed (no __timelines)")

    # ── Fallback to closest template ─────────────────────────────────────
    fallback = _match_fallback_template(concept_description)
    print(f"[MG-GEN] Falling back to template: {fallback}")
    return None, f"fallback:{fallback}"


# ── Rendering ─────────────────────────────────────────────────────────────────


def _chroma_keyed_html(html_content: str) -> str:
    """Force the page background to the chroma-key color (overrides `background:transparent`)."""
    return html_content.replace(
        "<style>",
        f"<style>html,body{{background:#{CHROMA_KEY_HEX} !important;}}",
        1,
    )


def _render_with_puppeteer(
    html_content: str,
    output_path: Path,
    duration: float,
    w: int,
    h: int,
    fps: int,
    work_dir: Path,
) -> bool:
    """Render via the hf_render.mjs Puppeteer script under Xvfb (Railway)."""
    script_path = Path(__file__).parent / "hf_render.mjs"
    if not script_path.exists():
        return False

    html_path = work_dir / f"{Path(output_path).stem}_comp.html"
    html_path.write_text(html_content, encoding="utf-8")

    # Try with Xvfb display
    env = os.environ.copy()
    env["DISPLAY"] = ":99"

    _timeout = max(180, int(duration * 20))
    try:
        result = subprocess.run(
            [
                "node", str(script_path),
                str(html_path),
                str(output_path),
                str(duration),
                str(w),
                str(h),
                str(fps),
            ],
            capture_output=True, text=True, timeout=_timeout, env=env,
        )
    except Exception as e:
        html_path.unlink(missing_ok=True)
        print(f"[HF] Puppeteer error: {e}")
        return False

    html_path.unlink(missing_ok=True)

    if result.returncode == 0 and Path(output_path).exists() and Path(output_path).stat().st_size > 0:
        print(f"[HF] Puppeteer rendered: {output_path}")
        return True
    else:
        print(f"[HF] Puppeteer failed: {result.stderr[:200]}")
        return False


def render_composition_to_video(
    html_content: str,
    output_path: Path,
    duration: float,
    width: int,
    height: int,
    fps: int = 30,
    work_dir: Path | None = None,
) -> bool:
    """Render an HTML composition to a chroma-keyed MP4 clip.

    Priority order:
      1. Chromium single-screenshot → FFmpeg video (settled animation state)
      2. Puppeteer + Xvfb (real browser, real GSAP animations)
      3. FFmpeg drawtext fallback (no browser required)

    The HyperFrames CLI path is intentionally skipped here — `npx hyperframes`
    needs Node + a display and reliably fails on headless hosts (Railway).
    All remaining paths render against CHROMA_KEY_HEX so render.py's overlay
    pass can `colorkey` it out for real per-pixel transparency.

    Returns True when a usable clip was produced at output_path.
    """
    output_path = Path(output_path)
    if work_dir is None:
        work_dir = output_path.parent
    work_dir.mkdir(parents=True, exist_ok=True)

    keyed_html = _chroma_keyed_html(html_content)
    html_path = work_dir / f"{output_path.stem}_comp.html"
    html_path.write_text(keyed_html, encoding="utf-8")

    # ── Chromium screenshot → static video ───────────────────────────────
    chromium = _find(_CHROMIUM_CANDIDATES)
    if chromium:
        png_path = work_dir / f"{output_path.stem}_comp.png"
        try:
            vt_budget_ms = max(1000, int(duration * 1000))
            r = subprocess.run(
                [
                    chromium,
                    "--headless=new",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    f"--window-size={width},{height}",
                    "--force-device-scale-factor=1",
                    f"--virtual-time-budget={vt_budget_ms}",
                    f"--screenshot={png_path}",
                    f"file://{html_path.resolve()}",
                ],
                capture_output=True, timeout=30,
            )
            if r.returncode == 0 and png_path.exists() and png_path.stat().st_size > 0:
                subprocess.run(
                    [
                        FFMPEG_PATH, "-y", "-loglevel", "error",
                        "-loop", "1", "-i", str(png_path),
                        "-t", f"{duration:.3f}",
                        "-vf", f"scale={width}:{height},setsar=1:1,fps={fps}",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
                        "-pix_fmt", "yuv420p", "-an",
                        str(output_path),
                    ],
                    check=True, timeout=60,
                )
                png_path.unlink(missing_ok=True)
                html_path.unlink(missing_ok=True)
                print(f"[HYPERFRAMES] Chrome screenshot render OK: {output_path.name}")
                return True
            print(f"[HYPERFRAMES] Chrome screenshot failed (rc={r.returncode}): {r.stderr[-200:]}")
        except Exception as e:
            print(f"[HYPERFRAMES] Chrome path error: {e}")
        finally:
            png_path.unlink(missing_ok=True)
    else:
        print("[HYPERFRAMES] No chromium binary found")

    # ── Puppeteer + Xvfb (real browser, real GSAP animations) ────────────
    try:
        subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1920x1080x24"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import time as _time
        _time.sleep(1)
    except Exception:
        pass

    if _render_with_puppeteer(keyed_html, output_path, duration, width, height, fps, work_dir):
        html_path.unlink(missing_ok=True)
        return True

    # ── FFmpeg drawtext fallback — no browser required ───────────────────
    html_path.unlink(missing_ok=True)
    return _ffmpeg_text_fallback(html_content, output_path, duration, width, height, fps)


def render_slide_to_video(
    html_content: str,
    output_path: Path,
    duration: float,
    width: int,
    height: int,
    fps: int = 30,
    work_dir: Path | None = None,
) -> bool:
    """Render a full-screen slide HTML to MP4 via hf_render.mjs frame-by-frame.

    Uses the same Puppeteer/hf_render.mjs pipeline as overlay motion graphics
    so animated GSAP timelines play correctly. No chroma key — slides replace
    speaker footage entirely with a black background.
    """
    output_path = Path(output_path)
    if work_dir is None:
        work_dir = output_path.parent
    work_dir.mkdir(parents=True, exist_ok=True)

    # ── Primary: hf_render.mjs frame-by-frame (validated pipeline) ───────
    if _render_with_puppeteer(html_content, output_path, duration, width, height, fps, work_dir):
        print(f"[SLIDE] hf_render.mjs render OK: {output_path.name}")
        return True

    # ── Fallback: Chrome static screenshot ───────────────────────────────
    _WIN_CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    _chromium_candidates = _CHROMIUM_CANDIDATES + [_WIN_CHROME]
    chromium = _find(_chromium_candidates)
    if chromium:
        html_path = work_dir / f"{output_path.stem}_slide.html"
        html_path.write_text(html_content, encoding="utf-8")
        png_path = work_dir / f"{output_path.stem}_slide.png"
        try:
            vt_budget_ms = max(1500, int(duration * 1000))
            r = subprocess.run(
                [
                    chromium,
                    "--headless=new",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    f"--window-size={width},{height}",
                    "--force-device-scale-factor=1",
                    f"--virtual-time-budget={vt_budget_ms}",
                    f"--screenshot={png_path}",
                    f"file://{html_path.resolve()}",
                ],
                capture_output=True, timeout=30,
            )
            if r.returncode == 0 and png_path.exists() and png_path.stat().st_size > 0:
                subprocess.run(
                    [
                        FFMPEG_PATH, "-y", "-loglevel", "error",
                        "-loop", "1", "-i", str(png_path),
                        "-t", f"{duration:.3f}",
                        "-vf", f"scale={width}:{height},setsar=1:1,fps={fps}",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                        "-pix_fmt", "yuv420p", "-an",
                        str(output_path),
                    ],
                    check=True, timeout=60,
                )
                png_path.unlink(missing_ok=True)
                html_path.unlink(missing_ok=True)
                print(f"[SLIDE] Chrome screenshot fallback OK: {output_path.name}")
                return True
            print(f"[SLIDE] Chrome screenshot failed (rc={r.returncode}): {r.stderr[-200:]}")
        except Exception as e:
            print(f"[SLIDE] Chrome fallback error: {e}")
        finally:
            png_path.unlink(missing_ok=True)
            html_path.unlink(missing_ok=True)

    print(f"[SLIDE] All render paths failed for {output_path.name}")
    return False


def _dt_escape(text: str) -> str:
    """Escape text for use inside an ffmpeg drawtext filter argument."""
    return (
        text.replace("\\", "\\\\")
            .replace(":", "\\:")
            .replace("'", "’")  # drawtext can't escape single quotes inside '...'
            .replace("%", "pct")  # drawtext's '%' expansion syntax can't be escaped reliably
    )


def _ffmpeg_text_fallback(
    html_content: str,
    output_path: Path,
    duration: float,
    width: int,
    height: int,
    fps: int,
) -> bool:
    """Render a chroma-keyed text card via pure FFmpeg — no browser required.

    Last-resort path when no Chromium binary is available. Extracts the
    first headline text from the composition and draws it over
    CHROMA_KEY_HEX so render.py's overlay colorkey filter still produces a
    floating text card rather than a solid block.
    """
    texts = re.findall(
        r'class="[^"]*\b(?:hf-text|title|number|big|large|text|handle)\b[^"]*"[^>]*>([^<]+)<',
        html_content,
    )
    text = html.unescape(texts[0]).strip()[:40] if texts else ""

    vf = ["format=yuv420p"]
    if text:
        vf.append(
            f"drawtext=text='{_dt_escape(text)}':fontsize={int(height*0.07)}:fontcolor=white:"
            f"x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.6:boxborderw=24"
        )

    try:
        subprocess.run(
            [
                FFMPEG_PATH, "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", f"color=c=0x{CHROMA_KEY_HEX}:size={width}x{height}:rate={fps}",
                "-t", f"{duration:.3f}",
                "-vf", ",".join(vf),
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
                "-pix_fmt", "yuv420p", "-an",
                str(output_path),
            ],
            check=True, timeout=30,
        )
        print(f"[HYPERFRAMES] ffmpeg text fallback: {output_path.name} text={text!r}")
        return True
    except Exception as e:
        print(f"[HYPERFRAMES] ffmpeg text fallback failed: {e}")
        return False
