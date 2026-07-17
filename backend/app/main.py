"""FastAPI entrypoint for the AI Video Editor Agent."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode

import requests
from app import emails as _emails
from fastapi import (
    BackgroundTasks,
    Body,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.jobs import store
from app.api.pipeline import run_job, run_render_phase, _shutdown_event as _pipeline_shutdown, _RENDER_SEM
from app.api.upload import assembled_path, router as upload_router
from app.core.config import settings
from app.core.plans import DEFAULT_PLAN, effective_plan_info

try:
    from app.api.templates import router as templates_router
except Exception:
    templates_router = None

try:
    from app.api.brand import router as brand_router
except Exception:
    brand_router = None

try:
    from app.api.publish import router as publish_router
except Exception:
    publish_router = None

try:
    from app.api.analytics import router as analytics_router
except Exception:
    analytics_router = None

try:
    from app.api.billing import router as billing_router
except Exception:
    billing_router = None


def _cleanup_old_uploads() -> None:
    """Delete uploaded source videos older than upload_retention_hours."""
    import time as _time
    cutoff = _time.time() - settings.upload_retention_hours * 3600
    try:
        for f in settings.uploads_dir.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff:
                try:
                    f.unlink()
                except OSError:
                    pass
    except Exception:
        pass


def _purge_trash() -> None:
    """Permanently delete jobs trashed longer than TRASH_RETENTION_HOURS:
    removes the rendered output (+ thumbnail/landscape variants) from disk
    and the job entry itself."""
    try:
        n = store.purge_expired_trash(TRASH_RETENTION_HOURS)
        if n:
            print(f"[TRASH] Purged {n} job(s) older than {TRASH_RETENTION_HOURS}h in trash")
    except Exception as e:
        print(f"[TRASH] Purge failed: {e}")


async def _purge_trash_loop() -> None:
    """Re-checks trash every 12h for the process lifetime. Startup-only
    cleanup (like _cleanup_old_uploads) is fine for the uploads dir since
    deploys are frequent, but isn't a reliable 7-day SLA for trash if the
    container stays up for weeks without restarting.
    """
    while True:
        await asyncio.sleep(12 * 3600)
        _purge_trash()


_RESUME_WATCHDOG_S = 45 * 60  # 45 min — give resumed jobs time to finish


async def _auto_resume_bg(jobs_to_resume: list) -> None:
    """Dispatch threads for jobs that were in-flight at last shutdown.

    Called via asyncio.create_task() so the web server starts serving
    immediately — recovery runs in the background.

    Watchdog: logs a WARNING for any thread still alive after 45 min and
    exits the task.  The thread continues (daemon) and Layer C handles
    recovery on the next deploy if the render never finishes.

    Never raises — any error is logged and the finally block always runs.
    """
    if not jobs_to_resume:
        return
    print(f"[STARTUP] {len(jobs_to_resume)} job(s) to auto-resume", flush=True)
    _threads: list = []
    try:
        for _kind, _job in jobs_to_resume:
            _src = Path(_job.source_path) if _job.source_path else None
            if not _src or not _src.exists():
                store.update(
                    _job.id,
                    status="error",
                    is_retry=True,
                    error="Source vidéo introuvable après redémarrage",
                    message="Render interrompu — crédit remboursé.",
                    progress=100,
                )
                print(f"[STARTUP] job {_job.id}: source missing — marked error", flush=True)
                continue
            if _kind == "render":
                _t = threading.Thread(
                    target=run_render_phase,
                    args=(_job.id, _src),
                    daemon=True,
                    name=f"resume-render-{_job.id[:8]}",
                )
            elif _kind == "phase1":
                _params = dict(_job.params or {})
                _instructions = _params.pop("instructions", "")
                _format_hint  = _params.pop("format_hint", "auto")
                _t = threading.Thread(
                    target=run_job,
                    args=(_job.id, _src, _instructions, _format_hint),
                    kwargs=_params,
                    daemon=True,
                    name=f"resume-phase1-{_job.id[:8]}",
                )
            else:
                print(f"[STARTUP] WARN: unknown resume kind {_kind!r} for {_job.id}", flush=True)
                continue
            _t.start()
            print(f"[STARTUP] Auto-resuming {_kind} for job {_job.id}", flush=True)
            _threads.append((_job, _t))

        # Watchdog: poll every 30 s; log any thread still alive at deadline.
        _deadline = asyncio.get_event_loop().time() + _RESUME_WATCHDOG_S
        _alive = list(_threads)
        while _alive:
            _remaining = _deadline - asyncio.get_event_loop().time()
            if _remaining <= 0:
                break
            await asyncio.sleep(min(30.0, _remaining))
            _alive = [(_j, _t) for _j, _t in _alive if _t.is_alive()]
        for _job, _t in _alive:
            if _t.is_alive():
                print(
                    f"[STARTUP] WARN: job {_job.id} still running after "
                    f"{_RESUME_WATCHDOG_S // 60} min watchdog — "
                    "Layer C handles recovery on next deploy",
                    flush=True,
                )
    except Exception as _e:
        print(f"[STARTUP] WARN: auto-resume unexpected error: {_e}", flush=True)
    finally:
        print(
            f"[STARTUP] Auto-resume task done ({len(_threads)} thread(s) dispatched)",
            flush=True,
        )


app = FastAPI(title="AI Video Editor Agent", version="0.1.0")


@app.on_event("startup")
def _on_startup() -> None:
    import os as _os
    import signal as _signal

    _commit_file = Path(__file__).resolve().parent.parent / "COMMIT_HASH"
    _commit = _commit_file.read_text().strip() if _commit_file.exists() else "unknown"
    _commit = _os.environ.get("RAILWAY_GIT_COMMIT_SHA", "") or _commit
    print(f"[BUILD] Running commit: {_commit}")
    _cleanup_old_uploads()
    _purge_trash()
    asyncio.create_task(_purge_trash_loop())
    asyncio.create_task(_reengagement_loop())

    # ── SIGTERM handler — register AFTER uvicorn sets up its own handler ────
    # We wrap uvicorn's handler: set the shutdown flag (rejects new renders),
    # drain the in-flight render for up to _SIGTERM_DRAIN_SECS, then forward
    # to uvicorn so it exits normally.  Renders that outlast the drain window
    # are recovered on the next boot by the auto-resume logic below.
    #
    # Railway note: Railway's default SIGTERM→SIGKILL gap is 0 s.  This drain
    # window only works because railway.json sets drainingSeconds: 600.
    #
    # 570 s = 600 s (Railway hard-kill) − 30 s (clean-exit margin).
    # The app finishes draining before Railway's SIGKILL can interrupt it.
    # If you change drainingSeconds in railway.json, update this constant too,
    # keeping the 30 s margin intact.  Renders that outlast even 570 s are
    # killed by Railway and auto-resumed by Layer C on next boot.
    _SIGTERM_DRAIN_SECS = 570  # 9m30s — Railway 600s kill minus 30s margin
    _uvicorn_sigterm = _signal.getsignal(_signal.SIGTERM)

    def _sigterm_handler(signum: int, frame: object) -> None:
        _pipeline_shutdown.set()

        # Non-blocking check: does the semaphore show a render is currently held?
        _sem_free = _RENDER_SEM.acquire(blocking=False)
        if _sem_free:
            # Semaphore was free → no render in progress; release and exit fast.
            _RENDER_SEM.release()
            print(
                "[SHUTDOWN] SIGTERM received — 0 renders in flight; "
                "new requests rejected; exiting immediately",
                flush=True,
            )
        else:
            # Semaphore is held → 1 render in progress.
            print(
                f"[SHUTDOWN] SIGTERM received — 1 render in flight; "
                f"new requests rejected; draining (max {_SIGTERM_DRAIN_SECS // 60} min)…",
                flush=True,
            )
            try:
                acquired = _RENDER_SEM.acquire(timeout=_SIGTERM_DRAIN_SECS)
                if acquired:
                    _RENDER_SEM.release()
                    print("[SHUTDOWN] In-flight render completed — draining done", flush=True)
                else:
                    print(
                        f"[SHUTDOWN] Drain timeout after {_SIGTERM_DRAIN_SECS // 60} min — "
                        "Layer C auto-resume will pick this render up on next boot",
                        flush=True,
                    )
            except Exception as _e:
                print(f"[SHUTDOWN] Drain error: {_e}", flush=True)

        # Forward to uvicorn's handler so the process exits cleanly.
        if callable(_uvicorn_sigterm) and _uvicorn_sigterm not in (
            _signal.SIG_DFL, _signal.SIG_IGN
        ):
            _uvicorn_sigterm(signum, frame)

    _signal.signal(_signal.SIGTERM, _sigterm_handler)

    # ── Auto-resume jobs that were in-flight during the previous shutdown ───
    # Runs in the background so the web server is immediately ready.
    # _resumable_jobs is populated by JobStore._load() at import time.
    asyncio.create_task(_auto_resume_bg(store._resumable_jobs))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload_router)
if templates_router:
    app.include_router(templates_router)
if brand_router:
    app.include_router(brand_router)
if publish_router:
    app.include_router(publish_router)
if analytics_router:
    app.include_router(analytics_router)
if billing_router:
    app.include_router(billing_router)

AUTH_COOKIE = "lle_token"


def _auth_required() -> bool:
    return bool(settings.access_password)


def _check_auth(request: Request) -> None:
    if not _auth_required():
        return
    token = request.cookies.get(AUTH_COOKIE) or request.headers.get("x-access-token")
    if not token or not secrets.compare_digest(token, settings.access_password):
        raise HTTPException(status_code=401, detail="auth required")


# ── Google OAuth identity (separate from the site-wide access_password
# gate above -- this answers "which profile is this", not "is this visitor
# allowed in the beta") ────────────────────────────────────────────────────
SESSION_COOKIE = "lle_session"
OAUTH_STATE_COOKIE = "lle_oauth_state"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def _session_secret() -> str:
    if settings.session_secret:
        return settings.session_secret
    # Derive a stable key from access_password so sessions survive restarts
    # without requiring a new Railway env var. access_password is already a
    # server-only secret; this just namespaces it for a different purpose.
    base = settings.access_password or "lle-default-dev-secret"
    return hashlib.sha256(f"{base}:session-signing".encode()).hexdigest()


def _sign_session(profile_id: str) -> str:
    sig = hmac.new(_session_secret().encode(), profile_id.encode(), hashlib.sha256).hexdigest()
    return f"{profile_id}.{sig}"


def _verify_session(token: str | None) -> str | None:
    """Returns the profile_id if the signed session cookie is valid, else None."""
    if not token or "." not in token:
        return None
    profile_id, _, sig = token.rpartition(".")
    expected = hmac.new(_session_secret().encode(), profile_id.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return profile_id


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/debug-last-filter")
def debug_last_filter() -> Response:
    """Return the exact filter_complex string from the last render attempt.

    render.py writes /tmp/debug_filter.txt before every filter_complex call
    so a crash leaves the offending string on disk for inspection.
    """
    p = Path("/tmp/debug_filter.txt")
    if not p.exists():
        return Response(content="(no /tmp/debug_filter.txt found)", media_type="text/plain")
    return Response(content=p.read_text(encoding="utf-8", errors="replace"), media_type="text/plain")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/ffmpeg-info")
async def ffmpeg_info() -> dict:
    import subprocess as _sp
    from app.engine.transcribe import FFMPEG_PATH as _FFP
    r1 = _sp.run([_FFP, "-version"], capture_output=True, text=True)
    r2 = _sp.run([_FFP, "-filters"], capture_output=True, text=True)
    _interesting = {"overlay", "drawtext", "drawbox", "scale", "zoompan",
                    "fade", "rotate", "subtitles", "ass", "pad", "crop",
                    "eq", "colorbalance", "unsharp", "setpts", "atempo"}
    filters = [
        line for line in r2.stdout.split("\n")
        if any(tok in line.lower() for tok in _interesting)
    ]
    return {
        "ffmpeg_path": str(_FFP),
        "version": r1.stdout[:600],
        "version_stderr": r1.stderr[:200],
        "relevant_filters": filters,
        "total_filters_count": len([l for l in r2.stdout.split("\n") if l.strip()]),
    }


@app.get("/api/test-ffmpeg")
async def test_ffmpeg() -> dict:
    """Diagnostic: test four enable= syntax variants to find which one works.

    tA  backslash-comma, no quotes:  enable=gte(t\\,0.2)*lte(t\\,0.8)
    tB  no enable= at all            (baseline — must always pass)
    tC  plain commas, no quotes:     enable=gte(t,0.2)*lte(t,0.8)
    tD  volume backslash-comma:      volume=enable=gte(t\\,0.2)*lte(t\\,0.8):volume=0

    Each test uses:
      ffmpeg -f lavfi -i color=c=black:s=100x100:d=1
             -filter_complex {fc} -map [out] -f null -
    Returns returncode and stderr tail for every variant so we can see
    exactly which syntax FFmpeg 7.1.4 on Railway accepts.
    """
    import subprocess as _sp
    import os as _os
    from app.engine.transcribe import FFMPEG_PATH

    results: dict = {}

    def _t(label: str, cmd: list[str]) -> None:
        r = _sp.run(cmd, capture_output=True, text=True)
        results[label] = {
            "ok": r.returncode == 0,
            "returncode": r.returncode,
            "err": r.stderr[-600:],
        }

    BASE = [
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=black:s=100x100:d=1",
    ]

    # ── tA: backslash-comma, no quotes ───────────────────────────────────
    # Python \\, → literal \, in subprocess arg → FFmpeg sees \, as escaped comma
    fc_a = "[0:v]drawbox=x=0:y=0:w=50:h=50:color=red@1.0:t=fill:enable=gte(t\\,0.2)*lte(t\\,0.8)[out]"
    _t("tA_backslash_comma", BASE + [
        "-filter_complex", fc_a, "-map", "[out]", "-f", "null", "-",
    ])

    # ── tB: no enable= (baseline — always expected to pass) ──────────────
    fc_b = "[0:v]drawbox=x=0:y=0:w=50:h=50:color=red@1.0:t=fill[out]"
    _t("tB_no_enable", BASE + [
        "-filter_complex", fc_b, "-map", "[out]", "-f", "null", "-",
    ])

    # ── tC: plain commas, no quotes, no backslash ─────────────────────────
    fc_c = "[0:v]drawbox=x=0:y=0:w=50:h=50:color=red@1.0:t=fill:enable=gte(t,0.2)*lte(t,0.8)[out]"
    _t("tC_plain_commas", BASE + [
        "-filter_complex", fc_c, "-map", "[out]", "-f", "null", "-",
    ])

    # ── tD: volume filter with backslash-comma ────────────────────────────
    # Uses a sine source so [0:a] actually exists.
    _t("tD_volume_backslash", [
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-af", "volume=enable=gte(t\\,0.2)*lte(t\\,0.8):volume=0",
        "-f", "null", "-",
    ])

    results["all_passed"] = all(
        v.get("ok", False) for k, v in results.items() if k.startswith("t")
    )
    return results


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict:
    if not _auth_required():
        return {"required": False, "authed": True}
    cookie = request.cookies.get(AUTH_COOKIE)
    header_token = request.headers.get("x-access-token")
    token = cookie or header_token
    authed = bool(token) and secrets.compare_digest(token, settings.access_password)
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
        secure=True,
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
    video: UploadFile = File(None, max_upload_size=500 * 1024 * 1024),  # optional — omitted when upload_id is used; 500 MB ceiling for direct uploads
    upload_id: str = Form(""),        # set by chunked-upload flow
    instructions: str = Form(""),
    format_hint: Literal["short", "long", "auto"] = Form("auto"),
    caption_font: str = Form("Poppins Bold"),
    caption_color: str = Form("white"),
    caption_position: Literal["center", "bottom", "side-left", "side-right"] = Form("center"),
    caption_style: str = Form("impact"),
    editing_style: str = Form("viral"),
    brand_color: str = Form(""),
    theme: str = Form("dark-pro"),
    # Content brief fields (Feature 6)
    target_audience: str = Form(""),
    main_message: str = Form(""),
    desired_emotion: str = Form(""),
    platform: str = Form(""),
    content_type_hint: str = Form(""),
    # Template Memory (Feature 1)
    template_id: str = Form(""),
    # Style pack
    style_pack: str = Form("lean_glass"),
    # Coach profile (Feature 3)
    profile_id: str = Form(""),
    _: None = Depends(_check_auth),
) -> JSONResponse:
    if not settings.anthropic_api_key:
        raise HTTPException(500, "ANTHROPIC_API_KEY not set in backend/.env")

    # Load coach profile + enforce the plan's video quota BEFORE touching the
    # upload or creating a job — avoids spending Whisper/Claude costs on a
    # video that will never be delivered, and avoids accepting an upload
    # only to reject it afterwards.
    coach_profile: dict | None = None
    if profile_id:
        try:
            _profile_path = settings._data_root / "profiles" / f"{profile_id}.json"
            if _profile_path.exists():
                coach_profile = json.loads(_profile_path.read_text())
        except Exception:
            pass

    info = effective_plan_info(coach_profile)
    used = store.count_for_profile(profile_id, info["period"])
    # 80% quota warning — fire-and-forget, never blocks the request
    if (
        coach_profile
        and not info.get("unlimited")
        and 0 < info["limit"]
        and used >= int(info["limit"] * 0.8)
        and used < info["limit"]
        and profile_id
    ):
        _prof_warn_path = settings._data_root / "profiles" / f"{profile_id}.json"
        threading.Thread(
            target=_emails.send_quota_warning,
            args=(coach_profile, _prof_warn_path, used, info["limit"]),
            daemon=True,
        ).start()
    if used >= info["limit"]:
        period_label = "ce mois" if info["period"] == "monthly" else ""
        message = f"Tu as atteint ta limite de {info['limit']} vidéos {period_label} ({info['label']}). Passe à un plan supérieur pour continuer.".replace("  ", " ")
        raise HTTPException(403, {
            "error": "quota_exceeded",
            "message": message,
            "plan": (coach_profile or {}).get("plan") or DEFAULT_PLAN,
            "used": used,
            "limit": info["limit"],
        })

    # Refuse new edit submissions while a SIGTERM drain is in progress so the
    # job doesn't start and immediately get interrupted by the container dying.
    if _pipeline_shutdown.is_set():
        raise HTTPException(
            503,
            "Déploiement en cours — réessayez dans quelques secondes",
        )

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

    # Persist source path + run params so the job can be retried after a
    # server restart without the user having to re-upload the video.
    run_params = dict(
        instructions=instructions,
        format_hint=format_hint,
        caption_font=caption_font,
        caption_color=caption_color,
        caption_position=caption_position,
        caption_style=caption_style,
        editing_style=editing_style,
        brand_color=brand_color or None,
        aesthetic=theme,
        target_audience=target_audience,
        main_message=main_message,
        desired_emotion=desired_emotion,
        platform=platform,
        content_type_hint=content_type_hint,
        template_id=template_id,
        style_pack=style_pack,
        coach_profile=coach_profile,
    )
    store.update(job.id, source_path=str(dest), params=run_params, profile_id=profile_id)

    background.add_task(run_job, job.id, dest, **run_params)

    # Feature 15: record activity for social proof ticker
    try:
        _append_activity("vient d'éditer une vidéo")
    except Exception:
        pass

    return JSONResponse({"job_id": job.id, "status": job.status})


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, request: Request, _: None = Depends(_check_auth)) -> dict:
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_dict()


# ── Trash (soft-delete with 7-day auto-purge) ─────────────────────────────────
TRASH_RETENTION_HOURS = 7 * 24


@app.post("/api/jobs/{job_id}/trash")
def trash_job(job_id: str, _: None = Depends(_check_auth)) -> dict:
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    store.update(job_id, trashed_at=time.time())
    return {"job_id": job_id, "trashed": True}


@app.post("/api/jobs/{job_id}/restore")
def restore_job(job_id: str, _: None = Depends(_check_auth)) -> dict:
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    store.update(job_id, trashed_at=None)
    return {"job_id": job_id, "trashed": False}


@app.post("/api/retry/{job_id}")
async def retry_job(
    job_id: str,
    background: BackgroundTasks,
    request: Request,
    _: None = Depends(_check_auth),
) -> JSONResponse:
    """Re-run a failed job using the source video that is still on disk.
    Returns a NEW job_id — the frontend polls that one."""
    original = store.get(job_id)
    if not original:
        raise HTTPException(404, "Original job not found")
    if not original.source_path:
        raise HTTPException(400, "No source file stored for this job — please re-upload")
    src = Path(original.source_path)
    if not src.exists():
        raise HTTPException(400, "Source video is no longer on disk — please re-upload")

    new_job = store.create()
    run_params = original.params or {}
    # is_retry=True so this doesn't consume another quota slot — the user
    # already paid the cost of the original attempt.
    store.update(new_job.id, source_path=str(src), params=run_params,
                 profile_id=original.profile_id, is_retry=True)
    background.add_task(run_job, new_job.id, src, **run_params)
    return JSONResponse({"job_id": new_job.id})


@app.get("/api/jobs/{job_id}/plan")
def get_plan(job_id: str, request: Request, _: None = Depends(_check_auth)) -> dict:
    """Return the edit plan preview for a job that is in ready_for_review status."""
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status != "ready_for_review":
        raise HTTPException(400, f"Job is not ready for review (status: {job.status})")
    return {"plan_preview": job.preview or {}, "plan_data": job.plan_data or {}}


@app.post("/api/jobs/{job_id}/approve")
async def approve_job(
    job_id: str,
    background: BackgroundTasks,
    request: Request,
    _: None = Depends(_check_auth),
) -> JSONResponse:
    """Approve an edit plan and trigger the render phase (Phase 2)."""
    # Refuse render approvals during a SIGTERM drain for the same reason as /api/edit.
    if _pipeline_shutdown.is_set():
        raise HTTPException(
            503,
            "Déploiement en cours — réessayez dans quelques secondes",
        )

    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status != "ready_for_review":
        raise HTTPException(400, f"Job is not in ready_for_review state (status: {job.status})")
    if not job.source_path:
        raise HTTPException(400, "No source file stored for this job")
    src = Path(job.source_path)
    if not src.exists():
        raise HTTPException(400, "Source video is no longer on disk — please re-upload")

    store.update(job_id, status="rendering", progress=70,
                 message="Approved — starting render…")
    background.add_task(run_render_phase, job_id, src)
    return JSONResponse({"job_id": job_id, "status": "rendering"})


_WAITLIST_FILE = settings._data_root / "waitlist.json"


def _load_waitlist() -> list[dict]:
    try:
        return json.loads(_WAITLIST_FILE.read_text())
    except Exception:
        return []


def _save_waitlist(entries: list[dict]) -> None:
    _WAITLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    _WAITLIST_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False))


def _send_welcome_email(email: str) -> None:
    """Thin wrapper used by the /api/waitlist endpoint."""
    _emails.send_welcome({"email": email})


@app.post("/api/waitlist")
def waitlist_join(
    background: BackgroundTasks,
    payload: dict = Body(...),
) -> dict:
    email = (payload.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Invalid email")
    entries = _load_waitlist()
    is_new = not any(e.get("email") == email for e in entries)
    if is_new:
        entries.append({
            "email": email,
            "timestamp": payload.get("timestamp") or datetime.utcnow().isoformat(),
        })
        _save_waitlist(entries)
        # Send welcome email in background (non-blocking)
        background.add_task(_send_welcome_email, email)
    return {"success": True, "count": len(entries)}


@app.get("/api/waitlist/count")
def waitlist_count() -> dict:
    return {"count": len(_load_waitlist())}


_PROFILES_DIR = settings._data_root / "profiles"


# ── Re-engagement email loop (daily, mirrors the trash-purge pattern) ─────────

def _check_reengagement() -> None:
    """For each profile with at least one completed video, if the last completed
    job is older than 7 days, queue a re-engagement email (throttled per profile)."""
    try:
        for profile_path in _PROFILES_DIR.glob("*.json"):
            try:
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                pid = profile.get("profile_id") or profile_path.stem
                done_times = [
                    j.created_at for j in store._jobs.values()
                    if j.profile_id == pid and j.status == "done" and not j.is_retry
                ]
                if not done_times:
                    continue  # never edited — skip
                if time.time() - max(done_times) < 7 * 24 * 3600:
                    continue  # active in last 7 days — skip
                _emails.send_reengagement(profile, profile_path)
            except Exception as e:
                print(f"[reengagement] profile {profile_path.stem}: {e}")
    except Exception as e:
        print(f"[reengagement] scan failed: {e}")


async def _reengagement_loop() -> None:
    while True:
        await asyncio.sleep(24 * 3600)
        _check_reengagement()


@app.post("/api/profile")
async def save_profile(payload: dict = Body(...)) -> dict:
    """Persist a coach profile to disk. Returns a stable profile_id."""
    _PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    profile_id = payload.get("profile_id") or secrets.token_urlsafe(12)
    profile_path = _PROFILES_DIR / f"{profile_id}.json"

    # is_founder is a privileged, server-only flag (unlimited quota + 4K
    # access) -- never settable through this public endpoint. Strip any
    # client-supplied value and preserve whatever is already on disk.
    safe_payload = {k: v for k, v in payload.items() if k != "is_founder"}
    existing_is_founder = False
    if profile_path.exists():
        try:
            existing_is_founder = bool(
                json.loads(profile_path.read_text(encoding="utf-8")).get("is_founder")
            )
        except Exception:
            pass

    data = {**safe_payload, "profile_id": profile_id}
    if existing_is_founder:
        data["is_founder"] = True

    profile_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"profile_id": profile_id}


@app.get("/api/profile/{profile_id}")
async def get_profile(profile_id: str) -> dict:
    """Fetch a previously saved coach profile."""
    profile_path = _PROFILES_DIR / f"{profile_id}.json"
    if not profile_path.exists():
        raise HTTPException(404, "Profile not found")
    try:
        return json.loads(profile_path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(500, "Corrupt profile file")


def _oauth_redirect_uri(request: Request) -> str:
    configured = settings.app_base_url.strip().rstrip("/")
    base = configured if configured else str(request.base_url).rstrip("/")
    uri = f"{base}/api/auth/google/callback"
    print(f"[OAUTH] redirect_uri={uri!r}  (APP_BASE_URL={settings.app_base_url!r})")
    return uri


@app.get("/api/auth/google/login")
def google_login(request: Request) -> Response:
    if not settings.google_client_id:
        raise HTTPException(503, "Google login is not configured")
    state = secrets.token_urlsafe(24)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": _oauth_redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    resp = RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")
    resp.set_cookie(
        OAUTH_STATE_COOKIE, state,
        httponly=True, samesite="lax", secure=True, max_age=600,
    )
    return resp


@app.get("/api/auth/google/callback")
def google_callback(
    request: Request,
    code: str = Query(""),
    state: str = Query(""),
    error: str = Query(""),
) -> Response:
    base = settings.app_base_url.rstrip("/") if settings.app_base_url else str(request.base_url).rstrip("/")
    if error:
        return RedirectResponse(f"{base}/?login=error")

    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not state or not cookie_state or not secrets.compare_digest(state, cookie_state):
        raise HTTPException(400, "Invalid OAuth state")
    if not code:
        raise HTTPException(400, "Missing authorization code")

    try:
        token_resp = requests.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": _oauth_redirect_uri(request),
            "grant_type": "authorization_code",
        }, timeout=10)
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]

        userinfo_resp = requests.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        userinfo_resp.raise_for_status()
        info = userinfo_resp.json()
    except Exception as e:
        print(f"[OAUTH] Google exchange failed: {e}")
        raise HTTPException(502, "Google authentication failed")

    email = (info.get("email") or "").strip().lower()
    email_verified = bool(info.get("email_verified"))
    name = info.get("name") or ""

    if not email or not email_verified:
        raise HTTPException(403, "Google account email is not verified")

    _PROFILES_DIR.mkdir(parents=True, exist_ok=True)

    founder_email = (settings.founder_google_email or "").strip().lower()
    if founder_email and email == founder_email:
        # Exact match only -- no fuzzy/partial matching.
        profile_id = settings.founder_profile_id
        profile_path = _PROFILES_DIR / f"{profile_id}.json"
        profile: dict = {}
        if profile_path.exists():
            try:
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
            except Exception:
                profile = {}
        # Self-healing: recreate with is_founder=True even if the file was
        # lost again, so the founder never needs manual Railway-shell repair.
        profile["profile_id"] = profile_id
        profile["email"] = email
        profile.setdefault("name", name)
        profile["is_founder"] = True
        profile.setdefault("plan", "agency")
        profile_path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    else:
        profile_id = None
        for p_path in _PROFILES_DIR.glob("*.json"):
            try:
                p_data = json.loads(p_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if (p_data.get("email") or "").strip().lower() == email:
                profile_id = p_data.get("profile_id") or p_path.stem
                break
        if not profile_id:
            profile_id = secrets.token_urlsafe(12)
            new_profile = {
                "profile_id": profile_id,
                "email": email,
                "name": name,
                "plan": DEFAULT_PLAN,
            }
            profile_path = _PROFILES_DIR / f"{profile_id}.json"
            profile_path.write_text(json.dumps(new_profile, ensure_ascii=False, indent=2), encoding="utf-8")
            _emails.send_welcome(new_profile)

    resp = RedirectResponse(f"{base}/app?oauth=success")
    resp.delete_cookie(OAUTH_STATE_COOKIE)
    resp.set_cookie(
        SESSION_COOKIE, _sign_session(profile_id),
        httponly=True, samesite="lax", secure=True, max_age=60 * 60 * 24 * 30,
    )
    return resp


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict:
    profile_id = _verify_session(request.cookies.get(SESSION_COOKIE))
    if not profile_id:
        raise HTTPException(401, "Not logged in")
    profile_path = _PROFILES_DIR / f"{profile_id}.json"
    if not profile_path.exists():
        raise HTTPException(404, "Profile not found")
    try:
        return json.loads(profile_path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(500, "Corrupt profile file")


@app.get("/api/download/{job_id}")
@app.get("/api/v1/download/{job_id}")
def download(
    job_id: str,
    request: Request,
    fmt: str = Query("vertical"),
    _: None = Depends(_check_auth),
):
    out = settings.outputs_dir / f"{job_id}.mp4"
    if not out.exists():
        raise HTTPException(404, "Output not ready")

    if fmt == "landscape":
        landscape = settings.outputs_dir / f"{job_id}_landscape.mp4"
        if not landscape.exists():
            try:
                from app.engine.transcribe import FFMPEG_PATH
                subprocess.run([
                    FFMPEG_PATH, "-y", "-i", str(out),
                    "-vf",
                    "scale=1920:1080:force_original_aspect_ratio=decrease,"
                    "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black",
                    "-c:v", "libx264", "-crf", "20", "-preset", "fast",
                    "-c:a", "copy",
                    str(landscape),
                ], check=True, capture_output=True)
            except Exception as e:
                raise HTTPException(500, f"Landscape conversion failed: {e}")
        return FileResponse(landscape, media_type="video/mp4",
                            filename=f"youtube-{job_id}.mp4")

    return FileResponse(out, media_type="video/mp4", filename=f"edited-{job_id}.mp4")


@app.get("/api/upload/preview/{upload_id}")
def upload_preview(upload_id: str):
    """Extract a representative frame from an uploaded source video."""
    dest = assembled_path(upload_id)
    if dest is None:
        raise HTTPException(404, "No assembled file found")
    thumb = settings.uploads_dir / f"{upload_id}_preview.jpg"
    if not thumb.exists():
        try:
            from app.engine.transcribe import FFMPEG_PATH
            subprocess.run([
                FFMPEG_PATH, "-y", "-ss", "2", "-i", str(dest),
                "-vf", "scale=480:-2",
                "-frames:v", "1", "-q:v", "3",
                str(thumb),
            ], check=True, capture_output=True, timeout=15)
        except Exception as e:
            raise HTTPException(500, f"Preview extraction failed: {e}")
    return FileResponse(thumb, media_type="image/jpeg",
                        filename=f"preview-{upload_id}.jpg")


@app.get("/api/thumbnail/{job_id}")
def thumbnail(job_id: str, request: Request, _: None = Depends(_check_auth)):
    """Extract first frame as 1080×1080 square thumbnail."""
    out = settings.outputs_dir / f"{job_id}.mp4"
    if not out.exists():
        raise HTTPException(404, "Output not ready")
    thumb = settings.outputs_dir / f"{job_id}_thumb.jpg"
    if not thumb.exists():
        try:
            from app.engine.transcribe import FFMPEG_PATH
            subprocess.run([
                FFMPEG_PATH, "-y", "-i", str(out),
                "-vf",
                "scale=1080:1080:force_original_aspect_ratio=decrease,"
                "pad=1080:1080:(ow-iw)/2:(oh-ih)/2:color=black",
                "-frames:v", "1", "-q:v", "2",
                str(thumb),
            ], check=True, capture_output=True)
        except Exception as e:
            raise HTTPException(500, f"Thumbnail extraction failed: {e}")
    return FileResponse(thumb, media_type="image/jpeg",
                        filename=f"thumbnail-{job_id}.jpg")


# ── Feature 4: Hook Generator ─────────────────────────────────────────────────

@app.post("/api/generate-hooks")
async def generate_hooks(payload: dict = Body(...)) -> dict:
    """Ask Claude to generate 5 scored hook options for a topic."""
    topic = (payload.get("topic") or "").strip()
    if not topic:
        raise HTTPException(400, "topic required")
    if not settings.anthropic_api_key:
        raise HTTPException(500, "ANTHROPIC_API_KEY not set")

    try:
        import anthropic as _ant
        client = _ant.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1500,
            system=(
                "You are a viral short-form content strategist. "
                "Generate high-retention hooks. Always respond with valid JSON only — "
                "a JSON array of hook objects, nothing else."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Generate 5 different hook options for this topic: \"{topic}\"\n\n"
                    "Rules:\n"
                    "- Each hook ≤ 10 seconds spoken (≤ 20 words)\n"
                    "- Mix types: counterintuitive claim, specific stat, story opening, "
                    "contrast/flip, curiosity question\n"
                    "- Score 0–100 based on: curiosity gap + specificity + "
                    "counterintuitive element\n"
                    "- Write hooks in the SAME LANGUAGE as the topic\n"
                    "- The hook must NOT resolve the tension it creates\n\n"
                    "Return ONLY a JSON array:\n"
                    '[{"text":"...", "score":85, "why":"one sentence explaining why this '
                    'works"}, ...]'
                ),
            }],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        # Extract JSON array
        import re as _re
        m = _re.search(r"\[.*\]", raw, _re.DOTALL)
        hooks = json.loads(m.group(0)) if m else []
        return {"hooks": hooks[:5]}
    except Exception as e:
        raise HTTPException(500, f"Hook generation failed: {e}")


# ── Feature 6: Referral System ────────────────────────────────────────────────

_REFERRALS_FILE = settings._data_root / "referrals.json"


def _load_referrals() -> dict:
    try:
        return json.loads(_REFERRALS_FILE.read_text())
    except Exception:
        return {}


def _save_referrals(data: dict) -> None:
    _REFERRALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _REFERRALS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


@app.post("/api/referral/{ref_id}")
def track_referral(ref_id: str, request: Request) -> dict:
    """Record a referral visit for ref_id."""
    data = _load_referrals()
    entry = data.get(ref_id, {"count": 0, "visitors": []})
    ip = request.client.host if request.client else "unknown"
    # Deduplicate by IP
    if ip not in entry.get("visitors", []):
        entry["count"] = entry.get("count", 0) + 1
        visitors = entry.get("visitors", [])
        visitors.append(ip)
        entry["visitors"] = visitors[-200:]  # keep last 200
        entry["last_referral"] = datetime.utcnow().isoformat()
        data[ref_id] = entry
        _save_referrals(data)
    return {"ref_id": ref_id, "count": entry["count"]}


@app.get("/api/referral/{profile_id}/stats")
def get_referral_stats(profile_id: str) -> dict:
    """Return referral count for a profile."""
    data = _load_referrals()
    entry = data.get(profile_id, {"count": 0})
    return {"profile_id": profile_id, "count": entry.get("count", 0)}


# ── Feature 9: AI Video Coach ─────────────────────────────────────────────────

@app.post("/api/coach-chat")
async def coach_chat(payload: dict = Body(...)) -> dict:
    """Claude analyses the user's video history and answers coaching questions."""
    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "question required")
    if not settings.anthropic_api_key:
        raise HTTPException(500, "ANTHROPIC_API_KEY not set")

    video_history = payload.get("video_history") or []
    profile = payload.get("profile") or {}

    history_txt = ""
    if video_history:
        lines = []
        for v in video_history[:20]:
            title = v.get("title", "Untitled")
            score = v.get("retention_score", "?")
            fmt = v.get("format", "auto")
            date = v.get("date", "")[:10]
            lines.append(f"  - {date} | {title} | format={fmt} | retention={score}%")
        history_txt = "Historique vidéos récentes:\n" + "\n".join(lines)

    try:
        import anthropic as _ant
        client = _ant.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=800,
            system=(
                "Tu es un coach vidéo expert en short-form content (TikTok, Reels, Shorts). "
                "Tu analyses les données de performance de l'utilisateur et donnes des conseils "
                "concrets, actionnables, basés sur les données. "
                "Réponds en français, de manière directe et structurée. "
                "Maximum 4 points clés, chacun avec une action concrète."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Profil: {json.dumps(profile, ensure_ascii=False)}\n\n"
                    f"{history_txt}\n\n"
                    f"Question: {question}"
                ),
            }],
        )
        answer = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return {"answer": answer.strip()}
    except Exception as e:
        raise HTTPException(500, f"Coach chat failed: {e}")


