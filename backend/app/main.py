"""FastAPI entrypoint for the AI Video Editor Agent."""

from __future__ import annotations

import secrets
import shutil
from pathlib import Path
from typing import Literal

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.jobs import store
from app.api.pipeline import run_job
from app.api.upload import assembled_path, router as upload_router
from app.core.config import settings


app = FastAPI(title="AI Video Editor Agent", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload_router)

AUTH_COOKIE = "lle_token"


def _auth_required() -> bool:
    return bool(settings.access_password)


def _check_auth(request: Request) -> None:
    if not _auth_required():
        return
    token = request.cookies.get(AUTH_COOKIE) or request.headers.get("x-access-token")
    if not token or not secrets.compare_digest(token, settings.access_password):
        raise HTTPException(status_code=401, detail="auth required")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict:
    if not _auth_required():
        return {"required": False, "authed": True}
    cookie = request.cookies.get(AUTH_COOKIE)
    authed = bool(cookie) and secrets.compare_digest(cookie, settings.access_password)
    return {"required": True, "authed": authed}


@app.post("/api/auth/login")
def auth_login(password: str = Form(...)) -> Response:
    if not _auth_required():
        return JSONResponse({"ok": True})
    if not secrets.compare_digest(password, settings.access_password):
        raise HTTPException(401, "wrong password")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        AUTH_COOKIE,
        settings.access_password,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
        secure=False,
    )
    return resp


@app.post("/api/auth/logout")
def auth_logout() -> Response:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(AUTH_COOKIE)
    return resp


@app.post("/api/edit")
async def submit_edit(
    request: Request,
    background: BackgroundTasks,
    video: UploadFile = File(None),   # optional — omitted when upload_id is used
    upload_id: str = Form(""),        # set by chunked-upload flow
    instructions: str = Form(""),
    format_hint: Literal["short", "long", "auto"] = Form("auto"),
    caption_font: str = Form("Poppins Bold"),
    caption_color: str = Form("white"),
    caption_position: Literal["center", "bottom", "side-left", "side-right"] = Form("center"),
    caption_style: Literal["impact", "kinetic"] = Form("impact"),
    brand_color: str = Form(""),
    theme: str = Form("dark-pro"),
    _: None = Depends(_check_auth),
) -> JSONResponse:
    if not settings.anthropic_api_key:
        raise HTTPException(500, "ANTHROPIC_API_KEY not set in backend/.env")

    job = store.create()

    if upload_id:
        # Chunked-upload path: file was already assembled by /api/upload/assemble
        dest = assembled_path(upload_id)
        if dest is None:
            raise HTTPException(400, f"No assembled file found for upload_id={upload_id!r}. "
                                     "Call /api/upload/assemble first.")
    elif video and video.filename:
        # Direct upload path (files ≤ ~100 MB)
        suffix = Path(video.filename).suffix or ".mp4"
        dest = settings.uploads_dir / f"{job.id}{suffix}"
        with dest.open("wb") as f:
            shutil.copyfileobj(video.file, f)
    else:
        raise HTTPException(400, "Provide either a video file or an upload_id.")

    background.add_task(
        run_job,
        job.id,
        dest,
        instructions,
        format_hint,
        caption_font=caption_font,
        caption_color=caption_color,
        caption_position=caption_position,
        caption_style=caption_style,
        brand_color=brand_color or None,
        aesthetic=theme,
    )
    return JSONResponse({"job_id": job.id, "status": job.status})


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, request: Request, _: None = Depends(_check_auth)) -> dict:
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_dict()


@app.get("/api/download/{job_id}")
def download(job_id: str, request: Request, _: None = Depends(_check_auth)):
    out = settings.outputs_dir / f"{job_id}.mp4"
    if not out.exists():
        raise HTTPException(404, "Output not ready")
    return FileResponse(out, media_type="video/mp4", filename=f"edited-{job_id}.mp4")


frontend_dir = Path(__file__).resolve().parents[2] / "editor_frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
