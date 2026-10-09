"""Job tracker, persisted to disk so jobs survive container restarts.

In-memory only would lose every active job each time Railway redeploys.
This stores state at storage/jobs.json (an atomic write per update). On
boot we reload and triage non-terminal jobs:

  ready_for_review  → left unchanged (plan is on disk, no worker running)
  rendering         → if source + plan_data on disk, added to _resumable_jobs
                       for auto-requeue by startup; otherwise marked error
  queued            → if source on disk, added to _resumable_jobs for phase-1
                       requeue; otherwise marked error
  transcribing /
  planning          → marked error (can't resume mid-transcription); user can
                       click Retry which restarts from the stored source file
  anything else     → marked error with re-upload message

Mount a Railway Volume at /app/backend/storage (DATA_DIR=/data) so that
source files and jobs.json outlive container restarts. Without the volume,
source files vanish on every deploy and auto-resume falls back to error.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings


JOBS_FILE = settings._data_root / "jobs.json"
TERMINAL_STATUSES = {"done", "error"}
# Statuses that mean a plan exists on disk and no worker was running —
# safe to leave unchanged across a restart.
_STABLE_STATUSES = {"ready_for_review"}
# Statuses where a worker thread was actively running.
_ACTIVE_STATUSES = {"queued", "transcribing", "planning", "rendering"}
# Same set under a public name: the deploy guard needs it too, and the two
# must never drift apart.
ACTIVE_STATUSES = _ACTIVE_STATUSES
INTERRUPT_MESSAGE = "Serveur redémarré - veuillez re-uploader votre vidéo"


@dataclass
class Job:
    id: str
    status: str = "queued"
    progress: int = 0
    message: str = ""
    created_at: float = field(default_factory=time.time)
    result: dict[str, Any] | None = None
    error: str | None = None
    source_path: str | None = None          # absolute path to source video on disk
    params: dict[str, Any] = field(default_factory=dict)  # run_job kwargs for retry
    # Two-phase pipeline fields (Feature 7)
    plan_data: dict[str, Any] | None = None      # edit plan JSON (ready_for_review phase)
    transcript: dict[str, Any] | None = None     # whisper transcript dict
    subject_pos: dict[str, float] | None = None  # vision-detected face position
    energy_profile: list | None = None           # EnergyPoint dicts
    hook_overlay: dict[str, Any] | None = None   # rewrite_hook result
    preview: dict[str, Any] | None = None        # structured preview sent to frontend
    # Plan quota tracking
    profile_id: str | None = None   # coach profile that submitted this job
    is_retry: bool = False          # True if created via /api/retry — excluded from quota counting
    # Trash (Feature: soft-delete with 7-day auto-purge)
    trashed_at: float | None = None  # unix timestamp when moved to trash; None = not trashed
    # Wall-clock time when status reached "done" — used for render-time reporting
    completed_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Job":
        # Tolerate older / partial payloads.
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        # Populated by _load(); consumed once by main.py startup to dispatch
        # renders that were in-flight when the previous container died.
        self._resumable_jobs: list[tuple[str, "Job"]] = []
        self._load()

    def _load(self) -> None:
        if not JOBS_FILE.exists():
            return
        try:
            data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        for jid, jd in data.items():
            if not isinstance(jd, dict):
                continue
            try:
                self._jobs[jid] = Job.from_dict(jd)
            except TypeError:
                continue
        # Triage jobs that were non-terminal when the container died.
        for job in self._jobs.values():
            if job.status in TERMINAL_STATUSES:
                continue
            if job.status in _STABLE_STATUSES:
                # ready_for_review: plan is on disk, no worker was running.
                # Leave it unchanged so the user can still approve the plan.
                continue

            has_source = bool(job.source_path and Path(job.source_path).exists())
            has_plan   = bool(job.plan_data)

            if job.status == "rendering" and has_source and has_plan:
                # Phase 2 was running. Auto-restart: reset to queued so startup
                # dispatcher picks it up via _resumable_jobs.
                job.status   = "queued"
                job.progress = 65
                job.message  = "Rendu relancé après redémarrage du serveur…"
                job.is_retry = True
                self._resumable_jobs.append(("render", job))

            elif job.status == "queued" and has_source:
                # Never started — re-dispatch phase 1 from the stored source.
                self._resumable_jobs.append(("phase1", job))

            elif job.status in ("transcribing", "planning") and has_source:
                # Mid-transcription: can't resume. Mark error but keep source
                # so the user can retry without re-uploading.
                job.status   = "error"
                job.is_retry = True
                job.error    = "Analyse interrompue par redémarrage — cliquez Relancer"
                job.message  = "Analyse interrompue — crédit remboursé. Cliquez Relancer."
                job.progress = 100

            else:
                # Source gone or unknown state — full re-upload required.
                job.status   = "error"
                job.is_retry = True
                job.error    = INTERRUPT_MESSAGE
                job.message  = "Render interrompu — crédit remboursé."
                job.progress = 100

        # Persist triage results so a later GET /api/jobs/{id} sees correct state.
        self._save_locked()

    def _save_locked(self) -> None:
        """Caller must hold self._lock OR be in __init__ where no contention exists."""
        JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = JOBS_FILE.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({jid: j.to_dict() for jid, j in self._jobs.items()}),
            encoding="utf-8",
        )
        tmp.replace(JOBS_FILE)

    def create(self) -> Job:
        job = Job(id=uuid.uuid4().hex)
        with self._lock:
            self._jobs[job.id] = job
            self._save_locked()
        return job

    def update(self, job_id: str, **fields: Any) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            for k, v in fields.items():
                setattr(job, k, v)
            self._save_locked()
            return job

    def active(self) -> list["Job"]:
        """Jobs with a worker thread running, newest first.

        The deploy guard's source of truth. Deliberately the store rather than
        the render semaphore: the semaphore is held only for phase 2, so a job
        being transcribed or planned holds nothing and would read as idle.
        """
        with self._lock:
            jobs = [j for j in self._jobs.values()
                    if j.status in _ACTIVE_STATUSES and not getattr(j, "trashed", False)]
        jobs.sort(key=lambda j: getattr(j, "created_at", "") or "", reverse=True)
        return jobs

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def delete(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)
            self._save_locked()

    def list_for_profile(self, profile_id: str, *, trashed: bool | None = None) -> list[Job]:
        """List jobs submitted by this profile, optionally filtered by trash state.

        trashed=True -> only trashed jobs, trashed=False -> only non-trashed,
        trashed=None -> all jobs regardless of trash state.
        """
        if not profile_id:
            return []
        with self._lock:
            jobs = [j for j in self._jobs.values() if j.profile_id == profile_id]
        if trashed is True:
            jobs = [j for j in jobs if j.trashed_at is not None]
        elif trashed is False:
            jobs = [j for j in jobs if j.trashed_at is None]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    def purge_expired_trash(self, max_age_hours: float) -> int:
        """Permanently delete jobs trashed longer than max_age_hours: removes
        the rendered output (+ thumbnail/landscape variants) from disk and
        the job entry itself. Returns the number of jobs purged.
        """
        cutoff = time.time() - max_age_hours * 3600
        with self._lock:
            expired = [j for j in self._jobs.values() if j.trashed_at is not None and j.trashed_at < cutoff]
        for job in expired:
            out = settings.outputs_dir / f"{job.id}.mp4"
            thumb = settings.outputs_dir / f"{job.id}_thumb.jpg"
            landscape = settings.outputs_dir / f"{job.id}_landscape.mp4"
            for path in (out, thumb, landscape):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            self.delete(job.id)
        return len(expired)

    def count_for_profile(self, profile_id: str, period: str) -> int:
        """Count non-retry jobs submitted by this profile.

        period="monthly" restricts to the current UTC calendar month;
        period="lifetime" counts every job ever submitted by this profile.
        """
        if not profile_id:
            return 0
        now = datetime.now(timezone.utc)
        with self._lock:
            count = 0
            for job in self._jobs.values():
                if job.profile_id != profile_id or job.is_retry:
                    continue
                if period == "monthly":
                    jd = datetime.fromtimestamp(job.created_at, tz=timezone.utc)
                    if jd.year != now.year or jd.month != now.month:
                        continue
                count += 1
            return count


store = JobStore()