# ── Feature 11: Competitor Analysis ──────────────────────────────────────────

@app.post("/api/analyze-competitor")
async def analyze_competitor(payload: dict = Body(...)) -> dict:
    """Ask Claude to analyse a competitor video URL for content structure."""
    url = (payload.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url required")
    if not settings.anthropic_api_key:
        raise HTTPException(500, "ANTHROPIC_API_KEY not set")

    try:
        import anthropic as _ant
        client = _ant.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1000,
            system=(
                "Tu es un expert en analyse de contenu viral (TikTok, YouTube Shorts, Reels). "
                "Tu analyses des vidéos à partir de leur URL et identifies les patterns "
                "de rétention. Réponds en français avec une analyse structurée."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Analyse cette vidéo concurrente: {url}\n\n"
                    "Fournis une analyse détaillée en JSON avec ces champs:\n"
                    '{"hook_type": "type de hook (counterintuitive/stat/story/question/contrast)", '
                    '"hook_text": "texte approximatif du hook (si deductible de l\'URL/titre)", '
                    '"estimated_loop": "mécanique de boucle supposée", '
                    '"caption_style": "style de captions supposé", '
                    '"structure": "description de la structure narrative", '
                    '"retention_factors": ["facteur1", "facteur2", "facteur3"], '
                    '"weakness": "point faible identifié", '
                    '"steal_this": "élément précis à adapter pour nos vidéos"}'
                ),
            }],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        import re as _re
        m = _re.search(r"\{.*\}", raw, _re.DOTALL)
        analysis = json.loads(m.group(0)) if m else {"raw": raw}
        return {"analysis": analysis}
    except Exception as e:
        raise HTTPException(500, f"Competitor analysis failed: {e}")


