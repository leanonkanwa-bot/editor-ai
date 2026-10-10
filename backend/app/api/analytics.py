"""Analytics Feedback Loop API — Feature 4.

Endpoints:
  GET  /api/analytics/overview          — summary stats + top insights
  GET  /api/analytics/videos            — all videos with scores
  GET  /api/analytics/videos/{job_id}   — full analytics for one video
  GET  /api/analytics/insights          — full CreatorInsights object
  POST /api/analytics/collect           — manually trigger data collection
  GET  /api/analytics/compare           — side-by-side comparison
"""

from __future__ import annotations

from fastapi import Request, APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from app.core.session import caller_profile, require_owner
from app.api.jobs import store
from app.engine.analytics_engine import (
    AnalyticsEngine,
    load_insights,
    score_video,
)
from app.engine.publisher import _load_history, _load_tokens

router = APIRouter()
engine = AnalyticsEngine()


def _mine(request: Request, entries: list) -> list:
    """The caller's own analytics entries.

    overview() and list_videos() called load_all_analytics() and returned the
    lot: every user saw every other user's video performance. Analytics have no
    owner field of their own, so ownership is taken from the job each entry
    describes — the same source require_owner uses.
    """
    caller = caller_profile(request)
    if not caller:
        raise HTTPException(401, "Not authenticated")
    out = []
    for e in entries:
        job = store.get(e.get("job_id", "") or "")
        if job is not None and job.profile_id and job.profile_id == caller:
            out.append(e)
    return out


@router.get("/api/analytics/overview")
def overview(request: Request) -> JSONResponse:
    all_data  = _mine(request, engine.load_all_analytics())
    insights  = load_insights()

    scored = []
    for entry in all_data:
        platforms = entry.get("platforms", {})
        scores = [p.get("score", 0) for p in platforms.values()]
        if scores:
            job = store.get(entry.get("job_id", ""))
            title = ""
            if job and job.result:
                pkg   = job.result.get("packaging", {})
                title = pkg.get("title", "") or (job.result.get("titres_ctr", [""])[0] if job.result.get("titres_ctr") else "")
            scored.append({
                "job_id":   entry.get("job_id", ""),
                "title":    title,
                "score":    round(sum(scores) / len(scores), 2),
                "platforms": list(platforms.keys()),
            })

    avg_score = round(sum(s["score"] for s in scored) / max(1, len(scored)), 2) if scored else 0.0
    best = max(scored, key=lambda x: x["score"]) if scored else None

    return JSONResponse({
        "total_videos":    len(scored),
        "avg_score":       avg_score,
        "best_performing": best,
        "top_insights":    insights.get("top_insights", [])[:3],
        "recommended_settings": insights.get("recommended_settings", {}),
    })


@router.get("/api/analytics/videos")
def list_videos(request: Request) -> JSONResponse:
    all_data = _mine(request, engine.load_all_analytics())
    result   = []
    for entry in all_data:
        platforms = entry.get("platforms", {})
        scores    = [p.get("score", 0) for p in platforms.values()]
        if scores:
            job = store.get(entry.get("job_id", ""))
            pkg = (job.result or {}).get("packaging", {}) if job else {}
            result.append({
                "job_id":    entry.get("job_id", ""),
                "title":     pkg.get("title", "—"),
                "score":     round(sum(scores)/len(scores), 2),
                "platforms": {
                    p: {"score": d.get("score", 0), "collected_at": d.get("collected_at", "")}
                    for p, d in platforms.items()
                },
            })
    result.sort(key=lambda x: x["score"], reverse=True)
    return JSONResponse(result)


@router.get("/api/analytics/videos/{job_id}")
def get_video_analytics(job_id: str, request: Request) -> JSONResponse:
    require_owner(request, job_id)
    data = engine.load_analytics(job_id)
    if not data:
        raise HTTPException(404, "No analytics found for this job")
    return JSONResponse(data)


@router.get("/api/analytics/insights")
def get_insights(request: Request) -> JSONResponse:
    # Aggregate rather than per-user, but derived from everyone's data, so it
    # is for signed-in callers rather than the open internet.
    if not caller_profile(request):
        raise HTTPException(401, "Not authenticated")
    insights = load_insights()
    if not insights:
        return JSONResponse({
            "generated_at":       "",
            "videos_analyzed":    0,
            "top_insights":       [],
            "recommended_settings": {},
        })
    return JSONResponse(insights)


@router.post("/api/analytics/collect")
async def collect_analytics(request: Request) -> JSONResponse:
    """Manually trigger metric collection for all published videos.

    Admin only: it spends every stored platform token, for every user, against
    YouTube's and TikTok's rate limits — and anyone who knew the URL could fire
    it in a loop.
    """
    from app.main import _admin_authed
    if not _admin_authed(request):
        raise HTTPException(401, "admin only")
    tokens  = _load_tokens()
    history = _load_history()
    collected = 0
    updated   = 0

    for record in history:
        platform   = record.get("platform", "")
        post_id    = record.get("post_id", "")
        job_id     = record.get("job_id", "")
        token      = tokens.get(platform, {}).get("access_token", "")

        if not (platform and post_id and token):
            continue

        existing = engine.load_analytics(job_id)
        if existing and platform in existing.get("platforms", {}):
            updated += 1
            continue

        metrics: dict = {}
        if platform == "youtube":
            metrics = engine.collect_youtube(post_id, token)
        elif platform == "tiktok":
            metrics = engine.collect_tiktok(post_id, token)

        if metrics:
            engine.save_analytics(job_id, platform, metrics)
            collected += 1

    # Re-run pattern extraction if we have new data.
    if collected > 0:
        all_data = engine.load_all_analytics()
        engine.extract_patterns(all_data)

    return JSONResponse({"collected": collected, "updated": updated})


@router.get("/api/analytics/compare")
def compare_videos(
    request: Request,
    job_id_a: str = Query(...),
    job_id_b: str = Query(...),
) -> JSONResponse:
    # Both, not either: comparing one's own video against a stranger's would
    # have read out the stranger's numbers just as effectively.
    require_owner(request, job_id_a)
    require_owner(request, job_id_b)
    a = engine.load_analytics(job_id_a)
    b = engine.load_analytics(job_id_b)
    if not a:
        raise HTTPException(404, f"No analytics for job {job_id_a}")
    if not b:
        raise HTTPException(404, f"No analytics for job {job_id_b}")

    def _extract(data: dict) -> dict:
        platforms = data.get("platforms", {})
        scores    = [p.get("score", 0) for p in platforms.values()]
        return {
            "job_id": data.get("job_id", ""),
            "score":  round(sum(scores)/max(1, len(scores)), 2),
            "platforms": {p: d.get("metrics", {}) for p, d in platforms.items()},
        }

    result = {"a": _extract(a), "b": _extract(b)}
    result["winner"] = "a" if result["a"]["score"] >= result["b"]["score"] else "b"
    result["score_diff"] = round(abs(result["a"]["score"] - result["b"]["score"]), 2)
    return JSONResponse(result)
