"""Single source of truth for plan video-quota limits.

period:
  "lifetime" — never resets (the free trial: 1 video, ever).
  "monthly"  — resets each calendar month (UTC).
"""

from __future__ import annotations

DEFAULT_PLAN = "free"

PLAN_LIMITS: dict[str, dict] = {
    "free":    {"label": "Essai gratuit", "limit": 1,   "period": "lifetime"},
    "starter": {"label": "Starter",       "limit": 15,  "period": "monthly"},
    "pro":     {"label": "Pro",           "limit": 50,  "period": "monthly"},
    "agency":  {"label": "Agency",        "limit": 150, "period": "monthly"},
}


def plan_info(plan: str | None) -> dict:
    return PLAN_LIMITS.get(plan or DEFAULT_PLAN, PLAN_LIMITS[DEFAULT_PLAN])