# ── Feature 14: Caption Editor ────────────────────────────────────────────────

def _parse_ass_captions(ass_path: Path) -> list[dict]:
    """Parse an ASS subtitle file and return list of {start, end, text} dicts."""
    import re as _re
    captions = []
    for line in ass_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        # Dialogue: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        start_s = parts[1].strip()
        end_s   = parts[2].strip()
        text    = parts[9].strip()
        # Strip ASS override tags like {\c...}
        text_clean = _re.sub(r"\{[^}]*\}", "", text).strip()
        if text_clean:
            captions.append({"start": start_s, "end": end_s, "text": text_clean})
    return captions


def _ass_ts_to_sec(ts: str) -> float:
    """Convert ASS timestamp H:MM:SS.cs to seconds float."""
    try:
        h, m, rest = ts.split(":")
        s, cs = rest.split(".")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100
    except Exception:
        return 0.0


@app.get("/api/jobs/{job_id}/captions")
def get_job_captions(job_id: str, request: Request, _: None = Depends(_check_auth)) -> dict:
    """Return parsed captions for a completed job."""
    ass_path = settings.work_dir / job_id / "captions.ass"
    if not ass_path.exists():
        return {"captions": []}
    captions = _parse_ass_captions(ass_path)
    return {"captions": captions}


