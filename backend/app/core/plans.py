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


# Founder accounts: unlimited usage + agency-tier access, independent of
# Stripe billing. is_founder is a privileged flag that can ONLY be set by
# direct server-side file edit (see main.py's save_profile, which strips it
# from any client-supplied payload) -- never settable through the public API.
FOUNDER_LIMIT = 99999


def effective_plan_info(profile: dict | None) -> dict:
    """Like plan_info(), but a founder profile always gets agency-tier
    access with a near-unlimited quota, regardless of the profile's 'plan'
    field or Stripe billing status.
    """
    profile = profile or {}
    if profile.get("is_founder"):
        info = dict(PLAN_LIMITS["agency"])
        info["limit"] = FOUNDER_LIMIT
        return info
    return plan_info(profile.get("plan"))


def has_4k_access(profile: dict | None) -> bool:
    """4K export is currently founder-exclusive -- not a paid-plan feature
    yet. When 4K becomes a real paid tier, extend this check rather than
    adding a second flag; is_founder already implies it today.
    """
    return bool((profile or {}).get("is_founder"))
