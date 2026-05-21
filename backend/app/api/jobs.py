"""Job tracker, persisted to disk so jobs survive container restarts.

In-memory only would lose every active job each time Railway redeploys.
This stores state at storage/jobs.json (an atomic write per update). On
boot we reload + mark any non-terminal job as an error so polling clients
see a real message instead of a 404 / a forever-spinner.

NOTE: persistence only saves *state*, not the in-flight background task.
A job that was rendering when the container died cannot resume from where
it left off (the FFmpeg subprocess is gone). The user must re-upload.
For real continuity across deploys, mount a Railway Volume at
/app/backend/storage AND keep deploys clear of in-flight uploads.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import BACKEND_DIR


JOBS_FILE = BACKEND_DIR / "storage" / "jobs.json"
TERMINAL_STATUSES = {"done", "error"}
INTERRUPT_MESSAGE = (
    "Server restarted while this job was running. "
    "The work cannot be resumed — please re-upload your video."
)


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
        # Mark anything that was in flight as failed — there is no process
        # alive to finish it, the client should re-upload.
        for job in self._jobs.values():
            if job.status not in TERMINAL_STATUSES:
                job.status = "error"
                job.error = INTERRUPT_MESSAGE
                job.message = "Interrupted by server restart."
                job.progress = 100
        # Persist the corrections so a later request sees the right state.
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

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)


store = JobStore()
