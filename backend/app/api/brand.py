"""Brand Kit API — Feature 2.

Endpoints:
  GET  /api/brand         — get current brand kit
  POST /api/brand         — create/replace brand kit
  PUT  /api/brand         — partial update
  POST /api/brand/logo    — upload logo image
  POST /api/brand/intro   — upload intro video
  POST /api/brand/outro   — upload outro video
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.engine.brand_engine import BRAND_DIR, load_brand, save_brand
from app.engine.transcribe import FFPROBE_PATH
import subprocess

router = APIRouter()

_ALLOWED_IMAGE_EXT  = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_ALLOWED_VIDEO_EXT  = {".mp4", ".mov", ".webm"}
_MAX_INTRO_DURATION = 10.0  # seconds


@router.get("/api/brand")
def get_brand() -> JSONResponse:
    return JSONResponse(load_brand())


@router.post("/api/brand")
async def create_brand(request_body: dict) -> JSONResponse:
    """Replace the entire brand kit."""
    import uuid
    brand = load_brand()
    brand.update(request_body)
    brand.setdefault("id", uuid.uuid4().hex)
    save_brand(brand)
    return JSONResponse({"saved": True, "brand": brand})


@router.put("/api/brand")
async def update_brand(request_body: dict) -> JSONResponse:
    """Partial update — merge into existing brand kit."""
    brand = load_brand()
    _deep_merge(brand, request_body)
    save_brand(brand)
    return JSONResponse({"saved": True, "brand": brand})


@router.post("/api/brand/logo")
async def upload_logo(logo: UploadFile = File(...)) -> JSONResponse:
    suffix = Path(logo.filename or "logo.png").suffix.lower()
    if suffix not in _ALLOWED_IMAGE_EXT:
        raise HTTPException(400, f"Logo must be one of: {', '.join(_ALLOWED_IMAGE_EXT)}")

    dest = BRAND_DIR / f"logo{suffix}"
    with dest.open("wb") as f:
        shutil.copyfileobj(logo.file, f)

    # Probe dimensions.
    dims: dict = {}
    try:
        out = subprocess.check_output(
            [FFPROBE_PATH, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(dest)],
            text=True,
        ).strip()
        parts = out.split(",")
        if len(parts) == 2:
            dims = {"width": int(parts[0]), "height": int(parts[1])}
    except Exception:
        pass

    # Update brand kit with new logo path.
    brand = load_brand()
    brand["logo"]["path"] = str(dest)
    save_brand(brand)

    return JSONResponse({"path": str(dest), "dimensions": dims, "saved": True})


@router.post("/api/brand/intro")
async def upload_intro(intro: UploadFile = File(...)) -> JSONResponse:
    suffix = Path(intro.filename or "intro.mp4").suffix.lower()
    if suffix not in _ALLOWED_VIDEO_EXT:
        raise HTTPException(400, f"Intro must be one of: {', '.join(_ALLOWED_VIDEO_EXT)}")

    dest = BRAND_DIR / f"intro{suffix}"
    with dest.open("wb") as f:
        shutil.copyfileobj(intro.file, f)

    duration = _probe_duration(dest)
    if duration > _MAX_INTRO_DURATION:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"Intro must be ≤ {_MAX_INTRO_DURATION}s (got {duration:.1f}s)")

    brand = load_brand()
    brand["intro"]["path"]     = str(dest)
    brand["intro"]["duration"] = round(duration, 2)
    brand["intro"]["enabled"]  = True
    save_brand(brand)

    return JSONResponse({"path": str(dest), "duration": round(duration, 2), "saved": True})


@router.post("/api/brand/outro")
async def upload_outro(outro: UploadFile = File(...)) -> JSONResponse:
    suffix = Path(outro.filename or "outro.mp4").suffix.lower()
    if suffix not in _ALLOWED_VIDEO_EXT:
        raise HTTPException(400, f"Outro must be one of: {', '.join(_ALLOWED_VIDEO_EXT)}")

    dest = BRAND_DIR / f"outro{suffix}"
    with dest.open("wb") as f:
        shutil.copyfileobj(outro.file, f)

    duration = _probe_duration(dest)
    if duration > _MAX_INTRO_DURATION:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"Outro must be ≤ {_MAX_INTRO_DURATION}s (got {duration:.1f}s)")

    brand = load_brand()
    brand["outro"]["path"]     = str(dest)
    brand["outro"]["duration"] = round(duration, 2)
    brand["outro"]["enabled"]  = True
    save_brand(brand)

    return JSONResponse({"path": str(dest), "duration": round(duration, 2), "saved": True})


def _probe_duration(path: Path) -> float:
    try:
        out = subprocess.check_output(
            [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            text=True, timeout=15,
        )
        return float(out.strip())
    except Exception:
        return 0.0


def _deep_merge(base: dict, update: dict) -> None:
    for k, v in update.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