@app.post("/api/edit-captions/{job_id}")
async def edit_captions(
    job_id: str,
    payload: dict = Body(...),
    request: Request = None,
    _: None = Depends(_check_auth),
) -> dict:
    """Accept edited captions and rewrite the ASS file.

    Payload: {"captions": [{"start":"0:00:00.50","end":"0:00:01.20","text":"New text"}, ...]}
    The ASS header is preserved; only Dialogue lines are replaced.
    """
    captions = payload.get("captions") or []
    if not captions:
        raise HTTPException(400, "captions required")

    ass_path = settings.work_dir / job_id / "captions.ass"
    if not ass_path.exists():
        raise HTTPException(404, f"No captions file for job {job_id}")

    # Read original to preserve header
    original = ass_path.read_text(encoding="utf-8")
    header_lines = []
    for line in original.splitlines():
        if line.startswith("Dialogue:"):
            break
        header_lines.append(line)

    # Build new Dialogue lines
    new_lines = header_lines[:]
    for cap in captions:
        start = cap.get("start", "0:00:00.00")
        end   = cap.get("end",   "0:00:00.00")
        text  = cap.get("text",  "").replace("\n", " ")
        new_lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    ass_path.write_text("\n".join(new_lines), encoding="utf-8")
    return {"status": "ok", "lines": len(captions)}


