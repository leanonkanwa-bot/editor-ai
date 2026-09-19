"""Signed sign-in session, and the job-ownership check built on it.

The session helpers moved here from main.py unchanged (same secret, same
format), so routers outside main.py can identify the caller too. A job is
only reachable by the profile that owns it: the caller is always taken from
the signed cookie, never from anything the client sends.
"""
from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException, Request

from app.core.config import settings

SESSION_COOKIE = "lle_session"


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


def caller_profile(request: Request) -> str | None:
    return _verify_session(request.cookies.get(SESSION_COOKIE))


def require_owner(request: Request, job_id: str):
    """The job, if the signed-in caller owns it.

    No valid session: 401. A job of another profile, with no recorded owner, or
    missing: the same 404, so the routes do not reveal which job ids exist.
    """
    from app.api.jobs import store  # late import: app.api.jobs imports config only

    caller = caller_profile(request)
    if not caller:
        raise HTTPException(401, "Not authenticated")
    job = store.get(job_id)
    if not job or not job.profile_id or job.profile_id != caller:
        raise HTTPException(404, "Job not found")
    return job


def require_api_owner(api_key_profile: str | None, job_id: str):
    """Same check for the /api/v1 routes, whose caller is an API key's profile."""
    from app.api.jobs import store

    if not api_key_profile:
        raise HTTPException(401, "Invalid or missing X-API-Key")
    job = store.get(job_id)
    if not job or not job.profile_id or job.profile_id != api_key_profile:
        raise HTTPException(404, "Job not found")
    return job
