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
.number {{ font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.14)}px;font-weight:900;color:{brand_color};line-height:1; }}
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
body {{ display:flex;flex-direction:column;align-items:center;justify-content:flex-end;
       padding-bottom:{int(height*0.12)}px; }}
.small {{ font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.03)}px;color:rgba(255,255,255,0.7);margin-bottom:8px;opacity:0; }}
.large {{ font-family:'{font}',Inter,sans-serif;font-size:{int(height*0.07)}px;font-weight:800;color:{brand_color};
         text-align:center;line-height:1.15;opacity:0;transform:scale(0.85);
         text-shadow:0 4px 20px rgba(0,0,0,0.5); }}
</style>
</head>
<body data-duration="{duration}">
<div class="small" id="s">{small}</div>
<div class="large" id="l">{large}</div>
<script>
gsap.to("#s",{{opacity:1,y:0,duration:0.3,ease:"power2.out"}});
gsap.to("#l",{{opacity:1,scale:1,duration:0.4,delay:0.1,ease:"back.out(1.4)"}});
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
