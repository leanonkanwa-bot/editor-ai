"""FastAPI entrypoint for the AI Video Editor Agent."""

from __future__ import annotations

import json
import secrets
import shutil
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import (
    BackgroundTasks,
    Body,
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
from app.api.pipeline import run_job, run_render_phase
from app.api.upload import assembled_path, router as upload_router
from app.core.config import settings

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


app = FastAPI(title="AI Video Editor Agent", version="0.1.0")

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
    caption_style: Literal["impact", "kinetic"] = Form("impact"),
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
    # Coach profile (Feature 3)
    profile_id: str = Form(""),
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

    # Load coach profile from disk if profile_id provided
    coach_profile: dict | None = None
    if profile_id:
        try:
            from app.core.config import settings as _settings
            _profile_path = _settings.uploads_dir.parent / "profiles" / f"{profile_id}.json"
            if _profile_path.exists():
                import json as _json
                coach_profile = _json.loads(_profile_path.read_text())
        except Exception:
            pass

    # Persist source path + run params so the job can be retried after a
    # server restart without the user having to re-upload the video.
    run_params = dict(
        instructions=instructions,
        format_hint=format_hint,
        caption_font=caption_font,
        caption_color=caption_color,
        caption_position=caption_position,
        caption_style=caption_style,
        brand_color=brand_color or None,
        aesthetic=theme,
        target_audience=target_audience,
        main_message=main_message,
        desired_emotion=desired_emotion,
        platform=platform,
        content_type_hint=content_type_hint,
        template_id=template_id,
        coach_profile=coach_profile,
    )
    store.update(job.id, source_path=str(dest), params=run_params)

    background.add_task(run_job, job.id, dest, **run_params)
    return JSONResponse({"job_id": job.id, "status": job.status})


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, request: Request, _: None = Depends(_check_auth)) -> dict:
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_dict()


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
    store.update(new_job.id, source_path=str(src), params=run_params)
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


_WAITLIST_FILE = Path(__file__).resolve().parents[2] / "storage" / "waitlist.json"


def _load_waitlist() -> list[dict]:
    try:
        return json.loads(_WAITLIST_FILE.read_text())
    except Exception:
        return []


def _save_waitlist(entries: list[dict]) -> None:
    _WAITLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    _WAITLIST_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False))


def _send_welcome_email(email: str) -> None:
    """Fire-and-forget welcome email via Resend. Silently skips if key not set."""
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
        _resend.Emails.send({
            "from": "onboarding@resend.dev",
            "to": [email],
            "subject": "Bienvenue sur LeanRetention 🎬",
            "html": """
<!DOCTYPE html>
<html lang="fr">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#080808">
  <div style="font-family:'Helvetica Neue',Arial,sans-serif;background:#080808;color:#F5F5F6;
              padding:48px 32px;max-width:560px;margin:0 auto">

    <div style="margin-bottom:32px">
      <span style="background:#FF7751;color:#080808;font-size:13px;font-weight:700;
                   padding:6px 12px;border-radius:6px;letter-spacing:.04em">LEANRETENTION</span>
    </div>

    <h1 style="font-size:28px;font-weight:800;letter-spacing:-.02em;margin:0 0 12px;color:#F5F5F6">
      Bienvenue&nbsp;! Votre&nbsp;première vidéo&nbsp;vous&nbsp;attend.&nbsp;🔥
    </h1>

    <p style="font-size:16px;color:rgba(245,245,246,.65);line-height:1.6;margin:0 0 32px">
      LeanRetention analyse votre contenu, réécrit votre hook, supprime les
      silences et ajoute des captions automatiquement.<br><br>
      Première vidéo <strong style="color:#FF7751">offerte</strong> — aucune carte requise.
    </p>

    <a href="https://leanlead-production.up.railway.app/app"
       style="display:inline-block;background:#FF7751;color:#fff;
              padding:14px 28px;border-radius:8px;text-decoration:none;
              font-weight:700;font-size:15px;
              box-shadow:0 0 24px rgba(255,119,81,.35)">
      Commencer l'édition →
    </a>

    <div style="margin-top:48px;padding-top:24px;border-top:1px solid rgba(255,255,255,.08)">
      <p style="color:rgba(245,245,246,.35);font-size:12px;margin:0">
        LeanRetention · Paris<br>
        Vous recevez cet email car vous avez rejoint notre liste d'attente.<br>
        <a href="#" style="color:#FF7751;text-decoration:none">Se désabonner</a>
      </p>
    </div>
  </div>
</body>
</html>
""",
        })
    except Exception as e:
        print(f"[email] Failed to send welcome email to {email}: {e}")


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


_PROFILES_DIR = Path(__file__).resolve().parents[2] / "storage" / "profiles"


@app.post("/api/profile")
async def save_profile(payload: dict = Body(...)) -> dict:
    """Persist a coach profile to disk. Returns a stable profile_id."""
    _PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    profile_id = payload.get("profile_id") or secrets.token_urlsafe(12)
    profile_path = _PROFILES_DIR / f"{profile_id}.json"
    profile_path.write_text(
        json.dumps({**payload, "profile_id": profile_id}, ensure_ascii=False, indent=2),
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


@app.get("/api/download/{job_id}")
def download(job_id: str, request: Request, _: None = Depends(_check_auth)):
    out = settings.outputs_dir / f"{job_id}.mp4"
    if not out.exists():
        raise HTTPException(404, "Output not ready")
    return FileResponse(out, media_type="video/mp4", filename=f"edited-{job_id}.mp4")


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

    app.mount("/static", StaticFiles(directory=str(editor_dir)), name="frontend")
