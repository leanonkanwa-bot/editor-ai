#!/usr/bin/env python3
"""
Test suite for keyword-position anchoring of trigger-style cards.

Covers:
  - The exact f366e990 scenario: contrarian_take "impopulaire" appearing 4s after startSec
  - All 7 trigger-style types
  - Fallback to sentence-start when trigger word not found in Whisper
  - No backward movement allowed
  - Grounding guard now PASSES after anchoring (verifies dual benefit)
  - No regression on non-trigger card types

Run:  python -X utf8 backend/test_trigger_anchor.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.engine.storyboard import (
    _find_trigger_anchor,
    _grounding_overlap,
    _content_words,
    _ANCHOR_SEARCH_FORWARD_S,
    _ANCHOR_LEAD_S,
    _GROUNDING_OVERLAP_THRESHOLD,
    _GROUNDING_WINDOW_POST_S,
    _TRIGGER_STYLES,
    _TRIGGER_TEXT_FIELD,
)
from app.engine.captions import WordTiming

ok = 0
fail = 0

def check(label: str, got, expected, *, approx: bool = False, tol: float = 0.05):
    global ok, fail
    passed = (abs(got - expected) <= tol) if approx else (got == expected)
    status = "OK  " if passed else "FAIL"
    if passed:
        ok += 1
    else:
        fail += 1
        print(f"  [FAIL] {label}")
        print(f"         expected={expected!r}  got={got!r}")
        return
    print(f"  [OK  ] {label}  → {got!r}")

def words(pairs: list[tuple[str, float]]) -> list[WordTiming]:
    return [WordTiming(text=t, start=s, end=s + 0.3) for t, s in pairs]


print(f"\n{'='*68}")
print("  Keyword-position anchoring — trigger-style card tests")
print(f"{'='*68}\n")

# ── 1. Core scenario: f366e990 contrarian_take "impopulaire" at 4.0s ──────────
print("1. contrarian_take — 'impopulaire' 4s after sentence-start (real f366e990 pattern)")

card_ct = {
    "id": "c1", "startSec": 1.00, "endSec": 3.50,
    "contentHints": {
        "style": "contrarian_take",
        "take_text": "Le SEO est complètement mort, c'est impopulaire mais vrai",
    },
}
# Whisper stream: sentence opener 1.0s, trigger keyword "impopulaire" at 4.0s
ws_ct = words([
    ("Aujourd'hui", 0.80), ("je", 1.00), ("vais", 1.20), ("vous", 1.40),
    ("dire", 1.60), ("quelque", 1.80), ("chose", 2.00),
    ("d'impopulaire", 4.00), ("mais", 4.40), ("vrai", 4.70),
])

anchor = _find_trigger_anchor(card_ct, ws_ct)
check("anchor returns a value", anchor is not None, True)
check("anchor ≈ 4.00 - 0.20 = 3.80s", anchor, 3.80, approx=True)
# Offset reduction: was 4.00 - 1.00 = 3.0s early; now 4.00 - 3.80 = 0.20s (just the lead)
check("anchored startSec moved forward from sentence-start (1.00s)", anchor != 1.00, True)
# After anchoring, grounding window covers [3.30, 6.80] → "impopulaire"@4.0 is inside
card_ct_anchored = dict(card_ct, startSec=anchor)
overlap_after = _grounding_overlap(card_ct_anchored, ws_ct)
check("grounding overlap ≥ threshold AFTER anchoring", overlap_after >= _GROUNDING_OVERLAP_THRESHOLD, True)

print()

# ── 2. No anchor found → return None, keep sentence-start ─────────────────────
print("2. trigger word NOT in Whisper → anchor=None (fallback to LLM startSec)")

card_ct_inv = {
    "id": "c2", "startSec": 1.00, "endSec": 4.00,
    "contentHints": {
        "style": "contrarian_take",
        "take_text": "xyzzy frobozz quux",          # invented words absent from Whisper
    },
}
ws_gap = words([("Aujourd'hui", 0.80), ("je", 1.00), ("parle", 1.40)])
anchor_none = _find_trigger_anchor(card_ct_inv, ws_gap)
check("anchor=None when trigger words absent from Whisper", anchor_none, None)

print()

# ── 3. Never move startSec BACKWARD ───────────────────────────────────────────
print("3. trigger word found BEFORE current startSec → anchor clamped to startSec")

card_bwd = {
    "id": "c3", "startSec": 5.00, "endSec": 8.00,
    "contentHints": {
        "style": "warning_soft",
        "warning_text": "stratégie risquée",
    },
}
# "stratégie" at 4.0s — pre-window is [5.0 - 0.5, ...] = [4.5, ...], so 4.0s is
# outside the pre-window entirely → no match → None → caller keeps original startSec
ws_bwd = words([("cette", 3.60), ("stratégie", 4.00), ("est", 4.30), ("vraiment", 4.60)])
anchor_bwd = _find_trigger_anchor(card_bwd, ws_bwd)
check("word before pre-window → None (caller keeps original startSec=5.0)", anchor_bwd, None)

print()

# ── 4. All 7 trigger types — each finds its own keyword ───────────────────────
print("4. All 7 trigger-style types find their keyword")

type_cases = [
    ("contrarian_take", "take_text",      "impopulaire discutable",
     [("aujourd'hui", 1.0), ("impopulaire", 4.5)]),
    ("warning_soft",    "warning_text",   "dangereux risque important",
     [("attention", 1.0), ("dangereux", 3.8)]),
    ("myth_vs_fact",    "myth_text",      "mythe faux répandu",
     [("beaucoup", 1.0), ("mythe", 4.2)]),
    ("action_step_cta", "cta_text",       "télécharge template maintenant",
     [("voici", 1.0), ("télécharge", 3.5)]),
    ("secret_reveal",   "secret_text",    "secret clé succès",
     [("alors", 1.0), ("secret", 4.0)]),
    ("objection_response", "objection_text", "objection prix cher refus",
     [("souvent", 1.0), ("objection", 3.9)]),
    ("red_flag_list",   "flags",          ["arnaque", "faux témoignage", "promesse"],
     [("attention", 1.0), ("arnaque", 4.1)]),
]

for style, field, trigger, ws_pairs in type_cases:
    card_t = {
        "id": "cx", "startSec": 1.00, "endSec": 4.00,
        "contentHints": {"style": style, field: trigger},
    }
    ws_t = words(ws_pairs)
    a = _find_trigger_anchor(card_t, ws_t)
    trigger_word_time = ws_pairs[1][1]  # second entry = trigger keyword timestamp
    expected_anchor = max(trigger_word_time - _ANCHOR_LEAD_S, 1.00)
    check(f"{style}: anchor ≈ {expected_anchor:.2f}s", a, expected_anchor, approx=True)

print()

# ── 5. Trigger keyword exactly at window boundary (startSec + 6.0s) ───────────
print("5. keyword at boundary startSec + 6.0s — should be included")

card_edge = {
    "id": "ce", "startSec": 1.00, "endSec": 5.00,
    "contentHints": {
        "style": "secret_reveal",
        "secret_text": "formule secrète révélation",
    },
}
ws_edge = words([("voici", 1.0), ("formule", 7.0)])  # "formule" at 1.0 + 6.0 = 7.0s
anchor_edge = _find_trigger_anchor(card_edge, ws_edge)
check("keyword at +6.0s boundary is found", anchor_edge is not None, True)
check("anchor ≈ 7.0 - 0.20 = 6.80s", anchor_edge, 6.80, approx=True)

print()

# ── 6. keyword beyond window (startSec + 6.1s) → None ────────────────────────
print("6. keyword beyond +6.0s window → None (too late to be same sentence)")

card_far = {
    "id": "cf", "startSec": 1.00, "endSec": 5.00,
    "contentHints": {
        "style": "secret_reveal",
        "secret_text": "formule secrète révélation",
    },
}
ws_far = words([("voici", 1.0), ("formule", 7.2)])   # beyond 1.0 + 6.0
anchor_far = _find_trigger_anchor(card_far, ws_far)
check("keyword at +6.1s is NOT found (beyond window)", anchor_far, None)

print()

# ── 7. No trigger text field → None (cards without type-specific text unchanged) ─
print("7. card with no trigger text field → anchor=None (generic cards unaffected)")

card_nf = {
    "id": "cn", "startSec": 2.00, "endSec": 5.00,
    "contentHints": {"style": "contrarian_take"},   # no take_text
}
ws_nf = words([("quelque", 2.0), ("chose", 2.5)])
anchor_nf = _find_trigger_anchor(card_nf, ws_nf)
check("no take_text → anchor=None", anchor_nf, None)

print()

# ── 8. grounding now PASSES on correctly-anchored card (before would have been REJECT) ──
print("8. before anchoring: grounding FAILS; after anchoring: grounding PASSES")

card_pre = {
    "id": "cp", "startSec": 1.00, "endSec": 5.00,
    "contentHints": {
        "style": "warning_soft",
        # All content words (scandale, révélé) spoken at 4.5s+ — outside the original
        # grounding window [0.5, 4.0] → 0% overlap before anchoring.
        "warning_text": "scandale révélé attention",
    },
}
ws_grnd = words([
    ("voici", 0.9), ("maintenant", 1.3),
    ("scandale", 4.5), ("révélé", 4.8), ("attention", 5.1),
])
overlap_before = _grounding_overlap(card_pre, ws_grnd)
anchor_g = _find_trigger_anchor(card_pre, ws_grnd)
card_post = dict(card_pre, startSec=anchor_g)
overlap_after_g = _grounding_overlap(card_post, ws_grnd)

check("overlap BEFORE anchoring < threshold (would be rejected)", overlap_before < _GROUNDING_OVERLAP_THRESHOLD, True)
check("overlap AFTER anchoring >= threshold (now passes)", overlap_after_g >= _GROUNDING_OVERLAP_THRESHOLD, True)
print(f"         before={overlap_before:.0%}  after={overlap_after_g:.0%}")

print()
print(f"{'='*68}")
print(f"  RESULTS: {ok}/{ok+fail} passed  {'ALL OK' if fail == 0 else str(fail) + ' FAILURE(S)'}")
print(f"{'='*68}\n")
sys.exit(0 if fail == 0 else 1)
