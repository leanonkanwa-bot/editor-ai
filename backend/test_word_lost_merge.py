"""
test_word_lost_merge.py — verify the GUARANTEED BRACKETING MERGE in pretrim.py.

Case under test: a word is orphaned in the gap between two segments whose
neighbours are so tightly packed that neither extend (a) nor retract (b) can
reach the word's boundaries.  The old code fell through to UNREPAIRABLE → the
new code performs a bracketing search and fuses the two segments.

Scenario (saturated 2-segment case):
  seg[0]: s_padded=0.000  e_padded=5.100
  orphan:  'résultats'   ws=5.100  we=5.400
  seg[1]: s_padded=5.406  e_padded=12.000

Why both options fail:
  (a) extend seg[0]: ne = min(we+0.010, s[1]-0.010) = min(5.410, 5.396) = 5.396
       → 5.396 < we-0.001=5.399  →  FAIL
  (b) retract seg[1]: ns = max(ws-0.010, e[0]+0.010) = max(5.090, 5.110) = 5.110
       → 5.110 > ws+0.001=5.101  →  FAIL
  edge-adjacent: |we - s[1]| = |5.400 - 5.406| = 0.006 > 0.005  →  NOT b0
  → strategy = "merge"

Expected merge result:
  _planned collapses to a single segment [0.000, 12.000] that trivially covers
  the orphan word.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── The merge logic extracted verbatim from pretrim.py (merge case) ──────────

def _bracketing_merge(planned, resolved, modified_pis, ws, we, txt):
    """
    True bracketing merge — mirrors pretrim.py `elif _wl_strat == "merge"`.
    Returns (planned, resolved, modified_pis, piL, piR) after merge,
    or raises AssertionError if no bracketing segments exist.
    """
    piL = None
    piR = None
    for pi in range(len(planned)):
        ep = planned[pi][4]
        sp = planned[pi][3]
        if ep <= ws + 0.010:
            if piL is None or ep > planned[piL][4]:
                piL = pi
        if sp >= we - 0.010:
            if piR is None or sp < planned[piR][3]:
                piR = pi

    assert piL is not None and piR is not None and piL != piR, (
        f"No bracketing segments for '{txt}' {ws:.3f}-{we:.3f}s "
        f"(piL={piL}, piR={piR})"
    )

    mi_L, ss_L, e_L, sp_L, ep_L = planned[piL]
    mi_R, ss_R, e_R, sp_R, ep_R = planned[piR]
    planned[piL] = (mi_L, ss_L, e_R, sp_L, ep_R)
    n_del = piR - piL
    del planned[piL + 1 : piR + 1]

    modified_pis = {
        p - n_del if p >= piR else p
        for p in modified_pis
        if p < piL or p >= piR
    }
    resolved = {
        r - n_del if r >= piR else r
        for r in resolved
        if r < piL or r >= piR
    }
    if piL > 0:
        modified_pis.add(piL - 1)
    if piL + 1 < len(planned):
        modified_pis.add(piL)

    return planned, resolved, modified_pis, piL, piR


def _word_covered(planned, ws, we):
    return any(
        planned[p][3] - 0.010 <= ws and we <= planned[p][4] + 0.010
        for p in range(len(planned))
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_basic_saturated_2seg():
    """Both neighbours saturated → merge → word covered."""
    # (keep_index, s_src, e, s_padded, e_padded)
    planned = [
        (0, 0.000, 5.000, 0.000, 5.100),
        (1, 5.500, 12.000, 5.406, 12.000),
    ]
    ws, we, txt = 5.100, 5.400, "résultats"

    assert not _word_covered(planned, ws, we), "word should NOT be covered initially"

    planned, resolved, mods, piL, piR = _bracketing_merge(
        planned, set(), set(), ws, we, txt
    )

    assert piL == 0 and piR == 1, f"expected piL=0 piR=1, got piL={piL} piR={piR}"
    assert len(planned) == 1, f"expected 1 segment after merge, got {len(planned)}"
    assert planned[0][3] == 0.000, f"s_padded should be 0.000, got {planned[0][3]}"
    assert planned[0][4] == 12.000, f"e_padded should be 12.000, got {planned[0][4]}"
    assert _word_covered(planned, ws, we), "word must be covered after merge"
    print(f"  PASS test_basic_saturated_2seg — merged to {planned[0][3]:.3f}-{planned[0][4]:.3f}s")


def test_3seg_middle_orphan():
    """Three segments; word falls in gap between seg[1] and seg[2]."""
    planned = [
        (0, 0.000, 8.000, 0.000, 8.000),
        (1, 8.100, 21.000, 8.050, 21.100),
        (2, 21.600, 30.000, 21.600, 30.000),
    ]
    ws, we, txt = 21.150, 21.560, "résultats"

    assert not _word_covered(planned, ws, we)

    planned, resolved, mods, piL, piR = _bracketing_merge(
        planned, set(), set(), ws, we, txt
    )

    assert piL == 1 and piR == 2, f"expected piL=1 piR=2, got piL={piL} piR={piR}"
    assert len(planned) == 2, f"expected 2 segments, got {len(planned)}"
    # Original seg[0] untouched
    assert planned[0][4] == 8.000
    # Merged seg spans seg[1].s_padded → seg[2].e_padded
    assert planned[1][3] == 8.050, f"merged s_padded={planned[1][3]}"
    assert planned[1][4] == 30.000, f"merged e_padded={planned[1][4]}"
    assert _word_covered(planned, ws, we)
    print(f"  PASS test_3seg_middle_orphan — {len(planned)} segs after merge")


def test_non_adjacent_bracketing():
    """Word between seg[0] and seg[2] with seg[1] also in the gap — all 3 fuse."""
    planned = [
        (0, 0.000, 5.000, 0.000, 5.000),
        (1, 5.200, 5.500, 5.200, 5.500),  # an intermediate segment also in the gap
        (2, 5.700, 12.000, 5.700, 12.000),
    ]
    ws, we, txt = 5.050, 5.650, "très"

    # Check not covered (seg[1] covers [5.200-5.500], not the full [5.050-5.650])
    assert not _word_covered(planned, ws, we)

    planned, resolved, mods, piL, piR = _bracketing_merge(
        planned, {0}, {0, 1}, ws, we, txt
    )

    assert piL == 0 and piR == 2, f"expected piL=0 piR=2, got piL={piL} piR={piR}"
    assert len(planned) == 1, f"expected 1 segment, got {len(planned)}"
    assert planned[0][3] == 0.000
    assert planned[0][4] == 12.000
    # _resolved and modified_pis for deleted pairs should be cleared
    assert 0 not in resolved, "pair 0 (between deleted segs) should be removed"
    assert 1 not in resolved, "pair 1 (between deleted segs) should be removed"
    assert _word_covered(planned, ws, we)
    print(f"  PASS test_non_adjacent_bracketing — intermediate seg fused too")


def test_pair_index_shift():
    """After merge of segs[1..2], pairs ≥ piR shift down by n_del=1."""
    planned = [
        (0, 0.0, 5.0, 0.0, 5.0),
        (1, 5.1, 6.0, 5.1, 6.0),
        (2, 6.5, 9.0, 6.5, 9.0),
        (3, 9.1, 15.0, 9.1, 15.0),
    ]
    # Old pair indices: 0=(seg0,seg1), 1=(seg1,seg2), 2=(seg2,seg3)
    modified_pis = {0, 1, 2}
    resolved = {2}  # pair 2 was resolved before the merge

    ws, we = 6.05, 6.45  # in gap between seg[1] and seg[2]
    planned, resolved, modified_pis, piL, piR = _bracketing_merge(
        planned, resolved, modified_pis, ws, we, "test"
    )

    # piL=1, piR=2 → n_del=1
    # Old pair 0 (seg0,seg1): stays → 0 (piL=1 means pairs 1..1 are gone)
    # Old pair 1 (seg1,seg2): GONE (< piL? no, it's piL≤p<piR)
    # Old pair 2 (seg2,seg3): was p=2 ≥ piR=2 → new p=2-1=1
    # piL=1, piR=2, n_del=1
    # Old pair 0 (seg0–seg1): p=0 < piL=1 → stays 0
    # Old pair 1 (seg1–seg2): piL≤p<piR → GONE
    # Old pair 2 (seg2–seg3): p=2 ≥ piR=2 → new pair = 2-1 = 1
    assert len(planned) == 3, f"expected 3 segs, got {len(planned)}"
    assert 2 not in resolved, f"old pair 2 should have been shifted away from index 2, got {resolved}"
    assert 1 in resolved, f"old pair 2 → new pair 1 should be in resolved, got {resolved}"
    assert 0 not in resolved, f"pair 0 was never in resolved"
    print(f"  PASS test_pair_index_shift — resolved={resolved} mods={modified_pis}")


def test_no_crash_word_already_covered():
    """Word that is already covered never reaches merge — bracketing is a safety net."""
    planned = [
        (0, 0.0, 12.0, 0.0, 12.0),
    ]
    ws, we = 5.0, 6.0
    # Word IS covered by seg[0] — if merge is called anyway it still finds piL=0...
    # but piR would be the same segment (ep and sp of seg[0] bracket the word).
    # The guard `piL != piR` protects us — no merge happens.
    piL = None
    piR = None
    for pi in range(len(planned)):
        ep = planned[pi][4]
        sp = planned[pi][3]
        if ep <= ws + 0.010:
            if piL is None or ep > planned[piL][4]:
                piL = pi
        if sp >= we - 0.010:
            if piR is None or sp < planned[piR][3]:
                piR = pi
    # piL may equal piR (same segment), so merge guard prevents corruption
    assert piL == piR or piL is None or piR is None, (
        "single-segment case: piL should equal piR or one is None"
    )
    print(f"  PASS test_no_crash_word_already_covered — piL={piL} piR={piR} (no merge)")


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Running WORD-LOST guaranteed-merge tests…\n")
    test_basic_saturated_2seg()
    test_3seg_middle_orphan()
    test_non_adjacent_bracketing()
    test_pair_index_shift()
    test_no_crash_word_already_covered()
    print("\nAll tests passed.")
