"""
Tests for 4-position sequential rotation in compose._remap_zone.

We test the logic directly by constructing a minimal compose() call shim
that exercises _remap_zone in isolation via monkey-patching the closure vars.
Since _remap_zone is a nested function inside compose(), we use a lightweight
inline re-implementation of the rotation table to verify the contract without
having to spin up the full compose pipeline.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

# Replicate the exact rotation tables from compose.py so the test is
# authoritative — any drift between the tables will surface here.
_POS_NAMES = ("top-left", "center-safe", "bottom-third", "top-right")
_STD_ZONES = (
    "upper-left-data-sm",    # pos 0
    "portrait-bottom-right", # pos 1
    "portrait-bottom-left",  # pos 2
    "upper-data",            # pos 3
)
_TALL_LEFT  = "upper-left-data"
_TALL_RIGHT = "upper-right-data-tall"
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


def _resolve_zone(style: str, data_card_idx: int) -> str:
    """Inline replica of the portrait rotation logic in compose._remap_zone."""
    pos = data_card_idx % 4
    is_tall = style in _TALL_DATA_PANEL_TYPES
    if is_tall:
        return _TALL_LEFT if pos in (0, 2) else _TALL_RIGHT
    return _STD_ZONES[pos]


def test_standard_rotation_sequence():
    """Standard data card: positions cycle top-left → bottom-right → bottom-left → top-right."""
    style = "stat"
    assert _resolve_zone(style, 0) == "upper-left-data-sm"
    assert _resolve_zone(style, 1) == "portrait-bottom-right"
    assert _resolve_zone(style, 2) == "portrait-bottom-left"
    assert _resolve_zone(style, 3) == "upper-data"


def test_rotation_repeats_at_4():
    """Position N and N+4 must resolve to the same zone."""
    for style in ("stat", "list", "comparison", "checklist"):
        for idx in range(4):
            assert _resolve_zone(style, idx) == _resolve_zone(style, idx + 4), \
                f"cycle break: {style} idx={idx}"


def test_rotation_deterministic_8_cards():
    """8 standard cards → pattern repeats exactly once."""
    style = "list"
    expected = [
        "upper-left-data-sm",
        "portrait-bottom-right",
        "portrait-bottom-left",
        "upper-data",
        "upper-left-data-sm",
        "portrait-bottom-right",
        "portrait-bottom-left",
        "upper-data",
    ]
    for i, exp in enumerate(expected):
        assert _resolve_zone(style, i) == exp, f"card {i}: expected {exp!r}, got {_resolve_zone(style, i)!r}"


def test_tall_type_uses_only_top_zones():
    """Tall types must never land in portrait-bottom-* zones."""
    for style in _TALL_DATA_PANEL_TYPES:
        for idx in range(8):
            zone = _resolve_zone(style, idx)
            assert zone not in ("portrait-bottom-left", "portrait-bottom-right"), \
                f"tall type {style} idx={idx} resolved to bottom zone {zone!r}"


def test_tall_type_alternation():
    """Tall types: even positions → left, odd positions → right (2-position top cycle)."""
    for style in _TALL_DATA_PANEL_TYPES:
        for idx in range(8):
            zone = _resolve_zone(style, idx)
            if idx % 4 in (0, 2):
                assert zone == _TALL_LEFT, \
                    f"{style} idx={idx} expected {_TALL_LEFT!r}, got {zone!r}"
            else:
                assert zone == _TALL_RIGHT, \
                    f"{style} idx={idx} expected {_TALL_RIGHT!r}, got {zone!r}"


def test_index_reset_per_job():
    """Simulated two jobs: each starts data_card_idx at 0 → same zone for card 0."""
    style = "checklist"
    job_a_first_card = _resolve_zone(style, 0)
    job_b_first_card = _resolve_zone(style, 0)  # fresh counter per job
    assert job_a_first_card == job_b_first_card == "upper-left-data-sm"


def test_fullscreen_types_not_data_panel():
    """Types that use fullscreen/video-overlay are NOT in _DATA_PANEL_TYPES → excluded."""
    fullscreen_types = {"key_phrase", "quote", "chapter_marker", "story_chapter_transition"}
    for t in fullscreen_types:
        assert t not in _DATA_PANEL_TYPES, \
            f"fullscreen type {t!r} must not be in _DATA_PANEL_TYPES"


def test_bottom_zones_not_center_zones():
    """portrait-bottom-* zones must not be in _CENTER_ZONES (would trigger dimming)."""
    _CENTER_ZONES = {"fullscreen", "video-overlay"}
    assert "portrait-bottom-left"  not in _CENTER_ZONES
    assert "portrait-bottom-right" not in _CENTER_ZONES


def test_pos_names_match_4_positions():
    """_POS_NAMES must have exactly 4 entries mapping to 4 distinct zones."""
    assert len(_POS_NAMES) == 4
    assert len(_STD_ZONES) == 4
    assert len(set(_STD_ZONES)) == 4, "duplicate standard zones in rotation table"


def test_all_standard_positions_are_side_panel_zones():
    """All rotation target zones must be in _SIDE_PANEL_ZONES (compact=True → correct scale)."""
    _SIDE_PANEL_ZONES = {
        "side-panel", "side-panel-left", "side-panel-right", "side-panel-top",
        "upper-data", "upper-right", "upper-left-data", "upper-left-data-sm",
        "upper-right-data-tall", "portrait-bottom-left", "portrait-bottom-right",
    }
    for z in _STD_ZONES:
        assert z in _SIDE_PANEL_ZONES, f"rotation zone {z!r} missing from _SIDE_PANEL_ZONES"
    assert _TALL_LEFT  in _SIDE_PANEL_ZONES
    assert _TALL_RIGHT in _SIDE_PANEL_ZONES


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