# ── Feature 15: Activity Feed ─────────────────────────────────────────────────

_ACTIVITY_FILE = settings._data_root / "activity.json"
_FIRST_NAMES = [
    "Marie", "Lucas", "Camille", "Thomas", "Emma", "Léo", "Sarah", "Antoine",
    "Clara", "Julien", "Manon", "Nicolas", "Inès", "Alexandre", "Chloé",
    "Maxime", "Laura", "Romain", "Alice", "Pierre",
]
_ACTIONS = [
    "vient d'éditer une vidéo",
    "a généré 5 hooks IA",
    "a optimisé ses captions",
    "a téléchargé sa vidéo TikTok",
    "a analysé un concurrent",
    "a planifié du contenu",
    "vient de rejoindre LeanRetention",
]


def _load_activity() -> list:
    try:
        return json.loads(_ACTIVITY_FILE.read_text())
    except Exception:
        return []


def _append_activity(action: str) -> None:
    """Append an anonymized activity entry."""
    import random as _rnd
    entries = _load_activity()
    entries.insert(0, {
        "name": _rnd.choice(_FIRST_NAMES),
        "action": action,
        "ts": datetime.utcnow().isoformat(),
    })
    _ACTIVITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ACTIVITY_FILE.write_text(
        json.dumps(entries[:200], ensure_ascii=False, indent=2)
    )


