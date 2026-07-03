"""Chunked upload endpoints for large video files (> 100 MB).

Flow:
  1. POST /api/upload/init         → { upload_id }
  2. PUT  /api/upload/chunk/{id}/{n}  (body = raw bytes, up to 250 MB each)
  3. POST /api/upload/assemble/{id}  (body = { filename }) → { upload_id, size_bytes }

After step 3 the assembled file sits at uploads/{upload_id}.{ext}.
The client then calls POST /api/edit with upload_id=<id> instead of a
video file attachment — the edit endpoint skips the file copy and uses
the pre-assembled path directly.

File size ceiling: only disk space. 20 GB+ files are supported.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from app.core.config import settings

router = APIRouter()

_CHUNK_SUBDIR = "chunks"


def _chunk_dir(upload_id: str) -> Path:
    return settings.uploads_dir / f"_chunks_{upload_id}"


def assembled_path(upload_id: str, suffix: str = ".mp4") -> Path | None:
    """Return the assembled file path if it exists, else None."""
    for ext in (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"):
        p = settings.uploads_dir / f"{upload_id}{ext}"
        if p.exists():
            return p
    return None


@router.post("/api/upload/init")
async def upload_init(request: Request) -> JSONResponse:
    """Create a new chunked upload session. Returns upload_id."""
    import uuid
    upload_id = uuid.uuid4().hex
    _chunk_dir(upload_id).mkdir(parents=True, exist_ok=True)
    return JSONResponse({"upload_id": upload_id})


_MAX_CHUNK_BYTES = 250 * 1024 * 1024  # 250 MB per chunk


@router.put("/api/upload/chunk/{upload_id}/{chunk_index}")
async def upload_chunk(
    upload_id: str,
    chunk_index: int,
    request: Request,
) -> JSONResponse:
    """Store one raw-bytes chunk. Streams directly to disk — no full-body buffer."""
    d = _chunk_dir(upload_id)
    if not d.exists():
        raise HTTPException(404, "Upload session not found. Call /api/upload/init first.")

    chunk_path = d / f"chunk_{chunk_index:08d}"
    received = 0
    with chunk_path.open("wb") as fh:
        async for piece in request.stream():
            received += len(piece)
            if received > _MAX_CHUNK_BYTES:
                fh.close()
                chunk_path.unlink(missing_ok=True)
                raise HTTPException(413, f"Chunk too large (max {_MAX_CHUNK_BYTES // (1024*1024)} MB).")
            fh.write(piece)

    return JSONResponse({"chunk_index": chunk_index, "received": received})


@router.post("/api/upload/assemble/{upload_id}")
async def upload_assemble(
    upload_id: str,
    request: Request,
) -> JSONResponse:
    """
    Concatenate all stored chunks into the final file.
    Body JSON: { "filename": "myvideo.mp4" }
    """
    d = _chunk_dir(upload_id)
    if not d.exists():
        raise HTTPException(404, "Upload session not found.")

    body = await request.json()
    filename = body.get("filename", "video.mp4")
    suffix = Path(filename).suffix.lower() or ".mp4"
    final_path = settings.uploads_dir / f"{upload_id}{suffix}"

    chunks = sorted(d.glob("chunk_*"))
    if not chunks:
        raise HTTPException(400, "No chunks received — nothing to assemble.")

    with final_path.open("wb") as out:
        for chunk in chunks:
            with chunk.open("rb") as cf:
                shutil.copyfileobj(cf, out, 4 * 1024 * 1024)

    shutil.rmtree(d, ignore_errors=True)

    size = final_path.stat().st_size
    return JSONResponse({
        "upload_id": upload_id,
        "filename": filename,
        "size_bytes": size,
        "size_mb": round(size / (1024 * 1024), 1),
    })


# TEMP DEBUG — remove once HDR tone-mapping is finalised
@router.get("/api/debug/hdr-frame/{filename}")
def get_hdr_test_frame(filename: str) -> FileResponse:
    if "/" in filename or ".." in filename:
        raise HTTPException(404)
    path = Path("/data/hdr_test") / filename
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)
