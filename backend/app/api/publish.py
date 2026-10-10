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

import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.core.session import require_owner
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

_STATE_COOKIE = "lle_publish_state"

router = APIRouter()


def _admin_authed(request):
    from app.main import _admin_authed as _a   # late: main imports this module
    return _a(request)
publisher = Publisher()


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.get("/api/publish/connections")
def connections(request: Request) -> JSONResponse:
    # Global resource, not per-user: one shared kit / one shared set of
    # platform tokens for the whole install. So the guard is admin, not
    # ownership — there is no owner to compare against.
    if not _admin_authed(request):
        raise HTTPException(401, "admin only")
    return JSONResponse(get_connections())


@router.post("/api/publish/connect/{platform}")
def connect_platform(platform: str, request: Request) -> JSONResponse:
    # Global resource, not per-user: one shared kit / one shared set of
    # platform tokens for the whole install. So the guard is admin, not
    # ownership — there is no owner to compare against.
    if not _admin_authed(request):
        raise HTTPException(401, "admin only")
    redirect_uri = f"{_base_url(request)}/api/publish/callback/{platform}"
    nonce = secrets.token_urlsafe(32)
    try:
        auth_url = get_auth_url(platform, redirect_uri, state=f"{platform}:{nonce}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    resp = JSONResponse({"auth_url": auth_url, "platform": platform})
    # HttpOnly so script cannot read it, Lax so it still rides the provider's
    # top-level redirect back, and short-lived because an authorization flow
    # that takes ten minutes is not one worth completing.
    resp.set_cookie(
        _STATE_COOKIE, f"{platform}:{nonce}",
        httponly=True, samesite="lax", max_age=600, path="/",
        secure=request.url.scheme == "https",
    )
    return resp


@router.get("/api/publish/callback/{platform}")
async def oauth_callback(platform: str, code: str = "", state: str = "",
                         request: Request = None) -> RedirectResponse:
    # Checked BEFORE the code is used: a failed state must never reach the
    # platform's token endpoint, or the attacker still gets their exchange.
    expected = (request.cookies.get(_STATE_COOKIE) or "") if request else ""
    ok_state = bool(expected) and bool(state) and secrets.compare_digest(state, expected)
    if not ok_state:
        bad = RedirectResponse(f"/?oauth_error={platform}:bad_state")
        bad.delete_cookie(_STATE_COOKIE, path="/")
        return bad
    if not code:
        no_code = RedirectResponse("/?oauth_error=no_code")
        no_code.delete_cookie(_STATE_COOKIE, path="/")
        return no_code
    redirect_uri = f"{_base_url(request)}/api/publish/callback/{platform}"
    try:
        handle_callback(platform, code, redirect_uri)
        done = RedirectResponse(f"/?oauth_success={platform}")
    except Exception as e:
        done = RedirectResponse(f"/?oauth_error={platform}:{str(e)[:100]}")
    # Single use, whichever way it went: a replayed callback must not pass.
    done.delete_cookie(_STATE_COOKIE, path="/")
    return done


@router.delete("/api/publish/connections/{platform}")
def disconnect_platform(platform: str, request: Request) -> JSONResponse:
    # Global resource, not per-user: one shared kit / one shared set of
    # platform tokens for the whole install. So the guard is admin, not
    # ownership — there is no owner to compare against.
    if not _admin_authed(request):
        raise HTTPException(401, "admin only")
    revoke_connection(platform)
    return JSONResponse({"disconnected": True, "platform": platform})


@router.post("/api/publish/{job_id}")
async def publish_video(
    job_id: str,
    request: Request,
) -> JSONResponse:
    """Publish a finished job to one or more platforms."""
    require_owner(request, job_id)
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
    require_owner(request, job_id)
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
def publish_history(request: Request) -> JSONResponse:
    # Global resource, not per-user: one shared kit / one shared set of
    # platform tokens for the whole install. So the guard is admin, not
    # ownership — there is no owner to compare against.
    if not _admin_authed(request):
        raise HTTPException(401, "admin only")
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
