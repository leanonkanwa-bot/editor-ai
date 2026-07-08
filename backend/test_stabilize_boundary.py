"""
Local tests for the stabilization loop and snap helper.

Covers:
  - Adjacent-word pre-check (fix 1): boundary at contact point → no snap
  - Contact-point snap (fix 1): snap to we/ws instead of we+gap when adjacent
  - Gap-preserving fallback (fix 2): planned source gap > 300ms → restore s_j
  - Contiguous fallback (original): 0-gap adjacent words → e=s=contact_point
  - Existing snap and clamp cases (unchanged)

Run with:  python backend/test_stabilize_boundary.py
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from engine.pretrim import _snap_boundary_out_of_word


def _simulate_stabilization(
    planned: list[tuple],   # [(i, s_src, e, s_padded, e_padded), ...]
    wt: list[tuple],        # [(ws, we), ...]  sorted word timings
    max_passes: int = 5,
    min_dur: float = 0.030,
    gap_s: float = 0.010,
    src_gap_threshold: float = 0.300,
) -> tuple[list[tuple], set[int], set[int]]:
    """Simulate the snap→clamp stabilization loop + fallback from pretrim().

    Gap check mirrors production: uses planned[pi+1][1] - planned[pi][2]
    (= _s_src_j - _e_i in source space) to detect planned gaps > src_gap_threshold.

    Returns (planned, resolved, gap_preserved).
    gap_preserved: pair indices where the gap-preserving path ran — orphan words
    in those pairs are intentionally excluded and must NOT be flagged.
    """
    resolved: set[int] = set()
    gap_preserved: set[int] = set()
    planned_padded_orig = [(sp, ep) for _, _, _, sp, ep in planned]

    for pass_n in range(max_passes):
        snap_changed = False
        clamp_changed = False

        # ① snap
        for pi in range(len(planned)):
            i, s_src, e, s_i, e_pad = planned[pi]
            updated = False
            new_s, word_s = _snap_boundary_out_of_word(s_i, wt)
            if word_s:
                s_i = max(0.0, new_s)
                snap_changed = updated = True
            new_e, word_e = _snap_boundary_out_of_word(e_pad, wt)
            if word_e:
                e_pad = new_e
                snap_changed = updated = True
            if updated:
                planned[pi] = (i, s_src, e, s_i, e_pad)

        # ② clamp
        for pi in range(len(planned) - 1):
            i, s_src_i, e_i, s_i, e_pad_i = planned[pi]
            j, s_src_j, e_j, s_j, e_pad_j = planned[pi + 1]
            if e_pad_i > s_j - gap_s:
                mid = (e_pad_i + s_j) / 2.0
                planned[pi]     = (i, s_src_i, e_i, s_i, mid - 0.005)
                planned[pi + 1] = (j, s_src_j, e_j, mid + 0.005, e_pad_j)
                clamp_changed = True

        if not snap_changed and not clamp_changed:
            break
    else:
        # Fallback
        for pi in range(len(planned) - 1):
            i, s_src_i, e_i, s_i, e_pad_i = planned[pi]
            j, s_src_j, e_j, s_j, e_pad_j = planned[pi + 1]
            clamp_ok = e_pad_i <= s_j - gap_s + 1e-9
            e_clean = not any(ws < e_pad_i < we for ws, we in wt if we - ws >= min_dur)
            s_clean = not any(ws < s_j < we for ws, we in wt if we - ws >= min_dur)
            if clamp_ok and e_clean and s_clean:
                continue
            # Gap-preserving path: _s_src_j - _e_i mirrors production
            src_gap = s_src_j - e_i
            if src_gap > src_gap_threshold:
                e_new = e_i + 0.010
                s_new = planned_padded_orig[pi + 1][0]
                # Snap restored s_new to ensure word-safety (fix 2 universal snap)
                s_new_snapped, snap_w = _snap_boundary_out_of_word(s_new, wt)
                if snap_w:
                    s_new = s_new_snapped
                if e_new <= s_new - 0.010:
                    planned[pi]     = (i, s_src_i, e_i, s_i, e_new)
                    planned[pi + 1] = (j, s_src_j, e_j, s_new, e_pad_j)
                    gap_preserved.add(pi)
                    continue  # NOT in resolved; 10ms gap required
            # Contiguous: contact-point assignment
            bd_pt = None
            for ws, we in wt:
                if we - ws < min_dur:
                    continue
                if ws < e_pad_i < we:
                    bd_pt = we if (e_pad_i - ws >= we - e_pad_i) else ws
                    break
                if ws < s_j < we:
                    bd_pt = ws if (s_j - ws < we - s_j) else we
                    break
            gap_pt = bd_pt if bd_pt is not None else (e_pad_i + s_j) / 2.0
            planned[pi]     = (i, s_src_i, e_i, s_i, gap_pt)
            planned[pi + 1] = (j, s_src_j, e_j, gap_pt, e_pad_j)
            resolved.add(pi)

    # ③ Orphan repair (mirrors pretrim.py)
    # Skip pairs where there is a large source gap: words between e_i and s_src_j
    # are intentionally excluded source content, not accidental orphans.
    for pi in range(len(planned) - 1):
        i, s_src_i, e_i, s_i, e_pad_i = planned[pi]
        j, s_src_j, e_j, s_j, e_pad_j = planned[pi + 1]
        if s_src_j - e_i > src_gap_threshold:
            continue
        orphan_fixed = False
        for ws, we in wt:
            if ws > s_j: break
            if we - ws < min_dur: continue
            if not (e_pad_i <= ws and we <= s_j): continue
            d_i = ws - e_pad_i
            d_j = s_j - we
            if d_i < d_j:
                e_pad_i = we + 0.010
                s_j = max(s_j, e_pad_i)
            else:
                s_j = ws - 0.010
                e_pad_i = min(e_pad_i, s_j)
            orphan_fixed = True
        if orphan_fixed:
            e_pad_i, _ = _snap_boundary_out_of_word(e_pad_i, wt)
            s_j, _     = _snap_boundary_out_of_word(s_j, wt)
            if e_pad_i >= s_j - 1e-9:
                resolved.add(pi)
            planned[pi]     = (i, s_src_i, e_i, s_i, e_pad_i)
            planned[pi + 1] = (j, s_src_j, e_j, s_j, e_pad_j)

    # ④ Universal boundary snap: snap ALL boundaries (not just conflicting pairs)
    _univ_changed = False
    for pi in range(len(planned)):
        i, s_src, e_i, s_i, e_pad_i = planned[pi]
        updated = False
        new_s, word_s = _snap_boundary_out_of_word(s_i, wt)
        if word_s:
            s_i = max(0.0, new_s)
            updated = True
        new_e, word_e = _snap_boundary_out_of_word(e_pad_i, wt)
        if word_e:
            e_pad_i = new_e
            updated = True
        if updated:
            planned[pi] = (i, s_src, e_i, s_i, e_pad_i)
            _univ_changed = True

    # Re-verify pairwise after universal snap
    if _univ_changed:
        for pi in range(len(planned) - 1):
            i, s_src_i, e_i, s_i, e_pad_i = planned[pi]
            j, s_src_j, e_j, s_j, e_pad_j = planned[pi + 1]
            if e_pad_i > s_j - gap_s and pi not in resolved:
                mid = (e_pad_i + s_j) / 2.0
                planned[pi]     = (i, s_src_i, e_i, s_i, mid - 0.005)
                planned[pi + 1] = (j, s_src_j, e_j, mid + 0.005, e_pad_j)

    return planned, resolved, gap_preserved


def _assert_invariants(planned, wt, resolved, gap_preserved=None,
                       src_gap_threshold: float = 0.300):
    for pi in range(len(planned) - 1):
        i, _, e_i, s_i, e_pad_i = planned[pi]
        j, s_src_j, _, s_j, _ = planned[pi + 1]
        min_gap = 0.0 if pi in resolved else 0.010
        assert e_pad_i <= s_j - min_gap + 1e-9, (
            f"seg[{i}].e={e_pad_i:.3f} > seg[{j}].s - {min_gap} = {s_j - min_gap:.3f}"
        )
        _has_src_gap = (s_src_j - e_i) > src_gap_threshold
        for ws, we in wt:
            if ws > s_j + 0.5:
                break
            if we - ws < 0.030:
                continue
            assert not (ws < e_pad_i < we), (
                f"seg[{i}].e={e_pad_i:.3f} inside word [{ws:.3f},{we:.3f}]"
            )
            assert not (ws < s_j < we), (
                f"seg[{j}].s={s_j:.3f} inside word [{ws:.3f},{we:.3f}]"
            )
            # Orphan: word entirely between e[i] and s[j]
            # Skip for large source gaps: excluded words are intentional.
            if not _has_src_gap:
                assert not (e_pad_i <= ws and we <= s_j), (
                    f"word [{ws:.3f},{we:.3f}] orphaned between seg[{i}].e={e_pad_i:.3f}"
                    f" and seg[{j}].s={s_j:.3f}"
                )


# ── Unit tests for _snap_boundary_out_of_word ──────────────────────────────

def test_word_snap_out_of_word():
    """Unit-test _snap_boundary_out_of_word — non-adjacent words."""
    wt = [(1.0, 2.0), (3.0, 4.0)]  # gap = 1.0s >> 20ms

    # t before any word — no snap
    t, w = _snap_boundary_out_of_word(0.5, wt)
    assert t == 0.5 and w is None, f"expected no snap, got t={t} w={w}"

    # t inside first word, majority before → word goes to left clip → we + gap
    t, w = _snap_boundary_out_of_word(1.8, wt)
    assert abs(t - (2.0 + 0.010)) < 1e-9 and w == (1.0, 2.0), f"got t={t} w={w}"

    # t inside first word, majority after → word goes to right clip → ws - gap
    t, w = _snap_boundary_out_of_word(1.2, wt)
    assert abs(t - (1.0 - 0.010)) < 1e-9 and w == (1.0, 2.0), f"got t={t} w={w}"

    # t at word boundary (ws or we) — strict inequality, no snap
    t, w = _snap_boundary_out_of_word(1.0, wt)
    assert t == 1.0 and w is None, "t==ws should not snap"
    t, w = _snap_boundary_out_of_word(2.0, wt)
    assert t == 2.0 and w is None, "t==we should not snap"

    print("PASS  test_word_snap_out_of_word")


def test_adjacent_word_contact_point():
    """Fix 1: boundary between adjacent words is valid at the contact point — no snap."""
    # Two words with 0ms gap (exact contact)
    wt_contact = [(1.0, 1.5), (1.5, 2.0)]

    # t exactly at the contact point
    t, w = _snap_boundary_out_of_word(1.5, wt_contact)
    assert t == 1.5 and w is None, f"contact point should not snap: t={t} w={w}"

    # Two words with 10ms gap (< 20ms tol)
    wt_near = [(1.0, 1.50), (1.51, 2.00)]  # gap = 10ms

    # t in the 10ms gap — valid as-is
    t, w = _snap_boundary_out_of_word(1.505, wt_near)
    assert t == 1.505 and w is None, f"near-contact gap should not snap: t={t} w={w}"

    # t inside word_a, majority_before, next word adjacent → snap to contact point (we), not we+gap
    # word_a=[1.0,1.5], majority_before means t closer to we=1.5
    t, w = _snap_boundary_out_of_word(1.48, wt_near)
    assert abs(t - 1.50) < 1e-9 and w == (1.0, 1.50), (
        f"adjacent snap should land at we=1.50 not we+gap: t={t:.4f}"
    )

    # t inside word_b, majority_after, prev word adjacent → snap to contact point (ws_b=1.51, via prev.we=1.50)
    # Actually returns valid[idx-1][1] = 1.50
    t, w = _snap_boundary_out_of_word(1.515, wt_near)
    assert abs(t - 1.50) < 1e-9 and w == (1.51, 2.00), (
        f"adjacent snap from word_b should land at prev.we=1.50: t={t:.4f}"
    )

    # Two words with 25ms gap (> 20ms tol) — normal snap with gap_s applies
    wt_far = [(1.0, 1.50), (1.525, 2.00)]  # gap = 25ms

    t, w = _snap_boundary_out_of_word(1.48, wt_far)
    assert abs(t - (1.50 + 0.010)) < 1e-9 and w == (1.0, 1.50), (
        f"non-adjacent snap should use we+gap_s: t={t:.4f}"
    )

    print("PASS  test_adjacent_word_contact_point")


# ── Integration tests for the stabilization loop ───────────────────────────

def test_clean_no_overlap():
    """No overlap, no words straddling — loop should exit on pass 0 unchanged."""
    wt = [(10.0, 10.5), (10.6, 11.0)]
    planned = [
        (0, 9.0, 10.5, 9.85, 10.65),   # e_pad = 10.65
        (1, 10.7, 11.0, 10.75, 11.10), # s = 10.75 — gap = 0.10 > 0.010 ✓
    ]
    result, resolved, gap_pres = _simulate_stabilization([t for t in planned], wt)
    _assert_invariants(result, wt, resolved, gap_pres)
    print("PASS  test_clean_no_overlap")


def test_standard_overlap_clamp():
    """Padding pushes two clips into overlap; clamp resolves without oscillation."""
    wt = [(9.0, 9.5), (10.0, 10.5), (11.0, 11.5)]
    planned = [
        (0, 8.0, 9.5, 8.85, 10.65),   # e_pad = 10.65
        (1, 10.0, 10.5, 9.85, 11.15), # s = 9.85 — overlap!
    ]
    result, resolved, gap_pres = _simulate_stabilization([t for t in planned], wt)
    _assert_invariants(result, wt, resolved, gap_pres)
    print("PASS  test_standard_overlap_clamp")


def test_jamais_adjacent_words():
    """
    The 'jamais' scenario: two adjacent words (0-gap) straddle the boundary.
      jamais1 = [16.02, 16.73]  (GAP-RESCUE'd into seg3)
      jamais2 = [16.73, 16.88]  (first word of seg4)
    Segments are CONTIGUOUS in source space (s_src_j = e_i = 16.73 → src_gap=0).
    Fallback must place e=s=16.73 (inter-word contact point).
    """
    wt = [
        (15.50, 16.02),  # some preceding word
        (16.02, 16.73),  # jamais1
        (16.73, 16.88),  # jamais2
        (16.90, 17.20),  # next word
    ]
    # seg3: e_pad = 16.75 (inside jamais2, produced by padding after word-snap of e)
    # seg4: s = 16.73 (start of jamais2); s_src = 16.73 → src_gap = 16.73 - 16.73 = 0
    planned = [
        (3, 14.0, 16.73, 14.85, 16.75),  # e_i=16.73, s_src=14.0
        (4, 16.73, 17.20, 16.73, 17.35), # s_src=16.73 → src_gap = 16.73 - 16.73 = 0
    ]
    result, resolved, gap_pres = _simulate_stabilization([t for t in planned], wt)

    # Both invariants must hold
    _assert_invariants(result, wt, resolved, gap_pres)

    e_final = result[0][4]
    s_final = result[1][3]
    print(f"      jamais scenario: e={e_final:.3f} s={s_final:.3f} resolved={resolved}")

    # Boundary must be at the inter-word point (16.73) or just before it
    assert e_final <= 16.73 + 1e-9, f"e_final {e_final:.3f} should be <= 16.73"
    assert s_final >= 16.73 - 1e-9, f"s_final {s_final:.3f} should be >= 16.73"
    print("PASS  test_jamais_adjacent_words")


def test_planned_gap_preserved_in_fallback():
    """
    Fix 2: when a planned source gap > 300ms exists between seg[i] and seg[j],
    the fallback must NOT bridge it. It must set:
      e[i] = e_i + 10ms  (just past last word of seg[i])
      s[j] = original s_padded of seg[j]  (unmodified by clamp)
    'retiens' at [19.0, 19.5] lives in seg[j]'s clip and must be included.
    """
    wt = [
        (15.50, 16.02),  # word before jamais1
        (16.02, 16.73),  # jamais1 (last word of seg3)
        (16.73, 16.88),  # jamais2 (first word in the gap — excluded)
        (17.00, 17.30),  # 'et' (in gap — excluded)
        (18.20, 18.60),  # first word of seg4 (after 1.4s gap)
        (19.00, 19.50),  # 'retiens' (must appear in seg4)
    ]
    # seg3: ends at source 16.73 (e_i=16.73), padded end = 16.73 + 0.12 = 16.85
    # seg4: starts at source 18.13 (s_src_j=18.13), padded start = 18.13 - 0.12 = 18.01
    # Source gap = s_src_j - e_i = 18.13 - 16.73 = 1.40s → GAP-PRESERVING path
    planned = [
        (3, 14.0, 16.73, 14.85, 16.85),  # e_i=16.73, e_pad=16.85 (inside jamais2)
        (4, 18.13, 19.50, 18.01, 19.62), # s_src=18.13, s_pad_orig=18.01
    ]
    result, resolved, gap_pres = _simulate_stabilization([t for t in planned], wt)

    _assert_invariants(result, wt, resolved, gap_pres)

    e_final = result[0][4]
    s_final = result[1][3]
    print(f"      gap-preserved: e={e_final:.3f} s={s_final:.3f} resolved={resolved}")

    # e[i] must be just after jamais2 (16.88 + gap_s = 16.89), NOT inside the gap content
    # (gap content = words between 16.88 and 18.20: 'et' at 17.00-17.30)
    assert e_final < 17.00 - 1e-9, (
        f"e_final={e_final:.3f} should be before 'et' at 17.00, not in gap content"
    )
    # s[j] must be restored to original s_pad ~18.01, not pulled back to 16.73
    assert s_final >= 17.50, (
        f"s_final={s_final:.3f} should be near 18.01 (original), not gap-bridged"
    )
    # 'retiens' [19.0, 19.5] must NOT straddle s_final
    assert not (19.00 < s_final < 19.50), (
        f"s_final={s_final:.3f} is inside 'retiens' — it would be cut"
    )
    # Must NOT be in _resolved (gap case keeps 10ms constraint)
    assert 0 not in resolved, "gap-preserving fallback must not add to resolved"
    print("PASS  test_planned_gap_preserved_in_fallback")


def test_orphan_word_between_segments():
    """
    Fix 3 (orphan repair): a word [ws, we] fully between e_pad_i and s_j
    must be assigned to the closer segment. Two sub-cases:
      A) word closer to seg[i] → e[i] extends to we+gap_s
      B) word closer to seg[j] → s[j] pulls back to ws-gap_s
    """
    # Case A: word [13.08, 13.36] equally close to both sides
    # e_pad_i=13.080, s_j=13.360 → word exactly fills the gap
    # d_i = 13.08 - 13.08 = 0 < d_j = 13.36 - 13.36 = 0 → tie → seg[j] wins
    wt_a = [
        (12.50, 13.08),   # last word of seg[i]
        (13.08, 13.36),   # ORPHAN word
        (13.36, 13.90),   # first word of seg[j]
    ]
    planned_a = [
        (1, 12.00, 13.08, 12.50, 13.08),   # e_pad = 13.08 (= ws)
        (2, 13.36, 13.90, 13.36, 14.02),   # s_j   = 13.36 (= we)
    ]
    result_a, resolved_a, gap_pres_a = _simulate_stabilization(list(planned_a), wt_a)
    _assert_invariants(result_a, wt_a, resolved_a, gap_pres_a)
    print(f"      orphan-A: e={result_a[0][4]:.3f} s={result_a[1][3]:.3f}")
    print("PASS  test_orphan_word_between_segments (case A)")

    # Case B: word [21.74, 21.98] orphaned because e_pad_i == ws and s_j == we
    # (strict-inequality snap doesn't fire at exact boundaries).
    # source gap = 21.98 - 21.70 = 0.28s < 300ms → orphan repair applies.
    # d_i = 21.74 - 21.74 = 0 (tie), d_j = 21.98 - 21.98 = 0 → seg[j] wins.
    wt_b = [
        (20.80, 21.50),   # last word of seg[i]
        (21.74, 21.98),   # ORPHAN — e_pad_i exactly at ws, s_j exactly at we
        (22.10, 22.60),   # first word of seg[j]
    ]
    planned_b = [
        (3, 20.00, 21.70, 20.50, 21.74),   # e_i=21.70, e_pad=21.74 (=ws, no strict snap)
        (4, 21.98, 22.60, 21.98, 22.72),   # s_src=21.98, s_j=21.98 (=we, no strict snap)
    ]
    result_b, resolved_b, gap_pres_b = _simulate_stabilization(list(planned_b), wt_b)
    _assert_invariants(result_b, wt_b, resolved_b, gap_pres_b)
    e_b = result_b[0][4]
    s_b = result_b[1][3]
    print(f"      orphan-B: e={e_b:.3f} s={s_b:.3f}")
    # s[j] must be pulled before orphan word (21.74) or to contact point
    assert s_b <= 21.74, f"s[j]={s_b:.3f} should be <= 21.74 (before orphan)"
    # e[i] must stay before or at s[j]
    assert e_b < s_b + 1e-9, f"e[i]={e_b:.3f} must be < s[j]={s_b:.3f}"
    print("PASS  test_orphan_word_between_segments (case B)")


def test_gap_fallback_snaps_restored_s():
    """
    Fix 4 (universal snap): when the gap-preserving fallback restores the
    original s_padded for seg[j], it must snap that value out of any word.
    Here s_padded_orig = 27.52 falls inside 'retiens' [27.40, 27.80].
    After fallback + snap, s[j] must be outside that word.
    """
    wt = [
        (25.00, 25.60),   # last word of seg[i]
        (26.20, 26.70),   # word in gap (excluded)
        (27.40, 27.80),   # 'retiens' — s_padded_orig lands inside this
        (28.00, 28.50),   # first word of seg[j]'s clip
    ]
    # Source gap = s_src_j - e_i = 27.50 - 25.60 = 1.90s > 300ms → GAP-PRESERVING
    # s_padded_orig = 27.52 (inside 'retiens')
    planned = [
        (4, 24.00, 25.60, 24.50, 25.72),   # e_i=25.60
        (5, 27.50, 28.50, 27.52, 28.62),   # s_src=27.50, s_pad_orig=27.52
    ]
    result, resolved, gap_pres = _simulate_stabilization(list(planned), wt)
    _assert_invariants(result, wt, resolved, gap_pres)

    s_final = result[1][3]
    print(f"      fallback-snap: s_final={s_final:.3f}")
    # s[j] must NOT be inside 'retiens' [27.40, 27.80]
    assert not (27.40 < s_final < 27.80), (
        f"s_final={s_final:.3f} is inside 'retiens' [27.40,27.80] — snap failed"
    )
    print("PASS  test_gap_fallback_snaps_restored_s")


def test_universal_snap_non_conflicting():
    """
    Fix 1 (universal snap): a boundary that lands inside a word on a pair
    with a large gap (no pairwise conflict) must still be snapped.

    's[5]=27.640' scenario: seg[4].e_pad and seg[5].s are far apart (gap +1.38s),
    so the stabilization loop never clamps them — but s[5]=27.640 is inside
    'retiens' [27.40, 27.80]. Universal snap must move it to 27.39 (ws-gap_s).
    """
    wt = [
        (26.00, 26.50),   # last word of seg[4]
        (27.40, 27.80),   # 'retiens' — s[5] lands inside this
        (28.10, 28.60),   # first word of seg[5]
    ]
    # gap between seg[4] end-pad and seg[5] start = 27.640 - 26.620 = +1.02s
    # → no pairwise conflict, snap loop never fires on seg[5].s during stab
    planned = [
        (4, 25.00, 26.50, 25.20, 26.62),   # e_pad = 26.62 (clean)
        (5, 27.50, 28.60, 27.64, 28.72),   # s = 27.64 (INSIDE 'retiens')
    ]
    result, resolved, gap_pres = _simulate_stabilization(list(planned), wt)
    _assert_invariants(result, wt, resolved, gap_pres)

    s_final = result[1][3]
    print(f"      universal-snap: s_final={s_final:.3f}")
    # s[5] must be snapped OUT of 'retiens' [27.40, 27.80]
    assert not (27.40 < s_final < 27.80), (
        f"s_final={s_final:.3f} still inside 'retiens' [27.40,27.80]"
    )
    print("PASS  test_universal_snap_non_conflicting")


if __name__ == "__main__":
    test_word_snap_out_of_word()
    test_adjacent_word_contact_point()
    test_clean_no_overlap()
    test_standard_overlap_clamp()
    test_jamais_adjacent_words()
    test_planned_gap_preserved_in_fallback()
    test_orphan_word_between_segments()
    test_gap_fallback_snaps_restored_s()
    test_universal_snap_non_conflicting()
    print("\nAll tests passed.")
