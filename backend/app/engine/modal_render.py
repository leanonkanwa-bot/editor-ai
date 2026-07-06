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
        "xvfb",
    )
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_22.x | bash -",
        "apt-get install -y nodejs",
        "PUPPETEER_SKIP_DOWNLOAD=true npm install -g hyperframes@0.7.5 --prefix /usr",
        "node -e \"console.log(require.resolve('hyperframes/package.json'))\" || true",
        "DISPLAY=:99 Xvfb :99 -screen 0 1920x1080x24 & hyperframes browser ensure || true",
    )
    .env({"PUPPETEER_EXECUTABLE_PATH": "/usr/bin/chromium"})
)

app = modal.App("leanlead-hyperframes", image=_image)

_HF_CLI = Path("/usr/lib/node_modules/hyperframes/dist/cli.js")


@app.function(
    gpu="A10G",
    timeout=1200,
    memory=8192,
    min_containers=1,
)
def render_hf(project_zip: bytes) -> bytes:
    """Unzip public_dir, render with HyperFrames on A10G, return mp4 bytes."""
    with tempfile.TemporaryDirectory() as tmp:
        public_dir = Path(tmp) / "public"
        public_dir.mkdir()
        with zipfile.ZipFile(io.BytesIO(project_zip)) as zf:
            zf.extractall(public_dir)

        output_mp4 = Path(tmp) / "output.mp4"
        import subprocess as _sp

        _dbg1 = _sp.run(["node", "-e", "console.log(process.execPath)"], capture_output=True, text=True)
        print(f"[MODAL] node exec: {_dbg1.stdout.strip()}", flush=True)
        _dbg2 = _sp.run(["npm", "root", "-g"], capture_output=True, text=True)
        print(f"[MODAL] npm global root: {_dbg2.stdout.strip()}", flush=True)

        import os as _os
        xvfb = _sp.Popen(["Xvfb", ":99", "-screen", "0", "1920x1080x24"])
        _os.environ["DISPLAY"] = ":99"
        _os.environ["PUPPETEER_ARGS"] = (
            "--no-sandbox "
            "--disable-setuid-sandbox "
            "--disable-dev-shm-usage "
            "--disable-gpu-sandbox "
            "--use-gl=swiftshader "
            "--enable-unsafe-swiftshader"
        )

        _which = _sp.run(["which", "hyperframes"], capture_output=True, text=True)
        hf_cmd = [_which.stdout.strip()] if _which.returncode == 0 else ["npx", "hyperframes"]
        try:
            proc = subprocess.run(
                [
                    *hf_cmd, "render", str(public_dir),
                    "-o", str(output_mp4),
                    "--fps", "30",
                    "--quality", "standard",
                ],
                capture_output=True,
                text=True,
                timeout=840,
            )
        finally:
            xvfb.terminate()

        if proc.returncode != 0 or not output_mp4.exists():
            raise RuntimeError(
                f"HyperFrames render failed (rc={proc.returncode}):\n"
                f"stdout: {proc.stdout[-500:]}\nstderr: {proc.stderr[-500:]}"
            )
        return output_mp4.read_bytes()


@app.local_entrypoint()
def test():
    import io, zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("index.html", "<html><body>test</body></html>")

    print("Calling render_hf.remote()...")
    try:
        result = render_hf.remote(buf.getvalue())
        print(f"Success: {len(result)} bytes")
    except Exception as e:
        print(f"Error: {e}")
