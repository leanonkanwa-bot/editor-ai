"""Analytics Feedback Loop Engine — Feature 4.

Collects performance metrics from YouTube and TikTok, scores videos,
extracts creator-specific patterns, and feeds insights back into future edits.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import BACKEND_DIR

ANALYTICS_DIR  = BACKEND_DIR / "storage" / "analytics"
INSIGHTS_FILE  = BACKEND_DIR / "storage" / "creator_insights.json"
ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class PerformanceScore:
    score: float          # 1–10
    platform: str
    metrics: dict[str, Any] = field(default_factory=dict)
    collected_at: str = ""


@dataclass
class CreatorInsight:
    insight: str
    confidence: float
    based_on: str
    action: str


@dataclass
class CreatorInsights:
    generated_at: str
    videos_analyzed: int
    top_insights: list[dict[str, Any]]
    recommended_settings: dict[str, Any]


def score_video(metrics: dict[str, Any], platform: str) -> float:
    """Return a 1–10 performance score based on platform-specific benchmarks."""
    if platform == "youtube":
        avp  = min(1.0, float(metrics.get("average_view_percentage", 30)) / 100)
        ctr  = min(1.0, float(metrics.get("click_through_rate", 3)) / 10)
        views = float(metrics.get("views", 0))
        likes = float(metrics.get("likes", 0))
        like_rate  = (likes / views) if views > 0 else 0.0
        comments   = float(metrics.get("comments", 0))
        comment_rate = (comments / views) if views > 0 else 0.0
        shares     = float(metrics.get("shares", 0))
        share_rate = (shares / views) if views > 0 else 0.0

        raw = (avp * 0.40 + ctr * 0.25 + min(1.0, like_rate * 100) * 0.15
               + min(1.0, comment_rate * 200) * 0.10
               + min(1.0, share_rate * 200) * 0.10)
        return round(max(1.0, min(10.0, raw * 10)), 2)

    elif platform == "tiktok":
        fvwr  = min(1.0, float(metrics.get("full_video_watched_rate", 0.20)))
        views = float(metrics.get("play_count", 0))
        shares  = float(metrics.get("share_count", 0))
        likes   = float(metrics.get("like_count", 0))
        comments = float(metrics.get("comment_count", 0))
        share_rate   = (shares / views)   if views > 0 else 0.0
        like_rate    = (likes / views)    if views > 0 else 0.0
        comment_rate = (comments / views) if views > 0 else 0.0

        raw = (fvwr * 0.45 + min(1.0, share_rate * 100) * 0.25
               + min(1.0, like_rate * 20) * 0.20
               + min(1.0, comment_rate * 500) * 0.10)
        return round(max(1.0, min(10.0, raw * 10)), 2)

    return 5.0


class AnalyticsEngine:
    """Collects metrics, scores videos, and extracts creator patterns."""

    # ── Data collection ───────────────────────────────────────────────────

    def collect_youtube(self, video_id: str, access_token: str) -> dict[str, Any]:
        """Fetch YouTube Analytics for a single video."""
        try:
            import urllib.request
            url = (
                "https://youtubeanalytics.googleapis.com/v2/reports"
                f"?ids=channel%3D%3D~mine&metrics=views,watchTimeinMinutes,"
                "averageViewDuration,averageViewPercentage,likes,comments,shares,"
                "subscribersGained,estimatedMinutesWatched,annotationClickThroughRate"
                f"&filters=video%3D%3D{video_id}&dimensions=video"
                "&startDate=2020-01-01&endDate=2099-12-31"
            )
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode())
            rows = data.get("rows", [[]])
            cols = [c["name"] for c in data.get("columnHeaders", [])]
            if rows:
                return dict(zip(cols, rows[0]))
        except Exception:
            pass
        return {}

    def collect_tiktok(self, video_id: str, access_token: str) -> dict[str, Any]:
        """Fetch TikTok video metrics."""
        try:
            import urllib.request
            import urllib.parse
            params = urllib.parse.urlencode({
                "fields": "play_count,like_count,comment_count,share_count,reach,average_watch_time",
                "filters": json.dumps({"video_ids": [video_id]}),
            })
            req = urllib.request.Request(
                f"https://open.tiktokapis.com/v2/video/query/?{params}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode())
            videos = data.get("data", {}).get("videos", [])
            return videos[0] if videos else {}
        except Exception:
            return {}

    def save_analytics(self, job_id: str, platform: str, metrics: dict[str, Any]) -> None:
        path = ANALYTICS_DIR / f"{job_id}.json"
        existing: dict[str, Any] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass

        existing.setdefault("job_id", job_id)
        existing.setdefault("platforms", {})
        existing["platforms"][platform] = {
            "metrics":      metrics,
            "score":        score_video(metrics, platform),
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
        path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

    def load_analytics(self, job_id: str) -> dict[str, Any]:
        path = ANALYTICS_DIR / f"{job_id}.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def load_all_analytics(self) -> list[dict[str, Any]]:
        results = []
        for p in sorted(ANALYTICS_DIR.glob("*.json")):
            try:
                results.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
        return results

    # ── Pattern extraction ────────────────────────────────────────────────

    def extract_patterns(self, all_analytics: list[dict[str, Any]]) -> CreatorInsights:
        """
        Analyse all videos with performance data.
        Returns insights about what works for this specific creator.
        """
        scored: list[dict[str, Any]] = []
        for entry in all_analytics:
            platforms = entry.get("platforms", {})
            scores    = [p["score"] for p in platforms.values() if "score" in p]
            if not scores:
                continue
            avg_score = sum(scores) / len(scores)
            scored.append({"entry": entry, "score": avg_score})

        if len(scored) < 3:
            return self._default_insights(len(scored))

        high = [s for s in scored if s["score"] >= 7.0]
        low  = [s for s in scored if s["score"] <= 4.0]

        insights: list[dict[str, Any]] = []
        recommended: dict[str, Any] = {}

        # Hook type insight.
        hook_insight = self._analyze_hook_types(high, low)
        if hook_insight:
            insights.append(hook_insight)
            recommended["hook_type"] = hook_insight.get("_best_hook", "stat")

        # Pacing insight.
        pacing_insight = self._analyze_pacing(high, low)
        if pacing_insight:
            insights.append(pacing_insight)
            cpm = pacing_insight.get("_best_cpm")
            if cpm:
                recommended["cuts_per_minute"] = cpm

        # Content type insight.
        ct_insight = self._analyze_content_types(high, low)
        if ct_insight:
            insights.append(ct_insight)
            recommended["preferred_content_type"] = ct_insight.get("_best_type", "coaching")

        # Graphic density insight.
        gfx_insight = self._analyze_graphics(high, low)
        if gfx_insight:
            insights.append(gfx_insight)
            recommended["graphic_density"] = gfx_insight.get("_best_density", 2.0)

        # Defaults for unresolved settings.
        recommended.setdefault("hook_type",             "stat")
        recommended.setdefault("cuts_per_minute",       15)
        recommended.setdefault("graphic_density",       2.0)
        recommended.setdefault("pause_before_principle", True)
        recommended.setdefault("preferred_content_type", "coaching")

        ci = CreatorInsights(
            generated_at=datetime.now(timezone.utc).isoformat(),
            videos_analyzed=len(scored),
            top_insights=[{k: v for k, v in ins.items() if not k.startswith("_")} for ins in insights],
            recommended_settings=recommended,
        )
        self._save_insights(ci)
        return ci

    def _analyze_hook_types(self, high: list, low: list) -> dict | None:
        def hook_types(group):
            types: dict[str, list[float]] = {}
            for s in group:
                ht = s["entry"].get("hook_type", "")
                if ht:
                    types.setdefault(ht, []).append(s["score"])
            return {k: sum(v)/len(v) for k, v in types.items()}

        high_types = hook_types(high)
        low_types  = hook_types(low)
        if not high_types:
            return None

        best = max(high_types, key=lambda k: high_types[k])
        if best not in low_types or high_types[best] > low_types.get(best, 0) * 1.2:
            ratio = round(high_types[best] / max(0.01, low_types.get(best, high_types[best] * 0.5)), 1)
            return {
                "insight":    f"Your {best}-led hooks get {ratio}x higher engagement than other hook types",
                "confidence": min(0.95, 0.5 + len(high) * 0.05),
                "based_on":   f"{len(high)} videos",
                "action":     f"Prefer {best} hooks in future edits",
                "_best_hook": best,
            }
        return None

    def _analyze_pacing(self, high: list, low: list) -> dict | None:
        def avg_cpm(group):
            cpms = [s["entry"].get("avg_cuts_per_minute", 0) for s in group
                    if s["entry"].get("avg_cuts_per_minute", 0) > 0]
            return round(sum(cpms) / len(cpms), 1) if cpms else None

        high_cpm = avg_cpm(high)
        low_cpm  = avg_cpm(low)
        if high_cpm is None or low_cpm is None or abs(high_cpm - low_cpm) < 2:
            return None

        direction = "faster" if high_cpm > low_cpm else "slower"
        retention_gain = round(abs(high_cpm - low_cpm) * 2.5, 0)
        return {
            "insight":  f"Videos with {int(high_cpm)}-{int(high_cpm)+2} cuts/min retain "
                        f"{retention_gain:.0f}% more viewers than {direction if direction=='slower' else 'faster'} pacing",
            "confidence": min(0.95, 0.5 + len(high) * 0.04),
            "based_on": f"{len(high)} videos",
            "action":   f"Target {int(high_cpm)} cuts/min for your content",
            "_best_cpm": int(high_cpm),
        }

    def _analyze_content_types(self, high: list, low: list) -> dict | None:
        def type_scores(group):
            ts: dict[str, list[float]] = {}
            for s in group:
                ct = s["entry"].get("content_type", "")
                if ct:
                    ts.setdefault(ct, []).append(s["score"])
            return {k: sum(v)/len(v) for k, v in ts.items()}

        high_types = type_scores(high)
        if not high_types:
            return None

        best = max(high_types, key=lambda k: high_types[k])
        return {
            "insight":   f"Your {best} content consistently outperforms other formats",
            "confidence": min(0.90, 0.5 + len(high) * 0.04),
            "based_on":  f"{len(high)} videos",
            "action":    f"Prioritise {best} framing in future edits",
            "_best_type": best,
        }

    def _analyze_graphics(self, high: list, low: list) -> dict | None:
        def avg_density(group):
            vals = [s["entry"].get("graphics_per_minute", 0) for s in group
                    if s["entry"].get("graphics_per_minute", 0) > 0]
            return round(sum(vals)/len(vals), 2) if vals else None

        high_d = avg_density(high)
        if high_d is None:
            return None
        return {
            "insight":   f"Top-performing videos average {high_d} graphics/min",
            "confidence": min(0.85, 0.4 + len(high) * 0.04),
            "based_on":  f"{len(high)} videos",
            "action":    f"Target {high_d} graphics/min",
            "_best_density": high_d,
        }

    def _default_insights(self, n: int) -> CreatorInsights:
        return CreatorInsights(
            generated_at=datetime.now(timezone.utc).isoformat(),
            videos_analyzed=n,
            top_insights=[{
                "insight":    "Not enough data yet — publish more videos and connect analytics",
                "confidence": 0.0,
                "based_on":   f"{n} videos",
                "action":     "Keep publishing to unlock personalized insights",
            }],
            recommended_settings={
                "hook_type":             "stat",
                "cuts_per_minute":       15,
                "graphic_density":       2.0,
                "pause_before_principle": True,
                "preferred_content_type": "coaching",
            },
        )

    def _save_insights(self, ci: CreatorInsights) -> None:
        INSIGHTS_FILE.write_text(
            json.dumps(asdict(ci), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def load_insights() -> dict[str, Any]:
    """Load latest creator insights from disk."""
    if not INSIGHTS_FILE.exists():
        return {}
    try:
        return json.loads(INSIGHTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_insights_instructions(insights: dict[str, Any]) -> str:
    """Convert insights into planner instruction hints."""
    rec = insights.get("recommended_settings", {})
    if not rec:
        return ""
    parts = []
    if "hook_type" in rec:
        parts.append(f"ANALYTICS INSIGHT: Prefer {rec['hook_type']}-style hooks — they perform best for this creator.")
    if "cuts_per_minute" in rec:
        parts.append(f"ANALYTICS INSIGHT: Target ~{rec['cuts_per_minute']} cuts/min — proven optimal pacing for this creator.")
    if "preferred_content_type" in rec:
        parts.append(f"ANALYTICS INSIGHT: Frame as {rec['preferred_content_type']} content — highest-performing format.")
    if "graphic_density" in rec:
        parts.append(f"ANALYTICS INSIGHT: Target {rec['graphic_density']} graphics/min.")
    return "\n".join(parts)
