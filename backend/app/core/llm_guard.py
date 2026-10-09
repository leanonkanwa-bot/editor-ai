"""Who may spend Anthropic credit, and how often.

Five routes called Claude for anyone who knew the URL: no session, no owner, no
ceiling. With a key billed per token that is a stranger's budget to spend, and
the bills that prompted this audit were $67 and $36 on consecutive days.

Two limits, because they fail differently:

  - per profile, so one signed-in account cannot loop on a route;
  - a global ceiling, so a handful of accounts together cannot either. It is the
    backstop for the case the per-profile limit cannot see.

The counters live in memory. That is deliberate and worth stating plainly: a
restart clears them, and a second container would hold its own. This is a spend
brake, not a security boundary — the security boundary is the session check
above it, which is exact. A durable counter belongs with the billing data, and
usage records already exist for that.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import HTTPException, Request

from app.core.session import caller_profile

# An interactive assistant: a person clicking buttons stays far below these.
PER_PROFILE_PER_HOUR = 40
GLOBAL_PER_HOUR = 300
WINDOW_S = 3600.0

_lock = threading.Lock()
_by_profile: dict[str, deque[float]] = {}
_global: deque[float] = deque()


def _trim(dq: deque[float], now: float) -> None:
    cutoff = now - WINDOW_S
    while dq and dq[0] < cutoff:
        dq.popleft()


def require_llm_caller(request: Request, *, cost: int = 1) -> str:
    """The signed-in profile allowed to spend here, or raise 401/429.

    `cost` lets a heavier route count for more than one call.
    """
    profile = caller_profile(request)
    if not profile:
        raise HTTPException(401, "Not authenticated")

    now = time.monotonic()
    with _lock:
        dq = _by_profile.setdefault(profile, deque())
        _trim(dq, now)
        _trim(_global, now)

        if len(dq) + cost > PER_PROFILE_PER_HOUR:
            raise HTTPException(429, "Trop de requêtes — réessayez dans quelques minutes.")
        if len(_global) + cost > GLOBAL_PER_HOUR:
            raise HTTPException(429, "Service temporairement saturé — réessayez plus tard.")

        for _ in range(cost):
            dq.append(now)
            _global.append(now)

        # a profile that stopped calling should not keep an entry for ever
        if len(_by_profile) > 5000:
            for key in [k for k, v in _by_profile.items() if not v]:
                del _by_profile[key]

    return profile


def _reset_for_tests() -> None:
    with _lock:
        _by_profile.clear()
        _global.clear()
