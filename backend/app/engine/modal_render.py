"""Remote render on Modal A10G: receives hf_project/public/ as zip bytes,
renders with HyperFrames, returns mp4 bytes.

Deploy once: modal deploy backend/app/engine/modal_render.py
Then set MODAL_TOKEN_ID + MODAL_TOKEN_SECRET in Railway Variables.
"""

import io
import subprocess
import tempfile
import zipfile
from pathlib import Path

import modal

_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install(
        "curl",
        "ffmpeg",
        "chromium",
        "libnss3",
        "libatk1.0-0",
        "libatk-bridge2.0-0",
        "libcups2",
        "libdrm2",
        "libxkbcommon0",
        "libxcomposite1",
        "libxdamage1",
        "libxrandr2",
        "libgbm1",
        "libasound2",
        "libpango-1.0-0",
        "libpangocairo-1.0-0",
        "fonts-liberation",
        "libglib2.0-0",
        "libgtk-3-0",
    )
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
        "apt-get install -y nodejs",
        "PUPPETEER_SKIP_DOWNLOAD=true npm install -g hyperframes@0.7.5",
    )
    .env({"PUPPETEER_EXECUTABLE_PATH": "/usr/bin/chromium"})
)

app = modal.App("leanlead-hyperframes", image=_image)

_HF_CLI = Path("/usr/local/lib/node_modules/hyperframes/dist/cli.js")


@app.function(
    gpu="A10G",
    timeout=600,
    memory=8192,
)
def render_hf(project_zip: bytes) -> bytes:
    """Unzip public_dir, render with HyperFrames on A10G, return mp4 bytes."""
    with tempfile.TemporaryDirectory() as tmp:
        public_dir = Path(tmp) / "public"
        public_dir.mkdir()
        with zipfile.ZipFile(io.BytesIO(project_zip)) as zf:
            zf.extractall(public_dir)

        output_mp4 = Path(tmp) / "output.mp4"
        proc = subprocess.run(
            [
                "node", str(_HF_CLI),
                "render", str(public_dir),
                "-o", str(output_mp4),
                "--fps", "30",
                "--quality", "standard",
                "--no-browser-gpu",
            ],
            capture_output=True,
            text=True,
            timeout=550,
        )
        if proc.returncode != 0 or not output_mp4.exists():
            raise RuntimeError(
                f"HyperFrames render failed (rc={proc.returncode}):
"
                f"stdout: {proc.stdout[-500:]}
stderr: {proc.stderr[-500:]}"
            )
        return output_mp4.read_bytes()
