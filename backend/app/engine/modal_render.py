"""Remote render on Modal A10G: receives hf_project/public/ as zip bytes,
renders with HyperFrames (GPU via EGL), returns mp4 bytes.

Deploy once (from your local machine, NOT Railway):
    pip install modal
    modal deploy backend/app/engine/modal_render.py

Then set in Railway Variables:
    MODAL_TOKEN_ID     = <from modal.com Settings → API Tokens>
    MODAL_TOKEN_SECRET = <same token>
"""

import io
import subprocess
import tempfile
import zipfile
from pathlib import Path

import modal

# ── Image ────────────────────────────────────────────────────────────────────
# Installs Chrome + HyperFrames inside the Modal container.
# PRODUCER_BROWSER_GPU_MODE=hardware → HF uses --use-gl=angle --use-angle=gl-egl
# which gives full A10G GPU acceleration for WebGL/GSAP compositing.
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
        "xvfb",          # fallback display for Chrome init
    )
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_22.x | bash -",
        "apt-get install -y nodejs",
        "PUPPETEER_SKIP_DOWNLOAD=true npm install -g hyperframes@0.7.5 --prefix /usr",
        # Pre-warm the HF headless shell download inside the image layer
        "DISPLAY=:99 Xvfb :99 -screen 0 1920x1080x24 & "
        "PRODUCER_BROWSER_GPU_MODE=hardware hyperframes browser ensure || true",
    )
    .env({
        "PUPPETEER_EXECUTABLE_PATH": "/usr/bin/chromium",
        # Tell HyperFrames to use the host GPU (A10G EGL) instead of SwiftShader
        "PRODUCER_BROWSER_GPU_MODE": "hardware",
    })
)

app = modal.App("leanlead-hyperframes", image=_image)


@app.function(
    gpu="A10G",
    timeout=1200,       # 20 min hard ceiling per segment
    memory=16384,       # 16 GB RAM — headroom for parallel Chrome workers
    min_containers=0,   # no always-on container (saves ~$15/day during testing)
)
def render_hf(project_zip: bytes) -> bytes:
    """Unzip public_dir, render with HyperFrames on A10G GPU, return mp4 bytes."""
    import os as _os

    with tempfile.TemporaryDirectory() as tmp:
        public_dir = Path(tmp) / "public"
        public_dir.mkdir()
        with zipfile.ZipFile(io.BytesIO(project_zip)) as zf:
            zf.extractall(public_dir)

        output_mp4 = Path(tmp) / "output.mp4"
        hf_tmp    = Path(tmp) / "hf_tmp"
        hf_tmp.mkdir()

        # Xvfb as a safety-net display (Chrome may still probe DISPLAY on init
        # even with --headless; actual rendering uses EGL, not X).
        xvfb = subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1920x1080x24"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _os.environ["DISPLAY"] = ":99"
        # PRODUCER_BROWSER_GPU_MODE=hardware is already set in the image env;
        # re-assert here so it survives any env mutation.
        _os.environ["PRODUCER_BROWSER_GPU_MODE"] = "hardware"

        which = subprocess.run(["which", "hyperframes"], capture_output=True, text=True)
        hf_cmd = [which.stdout.strip()] if which.returncode == 0 else ["npx", "hyperframes"]

        try:
            proc = subprocess.run(
                [
                    *hf_cmd, "render", str(public_dir),
                    "-o",                str(output_mp4),
                    "--fps",             "30",
                    "--quality",         "standard",
                    "--crf",             "18",
                    "--workers",         "4",          # 4 Chrome workers on A10G (24 GB VRAM)
                    "--video-frame-format", "jpg",
                    "--protocol-timeout", "800000",   # 800 s Chrome protocol timeout (ms)
                    "--tmp-dir",         str(hf_tmp),
                    "--browser-gpu",                  # activates EGL path on Linux
                ],
                capture_output=True,
                text=True,
                timeout=900,   # 15 min; Modal hard-kills at 20 min
            )
        finally:
            xvfb.terminate()

        if proc.returncode != 0 or not output_mp4.exists():
            raise RuntimeError(
                f"HyperFrames render failed (rc={proc.returncode}):\n"
                f"stdout: {proc.stdout[-600:]}\nstderr: {proc.stderr[-600:]}"
            )

        print(
            f"[MODAL] Render done — {output_mp4.stat().st_size // 1024}KB",
            flush=True,
        )
        return output_mp4.read_bytes()


@app.local_entrypoint()
def test():
    """Quick smoke-test: deploy then run `modal run backend/app/engine/modal_render.py`."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("index.html", "<html><body>test</body></html>")

    print("Calling render_hf.remote() ...")
    try:
        result = render_hf.remote(buf.getvalue())
        print(f"Success: {len(result)} bytes returned")
    except Exception as e:
        print(f"Error: {e}")
