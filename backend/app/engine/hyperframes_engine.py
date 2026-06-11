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

import os
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

# ─────────────────────────────────────────────────────────────────────────────


def _find(candidates: list[str]) -> str | None:
    return next((p for p in candidates if p and os.path.exists(p)), None)


def is_hyperframes_available() -> bool:
    npx = _find(_NPX_BINS)
    if not npx:
        return False
    try:
        r = subprocess.run(
            [npx, "hyperframes", "--version"],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


# ── HTML generation (GSAP compositions) ──────────────────────────────────────

def _esc(t: str) -> str:
    return (
        t.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


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
        step  = _esc(str(content.get("step", "01")))
        title = _esc(str(content.get("title", content.get("text", ""))).upper())
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
html,body {{ width:{width}px;height:{height}px;overflow:hidden;background:#0D0D0D; }}
body {{ display:flex;flex-direction:column;align-items:center;justify-content:center;gap:{int(height*0.04)}px; }}
.step {{
    font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.05)}px;font-weight:900;
    color:{brand_color};letter-spacing:0.3em;opacity:0;transform:translateY(-20px);
}}
.icon {{
    width:{int(height*0.24)}px;height:{int(height*0.24)}px;border-radius:50%;
    border:4px solid {brand_color};display:flex;align-items:center;justify-content:center;
    opacity:0;transform:scale(0.6);
}}
.icon::before {{
    content:'';width:46%;height:46%;border-radius:50%;background:{brand_color};
    box-shadow:0 0 30px {brand_color}99;
}}
.title {{
    font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.045)}px;font-weight:800;
    color:#fff;text-align:center;max-width:{int(width*0.8)}px;opacity:0;transform:translateY(20px);
}}
</style>
</head>
<body data-duration="{duration}">
<div class="step" id="s">STEP {step}</div>
<div class="icon" id="i"></div>
<div class="title" id="t">{title}</div>
<script>
gsap.to("#s",{{opacity:1,y:0,duration:0.35,ease:"power2.out"}});
gsap.to("#i",{{opacity:1,scale:1,duration:0.45,delay:0.1,ease:"back.out(1.6)"}});
gsap.to("#t",{{opacity:1,y:0,duration:0.4,delay:0.2,ease:"power2.out"}});
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

    # Transparent fallback (lower_third or unknown type)
    name = _esc(str(content.get("name", content.get("text", ""))))
    role = _esc(str(content.get("role", content.get("label", ""))))
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
html,body {{ width:{width}px;height:{height}px;overflow:hidden;background:transparent; }}
body {{ display:flex;align-items:flex-end;padding:0 {int(width*0.05)}px {int(height*0.10)}px; }}
.lower {{ background:rgba(0,0,0,0.80);border-left:4px solid {brand_color};
          padding:12px 20px;border-radius:4px 8px 8px 4px;opacity:0; }}
.name {{ font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.04)}px;font-weight:700;color:#fff; }}
.role {{ font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.025)}px;color:{brand_color};margin-top:4px; }}
</style>
</head>
<body data-duration="{duration}">
<div class="lower" id="lt"><div class="name">{name}</div><div class="role">{role}</div></div>
<script>gsap.to("#lt",{{opacity:1,y:0,duration:0.35,ease:"power2.out"}});</script>
</body>
</html>"""


# ── Rendering ─────────────────────────────────────────────────────────────────


def render_composition_to_video(
    html_content: str,
    output_path: Path,
    duration: float,
    width: int,
    height: int,
    fps: int = 30,
    work_dir: Path | None = None,
) -> bool:
    """Render an HTML composition to a silent MP4 clip.

    Priority order:
      1. HyperFrames CLI  (npx hyperframes render)
      2. Chromium single-screenshot → FFmpeg video
      3. Transparent fallback clip

    Returns True when a usable clip was produced at output_path.
    """
    output_path = Path(output_path)
    if work_dir is None:
        work_dir = output_path.parent
    work_dir.mkdir(parents=True, exist_ok=True)

    html_path = work_dir / f"{output_path.stem}_comp.html"
    html_path.write_text(html_content, encoding="utf-8")

    # ── Path 1: HyperFrames CLI ──────────────────────────────────────────────
    npx = _find(_NPX_BINS)
    if npx and is_hyperframes_available():
        try:
            r = subprocess.run(
                [
                    npx, "hyperframes", "render",
                    str(html_path),
                    "--output", str(output_path),
                    "--fps", str(fps),
                    "--width",  str(width),
                    "--height", str(height),
                ],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                html_path.unlink(missing_ok=True)
                print(f"[HYPERFRAMES] CLI render OK: {output_path.name}")
                return True
            print(f"[HYPERFRAMES] CLI failed (rc={r.returncode}): {r.stderr[:200]}")
        except Exception as e:
            print(f"[HYPERFRAMES] CLI error: {e}")

    # ── Path 2: Chromium screenshot → static video ───────────────────────────
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
                    "--force-device-scale-factor=2",
                    f"--virtual-time-budget={vt_budget_ms}",
                    f"--screenshot={png_path}",
                    f"file://{html_path}",
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

    # ── Path 3: Transparent fallback ─────────────────────────────────────────
    html_path.unlink(missing_ok=True)
    return _transparent_fallback(output_path, duration, width, height, fps)


def _transparent_fallback(
    output_path: Path,
    duration: float,
    width: int,
    height: int,
    fps: int,
) -> bool:
    try:
        subprocess.run(
            [
                FFMPEG_PATH, "-y", "-loglevel", "error",
                "-f", "lavfi",
                "-i", f"color=black@0.0:size={width}x{height}:rate={fps}",
                "-t", f"{duration:.3f}",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
                "-pix_fmt", "yuv420p", "-an",
                str(output_path),
            ],
            check=True, timeout=30,
        )
        print(f"[HYPERFRAMES] Transparent fallback: {output_path.name}")
        return True
    except Exception as e:
        print(f"[HYPERFRAMES] Fallback failed: {e}")
        return False
