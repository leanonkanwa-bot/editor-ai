#!/usr/bin/env python3
"""One-shot script: generate 6 style-pack preview clips.

Run on Railway via: python -m scripts.generate_previews
Or locally if hyperframes CLI + Chrome are available.

Outputs 6 silent MP4 files to editor_frontend/previews/.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine.compose import (
    _PACKS, _build_card_host, _build_timeline_js,
    _COMP_ID, _THEMES,
)


def _build_preview_composition(pack_id: str, work_dir: Path) -> Path:
    """Build a minimal HyperFrames project for a pack preview."""
    pack = _PACKS[pack_id]
    project_dir = work_dir / pack_id
    public_dir = project_dir / "public"
    vendor_dir = public_dir / "vendor"
    public_dir.mkdir(parents=True, exist_ok=True)
    vendor_dir.mkdir(parents=True, exist_ok=True)

    # Copy GSAP
    gsap_src = Path(__file__).resolve().parent.parent / "app" / "engine" / "node_modules" / "gsap" / "dist" / "gsap.min.js"
    if not gsap_src.exists():
        gsap_src = Path(__file__).resolve().parent.parent.parent / ".agents" / "skills" / "graphic-overlays" / "assets" / "vendor" / "gsap.min.js"
    if gsap_src.exists():
        shutil.copy2(gsap_src, vendor_dir / "gsap.min.js")
    else:
        print(f"  WARNING: gsap.min.js not found for {pack_id}")

    # Single clean key_phrase card — minimal text, max visual clarity at small size
    cards = [
        {"id": "prev-kp", "type": "graphic", "startSec": 0.5, "endSec": 3.5,
         "zone": "video-overlay",
         "contentHints": {"title": "Impact", "style": "key_phrase"}},
    ]

    width, height = 1080, 1920
    fps = 30
    duration = 4.0
    layout = "portrait"

    card_hosts = []
    for c in cards:
        card_hosts.append(_build_card_host(c, layout, track_index=2, pack=pack))

    timeline_js = _build_timeline_js(cards, pack=pack)

    # Background color from the pack (or a neutral dark)
    bg_color = pack["bg"] if not pack["bg"].startswith("linear") else "#111"
    # For gradient packs, use a CSS background
    bg_style = f"background: {pack['bg']};" if "gradient" in pack["bg"] else f"background: {pack['bg']};"

    # Google Fonts import
    _font_imports = {
        "lean_vibe": "Poppins:wght@400;800",
        "lean_craft": "Permanent+Marker",
        "lean_cinema": "Playfair+Display:wght@400;700",
        "lean_ledger": "IBM+Plex+Mono:wght@400;600",
    }
    font_link = ""
    fi = _font_imports.get(pack_id, "")
    if fi:
        font_link = f'<link href="https://fonts.googleapis.com/css2?family={fi}&display=block" rel="stylesheet" />'

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
{font_link}
<style>
  * {{ box-sizing: border-box; }}
  html, body {{
    margin: 0; padding: 0; width: 100%; height: 100%;
    overflow: hidden; background: #000;
    font-family: "Inter", ui-sans-serif, system-ui, sans-serif;
  }}
  #stage {{ position: relative; width: 100%; height: 100%; overflow: hidden; }}
  .bg-fill {{
    position: absolute; inset: 0;
    {bg_style}
  }}
  .card-host {{
    position: absolute; pointer-events: none; overflow: hidden;
  }}
  .card-host .card {{ position: relative; width: 100%; height: 100%; overflow: hidden; }}
</style>
</head>
<body>
  <div id="stage"
       data-composition-id="{_COMP_ID}"
       data-start="0"
       data-duration="{duration:.3f}"
       data-fps="{fps}"
       data-width="{width}"
       data-height="{height}">

    <div class="bg-fill"></div>

{chr(10).join(f"    {host}" for host in card_hosts)}

    <script src="vendor/gsap.min.js"></script>
    <script>
{timeline_js}
    </script>
  </div>
</body>
</html>"""

    (public_dir / "index.html").write_text(html, encoding="utf-8")
    print(f"  [{pack_id}] Composition written: {project_dir}")
    return project_dir


