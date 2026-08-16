#!/usr/bin/env python3
"""
Standalone local pipeline runner — bypasses FastAPI server.
Runs Phase 1 (transcribe + plan + LLM editorial cuts) + Phase 2 (render)
directly on a local video file, printing all logs to stdout.

Usage:
    python backend/run_local_pipeline.py

Output video: backend/storage/outputs/<job_id>/output.mp4
"""
import os
import sys
import time
import uuid
from pathlib import Path

# Force UTF-8 stdout so French/Unicode log messages don't crash on Windows cp1252
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND   = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND))

# ── Environment variables — must be set BEFORE importing settings ─────────────
# Mirror Railway production settings for the 3 failing signals:
#   CUT_FILLERS=true  → filler + LLM-edit cuts are applied
#   REPORT_ONLY=true  → detection PDF mode is on, but CUT_FILLERS=true overrides
#                       it at line 1417 so cuts ARE applied (prod behaviour)
# For render output, set REPORT_ONLY=false to get the actual rendered video.
os.environ.setdefault("CUT_FILLERS",  "true")
os.environ.setdefault("REPORT_ONLY",  "false")
os.environ.setdefault("CUT_PAUSES",   "true")

# Load ANTHROPIC_API_KEY from .env if not already in env
_env_file = BACKEND / ".env"
if _env_file.exists() and not os.environ.get("ANTHROPIC_API_KEY"):
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        if _line.startswith("ANTHROPIC_API_KEY="):
            os.environ["ANTHROPIC_API_KEY"] = _line.split("=", 1)[1].strip()

# ── Video source ───────────────────────────────────────────────────────────────
SRC = REPO_ROOT / "4d9673bf66de428a9f258b978ed2d526.mp4"
if not SRC.exists():
    sys.exit(f"ERROR: source video not found at {SRC}")

print(f"[LOCAL] Source: {SRC} ({SRC.stat().st_size // 1024} KB)", flush=True)

# ── Mock JobStore — in-memory, no disk writes ──────────────────────────────────
class _MockJob:
    def __init__(self, job_id: str, src_path: str):
        self.id             = job_id
        self.status         = "queued"
        self.progress       = 0
        self.message        = ""
        self.transcript     = None
        self.plan_data      = None
        self.subject_pos    = None
        self.energy_profile = None
        self.params         = {}
        self.source_path    = src_path
        self.profile_id     = None
        self.is_retry       = False
        self.hook_overlay   = None
        self.preview        = None
        self.result         = None
        self.error          = None
        self.created_at     = time.time()
        self.trashed_at     = None
        self.completed_at   = None

    def to_dict(self):
        return vars(self)


class _MockStore:
    def __init__(self, job: _MockJob):
        self._job = job

    def update(self, job_id: str, **fields):
        for k, v in fields.items():
            setattr(self._job, k, v)
        # Print status/progress updates but skip large blobs
        _skip = {"transcript", "plan_data", "energy_profile", "result", "preview",
                 "hook_overlay", "subject_pos"}
        _shown = {k: v for k, v in fields.items() if k not in _skip}
        if _shown:
            print(f"[STORE] {', '.join(f'{k}={v!r}' for k, v in _shown.items())}", flush=True)

    def get(self, job_id: str):
        return self._job


# ── Patch store before importing pipeline ─────────────────────────────────────
import app.api.jobs as _jobs_module
JOB_ID = uuid.uuid4().hex[:12]
_mock_job = _MockJob(JOB_ID, str(SRC))
_mock_store = _MockStore(_mock_job)
_jobs_module.store = _mock_store          # patch the module-level singleton

import app.api.pipeline as _pipeline_module
_pipeline_module.store = _mock_store      # patch the reference already imported in pipeline

print(f"[LOCAL] job_id={JOB_ID}", flush=True)

# ── Phase 1: transcription + planning + LLM editorial cuts ────────────────────
print("\n" + "="*70, flush=True)
print("PHASE 1 — transcription + planning + LLM editorial cuts", flush=True)
print("="*70, flush=True)

t0 = time.perf_counter()
try:
    _pipeline_module.run_job(
        job_id       = JOB_ID,
        src          = SRC,
        instructions = "",          # no special instructions — use default
        format_hint  = "short",     # portrait short-form (as in production)
        style_pack   = "lean_glass",
    )
except SystemExit as e:
    print(f"[LOCAL] run_job raised SystemExit({e})", flush=True)
except Exception as e:
    import traceback
    print(f"[LOCAL] run_job raised {type(e).__name__}: {e}", flush=True)
    traceback.print_exc()

elapsed1 = time.perf_counter() - t0
print(f"\n[LOCAL] Phase 1 done in {elapsed1:.1f}s — status={_mock_job.status}", flush=True)

if _mock_job.status == "error":
    print(f"[LOCAL] PHASE 1 ERROR: {_mock_job.error}", flush=True)
    sys.exit(1)

# ── Phase 2: render ───────────────────────────────────────────────────────────
print("\n" + "="*70, flush=True)
print("PHASE 2 — render", flush=True)
print("="*70, flush=True)

t1 = time.perf_counter()
try:
    _pipeline_module.run_render_phase(job_id=JOB_ID, src=SRC)
except SystemExit as e:
    print(f"[LOCAL] run_render_phase raised SystemExit({e})", flush=True)
except Exception as e:
    import traceback
    print(f"[LOCAL] run_render_phase raised {type(e).__name__}: {e}", flush=True)
    traceback.print_exc()

elapsed2 = time.perf_counter() - t1
print(f"\n[LOCAL] Phase 2 done in {elapsed2:.1f}s — status={_mock_job.status}", flush=True)

if _mock_job.error:
    print(f"[LOCAL] PHASE 2 ERROR: {_mock_job.error}", flush=True)

# ── Output file location ───────────────────────────────────────────────────────
from app.core.config import settings
output_candidates = list((settings.outputs_dir / JOB_ID).glob("*.mp4"))
if not output_candidates:
    output_candidates = list(settings.outputs_dir.glob(f"*{JOB_ID}*.mp4"))

print("\n" + "="*70, flush=True)
if output_candidates:
    out = output_candidates[0]
    print(f"[LOCAL] OUTPUT: {out}", flush=True)
    print(f"[LOCAL] SIZE:   {out.stat().st_size // 1024} KB", flush=True)
else:
    print(f"[LOCAL] WARNING: no output .mp4 found in {settings.outputs_dir}", flush=True)
    # Try result dict
    if _mock_job.result and isinstance(_mock_job.result, dict):
        url = _mock_job.result.get("url") or _mock_job.result.get("video_url")
        print(f"[LOCAL] result.url = {url}", flush=True)

print(f"[LOCAL] Total: {time.perf_counter()-t0:.1f}s", flush=True)
