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

import re
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from app.core.config import settings

router = APIRouter()

_CHUNK_SUBDIR = "chunks"


def _chunk_dir(upload_id: str) -> Path:
    return settings.uploads_dir / f"_chunks_{upload_id}"


_CHUNK_NAME = re.compile(r"^chunk_(\d{8})$")
# Chunk directories untouched for this long belong to abandoned uploads.
_ORPHAN_CHUNK_MAX_AGE_S = 48 * 3600


def _complete_chunks(d: Path) -> dict[int, int]:
    """{index: size} for fully written chunks only.

    A chunk is written to chunk_XXXXXXXX.part and renamed when the last byte is
    on disk, so a file without the .part suffix is complete by construction; a
    connection that dies mid-chunk leaves at most a .part, which never counts.
    """
    out: dict[int, int] = {}
    if not d.exists():
        return out
    for p in d.iterdir():
        m = _CHUNK_NAME.match(p.name)
        if m:
            out[int(m.group(1))] = p.stat().st_size
    return out


def purge_orphan_chunk_dirs(max_age_s: float = _ORPHAN_CHUNK_MAX_AGE_S) -> int:
    """Delete chunk directories of uploads abandoned for more than max_age_s.

    Before this, every upload that died mid-way left its chunks on the volume
    forever (4.1 GB across 8 directories when first measured).
    """
    import time as _time
    now = _time.time()
    removed = 0
    for d in settings.uploads_dir.glob("_chunks_*"):
        try:
            if d.is_dir() and now - d.stat().st_mtime > max_age_s:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


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
    # Write to .part and rename once complete: a connection or a process that
    # dies mid-chunk can then never leave a truncated file that looks finished
    # and gets concatenated into the video.
    part_path = d / f"chunk_{chunk_index:08d}.part"
    received = 0
    try:
        with part_path.open("wb") as fh:
            async for piece in request.stream():
                received += len(piece)
                if received > _MAX_CHUNK_BYTES:
                    fh.close()
                    part_path.unlink(missing_ok=True)
                    raise HTTPException(413, f"Chunk too large (max {_MAX_CHUNK_BYTES // (1024*1024)} MB).")
                fh.write(piece)
    except ClientDisconnect:
        # Client dropped the connection mid-upload — delete the partial chunk
        # so a retry sends a clean file. Return 499 so the frontend knows it
        # can retry this specific chunk without restarting the whole upload.
        part_path.unlink(missing_ok=True)
        return JSONResponse({"error": "client_disconnect", "chunk_index": chunk_index}, status_code=499)
    part_path.replace(chunk_path)

    return JSONResponse({"chunk_index": chunk_index, "received": received})


@router.get("/api/upload/status/{upload_id}")
async def upload_status(upload_id: str) -> JSONResponse:
    """Which chunks the server already holds, so an interrupted upload resumes.

    Returns {"exists": false} when the session is unknown (never created,
    already assembled, or purged) — the client then starts a new upload.
    """
    d = _chunk_dir(upload_id)
    if not d.exists():
        return JSONResponse({"upload_id": upload_id, "exists": False, "chunks": {}})
    chunks = _complete_chunks(d)
    return JSONResponse({
        "upload_id": upload_id,
        "exists": True,
        "chunks": {str(i): n for i, n in sorted(chunks.items())},
    })


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

    complete = _complete_chunks(d)
    if not complete:
        raise HTTPException(400, "No chunks received — nothing to assemble.")

    # Refuse to assemble a file with a hole in it. The client states how many
    # chunks and how many bytes it sent; older clients that do not are still
    # held to contiguity (indices 0..n-1 with no gap).
    expected_n = body.get("total_chunks")
    expected_size = body.get("total_size")
    n = int(expected_n) if isinstance(expected_n, (int, float)) and expected_n > 0 else max(complete) + 1
    missing = [i for i in range(n) if i not in complete]
    if missing:
        raise HTTPException(409, {
            "error": "missing_chunks",
            "missing": missing[:200],
            "message": f"{len(missing)} morceau(x) manquant(s) — reprenez l'envoi.",
        })
    got_size = sum(complete[i] for i in range(n))
    if isinstance(expected_size, (int, float)) and expected_size > 0 and got_size != int(expected_size):
        raise HTTPException(409, {
            "error": "size_mismatch",
            "expected": int(expected_size),
            "received": got_size,
            "message": "La taille reçue ne correspond pas au fichier — reprenez l'envoi.",
        })
    chunks = [d / f"chunk_{i:08d}" for i in range(n)]

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