def _render_preview(pack_id: str, project_dir: Path, output_path: Path) -> bool:
    """Render a preview composition to MP4 via HyperFrames CLI."""
    public_dir = project_dir / "public"
    hf_cli = Path(__file__).resolve().parent.parent / "app" / "engine" / "node_modules" / ".bin" / "hyperframes"
    if not hf_cli.exists():
        hf_cli = Path(__file__).resolve().parent.parent / "app" / "engine" / "node_modules" / "hyperframes" / "dist" / "cli.js"
    hf_cmd = ["node", str(hf_cli)] if hf_cli.suffix == ".js" else [str(hf_cli)]

    env = os.environ.copy()
    env["DISPLAY"] = env.get("DISPLAY", ":99")

    print(f"  [{pack_id}] Rendering via HyperFrames CLI...")
    try:
        proc = subprocess.run(
            [
                *hf_cmd, "render",
                str(public_dir),
                "-o", str(output_path),
                "--fps", "24",
                "--quality", "standard",
                "--workers", "1",
                "--protocol-timeout", "120000",
                "--low-memory-mode",
            ],
            capture_output=True, text=True, timeout=180, env=env,
        )
        if proc.returncode != 0:
            print(f"  [{pack_id}] Render FAILED (rc={proc.returncode})")
            print(f"  stderr: {proc.stderr[-500:]}")
            return False
    except subprocess.TimeoutExpired:
        print(f"  [{pack_id}] Render TIMED OUT (180s)")
        return False

    if not output_path.exists():
        print(f"  [{pack_id}] Output file missing after render")
        return False

    # Compress: re-encode with CRF 30, strip audio, target small file
    try:
        from app.engine.transcribe import FFMPEG_PATH
        compressed = output_path.with_suffix(".tmp.mp4")
        subprocess.run([
            FFMPEG_PATH, "-y", "-i", str(output_path),
            "-an",
            "-vf", "scale=540:960",
            "-c:v", "libx264", "-crf", "24", "-preset", "fast",
            "-movflags", "+faststart",
            str(compressed),
        ], capture_output=True, timeout=60)
        if compressed.exists() and compressed.stat().st_size > 0:
            compressed.replace(output_path)
        elif compressed.exists():
            compressed.unlink()
    except Exception as e:
        print(f"  [{pack_id}] Compression failed (keeping raw): {e}")

    size_kb = output_path.stat().st_size // 1024
    print(f"  [{pack_id}] Done: {output_path} ({size_kb} KB)")
    return True


def main():
    base_dir = Path(__file__).resolve().parent.parent.parent
    work_dir = Path("/tmp/preview_gen")
    output_dir = base_dir / "editor_frontend" / "previews"
    output_dir.mkdir(parents=True, exist_ok=True)

    pack_ids = ["lean_glass", "lean_paper", "lean_vibe", "lean_ledger", "lean_craft", "lean_cinema"]
    results = {}

    for pack_id in pack_ids:
        print(f"\n{'='*50}")
        print(f"Generating preview for: {pack_id}")
        print(f"{'='*50}")

        project_dir = _build_preview_composition(pack_id, work_dir)
        output_path = output_dir / f"{pack_id}.mp4"
        ok = _render_preview(pack_id, project_dir, output_path)
        results[pack_id] = ok

        if not ok:
            print(f"  FAILED — stopping (fix this pack before continuing)")
            break

    print(f"\n{'='*50}")
    print("RESULTS:")
    for pid, ok in results.items():
        print(f"  {pid}: {'OK' if ok else 'FAILED'}")

    total_kb = sum(
        (output_dir / f"{pid}.mp4").stat().st_size // 1024
        for pid in results if results[pid] and (output_dir / f"{pid}.mp4").exists()
    )
    print(f"Total size: {total_kb} KB ({total_kb // 1024} MB)")


if __name__ == "__main__":
    main()