@app.get("/api/activity")
def get_activity() -> dict:
    """Return last 10 anonymized activity entries for social proof ticker."""
    return {"activity": _load_activity()[:10]}


# ── Feature 7: Weekly Email Digest ───────────────────────────────────────────

def _send_weekly_digests() -> None:
    """Iterate profiles and send weekly digest email to each coach."""
    import os
    try:
        import resend as _resend
    except ImportError:
        return
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        return
    _resend.api_key = api_key
    try:
        for profile_path in _PROFILES_DIR.glob("*.json"):
            try:
                profile = json.loads(profile_path.read_text())
                email = profile.get("email", "")
                if not email:
                    continue
                name = profile.get("name") or profile.get("brandName") or "Coach"
                html = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#080808;font-family:'Helvetica Neue',Arial,sans-serif">
  <div style="background:#080808;color:#F5F5F6;padding:40px 28px;max-width:520px;margin:0 auto">
    <div style="margin-bottom:24px">
      <span style="background:#FF7751;color:#080808;font-size:12px;font-weight:700;
                   padding:5px 10px;border-radius:5px">LEANRETENTION</span>
    </div>
    <h1 style="font-size:22px;font-weight:800;margin:0 0 8px">
      Votre résumé de la semaine, {name} 📊
    </h1>
    <p style="color:rgba(245,245,246,.6);font-size:15px;line-height:1.6;margin:0 0 24px">
      Continuez sur cette lancée — chaque vidéo optimisée améliore votre rétention.
    </p>
    <div style="background:rgba(255,255,255,.04);border-radius:10px;padding:18px 20px;
                margin-bottom:20px;border:1px solid rgba(255,255,255,.08)">
      <p style="color:#FF7751;font-size:12px;font-weight:700;text-transform:uppercase;
                letter-spacing:.06em;margin:0 0 10px">Conseil IA cette semaine</p>
      <p style="font-size:14px;line-height:1.6;margin:0">
        Ouvrez au moins <strong>2 boucles de curiosité</strong> dans les 30 premières secondes.
        Les vidéos avec 3+ loops retiennent 40% de spectateurs supplémentaires à la marque.
      </p>
    </div>
    <a href="https://leanretention.com/app"
       style="display:inline-block;background:#FF7751;color:#fff;padding:12px 24px;
              border-radius:8px;text-decoration:none;font-weight:700;font-size:14px">
      Éditer une nouvelle vidéo →
    </a>
    <div style="margin-top:36px;padding-top:18px;border-top:1px solid rgba(255,255,255,.07)">
      <p style="color:rgba(245,245,246,.3);font-size:11px;margin:0">
        LeanRetention · <a href="#" style="color:#FF7751;text-decoration:none">Se désabonner</a>
      </p>
    </div>
  </div>
</body></html>"""
                _resend.Emails.send({
                    "from": "hello@leanretention.com",
                    "to": [email],
                    "subject": f"📊 Votre résumé LeanRetention — semaine du {datetime.utcnow().strftime('%d/%m')}",
                    "html": html,
                })
            except Exception as e:
                print(f"[digest] Failed for profile {profile_path.stem}: {e}")
    except Exception as e:
        print(f"[digest] Error iterating profiles: {e}")


def _digest_scheduler() -> None:
    """Check every hour if it's Monday 9AM UTC → send digest."""
    try:
        now = datetime.utcnow()
        if now.weekday() == 0 and now.hour == 9 and now.minute < 60:
            _send_weekly_digests()
    except Exception as e:
        print(f"[digest] Scheduler error: {e}")
    t = threading.Timer(3600, _digest_scheduler)
    t.daemon = True
    t.start()


# Start digest scheduler in background (first check after 30s)
_digest_t = threading.Timer(30, _digest_scheduler)
_digest_t.daemon = True
_digest_t.start()


# ── Feature 17/18 — Description Generator ────────────────────────────────────
@app.post("/api/generate-descriptions")
async def generate_descriptions(payload: dict = Body(...)) -> dict:
    """Generate platform-specific descriptions via Claude."""
    from app.core.config import settings
    import anthropic

    job_id = payload.get("job_id", "")
    title = payload.get("title", "")
    fmt = payload.get("format", "")
    context = payload.get("context", "")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    prompt = f"""You are a social media copywriter expert. Generate platform-specific descriptions for a video.

Video title: {title}
Format: {fmt}
Context: {context}

Generate descriptions in French for:
1. YouTube (SEO-optimized, ~500 words, include timestamps placeholder, 5 keywords)
2. TikTok (max 150 chars + 5 hashtags)
3. Instagram (emotional hook + CTA + 10 hashtags)
4. LinkedIn (professional tone, insights, 3 paragraphs)

Respond ONLY with valid JSON in this exact format:
{{"youtube": "...", "tiktok": "...", "instagram": "...", "linkedin": "..."}}"""

    msg = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    # Extract JSON if wrapped in markdown
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except Exception:
        return {"youtube": raw, "tiktok": "", "instagram": "", "linkedin": ""}


# ── Feature 20 — Performance Storage ─────────────────────────────────────────
_PERF_FILE = settings._data_root / "performance.json"


def _load_perf() -> dict:
    try:
        return json.loads(_PERF_FILE.read_text()) if _PERF_FILE.exists() else {}
    except Exception:
        return {}


def _save_perf(data: dict) -> None:
    _PERF_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PERF_FILE.write_text(json.dumps(data, indent=2))


@app.post("/api/performance/{job_id}")
def save_performance(job_id: str, payload: dict = Body(...)) -> dict:
    data = _load_perf()
    data[job_id] = payload
    _save_perf(data)
    return {"ok": True}


@app.get("/api/performance/{job_id}")
def get_performance(job_id: str) -> dict:
    data = _load_perf()
    return data.get(job_id, {})


