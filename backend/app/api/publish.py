"""Publishing Integration API — Feature 3.

Endpoints:
  GET  /api/publish/connections               — list connected platforms
  POST /api/publish/connect/{platform}        — get OAuth auth URL
  GET  /api/publish/callback/{platform}       — handle OAuth callback
  POST /api/publish/{job_id}                  — publish to platforms
  GET  /api/publish/history                   — published videos history
  DELETE /api/publish/connections/{platform}  — disconnect platform
  POST /api/publish/metadata/{job_id}         — generate platform metadata
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.api.jobs import store
from app.core.config import settings
from app.engine.publisher import (
    Publisher,
    PublishMetadata,
    _load_history,
    generate_metadata,
    get_auth_url,
    get_connections,
    handle_callback,
    revoke_connection,
)

router = APIRouter()
publisher = Publisher()


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.get("/api/publish/connections")
def connections() -> JSONResponse:
    return JSONResponse(get_connections())


@router.post("/api/publish/connect/{platform}")
def connect_platform(platform: str, request: Request) -> JSONResponse:
    redirect_uri = f"{_base_url(request)}/api/publish/callback/{platform}"
    try:
        auth_url = get_auth_url(platform, redirect_uri)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"auth_url": auth_url, "platform": platform})


@router.get("/api/publish/callback/{platform}")
async def oauth_callback(platform: str, code: str = "", request: Request = None) -> RedirectResponse:
    if not code:
        return RedirectResponse("/?oauth_error=no_code")
    redirect_uri = f"{_base_url(request)}/api/publish/callback/{platform}"
    try:
        handle_callback(platform, code, redirect_uri)
        return RedirectResponse(f"/?oauth_success={platform}")
    except Exception as e:
        return RedirectResponse(f"/?oauth_error={platform}:{str(e)[:100]}")


@router.delete("/api/publish/connections/{platform}")
def disconnect_platform(platform: str) -> JSONResponse:
    revoke_connection(platform)
    return JSONResponse({"disconnected": True, "platform": platform})


@router.post("/api/publish/{job_id}")
async def publish_video(
    job_id: str,
    request: Request,
) -> JSONResponse:
    """Publish a finished job to one or more platforms."""
    body = await request.json()
    platforms  = body.get("platforms", [])
    privacy    = body.get("privacy", "public")
    scheduled  = body.get("scheduled_at", None)

    if not platforms:
        raise HTTPException(400, "Provide at least one platform in 'platforms' list")

    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status != "done":
        raise HTTPException(400, f"Job is not done (status: {job.status})")

    result_data = job.result or {}
    video_url   = result_data.get("video_url", "")
    video_path  = settings.outputs_dir / f"{job_id}.mp4"
    if not video_path.exists():
        raise HTTPException(404, "Output video not found on disk")

    transcript_text = ""
    if job.transcript:
        transcript_text = job.transcript.get("text", "")[:600]

    hook_rewrite = ""
    if job.hook_overlay:
        hook_rewrite = job.hook_overlay.get("rewritten_hook", "")

    results = []
    for platform in platforms:
        # Auto-generate platform metadata.
        meta = generate_metadata(platform, transcript_text, hook_rewrite, result_data.get("plan"))
        meta.privacy = privacy
        meta.scheduled_at = scheduled

        if scheduled:
            _schedule_post(job_id, platform, meta, scheduled, video_path)
            results.append({"platform": platform, "status": "scheduled", "scheduled_at": scheduled})
        else:
            result = publisher.publish(platform, video_path, meta, job_id)
            results.append({
                "platform":     result.platform,
                "status":       result.status,
                "url":          result.url,
                "post_id":      result.post_id,
                "error":        result.error,
                "published_at": result.published_at,
            })

    return JSONResponse({"results": results})


@router.post("/api/publish/metadata/{job_id}")
async def get_metadata(job_id: str, request: Request) -> JSONResponse:
    """Generate platform-specific metadata for a finished job without publishing."""
    body = await request.json()
    platforms = body.get("platforms", ["youtube"])

    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    transcript_text = (job.transcript or {}).get("text", "")[:600]
    hook_rewrite    = (job.hook_overlay or {}).get("rewritten_hook", "")
    result_data     = job.result or {}

    output: dict[str, dict] = {}
    for platform in platforms:
        meta = generate_metadata(platform, transcript_text, hook_rewrite, result_data.get("plan"))
        output[platform] = {
            "title":       meta.title,
            "description": meta.description,
            "tags":        meta.tags,
            "privacy":     meta.privacy,
        }

    return JSONResponse(output)


@router.get("/api/publish/history")
def publish_history() -> JSONResponse:
    return JSONResponse(_load_history())


def _schedule_post(
    job_id: str,
    platform: str,
    meta: PublishMetadata,
    scheduled_at: str,
    video_path: Path,
) -> None:
    """Save a scheduled post entry to disk for background processing."""
    from app.engine.publisher import SCHEDULED_FILE
    scheduled: list = []
    if SCHEDULED_FILE.exists():
        try:
            scheduled = json.loads(SCHEDULED_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    scheduled.append({
        "job_id":       job_id,
        "platform":     platform,
        "scheduled_at": scheduled_at,
        "video_path":   str(video_path),
        "title":        meta.title,
        "description":  meta.description,
        "tags":         meta.tags,
        "privacy":      meta.privacy,
        "status":       "pending",
    })
    SCHEDULED_FILE.write_text(json.dumps(scheduled, indent=2, ensure_ascii=False), encoding="utf-8")
