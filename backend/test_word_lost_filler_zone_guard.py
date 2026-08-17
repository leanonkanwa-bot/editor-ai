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


# ── POST-RENDER loop simulation ───────────────────────────────────────────────
#
# Mirrors pretrim.py POST-RENDER loop (lines 1921-1953, post-ÉTAPE-9 patch).
# source_intervals: list of (si_start, si_end) tuples — kept source ranges.
# Returns (pr_lost, log_lines) so tests can assert both count and silence.

def _post_render_sequence(
    words,
    source_intervals,
    filler_drops,
    llm_zones,
) -> tuple[int, list[str]]:
    log_lines: list[str] = []
    pr_lost = 0
    for sw in words:
        ws  = float(sw.get("start", 0))
        we  = float(sw.get("end", 0))
        txt = str(sw.get("text", "")).strip()
        if not txt or (we - ws) < 0.030:
            continue
        # Check 1: word is inside a source interval
        if any(si[0] - 0.010 <= ws and we <= si[1] + 0.010 for si in source_intervals):
            continue
        # Check 2: word is covered by a rule-based filler_drop
        if any(d.start - 0.010 <= ws and we <= d.end + 0.010 for d in (filler_drops or [])):
            continue
        # Check 3: word starts inside an LLM-marked filler/tangent/repeat zone
        if any(
            _fz_s - 0.010 <= ws < _fz_e
            for _fz_s, _fz_e in (llm_zones or [])
        ):
            continue
        # Only flag inter-segment gaps (not pre/post-plan exclusions)
        pr_before = any(si[1] <= ws + 0.010 for si in source_intervals)
        pr_after  = any(si[0] >= we - 0.010 for si in source_intervals)
        if not (pr_before and pr_after):
            continue
        pr_lost += 1
        log_lines.append(f"[WORD-LOST] POST-RENDER '{txt}' at {ws:.2f}-{we:.2f}s — unaccounted")
    return pr_lost, log_lines


# ── TEST H — POST-RENDER: intentional LLM drop → no false alarm ───────────────
# Exactly mirrors the `Du` [23.020, 23.460] Railway scenario:
#   • source_intervals end at 23.010, next starts at 23.460 → word is bracketed
#   • word is NOT in filler_drops (it's a semantic drop, not a stutter)
#   • word IS in _wl_llm_filler_zones (reason=filler)
#
# EXPECTED: _pr_lost == 0 (Check 3 fires → continue → no warning)

def test_H_post_render_intentional_drop_no_false_alarm():
    """POST-RENDER: gap word in LLM filler zone → suppressed (no false-positive warning)."""
    source_intervals = [(0.0, 23.010), (23.460, 26.315)]
    filler_drops     = []
    llm_zones        = [(22.800, 24.000)]  # zone covering 'Du' at 23.020
    word             = _make_word("Du", 23.020, 23.460)

    pr_lost, log_lines = _post_render_sequence(
        [word], source_intervals, filler_drops, llm_zones
    )

    assert pr_lost == 0, (
        f"H: expected _pr_lost=0 (LLM filler zone guard), got {pr_lost}.\n"
        f"Log: {log_lines}"
    )
    assert not log_lines, (
        f"H: expected no POST-RENDER log lines, got: {log_lines}"
    )
    print("PASS  H: 'Du' in LLM filler zone → _pr_lost=0, no POST-RENDER warning ✓")


# ── TEST H2 — POST-RENDER: genuine lost word → alarm fires ────────────────────
# Same geometry as H but the word is NOT in any LLM filler zone.
# EXPECTED: _pr_lost == 1 (the system must still catch true orphans)

def test_H2_post_render_genuine_lost_word_alarm_fires():
    """POST-RENDER: gap word NOT in LLM filler zone → warning fires (true positive)."""
    source_intervals = [(0.0, 10.000), (11.500, 20.000)]
    filler_drops     = []
    llm_zones        = [(5.0, 8.0)]   # zone is elsewhere — "alors" is NOT in it
    word             = _make_word("alors", 10.200, 10.450)  # bracketed gap word

    pr_lost, log_lines = _post_render_sequence(
        [word], source_intervals, filler_drops, llm_zones
    )

    assert pr_lost == 1, (
        f"H2: expected _pr_lost=1 for genuine orphan outside LLM zone, got {pr_lost}."
    )
    assert len(log_lines) == 1, (
        f"H2: expected 1 POST-RENDER log line, got {len(log_lines)}: {log_lines}"
    )
    assert "alors" in log_lines[0], (
        f"H2: log line should mention 'alors', got: {log_lines[0]!r}"
    )
    print("PASS  H2: 'alors' outside LLM zone → _pr_lost=1, POST-RENDER warning fires ✓")


# ── TEST H3 — Source guard test for POST-RENDER block ─────────────────────────
# Verify the new Check 3 guard string is actually present inside the POST-RENDER
# block of pretrim.py (not just anywhere in the file).
# Checks for the unique comment marker "intentional drop" adjacent to the
# POST-RENDER context, after the "filler_drops" Check 2.

def test_H3_pretrim_source_contains_post_render_guard():
    """pretrim.py POST-RENDER block must contain the new Check 3 guard after ÉTAPE 9."""
    from pathlib import Path

    pretrim_path = Path(__file__).parent / "app" / "engine" / "pretrim.py"
    assert pretrim_path.exists(), f"pretrim.py introuvable : {pretrim_path}"

    source = pretrim_path.read_text(encoding="utf-8")

    # The new guard has a unique marker comment that appears only in the
    # POST-RENDER block (not in the final-assertion block).
    # "Check 3: word starts inside an LLM-marked filler/tangent/repeat zone — intentional drop"
    # is inserted specifically there.
    marker = "Check 3: word starts inside an LLM-marked filler/tangent/repeat zone"
    assert marker in source, (
        "H3: FAIL — POST-RENDER Check 3 guard marker NOT found in pretrim.py.\n"
        f"Expected string: {marker!r}\n"
        "Apply ÉTAPE 9 patch to fix."
    )

    # Also verify it appears AFTER the POST-RENDER sentinel comment
    post_render_sentinel = "Post-render word accounting"
    idx_pr  = source.find(post_render_sentinel)
    idx_ck3 = source.find(marker)
    assert idx_pr != -1, (
        "H3: 'Post-render word accounting' sentinel comment not found in pretrim.py"
    )
    assert idx_ck3 > idx_pr, (
        f"H3: Check 3 marker found at idx={idx_ck3} but POST-RENDER sentinel at idx={idx_pr} — "
        "guard appears BEFORE the POST-RENDER block (wrong location)."
    )
    print("PASS  H3: POST-RENDER Check 3 guard present in pretrim.py at correct location ✓")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    print("=" * 70)
    print("WORD-LOST final-assertion + POST-RENDER zone guard")
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

    print("── Test G (guard final-assertion dans pretrim.py) ──")
    _run("G", test_G_pretrim_source_contains_final_assertion_guard)

    print("── Test H (POST-RENDER: zone word → no false alarm) ──")
    _run("H", test_H_post_render_intentional_drop_no_false_alarm)

    print("── Test H2 (POST-RENDER: genuine orphan → alarm fires) ──")
    _run("H2", test_H2_post_render_genuine_lost_word_alarm_fires)

    print("── Test H3 (POST-RENDER Check 3 guard dans pretrim.py) ──")
    _run("H3", test_H3_pretrim_source_contains_post_render_guard)

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
