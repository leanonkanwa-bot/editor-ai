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
# Mirrors HyperFrames' own Dockerfile.render exactly:
#   1. apt Chromium (fallback for DTP discovery)
#   2. chrome-headless-shell (required for BeginFrame deterministic capture)
#   3. HyperFrames npm install (no PUPPETEER_SKIP_DOWNLOAD — let it resolve deps)
#   4. `browser ensure` with PRODUCER_HEADLESS_SHELL_PATH set → generates
#      /usr/lib/core/dist/hyperframe.manifest.json
#   5. PRODUCER_BROWSER_GPU_MODE=hardware → EGL GPU path on A10G
_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install(
        "curl",
        "unzip",          # chrome-headless-shell zip extraction
        "ffmpeg",
        "chromium",
        # GLVND — GL Vendor-Neutral Dispatch.
        # Modal injects libEGL_nvidia.so at runtime; GLVND routes EGL calls to it
        # WITHOUT needing /dev/dri (which Modal doesn't expose to containers).
        "libglvnd0",
        "libgl1",
        "libegl1",
        "libnss3",
        "libatk1.0-0",
        "libatk-bridge2.0-0",
        "libcups2",
        "libdrm2",
        "libxkbcommon0",
        "libxcomposite1",
        "libxdamage1",
        "libxfixes3",
        "libxrandr2",
        "libgbm1",
        "libasound2",
        "libpango-1.0-0",
        "libpangocairo-1.0-0",
        "libxshmfence1",
        "libgtk-3-0",
        "fonts-liberation",
        "fontconfig",
        "xvfb",
    )
    .run_commands(
        # GLVND EGL vendor config for NVIDIA.
        # Without this JSON, Chrome's EGL falls back to SwiftShader even when
        # libEGL_nvidia.so is present (Modal injects it at runtime).
        # With it, EGL dispatches to NVIDIA directly — no /dev/dri needed.
        "mkdir -p /usr/share/glvnd/egl_vendor.d && "
        "printf '{\"file_format_version\":\"1.0.0\",\"ICD\":{\"library_path\":\"libEGL_nvidia.so.0\"}}' "
        "> /usr/share/glvnd/egl_vendor.d/10_nvidia.json",
        # Node.js 22 LTS
        "curl -fsSL https://deb.nodesource.com/setup_22.x | bash -",
        "apt-get install -y nodejs",
        # HyperFrames (global install)
        "npm install -g hyperframes@0.7.5",
        # chrome-headless-shell — direct curl from Google Storage.
        # Avoids npx @puppeteer/browsers which fails in Modal build environment.
        # Pinned to same version as Railway Dockerfile line 10.
        "CHROME_VERSION=131.0.6778.85 && "
        "mkdir -p /root/.cache/puppeteer/chrome-headless-shell/${CHROME_VERSION}-linux64 && "
        "curl -fsSL https://storage.googleapis.com/chrome-for-testing-public/"
        "${CHROME_VERSION}/linux64/chrome-headless-shell-linux64.zip -o /tmp/chs.zip && "
        "unzip /tmp/chs.zip -d /root/.cache/puppeteer/chrome-headless-shell/${CHROME_VERSION}-linux64 && "
        "chmod +x /root/.cache/puppeteer/chrome-headless-shell/${CHROME_VERSION}-linux64/"
        "chrome-headless-shell-linux64/chrome-headless-shell && "
        "rm -f /tmp/chs.zip && "
        "echo 'chrome-headless-shell OK'",
        # Pre-build core runtime (generates hyperframe.manifest.json).
        # PRODUCER_HEADLESS_SHELL_PATH must be set for browser ensure to succeed.
        "SHELL_BIN=$(find /root/.cache/puppeteer/chrome-headless-shell "
        "-name 'chrome-headless-shell' -type f | head -1) && "
        "echo \"SHELL_BIN=$SHELL_BIN\" && "
        "Xvfb :99 -screen 0 1920x1080x24 & sleep 1 && "
        "DISPLAY=:99 PRODUCER_HEADLESS_SHELL_PATH=$SHELL_BIN "
        "hyperframes browser ensure && "
        "echo '[MODAL-BUILD] browser ensure OK'",
    )
    .env({
        "PUPPETEER_EXECUTABLE_PATH": "/usr/bin/chromium",
        "PRODUCER_BROWSER_GPU_MODE": "hardware",
        # Tell GLVND where to find the NVIDIA vendor JSON at runtime.
        "__EGL_VENDOR_LIBRARY_DIRS": "/usr/share/glvnd/egl_vendor.d",
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
    import time as _time

    _t0 = _time.perf_counter()

    with tempfile.TemporaryDirectory() as tmp:
        public_dir = Path(tmp) / "public"
        public_dir.mkdir()
        with zipfile.ZipFile(io.BytesIO(project_zip)) as zf:
            zf.extractall(public_dir)

        output_mp4 = Path(tmp) / "output.mp4"
        hf_tmp    = Path(tmp) / "hf_tmp"
        hf_tmp.mkdir()

        # ── GPU presence check ─────────────────────────────────────────────────
        _gpu_r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        if _gpu_r.returncode == 0:
            _gpu_status = f"A10G: {_gpu_r.stdout.strip()}"
        else:
            _gpu_status = f"nvidia-smi FAILED ({_gpu_r.stderr.strip()[:120]}) — SwiftShader risk"
        print(f"[MODAL-GPU] {_gpu_status}", flush=True)

        # ── DRI + GLVND EGL check ──────────────────────────────────────────────
        import glob as _glob, ctypes as _ct
        _dri = _glob.glob("/dev/dri/renderD*")
        _dri_status = f"DRI: {_dri}" if _dri else "DRI: NONE"
        _egl_json = Path("/usr/share/glvnd/egl_vendor.d/10_nvidia.json")
        _egl_status = "GLVND-nvidia: OK" if _egl_json.exists() else "GLVND-nvidia: MISSING"
        # Verify the library referenced in the JSON is actually loadable.
        try:
            _ct.CDLL("libEGL_nvidia.so.0")
            _lib_status = "libEGL_nvidia: LOADED"
        except OSError as _le:
            _lib_status = f"libEGL_nvidia: NOT FOUND ({str(_le)[:80]})"
        print(f"[MODAL-GPU] {_dri_status} | {_egl_status} | {_lib_status}", flush=True)

        # ── chrome-headless-shell path (required for BeginFrame capture) ─────────
        _shell_bins = sorted(_glob.glob(
            "/root/.cache/puppeteer/chrome-headless-shell/*/chrome-headless-shell-linux64/chrome-headless-shell"
        ))
        if _shell_bins:
            _os.environ["PRODUCER_HEADLESS_SHELL_PATH"] = _shell_bins[0]
            print(f"[MODAL-HF] headless-shell: {_shell_bins[0]}", flush=True)
        else:
            print("[MODAL-HF] WARNING: chrome-headless-shell not found", flush=True)

        # ── Xvfb — start but do NOT export DISPLAY before HF ──────────────────
        # chrome-headless-shell uses EGL surfaceless (no X11 needed).
        # Setting DISPLAY=:99 makes Chrome pick GLX/X11 path → no DRI → SwiftShader.
        # Keep Xvfb alive only as Chrome's display probe fallback; pass env without it.
        xvfb = subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1920x1080x24"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        which = subprocess.run(["which", "hyperframes"], capture_output=True, text=True)
        hf_cmd = [which.stdout.strip()] if which.returncode == 0 else ["npx", "hyperframes"]

        # Build env WITHOUT DISPLAY so Chrome uses EGL surfaceless (not GLX).
        # Add NVIDIA hints that route EGL/GL to the NVIDIA driver.
        _hf_env = {k: v for k, v in _os.environ.items() if k != "DISPLAY"}
        _hf_env["PRODUCER_BROWSER_GPU_MODE"] = "hardware"
        _hf_env["__NV_PRIME_RENDER_OFFLOAD"] = "1"
        _hf_env["__GLX_VENDOR_LIBRARY_NAME"] = "nvidia"
        _hf_env["LIBGL_ALWAYS_SOFTWARE"] = "0"
        print(f"[MODAL-HF] launching without DISPLAY (EGL surfaceless mode)", flush=True)

        try:
            proc = subprocess.run(
                [
                    *hf_cmd, "render", str(public_dir),
                    "-o",                str(output_mp4),
                    "--fps",             "30",
                    "--quality",         "standard",
                    "--crf",             "18",
                    "--workers",         "4",
                    "--video-frame-format", "jpg",
                    "--protocol-timeout", "800000",
                    "--tmp-dir",         str(hf_tmp),
                    "--browser-gpu",
                ],
                capture_output=True,
                text=True,
                timeout=1100,
                env=_hf_env,
            )
        finally:
            xvfb.terminate()

        if proc.returncode != 0 or not output_mp4.exists():
            raise RuntimeError(
                f"HyperFrames render failed (rc={proc.returncode}):\n"
                f"stdout: {proc.stdout[-1200:]}\nstderr: {proc.stderr[-400:]}"
            )

        # Print head (Chrome GPU init) + tail (render completion).
        hf_lines   = (proc.stdout or "").splitlines()
        hf_head    = "\n".join(hf_lines[:25])
        hf_tail    = "\n".join(hf_lines[-8:])
        render_s   = _time.perf_counter() - _t0
        size_kb    = output_mp4.stat().st_size // 1024
        print(f"[MODAL] Render done — {size_kb}KB in {render_s:.1f}s", flush=True)
        if hf_head:
            print(f"[MODAL-HF-HEAD]\n{hf_head}", flush=True)
        if hf_tail and hf_tail != hf_head:
            print(f"[MODAL-HF-TAIL]\n{hf_tail}", flush=True)

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
