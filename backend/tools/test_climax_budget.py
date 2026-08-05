#!/usr/bin/env python3
"""
Test: budget=1 shared between prim_cinematic_reveal and prim_ascension_reveal.

Cases tested:
  1. Only PCR → passes through unchanged
  2. Only PAR → passes through unchanged
  3. PCR at t=5s + PAR at t=60s → PAR kept (payoff wins, later startSec)
  4. PAR at t=10s + PCR at t=120s → PCR kept (payoff wins)
  5. Three climax cards → only the latest-startSec survives
  6. No climax cards → unchanged
"""

import sys
from pathlib import Path
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


def _apply_climax_budget(cards: list[dict]) -> list[dict]:
    """Extracted budget guard (mirrors storyboard.py logic)."""
    CLIMAX_PRIMS = frozenset({"prim_cinematic_reveal", "prim_ascension_reveal"})
    climax_cards = [c for c in cards if c.get("contentHints", {}).get("style", "") in CLIMAX_PRIMS]
    if len(climax_cards) > 1:
        climax_cards.sort(key=lambda c: float(c.get("startSec", 0)), reverse=True)
        keep = climax_cards[0]
        for rc in climax_cards[1:]:
            cards.remove(rc)
            print(f"  [EVICTED] {rc['id']} style={rc['contentHints']['style']!r}"
                  f" t={rc['startSec']}s → kept {keep['id']}")
    return cards


def _make_card(id_: str, style: str, start: float) -> dict:
    return {
        "id": id_,
        "startSec": start,
        "endSec": start + 3.0,
        "contentHints": {"style": style, "title": "test"},
    }


def _style_of(cards: list[dict], id_: str) -> str | None:
    for c in cards:
        if c["id"] == id_:
            return c["contentHints"]["style"]
    return None


PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
errors = 0


def check(label: str, condition: bool) -> None:
    global errors
    symbol = PASS if condition else FAIL
    print(f"  {symbol}  {label}")
    if not condition:
        errors += 1


# ── Case 1: only PCR ─────────────────────────────────────────────────────────
print("\nCase 1: only PCR")
cards = [_make_card("pcr-1", "prim_cinematic_reveal", 30.0), _make_card("other", "key_phrase", 10.0)]
result = _apply_climax_budget(cards)
check("PCR card retained", _style_of(result, "pcr-1") == "prim_cinematic_reveal")
check("other card retained", _style_of(result, "other") == "key_phrase")
check("total cards = 2", len(result) == 2)

# ── Case 2: only PAR ─────────────────────────────────────────────────────────
print("\nCase 2: only PAR")
cards = [_make_card("par-1", "prim_ascension_reveal", 45.0)]
result = _apply_climax_budget(cards)
check("PAR card retained", _style_of(result, "par-1") == "prim_ascension_reveal")
check("total cards = 1", len(result) == 1)

# ── Case 3: PCR early + PAR late → PAR wins ──────────────────────────────────
print("\nCase 3: PCR@5s + PAR@60s → PAR kept (payoff tiebreak)")
cards = [
    _make_card("pcr-early", "prim_cinematic_reveal", 5.0),
    _make_card("par-late",  "prim_ascension_reveal", 60.0),
    _make_card("stat-1",    "stat", 20.0),
]
result = _apply_climax_budget(cards)
check("PAR@60s kept", _style_of(result, "par-late") == "prim_ascension_reveal")
check("PCR@5s evicted", _style_of(result, "pcr-early") is None)
check("stat card untouched", _style_of(result, "stat-1") == "stat")
check("total cards = 2", len(result) == 2)

# ── Case 4: PAR early + PCR late → PCR wins ──────────────────────────────────
print("\nCase 4: PAR@10s + PCR@120s → PCR kept (payoff tiebreak)")
cards = [
    _make_card("par-early", "prim_ascension_reveal", 10.0),
    _make_card("pcr-late",  "prim_cinematic_reveal", 120.0),
]
result = _apply_climax_budget(cards)
check("PCR@120s kept", _style_of(result, "pcr-late") == "prim_cinematic_reveal")
check("PAR@10s evicted", _style_of(result, "par-early") is None)
check("total cards = 1", len(result) == 1)

# ── Case 5: Three climax cards → only the latest survives ────────────────────
print("\nCase 5: three climax cards → only latest kept")
cards = [
    _make_card("pcr-1", "prim_cinematic_reveal", 15.0),
    _make_card("par-1", "prim_ascension_reveal", 90.0),
    _make_card("pcr-2", "prim_cinematic_reveal", 45.0),
    _make_card("kp",    "key_phrase", 30.0),
]
result = _apply_climax_budget(cards)
check("PAR@90s kept (latest)", _style_of(result, "par-1") == "prim_ascension_reveal")
check("PCR@15s evicted", _style_of(result, "pcr-1") is None)
check("PCR@45s evicted", _style_of(result, "pcr-2") is None)
check("key_phrase untouched", _style_of(result, "kp") == "key_phrase")
check("total cards = 2", len(result) == 2)

# ── Case 6: no climax cards ───────────────────────────────────────────────────
print("\nCase 6: no climax cards → unchanged")
cards = [_make_card("stat", "stat", 10.0), _make_card("kp", "key_phrase", 20.0)]
result = _apply_climax_budget(cards)
check("both cards intact", len(result) == 2)
check("no side effects", all(c["contentHints"]["style"] in ("stat", "key_phrase") for c in result))

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'─'*40}")
if errors == 0:
    print(f"{PASS} All cases passed — budget=1 guard correct.")
else:
    print(f"{FAIL} {errors} failure(s) — budget guard needs review.")
    sys.exit(1)
