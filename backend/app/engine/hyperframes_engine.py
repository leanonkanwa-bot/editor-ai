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
        "text": "#FFFFFF", "accent": brand_color or "#CCFF00", "bg": "#000000",
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

    text_color = cfg["text_color"] or pal["text"]
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
        text = _esc(str(content.get("text", "")).upper())
        subtext = _esc(str(content.get("subtext", "")))
        pal = _style_palette(str(content.get("style", "momentum")), brand_color)
        sub_div = f'<div class="subtitle" id="st">{subtext}</div>' if subtext else ""
        sub_js = (
            f'gsap.to("#st",{{opacity:1,y:0,duration:0.3,delay:0.15,ease:"power2.out"}});\n'
            f'gsap.to("#st",{{opacity:0,duration:0.2,ease:"power2.in"}},"{duration:.3f}-0.2");'
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
    display:flex;flex-direction:column;align-items:center;justify-content:center;
    gap:{int(height*0.025)}px;
}}
.title {{
    font-family:'{pal["font"]}',sans-serif;font-size:{int(height*0.11)}px;font-weight:{pal["weight"]};
    color:{pal["text"]};text-transform:{pal["transform"]};text-align:center;line-height:1.05;
    max-width:{int(width*0.86)}px;opacity:0;transform:scale(0.9);
    text-shadow:0 4px 24px rgba(0,0,0,0.6);
}}
.subtitle {{
    font-family:'{pal["font"]}',sans-serif;font-size:{int(height*0.04)}px;font-weight:700;
    color:{pal["accent"]};text-transform:{pal["transform"]};text-align:center;
    letter-spacing:0.06em;opacity:0;transform:translateY(16px);
}}
</style>
</head>
<body data-duration="{duration:.3f}">
<div class="title" id="t">{text}</div>
{sub_div}
<script>
gsap.timeline()
  .to("#t",{{opacity:1,scale:1,duration:0.35,ease:"back.out(1.7)"}})
  .to("#t",{{opacity:0,duration:0.2,ease:"power2.in"}},"{duration:.3f}-0.2");
{sub_js}
</script>
</body>
</html>"""

    if graphic_type == "stat_card":
        text = _esc(str(content.get("text", "")))
        subtext = _esc(str(content.get("subtext", "")).upper())
        pal = _style_palette(str(content.get("style", "momentum")), brand_color)
        sub_div = f'<div class="label" id="lb">{subtext}</div>' if subtext else ""
        sub_js = (
            'gsap.to("#lb",{opacity:1,y:0,duration:0.3,delay:0.2,ease:"power2.out"});'
            if subtext else ""
        )
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
body {{ width:{width}px;height:{height}px;background:transparent;overflow:hidden;
        display:flex;align-items:center;justify-content:center; }}
.card {{
    background:{pal["bg"]}E6;border-radius:28px;padding:{int(height*0.05)}px {int(width*0.07)}px;
    text-align:center;border:2px solid {pal["accent"]}55;
    opacity:0;transform:scale(0.8);
}}
.number {{
    font-family:'{pal["font"]}',sans-serif;font-size:{int(height*0.18)}px;font-weight:{pal["weight"]};
    color:{pal["accent"]};line-height:1;text-shadow:0 0 40px {pal["accent"]}66;
}}
.label {{
    font-family:'{pal["font"]}',sans-serif;font-size:{int(height*0.035)}px;font-weight:700;
    color:{pal["text"]};text-transform:{pal["transform"]};letter-spacing:0.1em;
    margin-top:{int(height*0.015)}px;opacity:0;transform:translateY(10px);
}}
</style>
</head>
<body data-duration="{duration:.3f}">
<div class="card" id="c">
    <div class="number">{text}</div>
    {sub_div}
</div>
<script>
gsap.timeline()
  .to("#c",{{opacity:1,scale:1,duration:0.4,ease:"back.out(1.7)"}})
  .to("#c",{{opacity:0,duration:0.2,ease:"power2.in"}},"{duration:.3f}-0.2");
{sub_js}
</script>
</body>
</html>"""

    if graphic_type == "title_card":
        text = _esc(str(content.get("text", "")).upper())
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
html,body {{ width:{width}px;height:{height}px;overflow:hidden;background:#2B080C; }}
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
gsap.fromTo("#t",{{opacity:0,scale:0.95}},{{opacity:1,scale:1,duration:0.4,ease:"power2.out"}});
gsap.to("#t",{{scale:1.05,duration:{duration:.2f},ease:"power1.inOut"}});
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
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
html,body {{ width:{width}px;height:{height}px;overflow:hidden;background:transparent; }}
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
<script>gsap.to("#c",{{opacity:1,scale:1,duration:0.4,ease:"back.out(1.7)"}});</script>
</body>
</html>"""

    if graphic_type == "key_phrase":
        small = _esc(str(content.get("context", "")))
        large = _esc(str(content.get("phrase", "")))
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
html,body {{ width:{width}px;height:{height}px;overflow:hidden;background:transparent; }}
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
          font-weight:800;color:{brand_color};text-align:center;line-height:1.1;
          opacity:0;transform:translateY(20px);
          text-shadow:0 2px 30px rgba(0,0,0,0.8); }}
</style>
</head>
<body data-duration="{duration}">
<div class="bg"></div>
<div class="accent" id="a"></div>
<div class="small" id="s">{small}</div>
<div class="large" id="l">{large}</div>
<script>
gsap.to("#a",{{opacity:1,scaleX:1,duration:0.25,ease:"power2.out"}});
gsap.to("#s",{{opacity:1,duration:0.3,delay:0.1,ease:"power2.out"}});
gsap.to("#l",{{opacity:1,y:0,duration:0.4,delay:0.15,ease:"back.out(1.4)"}});
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
        anims = "".join(
            f'gsap.to("#i{j}",{{opacity:1,x:0,duration:0.3,delay:{j*0.25:.2f},ease:"power2.out"}});'
            for j in range(len(items))
        )
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
html,body {{ width:{width}px;height:{height}px;overflow:hidden;background:transparent; }}
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
<div class="card"><div class="title">{title}</div>{items_html}</div>
<script>{anims}</script>
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
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
html,body {{ width:{width}px;height:{height}px;overflow:hidden;background:transparent; }}
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
gsap.fromTo("#b",{{x:-{width*1.2},rotation:-4}},{{x:0,rotation:-4,duration:0.5,ease:"power3.out"}});
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
            f'gsap.to("#d",{{opacity:1,y:0,duration:0.35,delay:0.25,ease:"power2.out"}});\n'
            f'gsap.to("#d",{{opacity:0,duration:0.2,ease:"power2.in"}},"{duration:.3f}-0.2");'
            if subtext else ""
        )
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
body {{ width:{width}px;height:{height}px;background:transparent;overflow:hidden;
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
gsap.timeline()
  .to("#b",{{opacity:1,scale:1,duration:0.4,ease:"back.out(1.7)"}})
  .to("#t",{{opacity:1,y:0,duration:0.35,ease:"power2.out"}},"-=0.15")
  .to(["#b","#t"],{{opacity:0,duration:0.2,ease:"power2.in"}},"{duration:.3f}-0.2");
{sub_js}
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
    color:{brand_color};line-height:1;opacity:0;transform:scale(0.7);
    text-shadow:0 0 50px {brand_color}80;
}}
.label {{
    font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.03)}px;font-weight:700;
    color:#fff;text-transform:uppercase;letter-spacing:0.12em;margin-top:{int(height*0.015)}px;
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

    # lower_third (and fallback for unknown types) — text/subtext schema
    text = _esc(str(content.get("text", content.get("name", ""))))
    subtext = _esc(str(content.get("subtext", content.get("role", ""))))
    pal = _style_palette(str(content.get("style", "momentum")), brand_color)
    sub_div = f'<div class="subtext" id="sub">{subtext}</div>' if subtext else ""
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
body {{ width:{width}px;height:{height}px;background:transparent;overflow:hidden;
        display:flex;align-items:flex-end;padding:0 {int(width*0.05)}px {int(height*0.10)}px; }}
.lower {{
    background:{pal["bg"]}E6;border-left:6px solid {pal["accent"]};
    padding:{int(height*0.018)}px {int(width*0.025)}px;border-radius:4px 10px 10px 4px;
    opacity:0;transform:translateX(-30px);
}}
.text {{
    font-family:'{pal["font"]}',sans-serif;font-size:{int(height*0.04)}px;font-weight:{pal["weight"]};
    color:{pal["text"]};text-transform:{pal["transform"]};
}}
.subtext {{
    font-family:'{pal["font"]}',sans-serif;font-size:{int(height*0.025)}px;font-weight:600;
    color:{pal["accent"]};margin-top:4px;
}}
</style>
</head>
<body data-duration="{duration:.3f}">
<div class="lower" id="lt"><div class="text">{text}</div>{sub_div}</div>
<script>
gsap.timeline()
  .to("#lt",{{opacity:1,x:0,duration:0.35,ease:"power2.out"}})
  .to("#lt",{{opacity:0,x:-30,duration:0.2,ease:"power2.in"}},"{duration:.3f}-0.2");
</script>
</body>
</html>"""


# ── Rendering ─────────────────────────────────────────────────────────────────


def _chroma_keyed_html(html_content: str) -> str:
    """Force the page background to the chroma-key color (overrides `background:transparent`)."""
    return html_content.replace(
        "<style>",
        f"<style>html,body{{background:#{CHROMA_KEY_HEX} !important;}}",
        1,
    )


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
      2. FFmpeg drawtext fallback (no browser required)

    The HyperFrames CLI path is intentionally skipped here — `npx hyperframes`
    needs Node + a display and reliably fails on headless hosts (Railway).
    Both remaining paths render against CHROMA_KEY_HEX so render.py's overlay
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
        print("[HYPERFRAMES] No chromium binary found — using ffmpeg text fallback")

    # ── FFmpeg drawtext fallback — no browser required ───────────────────
    html_path.unlink(missing_ok=True)
    return _ffmpeg_text_fallback(html_content, output_path, duration, width, height, fps)


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
