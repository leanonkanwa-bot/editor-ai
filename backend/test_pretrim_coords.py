#!/usr/bin/env python3
"""
Minimal smoke-test for pretrim coordinate-space fix.
Exercises _c2s() and the src_word_timings tuple format used by
_snap_to_word_boundary() — no FFmpeg, no real video needed.

Run:
    python backend/test_pretrim_coords.py
"""
import sys
from pathlib import Path

# Make app importable without installing the package
sys.path.insert(0, str(Path(__file__).parent))


def test_c2s_basic():
    from app.engine.pretrim import _c2s
    from app.engine.silence_remover import DropSegment

    # One 2s drop at compressed position 5–7 → source offset = +2 for anything after
    drops = [DropSegment(start=5.0, end=7.0, reason="test")]

    assert _c2s(3.0, drops) == 3.0,   "before drop: no offset"
    assert _c2s(5.0, drops) == 5.0,   "at drop start: no offset yet"
    assert abs(_c2s(6.0, drops) - 8.0) < 1e-9, "inside drop region: offset = 2"
    assert abs(_c2s(10.0, drops) - 12.0) < 1e-9, "after drop: offset = 2"
    print("PASS  _c2s basic")


def test_c2s_two_drops():
    from app.engine.pretrim import _c2s
    from app.engine.silence_remover import DropSegment

    # Drop A: compressed 2–3 (1s), Drop B: compressed 6–8 (2s)
    drops = sorted([
        DropSegment(start=2.0, end=3.0, reason="a"),
        DropSegment(start=6.0, end=8.0, reason="b"),
    ], key=lambda d: d.start)

    assert _c2s(1.0, drops) == 1.0,  "before A"
    assert abs(_c2s(4.0, drops) - 5.0) < 1e-9, "after A, before B: +1"
    assert abs(_c2s(9.0, drops) - 12.0) < 1e-9, "after both drops: +3"
    print("PASS  _c2s two drops")


def test_snap_uses_tuples():
    """src_word_timings must be list[tuple[float,float]] for _snap_to_word_boundary."""
    from app.engine.render import _snap_to_word_boundary

    # 3 words with clean gaps between them
    words: list[tuple[float, float]] = [
        (1.0, 1.8),   # word 1
        (2.2, 3.0),   # word 2  — gap before: 0.4s ≥ 0.15 ✓
        (3.5, 4.2),   # word 3  — gap before: 0.5s ≥ 0.15 ✓
    ]

    # snap start: ask for a cut at 2.0 → should find word 2 start (2.2) with gap=0.4s
    result = _snap_to_word_boundary(2.0, words, edge="start")
    assert abs(result - 2.2) < 0.01, f"expected 2.2, got {result}"

    # snap end: ask for a cut at 3.2 → should find word 2 end (3.0) with gap_after=0.5s
    result = _snap_to_word_boundary(3.2, words, edge="end")
    assert abs(result - 3.0) < 0.01, f"expected 3.0, got {result}"

    print("PASS  _snap_to_word_boundary with tuples")


def test_source_words_to_tuples():
    """Verify the pretrim tuple-building logic doesn't raise on real dict input."""
    source_words = [
        {"start": 1.0, "end": 1.8, "text": "hello"},
        {"start": 2.2, "end": 3.0, "text": "world"},
        {"start": "",  "end": 4.2, "text": ""},       # blank text → filtered out
    ]

    src_word_timings = sorted(
        [
            (float(w.get("start", 0)), float(w.get("end", 0)))
            for w in source_words
            if w.get("text", "").strip()
        ],
        key=lambda t: t[0],
    )

    assert src_word_timings == [(1.0, 1.8), (2.2, 3.0)], f"got {src_word_timings}"

    # Must work with _snap_to_word_boundary without TypeError
    from app.engine.render import _snap_to_word_boundary
    r = _snap_to_word_boundary(2.0, src_word_timings, edge="start")
    assert isinstance(r, float)
    print("PASS  source_words -> tuples -> _snap_to_word_boundary (no TypeError)")


if __name__ == "__main__":
    test_c2s_basic()
    test_c2s_two_drops()
    test_snap_uses_tuples()
    test_source_words_to_tuples()
    print("\nAll tests passed.")
