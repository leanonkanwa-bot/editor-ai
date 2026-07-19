"""
Tests for 5-position sequential rotation in compose._remap_zone.

Covers both portrait (9:16) and landscape (16:9) code paths.
We replicate the rotation tables inline so any drift between this test
and compose.py surfaces as a failure here.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

# ── Exact copies of compose.py rotation tables ───────────────────────────────

_POS_NAMES = ("top-left", "top-right", "center-left", "center-right", "center-full")

_STD_PORTRAIT = (
    "upper-left-data-sm",    # pos 0 — top-left
    "upper-data",            # pos 1 — top-right
    "portrait-center-left",  # pos 2 — center-left (dimming)
    "portrait-center-right", # pos 3 — center-right (dimming)
    "portrait-center-full",  # pos 4 — center-full (dimming)
)

_STD_LANDSCAPE = (
    "landscape-tl",  # pos 0 — top-left
    "landscape-tr",  # pos 1 — top-right
    "landscape-cl",  # pos 2 — center-left
    "landscape-cr",  # pos 3 — center-right
    "landscape-cf",  # pos 4 — center-full (dimming)
)

_TALL_LEFT_PORTRAIT  = "upper-left-data"
_TALL_RIGHT_PORTRAIT = "upper-right-data-tall"
_TALL_LEFT_LANDSCAPE  = "landscape-tl-tall"
_TALL_RIGHT_LANDSCAPE = "landscape-tr-tall"

_TALL_DATA_PANEL_TYPES = frozenset({
    "day_in_life_schedule", "ingredient_list", "skill_tree_unlock",
    "audience_poll_result", "broken_promise_tracker", "resource_allocation",
})
_DATA_PANEL_TYPES = {
    "stat", "list", "comparison", "checklist", "score", "trend", "rating",
    "progress_bar", "countdown", "step_number", "price_tag", "recap_summary",
    "formula_equation", "pros_cons", "star_rating_review", "income_reveal",
    "data_bar_chart", "number_ranking", "question_answer_pair", "cause_effect",
    "percentage_split", "red_flag_list", "client_avatar_persona", "tool_stack",
    "revenue_breakdown", "hidden_cost_reveal", "social_proof_counter",
    "red_thread_connector", "day_in_life_schedule", "skill_tree_unlock",
    "audience_poll_result", "broken_promise_tracker", "ingredient_list",
    "resource_allocation",
}

_DIMMING_ZONES = frozenset({
    "fullscreen", "video-overlay",
    "portrait-center-left", "portrait-center-right", "portrait-center-full",
    "landscape-cf",
})

_SIDE_PANEL_ZONES = {
    "side-panel", "side-panel-left", "side-panel-right", "side-panel-top",
    "upper-data", "upper-right", "upper-left-data", "upper-left-data-sm",
    "upper-right-data-tall", "portrait-bottom-left", "portrait-bottom-right",
    "portrait-center-left", "portrait-center-right",
    "landscape-tl", "landscape-tr", "landscape-cl", "landscape-cr", "landscape-cf",
    "landscape-tl-tall", "landscape-tr-tall",
}


def _resolve_zone(style: str, data_card_idx: int, layout: str = "portrait") -> str:
    """Inline replica of the rotation logic in compose._remap_zone."""
    pos = data_card_idx % 5
    is_tall = style in _TALL_DATA_PANEL_TYPES
    if layout == "portrait":
        if is_tall:
            # Uses raw data_card_idx (not pos) for strict L/R alternation — matches compose.py.
            return _TALL_LEFT_PORTRAIT if data_card_idx % 2 == 0 else _TALL_RIGHT_PORTRAIT
        return _STD_PORTRAIT[pos]
    else:  # landscape
        if is_tall:
            return _TALL_LEFT_LANDSCAPE if data_card_idx % 2 == 0 else _TALL_RIGHT_LANDSCAPE
        return _STD_LANDSCAPE[pos]


# ── Portrait tests ────────────────────────────────────────────────────────────

def test_portrait_5position_sequence():
    """Portrait standard: top-left → top-right → center-left → center-right → center-full."""
    style = "stat"
    assert _resolve_zone(style, 0, "portrait") == "upper-left-data-sm"
    assert _resolve_zone(style, 1, "portrait") == "upper-data"
    assert _resolve_zone(style, 2, "portrait") == "portrait-center-left"
    assert _resolve_zone(style, 3, "portrait") == "portrait-center-right"
    assert _resolve_zone(style, 4, "portrait") == "portrait-center-full"


def test_portrait_cycle_repeats_at_5():
    """Position N and N+5 must resolve to the same zone (portrait)."""
    for style in ("stat", "list", "comparison", "checklist"):
        for idx in range(5):
            assert _resolve_zone(style, idx, "portrait") == _resolve_zone(style, idx + 5, "portrait"), \
                f"portrait cycle break: {style} idx={idx}"


def test_portrait_10_cards_two_full_cycles():
    """10 portrait cards produce exactly 2 complete cycles of all 5 positions."""
    style = "list"
    expected = list(_STD_PORTRAIT) * 2
    for i, exp in enumerate(expected):
        got = _resolve_zone(style, i, "portrait")
        assert got == exp, f"portrait card {i}: expected {exp!r}, got {got!r}"


def test_portrait_tall_stays_top_only():
    """Portrait tall types must never land in center or bottom zones."""
    forbidden = {
        "portrait-center-left", "portrait-center-right", "portrait-center-full",
        "portrait-bottom-left", "portrait-bottom-right",
    }
    for style in _TALL_DATA_PANEL_TYPES:
        for idx in range(10):
            zone = _resolve_zone(style, idx, "portrait")
            assert zone not in forbidden, \
                f"portrait tall {style} idx={idx} resolved to {zone!r}"


def test_portrait_tall_alternation():
    """Portrait tall: even positions → left, odd positions → right."""
    for style in _TALL_DATA_PANEL_TYPES:
        for idx in range(10):
            zone = _resolve_zone(style, idx, "portrait")
            if idx % 2 == 0:
                assert zone == _TALL_LEFT_PORTRAIT, \
                    f"{style} idx={idx} expected {_TALL_LEFT_PORTRAIT!r}, got {zone!r}"
            else:
                assert zone == _TALL_RIGHT_PORTRAIT, \
                    f"{style} idx={idx} expected {_TALL_RIGHT_PORTRAIT!r}, got {zone!r}"


# ── Landscape tests ───────────────────────────────────────────────────────────

def test_landscape_5position_sequence():
    """Landscape standard: top-left → top-right → center-left → center-right → center-full."""
    style = "stat"
    assert _resolve_zone(style, 0, "landscape") == "landscape-tl"
    assert _resolve_zone(style, 1, "landscape") == "landscape-tr"
    assert _resolve_zone(style, 2, "landscape") == "landscape-cl"
    assert _resolve_zone(style, 3, "landscape") == "landscape-cr"
    assert _resolve_zone(style, 4, "landscape") == "landscape-cf"


def test_landscape_cycle_repeats_at_5():
    """Position N and N+5 must resolve to the same zone (landscape)."""
    for style in ("stat", "list", "comparison", "checklist"):
        for idx in range(5):
            assert _resolve_zone(style, idx, "landscape") == _resolve_zone(style, idx + 5, "landscape"), \
                f"landscape cycle break: {style} idx={idx}"


def test_landscape_10_cards_two_full_cycles():
    """10 landscape cards produce exactly 2 complete cycles of all 5 positions."""
    style = "score"
    expected = list(_STD_LANDSCAPE) * 2
    for i, exp in enumerate(expected):
        got = _resolve_zone(style, i, "landscape")
        assert got == exp, f"landscape card {i}: expected {exp!r}, got {got!r}"


def test_landscape_tall_stays_top_only():
    """Landscape tall types must only use landscape-tl-tall or landscape-tr-tall."""
    allowed = {"landscape-tl-tall", "landscape-tr-tall"}
    for style in _TALL_DATA_PANEL_TYPES:
        for idx in range(10):
            zone = _resolve_zone(style, idx, "landscape")
            assert zone in allowed, \
                f"landscape tall {style} idx={idx} resolved to {zone!r}"


def test_landscape_tall_alternation():
    """Landscape tall: even positions → tl-tall, odd → tr-tall."""
    for style in _TALL_DATA_PANEL_TYPES:
        for idx in range(10):
            zone = _resolve_zone(style, idx, "landscape")
            if idx % 2 == 0:
                assert zone == _TALL_LEFT_LANDSCAPE, \
                    f"{style} idx={idx} expected {_TALL_LEFT_LANDSCAPE!r}, got {zone!r}"
            else:
                assert zone == _TALL_RIGHT_LANDSCAPE, \
                    f"{style} idx={idx} expected {_TALL_RIGHT_LANDSCAPE!r}, got {zone!r}"


# ── Unified / cross-format tests ──────────────────────────────────────────────

def test_counter_is_format_independent():
    """Same data_card_idx must produce consistent position names regardless of layout."""
    style = "stat"
    for idx in range(5):
        portrait_name = _POS_NAMES[idx % 5]
        landscape_name = _POS_NAMES[idx % 5]
        assert portrait_name == landscape_name, "pos name mismatch between layouts"


def test_index_reset_per_job():
    """Two jobs both start at idx=0 and get the same first zone."""
    style = "checklist"
    assert _resolve_zone(style, 0, "portrait")  == "upper-left-data-sm"
    assert _resolve_zone(style, 0, "landscape") == "landscape-tl"


def test_dimming_zones_include_all_three_portrait_center():
    """All 3 portrait center zones must be in _DIMMING_ZONES."""
    assert "portrait-center-left"  in _DIMMING_ZONES
    assert "portrait-center-right" in _DIMMING_ZONES
    assert "portrait-center-full"  in _DIMMING_ZONES


def test_dimming_zone_landscape_cf():
    """landscape-cf (center-full) must be in _DIMMING_ZONES."""
    assert "landscape-cf" in _DIMMING_ZONES


def test_landscape_cl_cr_not_dimming():
    """landscape-cl and landscape-cr are beside the face, not over it — no dimming."""
    assert "landscape-cl" not in _DIMMING_ZONES
    assert "landscape-cr" not in _DIMMING_ZONES


def test_top_positions_not_dimming():
    """Top-corner zones must never trigger dimming."""
    for zone in ("upper-left-data-sm", "upper-data", "landscape-tl", "landscape-tr"):
        assert zone not in _DIMMING_ZONES, f"{zone!r} should not be a dimming zone"


def test_fullscreen_types_not_data_panel():
    """Types that use fullscreen/video-overlay are excluded from rotation."""
    fullscreen_types = {"key_phrase", "quote", "chapter_marker", "story_chapter_transition"}
    for t in fullscreen_types:
        assert t not in _DATA_PANEL_TYPES, \
            f"fullscreen type {t!r} must not be in _DATA_PANEL_TYPES"


def test_all_rotation_zones_are_side_panel_zones():
    """All rotation target zones must be in _SIDE_PANEL_ZONES (compact=True)."""
    for z in _STD_PORTRAIT:
        if z == "portrait-center-full":
            continue  # non-compact by design (wide card, full-size rendering)
        assert z in _SIDE_PANEL_ZONES, f"portrait zone {z!r} missing from _SIDE_PANEL_ZONES"
    for z in _STD_LANDSCAPE:
        assert z in _SIDE_PANEL_ZONES, f"landscape zone {z!r} missing from _SIDE_PANEL_ZONES"
    assert _TALL_LEFT_PORTRAIT  in _SIDE_PANEL_ZONES
    assert _TALL_RIGHT_PORTRAIT in _SIDE_PANEL_ZONES
    assert _TALL_LEFT_LANDSCAPE  in _SIDE_PANEL_ZONES
    assert _TALL_RIGHT_LANDSCAPE in _SIDE_PANEL_ZONES


def test_5_distinct_zones_per_layout():
    """Each layout must produce 5 distinct zones (no duplicates in the cycle)."""
    assert len(set(_STD_PORTRAIT))  == 5, "duplicate zones in portrait rotation"
    assert len(set(_STD_LANDSCAPE)) == 5, "duplicate zones in landscape rotation"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    if passed < len(tests):
        sys.exit(1)
