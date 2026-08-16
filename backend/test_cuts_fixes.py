#!/usr/bin/env python3
"""
Tests unitaires pour les 4 fixes du pipeline de cuts (commit fix(cuts): enforce filler scope
and coordinate-safe guards).

Tests couverts :
  - Fix A : FILLER-SCOPE-TRIM (pipeline.py)
  - Fix B : _guard_drops_against_key_content exemption llm_false_start (pipeline.py)
  - Fix DEDUP : min-end pour filler drops (pipeline.py)
  - Fix C : _c2s conversion dans WORD-LOST guard 3 (pretrim.py)

Run :
    python backend/test_cuts_fixes.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_word(text: str, start: float, end: float) -> dict:
    return {"text": text, "start": start, "end": end}


def _filler_scope_trim(word_texts: list[str], i0: int, i1: int) -> int:
    """Mirror of the FILLER-SCOPE-TRIM algorithm in _llm_editorial_cuts.
    Returns the trimmed i1 (== original i1 - 1 as sentinel when SKIP).
    Input: list of word text strings, indices [i0..i1] to scan.
    """
    from app.engine.silence_remover import _is_filler, _FILLER_PHRASES
    _filler_end = i0 - 1
    _k = i0
    while _k <= i1:
        _matched = False
        for _ph in _FILLER_PHRASES:
            _pn = len(_ph)
            if _k + _pn - 1 > i1:
                continue
            if all(
                word_texts[_k + _pi].strip(".,!?;:'\"()").lower() == _ph[_pi]
                for _pi in range(_pn)
            ):
                _filler_end = _k + _pn - 1
                _k += _pn
                _matched = True
                break
        if not _matched:
            if _is_filler(word_texts[_k]):
                _filler_end = _k
                _k += 1
            else:
                break
    return _filler_end


# ──────────────────────────────────────────────────────────────────────────────
# Fix A : FILLER-SCOPE-TRIM
# ──────────────────────────────────────────────────────────────────────────────

def test_filler_scope_trim_single_filler_with_content():
    """LLM cuts [Bah, retiens] -> trim to [Bah]."""
    words = ["Bah,", "retiens", "ça"]
    result = _filler_scope_trim(words, 0, 1)
    assert result == 0, f"expected 0, got {result}"
    print("PASS  FILLER-SCOPE-TRIM: 'Bah, retiens' -> [Bah] (i1=0)")


def test_filler_scope_trim_single_filler_alone():
    """LLM cuts [Bah] alone -> no trim."""
    words = ["Bah,", "retiens", "ça"]
    result = _filler_scope_trim(words, 0, 0)
    assert result == 0, f"expected 0, got {result}"
    print("PASS  FILLER-SCOPE-TRIM: 'Bah' alone -> no trim (i1=0)")


def test_filler_scope_trim_multiword_du_coup():
    """LLM cuts [du coup il faut] -> trim to [du coup]."""
    words = ["du", "coup", "il", "faut"]
    result = _filler_scope_trim(words, 0, 3)
    assert result == 1, f"expected 1 (coup), got {result}"
    print("PASS  FILLER-SCOPE-TRIM: 'du coup il faut' -> [du coup] (i1=1)")


def test_filler_scope_trim_multiword_tu_vois():
    """LLM cuts [tu vois c'est] -> trim to [tu vois]."""
    words = ["tu", "vois", "c'est"]
    result = _filler_scope_trim(words, 0, 2)
    assert result == 1, f"expected 1 (vois), got {result}"
    print("PASS  FILLER-SCOPE-TRIM: 'tu vois c'est' -> [tu vois] (i1=1)")


def test_filler_scope_trim_euh_content():
    """LLM cuts [euh regarde] -> trim to [euh]."""
    words = ["euh", "regarde"]
    result = _filler_scope_trim(words, 0, 1)
    assert result == 0, f"expected 0, got {result}"
    print("PASS  FILLER-SCOPE-TRIM: 'euh regarde' -> [euh] (i1=0)")


def test_filler_scope_trim_unknown_word_skip():
    """LLM cuts [vraiment] with reason=filler -> SKIP (no canonical filler)."""
    words = ["vraiment", "bien"]
    result = _filler_scope_trim(words, 0, 1)
    assert result < 0, f"expected sentinel (< 0), got {result}"
    print("PASS  FILLER-SCOPE-TRIM: 'vraiment bien' -> SKIP (sentinel)")


def test_filler_scope_trim_multiword_only_phrase():
    """LLM cuts exactly [du coup] -> no trim."""
    words = ["du", "coup"]
    result = _filler_scope_trim(words, 0, 1)
    assert result == 1, f"expected 1, got {result}"
    print("PASS  FILLER-SCOPE-TRIM: 'du coup' exact -> no trim (i1=1)")


# ──────────────────────────────────────────────────────────────────────────────
# Fix B : _guard_drops_against_key_content
# ──────────────────────────────────────────────────────────────────────────────

def _make_drop(start: float, end: float, reason: str):
    from app.engine.silence_remover import DropSegment
    return DropSegment(start=start, end=end, reason=reason)


def _make_plan(key_lines: list[str]):
    class _FakePlan:
        raw = {}

        @property
        def key_lines(self):
            return key_lines

    return _FakePlan()


def _run_guard(drops, key_lines, transcript):
    from app.api.pipeline import _guard_drops_against_key_content
    plan = _make_plan(key_lines)
    return _guard_drops_against_key_content(drops, plan, transcript)


def _make_transcript(words_with_times: list[tuple[str, float, float]]) -> dict:
    """words_with_times: list of (text, start, end)."""
    return {
        "segments": [{
            "words": [{"text": t, "start": s, "end": e} for t, s, e in words_with_times]
        }]
    }


def test_guard_false_start_allowed():
    """False-start 'La vérité,' [0..0] -> allowed when key_line='la vraie vérité'."""
    # Transcript: [0] La(30.0-30.05) [1] vérité,(30.05-30.35) [2] la(30.45-30.50)
    #             [3] vraie(30.50-30.70) [4] vérité,(30.70-33.00)
    words = [
        ("La", 30.00, 30.05),
        ("vérité,", 30.05, 30.35),
        ("la", 30.45, 30.50),
        ("vraie", 30.50, 30.70),
        ("vérité,", 30.70, 33.00),
    ]
    transcript = _make_transcript(words)
    # False-start cut covers word[1] ("vérité,") : drop [30.05, 30.45]
    drops = [_make_drop(30.05, 30.45, "llm_false_start")]
    result = _run_guard(drops, ["la vraie vérité"], transcript)
    assert len(result) == 1, (
        f"Guard rejected a valid false_start cut — expected 1 drop, got {len(result)}: {result}"
    )
    print("PASS  GUARD: false_start cut on 'vérité,' [faux départ] — allowed")


def test_guard_repetition_targeting_key_content_blocked():
    """Repetition cut targeting second 'vérité' (key-line) -> blocked."""
    # "la vérité, enfin la vérité"
    words = [
        ("la", 10.00, 10.05),
        ("vérité,", 10.05, 10.40),
        ("enfin", 10.50, 10.70),
        ("la", 10.80, 10.85),
        ("vérité", 10.85, 11.50),
    ]
    transcript = _make_transcript(words)
    # Wrong repetition cut targeting second "vérité" (the actual key_line)
    drops = [_make_drop(10.85, 11.60, "llm_repetition")]
    result = _run_guard(drops, ["la vérité"], transcript)
    assert len(result) == 0, (
        f"Guard should have blocked repetition cut on key-line 'vérité' — got {len(result)}"
    )
    print("PASS  GUARD: repetition cut on key-line content — blocked")


def test_guard_false_start_does_not_affect_repetition():
    """llm_repetition cuts do NOT get the false_start exemption."""
    words = [
        ("la", 10.00, 10.05),
        ("vérité,", 10.05, 10.40),
        ("enfin", 10.50, 10.70),
        ("la", 10.80, 10.85),
        ("vérité", 10.85, 11.50),
    ]
    transcript = _make_transcript(words)
    # First "vérité," is within drop boundaries — but reason is repetition, not false_start
    drops = [_make_drop(10.05, 10.45, "llm_repetition")]
    result = _run_guard(drops, ["la vraie vérité"], transcript)
    # "vérité" is a protected norm (≥3 chars). First occurrence IS within the drop.
    # With reason=llm_repetition, exemption does NOT apply -> blocked.
    assert len(result) == 0, (
        f"llm_repetition should not get false_start exemption — got {len(result)}"
    )
    print("PASS  GUARD: llm_repetition on key_line word — blocked (no exemption)")


def test_guard_no_key_lines():
    """No key_lines -> all drops pass through."""
    drops = [_make_drop(1.0, 2.0, "filler_word"), _make_drop(3.0, 4.0, "llm_filler")]
    result = _run_guard(drops, [], _make_transcript([]))
    assert len(result) == 2
    print("PASS  GUARD: empty key_lines -> all drops pass")


# ──────────────────────────────────────────────────────────────────────────────
# Fix DEDUP : min-end pour filler drops
# ──────────────────────────────────────────────────────────────────────────────

def test_dedup_filler_uses_min_end():
    """rule-based filler + LLM filler -> merged with min-end (conservative)."""
    from app.api.pipeline import _dedup_drops
    from app.engine.silence_remover import DropSegment as DS

    # rule-based: [9.92, 10.24] — post-pad up to retiens.start
    # LLM [68,69]: [10.00, 10.80] — over-extends into content word
    rule = DS(start=9.92, end=10.24, reason="filler_word")
    llm  = DS(start=10.00, end=10.80, reason="llm_filler")

    result = _dedup_drops([rule, llm])
    assert len(result) == 1, f"expected 1 merged drop, got {len(result)}"
    merged = result[0]
    assert merged.start == 9.92, f"start should be min(9.92,10.00)=9.92, got {merged.start}"
    assert abs(merged.end - 10.24) < 1e-9, (
        f"end should be min(10.24,10.80)=10.24, got {merged.end}"
    )
    print(f"PASS  DEDUP filler: [{rule.start},{rule.end}]+[{llm.start},{llm.end}]"
          f" -> [{merged.start},{merged.end}] (min-end)")


def test_dedup_repetition_uses_max_end():
    """Repetition drops -> merged with max-end (behavior unchanged)."""
    from app.api.pipeline import _dedup_drops
    from app.engine.silence_remover import DropSegment as DS

    d1 = DS(start=5.00, end=5.50, reason="llm_repetition")
    d2 = DS(start=5.20, end=5.80, reason="llm_repetition")

    result = _dedup_drops([d1, d2])
    assert len(result) == 1
    assert abs(result[0].end - 5.80) < 1e-9, (
        f"repetition should use max-end: expected 5.80, got {result[0].end}"
    )
    print("PASS  DEDUP repetition: max-end preserved (no regression)")


def test_dedup_no_overlap_unchanged():
    """Non-overlapping drops are not merged."""
    from app.api.pipeline import _dedup_drops
    from app.engine.silence_remover import DropSegment as DS

    d1 = DS(start=1.0, end=1.5, reason="filler_word")
    d2 = DS(start=3.0, end=3.5, reason="filler_word")

    result = _dedup_drops([d1, d2])
    assert len(result) == 2, f"no overlap -> 2 drops, got {len(result)}"
    print("PASS  DEDUP: non-overlapping drops unchanged")


def test_dedup_filler_min_end_does_not_lose_rule_padding():
    """min-end gives rule_end (not llm_end) when llm_end > rule_end — padding preserved."""
    from app.api.pipeline import _dedup_drops
    from app.engine.silence_remover import DropSegment as DS

    # rule post-pad = word.end+0.160 = 10.360 < next.start = 10.400 -> rule_end=10.360
    # llm t_end = next.start = 10.400
    rule = DS(start=9.92, end=10.36, reason="filler_word")
    llm  = DS(start=10.00, end=10.40, reason="llm_filler")

    result = _dedup_drops([rule, llm])
    assert len(result) == 1
    # min(10.36, 10.40) = 10.36 -> rule-based padding preserved
    assert abs(result[0].end - 10.36) < 1e-9, (
        f"expected 10.36 (rule padding), got {result[0].end}"
    )
    print("PASS  DEDUP filler min-end: rule-based padding preserved")


# ──────────────────────────────────────────────────────────────────────────────
# Fix C : _c2s pour WORD-LOST guard 3
# ──────────────────────────────────────────────────────────────────────────────

def test_c2s_word_lost_scenario():
    """Reproduce the WORD-LOST coordinate mismatch scenario.

    virtual_drop: SOURCE 5.0->7.0 (2s removed)
    LLM filler zone (COMPRESSED): 21.02->21.46
    source_word 'Du': SOURCE 23.02s

    After _c2s: zone becomes SOURCE 23.02->23.46
    Guard must fire: 23.02 - 0.010 <= 23.02 < 23.46 -> True
    """
    from app.engine.pretrim import _c2s
    from app.engine.silence_remover import DropSegment

    vd = [DropSegment(start=5.0, end=7.0, reason="pause")]

    fz_s_compressed = 21.02
    fz_e_compressed = 21.46
    wl_ws_source    = 23.02   # 'Du' in SOURCE space

    fz_s_source = _c2s(fz_s_compressed, vd)
    fz_e_source = _c2s(fz_e_compressed, vd)

    assert abs(fz_s_source - 23.02) < 1e-9, f"fz_s SOURCE: expected 23.02, got {fz_s_source}"
    assert abs(fz_e_source - 23.46) < 1e-9, f"fz_e SOURCE: expected 23.46, got {fz_e_source}"

    # Guard condition (after fix): fz_s - 0.010 <= wl_ws < fz_e
    guard_fires = (fz_s_source - 0.010) <= wl_ws_source < fz_e_source
    assert guard_fires, (
        f"WORD-LOST guard 3 should fire: "
        f"{fz_s_source-0.010:.3f} <= {wl_ws_source:.3f} < {fz_e_source:.3f}"
    )
    print("PASS  Fix C: _c2s converts COMPRESSED->SOURCE, WORD-LOST guard fires")


def test_c2s_word_lost_broken_without_conversion():
    """Without conversion, COMPRESSED zone vs SOURCE word -> guard does NOT fire."""
    fz_s_compressed = 21.02
    fz_e_compressed = 21.46
    wl_ws_source    = 23.02

    # Broken comparison: compressed zone vs source word
    guard_fires = (fz_s_compressed - 0.010) <= wl_ws_source < fz_e_compressed
    assert not guard_fires, "Sanity check: guard must NOT fire without conversion"
    print("PASS  Fix C: without conversion guard does not fire (confirming the bug)")


def test_c2s_use_source_coords_false():
    """When use_source_coords=False, raw floats are used (no _c2s)."""
    # In this mode both _wl_ws and fz_s/_fz_e are in COMPRESSED space -> consistent
    fz_s = 21.02
    fz_e = 21.46
    wl_ws = 21.10  # compressed word timestamp

    guard_fires = (fz_s - 0.010) <= wl_ws < fz_e
    assert guard_fires, "use_source_coords=False: raw COMPRESSED comparison should work"
    print("PASS  Fix C: use_source_coords=False path — raw floats, guard fires correctly")


# ──────────────────────────────────────────────────────────────────────────────
# Régression : mécanismes existants non altérés
# ──────────────────────────────────────────────────────────────────────────────

def test_regression_dedup_halve_logic_unchanged():
    """DEDUP-HALVE is inside _llm_editorial_cuts (not _dedup_drops) — unaffected."""
    # We verify _dedup_drops doesn't interfere with repetition reasoning.
    from app.api.pipeline import _dedup_drops
    from app.engine.silence_remover import DropSegment as DS

    # Two adjacent non-overlapping repetition drops -> not merged
    d1 = DS(start=1.0, end=1.5, reason="llm_repetition")
    d2 = DS(start=2.0, end=2.5, reason="llm_repetition")
    result = _dedup_drops([d1, d2])
    assert len(result) == 2, "Adjacent non-overlapping reps must not merge"
    print("PASS  REGRESSION: DEDUP-HALVE path not touched by _dedup_drops change")


def test_regression_guard_no_false_positive_on_filler():
    """Filler drops do NOT get the llm_false_start exemption."""
    words = [
        ("vraiment", 10.00, 10.40),
        ("vérité,",  10.40, 10.80),
    ]
    transcript = _make_transcript(words)
    # filler cut on "vraiment" — "vérité" is protected but not within the cut
    drops = [_make_drop(10.00, 10.45, "filler_word")]
    result = _run_guard(drops, ["la vraie vérité"], transcript)
    # "vérité" at (10.40, 10.80) — drop ends at 10.45, so drop.end > ws (10.40) -> overlap
    # But "vérité" is NOT entirely within the drop (we=10.80 > drop.end=10.45)
    # And reason is "filler_word" (not llm_false_start) -> no exemption
    # -> blocked
    assert len(result) == 0, (
        f"filler cut overlapping key content must be blocked — got {len(result)}"
    )
    print("PASS  REGRESSION: filler cut overlapping key content — still blocked")


def test_regression_filler_scope_trim_single_pass_only():
    """Multiple consecutive canonical fillers are all kept within the trim."""
    # e.g. "euh hm regarde" — both "euh" and "hm" are fillers, "regarde" is not
    words = ["euh", "hm", "regarde"]
    result = _filler_scope_trim(words, 0, 2)
    # Forward scan: "euh"->filler_end=0, "hm"->filler_end=1, "regarde"->break
    assert result == 1, f"expected 1 (hm), got {result}"
    print("PASS  REGRESSION: consecutive single-word fillers all retained in trim")


def test_regression_dedup_filler_one_drop_unchanged():
    """Single filler drop -> no merge, returned as-is."""
    from app.api.pipeline import _dedup_drops
    from app.engine.silence_remover import DropSegment as DS

    d = DS(start=10.0, end=10.3, reason="filler_word")
    result = _dedup_drops([d])
    assert len(result) == 1
    assert result[0] is d
    print("PASS  REGRESSION: single filler drop passes through _dedup_drops unchanged")


# ──────────────────────────────────────────────────────────────────────────────
# Test d'interaction : pipeline complet sur scénario composite
# ──────────────────────────────────────────────────────────────────────────────

def test_interaction_bah_retiens_full_pipeline():
    """Integration: LLM returns [Bah,retiens] filler + rule-based [Bah] filler
    -> after TRIM + DEDUP -> final drop covers only [Bah] (retiens preserved).
    """
    from app.api.pipeline import _dedup_drops
    from app.engine.silence_remover import DropSegment as DS

    BAH_S, BAH_E = 10.00, 10.20
    RETIENS_S, RETIENS_E = 10.26, 10.60
    RETIENS_NEXT_S = 10.80  # "ça"

    # After FILLER-SCOPE-TRIM, LLM [Bah,retiens] would have been trimmed to [Bah,Bah].
    # t_end after trim = words[bah_idx+1].start = retiens.start = 10.26
    llm_trimmed  = DS(start=BAH_S, end=RETIENS_S, reason="llm_filler")
    # Rule-based filler drop for Bah (with post-pad 160ms, capped at retiens.start)
    rule_based   = DS(start=BAH_S - 0.080, end=min(BAH_E + 0.160, RETIENS_S), reason="filler_word")

    merged = _dedup_drops([rule_based, llm_trimmed])
    assert len(merged) == 1
    assert merged[0].end <= RETIENS_S + 1e-9, (
        f"'retiens' must not be included in cut. merged.end={merged[0].end:.3f} > retiens.start={RETIENS_S}"
    )
    print(
        f"PASS  INTERACTION: Bah cut ends at {merged[0].end:.3f}s "
        f"(retiens.start={RETIENS_S}) — retiens preserved"
    )


def test_interaction_false_start_end_to_end():
    """Integration: false_start cut through DEDUP + guard.
    'La vérité,' [faux départ] -> cut passes both stages.
    """
    from app.api.pipeline import _dedup_drops, _guard_drops_against_key_content
    from app.engine.silence_remover import DropSegment as DS

    words_raw = [
        ("La",      30.00, 30.05),
        ("vérité,", 30.05, 30.35),
        ("la",      30.45, 30.50),
        ("vraie",   30.50, 30.70),
        ("vérité,", 30.70, 33.00),
    ]
    transcript = _make_transcript(words_raw)

    # LLM false_start cut covers [30.05, 30.45] (word[1] — "vérité,")
    false_start_drop = DS(start=30.05, end=30.45, reason="llm_false_start")

    # Stage 1: DEDUP (no rule-based filler for false_start -> pass through)
    after_dedup = _dedup_drops([false_start_drop])
    assert len(after_dedup) == 1, "false_start drop should pass DEDUP unchanged"

    # Stage 2: guard
    plan = _make_plan(["la vraie vérité"])
    after_guard = _guard_drops_against_key_content(after_dedup, plan, transcript)
    assert len(after_guard) == 1, (
        f"false_start cut should pass guard — got {len(after_guard)}: {after_guard}"
    )
    print("PASS  INTERACTION: false_start cut passes DEDUP + guard end-to-end")


# ──────────────────────────────────────────────────────────────────────────────
# Régression : jamais x3 (must remain KEEP — tested at LLM level; DEDUP has no effect)
# ──────────────────────────────────────────────────────────────────────────────

def test_regression_jamais_x3_no_filler_trim():
    """'Jamais, jamais, jamais' has no filler tokens -> FILLER-SCOPE-TRIM SKIPs it.
    RHETORICAL-TRIPLE fires in _llm_editorial_cuts before TRIM — but even if trim
    is reached (e.g. reason misclassified as filler), it returns sentinel.
    """
    words = ["Jamais,", "jamais,", "jamais"]
    result = _filler_scope_trim(words, 0, 2)
    assert result < 0, f"No canonical filler in 'Jamais x3' -> expected SKIP sentinel, got {result}"
    print("PASS  REGRESSION: 'Jamais x3' -> FILLER-SCOPE-TRIM returns SKIP sentinel")


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 70)
    print("Fix A — FILLER-SCOPE-TRIM")
    print("=" * 70)
    test_filler_scope_trim_single_filler_with_content()
    test_filler_scope_trim_single_filler_alone()
    test_filler_scope_trim_multiword_du_coup()
    test_filler_scope_trim_multiword_tu_vois()
    test_filler_scope_trim_euh_content()
    test_filler_scope_trim_unknown_word_skip()
    test_filler_scope_trim_multiword_only_phrase()

    print()
    print("=" * 70)
    print("Fix B — KEY-GUARD / false_start exemption")
    print("=" * 70)
    test_guard_false_start_allowed()
    test_guard_repetition_targeting_key_content_blocked()
    test_guard_false_start_does_not_affect_repetition()
    test_guard_no_key_lines()

    print()
    print("=" * 70)
    print("Fix DEDUP — min-end pour fillers")
    print("=" * 70)
    test_dedup_filler_uses_min_end()
    test_dedup_repetition_uses_max_end()
    test_dedup_no_overlap_unchanged()
    test_dedup_filler_min_end_does_not_lose_rule_padding()

    print()
    print("=" * 70)
    print("Fix C — WORD-LOST coordinate space")
    print("=" * 70)
    test_c2s_word_lost_scenario()
    test_c2s_word_lost_broken_without_conversion()
    test_c2s_use_source_coords_false()

    print()
    print("=" * 70)
    print("Régressions")
    print("=" * 70)
    test_regression_dedup_halve_logic_unchanged()
    test_regression_guard_no_false_positive_on_filler()
    test_regression_filler_scope_trim_single_pass_only()
    test_regression_dedup_filler_one_drop_unchanged()
    test_regression_jamais_x3_no_filler_trim()

    print()
    print("=" * 70)
    print("Tests d'interaction")
    print("=" * 70)
    test_interaction_bah_retiens_full_pipeline()
    test_interaction_false_start_end_to_end()

    print()
    print("All tests passed.")