# ── Feature 21 — Team Collaboration ──────────────────────────────────────────
_TEAM_FILE = settings._data_root / "teams.json"


def _load_teams() -> dict:
    try:
        return json.loads(_TEAM_FILE.read_text()) if _TEAM_FILE.exists() else {}
    except Exception:
        return {}


def _save_teams(data: dict) -> None:
    _TEAM_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TEAM_FILE.write_text(json.dumps(data, indent=2))


@app.get("/api/team/{profile_id}")
def get_team(profile_id: str) -> dict:
    teams = _load_teams()
    return {"members": teams.get(profile_id, {}).get("members", []),
            "comments": teams.get(profile_id, {}).get("comments", [])}


@app.post("/api/team/{profile_id}")
def update_team(profile_id: str, payload: dict = Body(...)) -> dict:
    teams = _load_teams()
    if profile_id not in teams:
        teams[profile_id] = {"members": [], "comments": []}
    action = payload.get("action")
    if action == "add":
        member = payload.get("member", {})
        existing = [m["email"] for m in teams[profile_id]["members"]]
        if member.get("email") and member["email"] not in existing:
            teams[profile_id]["members"].append(member)
    elif action == "remove":
        email = payload.get("email")
        teams[profile_id]["members"] = [m for m in teams[profile_id]["members"] if m["email"] != email]
    elif action == "comment":
        comment = payload.get("comment", {})
        teams[profile_id]["comments"].insert(0, comment)
    _save_teams(teams)
    return {"ok": True}


# ── Feature 23 — API Keys ─────────────────────────────────────────────────────
_API_KEYS_FILE = settings._data_root / "api_keys.json"


def _load_api_keys() -> dict:
    try:
        return json.loads(_API_KEYS_FILE.read_text()) if _API_KEYS_FILE.exists() else {}
    except Exception:
        return {}


def _save_api_keys(data: dict) -> None:
    _API_KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _API_KEYS_FILE.write_text(json.dumps(data, indent=2))


def _validate_api_key(key: str) -> bool:
    if not key:
        return False
    data = _load_api_keys()
    return any(v.get("key") == key for v in data.values())


@app.post("/api/api-keys")
def create_api_key(payload: dict = Body(...)) -> dict:
    profile_id = payload.get("profile_id", "")
    key = payload.get("key", "")
    if not profile_id or not key:
        raise HTTPException(400, "profile_id and key required")
    data = _load_api_keys()
    data[profile_id] = {"key": key, "created": datetime.utcnow().isoformat(), "usage": 0}
    _save_api_keys(data)
    return {"ok": True}


@app.get("/api/api-keys/{profile_id}")
def get_api_key(profile_id: str) -> dict:
    data = _load_api_keys()
    entry = data.get(profile_id)
    return {"key": entry["key"] if entry else None, "usage": entry.get("usage", 0) if entry else 0}


# ── Feature 23 — Versioned API endpoints ─────────────────────────────────────
@app.post("/api/v1/edit")
async def v1_edit(
    request: Request,
    background: BackgroundTasks,
) -> JSONResponse:
    """Public API endpoint for programmatic video editing."""
    api_key = request.headers.get("x-api-key", "")
    if not _validate_api_key(api_key):
        raise HTTPException(401, "Invalid or missing X-API-Key")
    # Increment usage counter
    try:
        data = _load_api_keys()
        for v in data.values():
            if v.get("key") == api_key:
                v["usage"] = v.get("usage", 0) + 1
        _save_api_keys(data)
    except Exception:
        pass
    body = await request.json()
    job_id = secrets.token_hex(8)
    store.create(job_id)
    store.update(job_id, status="queued", message="API job queued")
    return JSONResponse({"job_id": job_id, "status": "queued"}, status_code=202)


@app.get("/api/v1/jobs/{job_id}")
def v1_get_job(job_id: str, request: Request) -> dict:
    api_key = request.headers.get("x-api-key", "")
    if not _validate_api_key(api_key):
        raise HTTPException(401, "Invalid or missing X-API-Key")
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {"job_id": job_id, "status": job.status, "progress": job.progress, "message": job.message}


@app.get("/api/test-broll")
async def test_broll():
    import requests, os
    key = os.environ.get("PEXELS_API_KEY", "")
    if not key:
        return {"error": "PEXELS_API_KEY not set", "key_set": False}
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": key},
            params={"query": "running", "per_page": 1, "orientation": "portrait"},
            timeout=10
        )
        return {
            "status": r.status_code,
            "key_set": True,
            "key_prefix": key[:8] + "...",
            "response": r.json()
        }
    except Exception as e:
        return {"error": str(e), "key_set": bool(key)}


@app.get("/api/caption-audit")
async def caption_audit():
    """Return the last job's caption coverage audit data."""
    audit_path = Path("/tmp/caption_audit.json")
    if not audit_path.exists():
        return {"error": "No audit data yet — run a job with RENDER_ENGINE=hyperframes first"}
    import json as _json
    data = _json.loads(audit_path.read_text())

    # Run the same coverage comparison the storyboard does
    src = data.get("source_words", [])
    remapped = data.get("remapped_words", [])

    # Find words in source but not in remapped (lost in pretrim)
    src_texts = [w["text"].strip() for w in src if w["text"].strip()]
    rem_texts = [w["text"].strip() for w in remapped if w["text"].strip()]

    lost_in_pretrim = []
    j = 0
    for i, word in enumerate(src_texts):
        if j < len(rem_texts) and rem_texts[j] == word:
            j += 1
        else:
            ctx = " ".join(src_texts[max(0,i-3):i]) + f" >>>{word}<<< " + " ".join(src_texts[i+1:i+4])
            lost_in_pretrim.append({"word": word, "index": i, "context": ctx,
                                     "start": src[i].get("start"), "end": src[i].get("end")})

    # Find zero-duration words in remapped
    zero_dur = [{"text": w["text"], "start": w["start"], "end": w["end"]}
                for w in remapped if w["end"] <= w["start"] and w["text"].strip()]

    return {
        "source_word_count": len(src_texts),
        "remapped_word_count": len(rem_texts),
        "lost_in_pretrim": lost_in_pretrim,
        "lost_count": len(lost_in_pretrim),
        "zero_duration_words": zero_dur,
        "zero_dur_count": len(zero_dur),
        "source_words": src,
        "remapped_words": remapped,
    }


