"""Template Memory API — Feature 1.

Endpoints:
  POST /api/templates/analyze    — analyse a video and save its style as a template
  GET  /api/templates            — list all saved templates
  GET  /api/templates/{id}       — get full template JSON
  DELETE /api/templates/{id}     — delete a template
  POST /api/templates/{id}/apply/{job_id} — apply template overrides to a job
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.engine.template_engine import (
    TemplateAnalyzer,
    apply_template,
    delete_template,
    get_template,
    list_templates,
)
from app.api.jobs import store

router = APIRouter()
analyzer = TemplateAnalyzer()


@router.post("/api/templates/analyze")
async def analyze_template(
    name: str = Form(...),
    video: UploadFile = File(None),
    video_url: str = Form(""),
) -> JSONResponse:
    """Analyse a reference video and save its editing style as a template."""
    work_dir = settings.work_dir / "_template_analysis"
    work_dir.mkdir(parents=True, exist_ok=True)

    if video and video.filename:
        suffix  = Path(video.filename).suffix or ".mp4"
        tmp_path = work_dir / f"ref{suffix}"
        with tmp_path.open("wb") as f:
            shutil.copyfileobj(video.file, f)
        video_path = tmp_path
    elif video_url:
        import urllib.request
        tmp_path = work_dir / "ref_url.mp4"
        urllib.request.urlretrieve(video_url, tmp_path)
        video_path = tmp_path
    else:
        raise HTTPException(400, "Provide either a video file or video_url.")

    try:
        template = analyzer.analyze_video(video_path, name)
    finally:
        if video_path.exists():
            video_path.unlink(missing_ok=True)

    style = template.get("style", {})
    return JSONResponse({
        "template_id": template["id"],
        "name":        template["name"],
        "style_summary": {
            "pacing":          style.get("pacing"),
            "zoom_intensity":  style.get("zoom_intensity"),
            "caption_style":   style.get("caption_style"),
            "energy_level":    style.get("energy_level"),
            "cuts_per_minute": style.get("avg_cuts_per_minute"),
            "color_temperature": style.get("color_temperature"),
        },
    })


@router.get("/api/templates")
def list_all_templates() -> JSONResponse:
    return JSONResponse(list_templates())


@router.get("/api/templates/{template_id}")
def get_one_template(template_id: str) -> JSONResponse:
    t = get_template(template_id)
    if not t:
        raise HTTPException(404, "Template not found")
    return JSONResponse(t)


@router.delete("/api/templates/{template_id}")
def remove_template(template_id: str) -> JSONResponse:
    if not delete_template(template_id):
        raise HTTPException(404, "Template not found")
    return JSONResponse({"deleted": True})


@router.post("/api/templates/{template_id}/apply/{job_id}")
def apply_to_job(template_id: str, job_id: str) -> JSONResponse:
    """Apply template overrides to an existing job's stored params."""
    t = get_template(template_id)
    if not t:
        raise HTTPException(404, "Template not found")

    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    params         = dict(job.params or {})
    updated_params = apply_template(t, params)

    overrides = [k for k in updated_params if k not in params or updated_params[k] != params[k]]
    store.update(job_id, params=updated_params)

    return JSONResponse({"applied": True, "overrides_applied": overrides})
