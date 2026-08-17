#!/usr/bin/env python3
"""
test_word_lost_filler_zone_guard.py — Tests for the missing _wl_llm_filler_zones guard
in the WORD-LOST final-assertion loop (pretrim.py).

ROOT CAUSE UNDER TEST:
  The repair loop (pretrim.py:1305-1314) correctly skips words whose start falls
  inside _wl_llm_filler_zones.  The final-assertion loop (pretrim.py:1528-1587)
  does NOT — it falls through to WORD-LOST-FALLBACK for any bracketed word that
  isn't covered by `_planned` or `filler_drops`, even if the word is inside a
  LLM semantic drop zone.

  fix: add Check 4 in the final-assertion loop, immediately before FALLBACK activation.

TESTS:
  A — zone word absent from filler_drops → FALLBACK before patch, skip-zone after
  B — zone word also in filler_drops     → Check 2 already protects (no regression)
  C — orphaned word outside any zone     → FALLBACK both before and after (correct)
  D — boundary: word.start = zone_end − 0.009 → must be IN zone (tolerance −0.010)

Run:
    python backend/test_word_lost_filler_zone_guard.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.engine.silence_remover import DropSegment


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_word(text: str, start: float, end: float) -> dict:
    return {"text": text, "start": start, "end": end}


def _make_planned(*segs):
    """Build a _planned list from (s_padded, e_padded) tuples.
    Full tuple format: (keep_index, s_src, e, s_padded, e_padded).
    """
    return [(i, s, e, s, e) for i, (s, e) in enumerate(segs)]


# ── Final-assertion check sequence ────────────────────────────────────────────
#
# Mirrors pretrim.py:1528-1587 exactly.
# `with_guard=True`  → as it will be AFTER the patch (Check 4 present)
# `with_guard=False` → as it is BEFORE the patch (Check 4 absent)
#
# Returns one of:
#   "skip-planned"      — Check 1 fired  (word covered by a segment)
#   "skip-filler_drop"  — Check 2 fired  (word inside a filler_drop)
#   "skip-not-bracketed"— Check 3 fired  (pre- or post-plan word)
#   "skip-zone"         — Check 4 fired  (word inside LLM filler zone)   [patch only]
#   "FALLBACK"          — none of the above → WORD-LOST-FALLBACK

def _assert_sequence(word, planned, filler_drops, llm_zones, *, with_guard: bool) -> str:
    ws  = float(word["start"])
    we  = float(word["end"])

    # Check 1: covered by a planned segment?
    if any(
        p[3] - 0.010 <= ws and we <= p[4] + 0.010
        for p in planned
    ):
        return "skip-planned"

    # Check 2: covered by a filler_drop?
    if any(
        d.start - 0.010 <= ws and we <= d.end + 0.010
        for d in (filler_drops or [])
    ):
        return "skip-filler_drop"

    # Check 3: bracketed (at least one seg ends before, one starts after)?
    has_before = any(p[4] <= ws + 0.010 for p in planned)
    has_after  = any(p[3] >= we - 0.010 for p in planned)
    if not (has_before and has_after):
        return "skip-not-bracketed"

    # Check 4 (new guard — only present after the patch):
    if with_guard:
        if any(
            _fz_s - 0.010 <= ws < _fz_e
            for _fz_s, _fz_e in (llm_zones or [])
        ):
            return "skip-zone"

    return "FALLBACK"


# ── TEST A ────────────────────────────────────────────────────────────────────
# Zone word absent from filler_drops → currently FALLBACK, must become skip-zone.
#
# Scenario:
#   LLM drop zone [5.2 – 7.0s] (reason=filler) — a semantic section, not a stutter
#   Word "tu" at [5.500, 5.650s] — starts inside the zone
#   filler_drops contains only DropSegment for "Euh" at [5.30, 5.50s] — NOT "tu"
#   planned: seg [0.0, 5.1s] and [7.1, 12.0s] — "tu" is in the inter-segment gap

def test_A_zone_word_absent_from_filler_drops_before_patch():
    """BEFORE patch: word in LLM zone, absent from filler_drops → FALLBACK (current bug)."""
    planned     = _make_planned((0.0, 5.1), (7.1, 12.0))
    filler_drops = [DropSegment(start=5.30, end=5.50, reason="llm_filler")]  # "Euh" only
    llm_zones   = [(5.2, 7.0)]
    word        = _make_word("tu", 5.500, 5.650)

    result = _assert_sequence(word, planned, filler_drops, llm_zones, with_guard=False)
    assert result == "FALLBACK", (
        f"BEFORE PATCH — expected FALLBACK for zone word absent from filler_drops, got {result!r}"
    )
    print("PASS  A (before patch): 'tu' in zone, absent from filler_drops → FALLBACK ✓")


def test_A_zone_word_absent_from_filler_drops_after_patch():
    """AFTER patch: same word → skip-zone (no FALLBACK)."""
    planned      = _make_planned((0.0, 5.1), (7.1, 12.0))
    filler_drops = [DropSegment(start=5.30, end=5.50, reason="llm_filler")]
    llm_zones    = [(5.2, 7.0)]
    word         = _make_word("tu", 5.500, 5.650)

    result = _assert_sequence(word, planned, filler_drops, llm_zones, with_guard=True)
    assert result == "skip-zone", (
        f"AFTER PATCH — expected skip-zone for zone word absent from filler_drops, got {result!r}"
    )
    print("PASS  A (after patch):  'tu' in zone, absent from filler_drops → skip-zone ✓")


# ── TEST B ────────────────────────────────────────────────────────────────────
# Word in both filler_drops AND LLM zone → Check 2 already protects it.
# The guard (Check 4) must not change this behavior.

def test_B_zone_word_in_filler_drops_no_change():
    """Word in filler_drops: Check 2 fires regardless of guard — no regression."""
    planned      = _make_planned((0.0, 5.1), (7.1, 12.0))
    # "Euh" IS in filler_drops AND in the zone
    filler_drops = [DropSegment(start=5.470, end=5.720, reason="llm_filler")]
    llm_zones    = [(5.2, 7.0)]
    word         = _make_word("Euh", 5.500, 5.700)

    before = _assert_sequence(word, planned, filler_drops, llm_zones, with_guard=False)
    after  = _assert_sequence(word, planned, filler_drops, llm_zones, with_guard=True)

    assert before == "skip-filler_drop", (
        f"B before: expected skip-filler_drop, got {before!r}"
    )
    assert after == "skip-filler_drop", (
        f"B after: expected skip-filler_drop (no regression), got {after!r}"
    )
    print("PASS  B: 'Euh' in filler_drops + zone → skip-filler_drop both before and after ✓")


# ── TEST C ────────────────────────────────────────────────────────────────────
# Orphaned word outside any LLM zone → FALLBACK must still fire (both before and after patch).
# This is the correct behavior for a genuine orphan that WORD-LOST should rescue.

def test_C_orphan_outside_zone_fallback_unchanged():
    """Word outside any LLM zone → FALLBACK both before and after patch (correct)."""
    planned      = _make_planned((0.0, 2.5), (3.5, 12.0))
    filler_drops = []
    llm_zones    = [(5.2, 7.0)]  # zone is elsewhere — "alors" is NOT in it
    word         = _make_word("alors", 3.000, 3.200)

    before = _assert_sequence(word, planned, filler_drops, llm_zones, with_guard=False)
    after  = _assert_sequence(word, planned, filler_drops, llm_zones, with_guard=True)

    assert before == "FALLBACK", (
        f"C before: non-zone orphan must trigger FALLBACK, got {before!r}"
    )
    assert after == "FALLBACK", (
        f"C after: non-zone orphan must STILL trigger FALLBACK after patch, got {after!r}"
    )
    print("PASS  C: 'alors' outside zone → FALLBACK before and after (regression-free) ✓")


# ── TEST D ────────────────────────────────────────────────────────────────────
# Boundary: word.start = zone_end − 0.009 → must be IN zone (tolerance is −0.010 on fz_s,
# strict < on fz_e side, so ws=zone_end−0.009 is still < zone_end → in zone).

def test_D_boundary_word_start_at_zone_end_minus_9ms():
    """word.start = zone_end − 0.009 → just inside the zone (ws < fz_e is strict <)."""
    zone_end  = 7.000
    word_start = zone_end - 0.009  # 6.991
    word_end   = zone_end + 0.100  # 7.100  (straddles zone edge, but start is inside)

    planned      = _make_planned((0.0, 5.1), (7.1, 12.0))
    filler_drops = []
    llm_zones    = [(5.2, zone_end)]
    word         = _make_word("qu", word_start, word_end)

    # Check that the guard condition itself is True (before examining full sequence)
    # fz_s - 0.010 = 5.2 - 0.010 = 5.190 <= 6.991 < 7.000 → True
    in_zone = any(
        fz_s - 0.010 <= word_start < fz_e
        for fz_s, fz_e in llm_zones
    )
    assert in_zone, (
        f"D: word.start={word_start:.3f} should satisfy fz_s-0.010={5.2 - 0.010:.3f} <= ws < fz_e={zone_end:.3f}"
    )

    before = _assert_sequence(word, planned, filler_drops, llm_zones, with_guard=False)
    after  = _assert_sequence(word, planned, filler_drops, llm_zones, with_guard=True)

    assert before == "FALLBACK", (
        f"D before: expected FALLBACK at boundary, got {before!r}"
    )
    assert after == "skip-zone", (
        f"D after: expected skip-zone at boundary (word.start={word_start:.3f} < zone_end={zone_end}), got {after!r}"
    )
    print(f"PASS  D: 'qu' start={word_start:.3f}s (zone_end−9ms) → FALLBACK before, skip-zone after ✓")


# ── TEST E — Régression : guard absent when llm_zones empty ──────────────────
# If the planning LLM emits no drop_segments, _wl_llm_filler_zones is empty.
# Guard must not fire (all zones empty → no match).

def test_E_empty_llm_zones_no_effect():
    """Empty llm_zones: guard never fires, FALLBACK still works."""
    planned      = _make_planned((0.0, 2.5), (3.5, 12.0))
    filler_drops = []
    llm_zones    = []  # no zones at all
    word         = _make_word("sais", 3.000, 3.200)

    result = _assert_sequence(word, planned, filler_drops, llm_zones, with_guard=True)
    assert result == "FALLBACK", (
        f"E: with empty llm_zones, guard must not fire — expected FALLBACK, got {result!r}"
    )
    print("PASS  E: empty llm_zones → guard does not fire, FALLBACK works ✓")


# ── TEST F — Régression : pre-plan word still skipped by Check 3 ─────────────

def test_F_pre_plan_word_not_bracketed():
    """Word before all segments (not bracketed) → skip-not-bracketed (Check 3), not zone."""
    planned      = _make_planned((5.0, 12.0),)  # only one segment, starts at 5.0
    filler_drops = []
    llm_zones    = [(0.5, 1.5)]
    word         = _make_word("Euh", 0.800, 1.000)  # in zone, before any seg

    result = _assert_sequence(word, planned, filler_drops, llm_zones, with_guard=True)
    # has_before: any(p[4] <= ws+0.010) = any(12.0 <= 0.810) = False → not bracketed
    assert result == "skip-not-bracketed", (
        f"F: pre-plan word → expected skip-not-bracketed, got {result!r}"
    )
    print("PASS  F: pre-plan word in zone → skip-not-bracketed (Check 3 fires first) ✓")


# ── TEST G — garde réelle dans pretrim.py (échoue AVANT le patch) ─────────────
# Ce test vérifie que le code source de pretrim.py contient bien le guard
# "assert-skip ... final assertion" dans la boucle finale.
# Il ÉCHOUE sur le code actuel (pré-patch) et PASSE après le patch.

def test_G_pretrim_source_contains_final_assertion_guard():
    """Le guard _wl_llm_filler_zones doit être présent dans la boucle finale de pretrim.py.

    Ce test échoue AVANT le patch (guard absent) et passe APRÈS (guard présent).
    """
    import inspect
    from pathlib import Path

    pretrim_path = Path(__file__).parent / "app" / "engine" / "pretrim.py"
    assert pretrim_path.exists(), f"pretrim.py introuvable : {pretrim_path}"

    source = pretrim_path.read_text(encoding="utf-8")

    # Le guard doit contenir le marqueur unique du message de log
    # "[WORD-LOST] assert-skip" — absent du code actuel (pré-patch).
    assert "assert-skip" in source, (
        "ÉCHEC AVANT PATCH — le guard final-assertion est ABSENT de pretrim.py.\n"
        "Attendu: log '[WORD-LOST] assert-skip ...  — starts inside LLM filler zone (final assertion)'\n"
        "Appliquer le patch (ÉTAPE 2) pour corriger."
    )
    print("PASS  G: guard 'assert-skip' présent dans pretrim.py (patch appliqué) ✓")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    print("=" * 70)
    print("WORD-LOST final-assertion zone guard")
    print("=" * 70)
    print()

    failures = []

    def _run(name, fn):
        try:
            fn()
        except AssertionError as e:
            failures.append((name, str(e)))
            print(f"FAIL  {name}:\n      {e}\n")

    print("── Test A (before patch — doit produire FALLBACK dans simulation) ──")
    _run("A_before", test_A_zone_word_absent_from_filler_drops_before_patch)

    print("── Test A (after patch — doit produire skip-zone dans simulation) ──")
    _run("A_after", test_A_zone_word_absent_from_filler_drops_after_patch)

    print("── Test B (before + after — aucun changement) ──")
    _run("B", test_B_zone_word_in_filler_drops_no_change)

    print("── Test C (before + after — FALLBACK inchangé hors zone) ──")
    _run("C", test_C_orphan_outside_zone_fallback_unchanged)

    print("── Test D (boundary — avant FALLBACK, après skip-zone) ──")
    _run("D", test_D_boundary_word_start_at_zone_end_minus_9ms)

    print("── Test E (zones vides — guard inactif) ──")
    _run("E", test_E_empty_llm_zones_no_effect)

    print("── Test F (régression pre-plan) ──")
    _run("F", test_F_pre_plan_word_not_bracketed)

    print("── Test G (guard dans pretrim.py — ÉCHOUE avant patch) ──")
    _run("G", test_G_pretrim_source_contains_final_assertion_guard)

    print()
    print("=" * 70)
    if failures:
        print(f"{len(failures)} test(s) ÉCHOUÉ(s):")
        for name, msg in failures:
            first_line = msg.split('\n')[0]
            print(f"  • {name}: {first_line}")
        print("=" * 70)
        sys.exit(1)
    else:
        print("Tous les tests ont passé.")
        print("=" * 70)
