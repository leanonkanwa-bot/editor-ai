"""FastAPI entrypoint for the AI Video Editor Agent."""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import threading
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
    Query,
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


app = FastAPI(title="AI Video Editor Agent", version="0.1.0")


@app.on_event("startup")
def _on_startup() -> None:
    _cleanup_old_uploads()

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

_REFERRALS_FILE = Path(__file__).resolve().parents[2] / "storage" / "referrals.json"


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

_ACTIVITY_FILE = Path(__file__).resolve().parents[2] / "storage" / "activity.json"
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
    <a href="https://leanlead-production.up.railway.app/app"
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
                    "from": "digest@resend.dev",
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
_PERF_FILE = Path(__file__).resolve().parents[2] / "storage" / "performance.json"


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
_TEAM_FILE = Path(__file__).resolve().parents[2] / "storage" / "teams.json"


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
_API_KEYS_FILE = Path(__file__).resolve().parents[2] / "storage" / "api_keys.json"


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

    app.mount("/", StaticFiles(directory=str(editor_dir)), name="frontend")