@app.get("/api/test-hyperframes")
async def test_hyperframes():
    """Render a 3s GSAP animation via the official HyperFrames CLI and return frames."""
    import subprocess as _sp
    import time as _time
    import base64 as _b64
    from app.engine.transcribe import FFMPEG_PATH, FFPROBE_PATH

    work = Path("/tmp/hf_test")
    work.mkdir(parents=True, exist_ok=True)
    comp_dir = work / "project"
    comp_dir.mkdir(parents=True, exist_ok=True)
    results: dict = {"steps": {}}

    # Write a minimal HyperFrames composition with GSAP animation
    (comp_dir / "index.html").write_text("""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #111; overflow: hidden; }
  [data-composition-id="root"] { position: relative; width: 1920px; height: 1080px; overflow: hidden; }
  .grid { position: absolute; inset: 0; display: grid;
          grid-template-columns: repeat(8, 1fr); grid-template-rows: repeat(4, 1fr); gap: 4px; padding: 40px; }
  .cell { border: 2px solid #333; border-radius: 8px; display: flex;
          align-items: center; justify-content: center; font: bold 24px system-ui; color: #666; }
  .box { position: absolute; width: 300px; height: 300px; background: #00C3FF;
         border-radius: 20px; top: 50%; left: 50%;
         display: flex; align-items: center; justify-content: center;
         font: bold 48px system-ui; color: #fff; }
  .label { position: absolute; bottom: 120px; left: 0; right: 0; text-align: center;
           font: bold 36px system-ui; color: #aaa; }
</style>
</head>
<body>
  <div data-composition-id="root" data-start="0" data-duration="3" data-width="1920" data-height="1080">
    <div class="grid" id="grid"></div>
    <div class="box" id="box">ZOOM</div>
    <div class="label" id="label">HyperFrames Deterministic Render Test</div>
  </div>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<script>
  // Fill grid with numbered cells as visual landmarks
  const grid = document.getElementById('grid');
  for (let i = 0; i < 32; i++) {
    const c = document.createElement('div');
    c.className = 'cell';
    c.textContent = String(i + 1).padStart(2, '0');
    grid.appendChild(c);
  }

  // Timeline contract: paused:true, registered to window.__timelines
  window.__timelines = window.__timelines || {};
  const tl = gsap.timeline({ paused: true });

  // Entrance: box scales from 0.3 to 1.0 and centers
  tl.fromTo('#box',
    { scale: 0.3, xPercent: -50, yPercent: -50 },
    { scale: 1.0, xPercent: -50, yPercent: -50, duration: 0.8, ease: 'back.out(1.7)' },
    0.1);
  // Rotation
  tl.to('#box', { rotation: 360, duration: 2.0, ease: 'none' }, 0.5);
  // Scale up past 1.0
  tl.to('#box', { scale: 1.5, duration: 1.0, ease: 'power2.inOut' }, 1.5);
  // Label entrance
  tl.from('#label', { opacity: 0, y: 30, duration: 0.5, ease: 'power3.out' }, 0.3);
  // Label exit (final scene only per SKILL.md rules)
  tl.to('#label', { opacity: 0, duration: 0.3, ease: 'power2.in' }, 2.5);

  window.__timelines["root"] = tl;
</script>
</body>
</html>""", encoding="utf-8")

    # Render via official HyperFrames CLI
    output_path = work / "output.mp4"
    t0 = _time.time()
    try:
        r = _sp.run(
            [
                "npx", "hyperframes", "render",
                str(comp_dir),
                "-o", str(output_path),
                "--fps", "30",
                "--quality", "draft",
                "--no-page-side-compositing",
            ],
            capture_output=True, text=True, timeout=180,
            env={**__import__("os").environ, "DISPLAY": ":99"},
        )
    except Exception as e:
        results["error"] = str(e)
        return results

    render_time = round(_time.time() - t0, 2)
    results["steps"]["render"] = {
        "ok": r.returncode == 0,
        "time_s": render_time,
        "stdout": r.stdout[-2000:],
        "stderr": r.stderr[-3000:],
    }

    render_ok = r.returncode == 0 and output_path.exists()
    frames_b64: list[dict] = []

    if output_path.exists():
        results["output_size_mb"] = round(output_path.stat().st_size / 1024 / 1024, 2)
        p = _sp.run([
            FFPROBE_PATH,
            "-v", "error", "-show_entries", "stream=codec_name,width,height,duration",
            "-of", "json", str(output_path),
        ], capture_output=True, text=True)
        if p.returncode == 0:
            import json as _json
            results["output_probe"] = _json.loads(p.stdout)

        for ft in [0.1, 0.5, 1.0, 1.5, 2.0, 2.5, 2.9]:
            fp = work / f"frame_{ft:.1f}.jpg"
            _sp.run([
                FFMPEG_PATH, "-y", "-loglevel", "error",
                "-ss", f"{ft:.3f}", "-i", str(output_path),
                "-frames:v", "1", "-q:v", "3", str(fp),
            ], capture_output=True, text=True)
            if fp.exists():
                frames_b64.append({
                    "t": ft,
                    "data": _b64.b64encode(fp.read_bytes()).decode("ascii"),
                })
                fp.unlink(missing_ok=True)

    results["frames"] = frames_b64

    # Cleanup
    for f in [output_path, comp_dir / "index.html"]:
        try:
            f.unlink(missing_ok=True)
        except Exception:
            pass
    try:
        comp_dir.rmdir()
    except Exception:
        pass

    results["verdict"] = "PASS" if render_ok else "FAIL"
    return results


@app.get("/api/test-hyperframes/view", include_in_schema=False)
async def test_hyperframes_view():
    """Run the HyperFrames test and show frames in an HTML page."""
    result = await test_hyperframes()
    frames = result.get("frames", [])
    verdict = result.get("verdict", "UNKNOWN")
    render_time = result.get("steps", {}).get("render", {}).get("time_s", "?")
    size_mb = result.get("output_size_mb", "?")

    imgs = ""
    for f in frames:
        imgs += (
            f'<div style="text-align:center">'
            f'<img src="data:image/jpeg;base64,{f["data"]}" '
            f'style="width:420px;border:1px solid #333;border-radius:4px">'
            f'<div style="margin-top:4px;font-size:14px;color:#ccc">t = {f["t"]:.1f}s</div>'
            f'</div>'
        )

    err_detail = ""
    render_step = result.get("steps", {}).get("render", {})
    if not render_step.get("ok"):
        err_detail = f"""<details open style="margin-top:16px"><summary style="color:#f66">Render Error</summary>
<pre style="background:#220000;padding:12px;border-radius:4px;overflow-x:auto;font-size:11px;max-height:400px;color:#faa">{render_step.get('stderr','')}</pre>
<pre style="background:#222;padding:12px;border-radius:4px;overflow-x:auto;font-size:11px;max-height:400px">{render_step.get('stdout','')}</pre>
</details>"""

    html = f"""<!DOCTYPE html>
<html><head><title>HyperFrames Animation Test</title></head>
<body style="background:#111;color:#fff;font-family:system-ui;padding:24px">
<h2>HyperFrames Official CLI Test -- {verdict}</h2>
<p>Render: {render_time}s | Output: {size_mb} MB |
GSAP animation: scale 0.3-1.5, rotation 360deg, label fade</p>
{err_detail}
<div style="display:flex;flex-wrap:wrap;gap:16px;margin-top:16px">{imgs}</div>
</body></html>"""
    return Response(content=html, media_type="text/html")


# ── Public demo-video streaming (no auth, landing page) ───────────────────
# Set DEMO_VIDEO_BEFORE / DEMO_VIDEO_AFTER in Railway Variables to the job IDs.
# Files are served straight from /data without committing large binaries to git.
@app.get("/demo/{which}", include_in_schema=False)
def demo_video(which: str):
    if which not in ("before", "after"):
        raise HTTPException(404)
    job_id = (
        os.getenv("DEMO_VIDEO_BEFORE", "") if which == "before"
        else os.getenv("DEMO_VIDEO_AFTER", "")
    ).strip()
    print(f"[demo/{which}] job_id={job_id!r} DATA_DIR={os.getenv('DATA_DIR')!r}"
          f" outputs_dir={settings.outputs_dir} data_root={settings._data_root}",
          flush=True)
    if not job_id:
        print(f"[demo/{which}] 404 — env var empty", flush=True)
        raise HTTPException(404)
    _EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")
    checked: list[str] = []
    for search_dir in (
        settings.outputs_dir,
        settings._data_root / "outputs",
        settings.uploads_dir,
        settings._data_root / "uploads",
    ):
        for ext in _EXTS:
            candidate = search_dir / f"{job_id}{ext}"
            checked.append(str(candidate))
            if candidate.exists():
                print(f"[demo/{which}] FOUND {candidate}", flush=True)
                return FileResponse(
                    str(candidate), media_type="video/mp4",
                    headers={"Cache-Control": "public, max-age=3600"},
                )
    print(f"[demo/{which}] 404 — tried:\n" + "\n".join(f"  {p}" for p in checked), flush=True)
    raise HTTPException(404)


editor_dir = Path(__file__).resolve().parents[2] / "editor_frontend"
if not editor_dir.exists():
    # fallback for local dev where files live in frontend/
    editor_dir = Path(__file__).resolve().parents[2] / "frontend"

if editor_dir.exists():
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(str(editor_dir / "landing.html"))

    @app.get("/app", include_in_schema=False)
    def app_page():
        return FileResponse(str(editor_dir / "index.html"))

    # Style-pack preview clips are re-rendered and re-committed occasionally
    # (e.g. content simplification). Without an explicit Cache-Control, browsers
    # and the Railway edge fall back to heuristic caching off Last-Modified,
    # which can keep serving a stale clip after redeploy even on hard refresh.
    _PREVIEWS_DIR = (editor_dir / "previews").resolve()

    @app.get("/previews/{filename}", include_in_schema=False)
    def preview_clip(filename: str):
        if not filename.endswith(".mp4"):
            raise HTTPException(404)
        path = (_PREVIEWS_DIR / filename).resolve()
        if path.parent != _PREVIEWS_DIR or not path.is_file():
            raise HTTPException(404)
        return FileResponse(
            str(path),
            media_type="video/mp4",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    app.mount("/", StaticFiles(directory=str(editor_dir)), name="frontend")
