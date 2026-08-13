#!/usr/bin/env python3
"""
Render a real prim_confession_frame video via HyperFrames / Chrome headless.

Pack : lean_glass
Card : fullscreen, startSec=0.0, endSec=3.5
Text : "J'ai douté de tout pendant des mois."
Output: backend/tools/pcf_test_lean_glass.mp4

Run from repo root:
    python backend/tools/render_pcf_test.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

os.environ.setdefault("CHROMIUM_PATH", "C:/Program Files/Google/Chrome/Application/chrome.exe")

# Ensure FFmpeg (used by HyperFrames encoder) is on PATH
from app.engine.transcribe import FFMPEG_PATH as _FFMPEG_PATH
_ffmpeg_bin = str(Path(_FFMPEG_PATH).parent) if _FFMPEG_PATH else ""
if _ffmpeg_bin and _ffmpeg_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _ffmpeg_bin + os.pathsep + os.environ.get("PATH", "")

from app.engine.compose import (
    _build_card_host,
    _LEAN_GLASS,
    _COMP_ID,
)
from app.engine.compose import _build_timeline_js
from app.engine.hyperframes_engine import _gsap_inline

CARD = {
    "id": "pcf_test_001",
    "type": "graphic",
    "startSec": 0.0,
    "endSec": 3.499,
    "zone": "fullscreen",
    "contentHints": {
        "style": "prim_confession_frame",
        "confession_text": "J'ai douté de tout pendant des mois.",
    },
}

DURATION = 3.5
WIDTH    = 1920
HEIGHT   = 1080
FPS      = 30


def main() -> None:
    pack = _LEAN_GLASS

    # Build card-host HTML (CSS + DOM for all PCF layers)
    card_host_html = _build_card_host(CARD, "landscape", track_index=2, pack=pack)

    # Build GSAP timeline script (PCF tweens)
    timeline_js = _build_timeline_js([CARD], pack=pack, layout="landscape")

    gsap_js = _gsap_inline()
    if not gsap_js:
        print("ERROR: gsap.min.js not found — cannot render")
        sys.exit(1)

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<style>
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background: #000; }}
  #stage {{ position: relative; width: {WIDTH}px; height: {HEIGHT}px; overflow: hidden; }}
  .card-host {{ position: absolute; pointer-events: none; overflow: hidden; }}
  .card-host .card {{ position: relative; width: 100%; height: 100%; overflow: hidden; }}
  .card-host .char {{ display: inline-block; visibility: visible; }}
</style>
</head>
<body>
  <div id="stage"
       data-composition-id="{_COMP_ID}"
       data-start="0"
       data-duration="{DURATION:.3f}"
       data-fps="{FPS}"
       data-width="{WIDTH}"
       data-height="{HEIGHT}">

{card_host_html}

    <script>{gsap_js}</script>
    <script>
{timeline_js}
    </script>
  </div>
</body>
</html>"""

    # Write project folder (HF CLI expects a directory with index.html)
    project_dir = Path(__file__).parent / "pcf_render_project"
    project_dir.mkdir(exist_ok=True)
    (project_dir / "index.html").write_text(html, encoding="utf-8")
    print(f"[PCF-RENDER] index.html written → {project_dir / 'index.html'}")

    output_path = Path(__file__).parent / "pcf_test_lean_glass.mp4"

    env = os.environ.copy()
    # On Windows, npx ships as npx.cmd — use shell=True so the PATH-resolved script runs.
    cmd_str = (
        f'npx hyperframes render "{project_dir}"'
        f' -o "{output_path}"'
        f' --fps {FPS}'
        f' --quality draft'
    )
    print(f"[PCF-RENDER] Running: {cmd_str}")

    result = subprocess.run(cmd_str, capture_output=True, text=True, timeout=300, env=env, shell=True)

    print("--- stdout ---")
    print(result.stdout[-2000:] if result.stdout else "(empty)")
    print("--- stderr ---")
    print(result.stderr[-2000:] if result.stderr else "(empty)")
    print(f"--- rc={result.returncode} ---")

    if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
        print(f"\n[PCF-RENDER] SUCCESS → {output_path} ({output_path.stat().st_size // 1024} KB)")
    else:
        print(f"\n[PCF-RENDER] FAILED — check logs above")
        sys.exit(1)


if __name__ == "__main__":
    main()
