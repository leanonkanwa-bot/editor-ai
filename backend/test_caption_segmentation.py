"""
Verify _segment_captions grouping cap.

Short format: max 4 words per card.
Long format: default (7 words), unchanged behaviour.
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from dataclasses import dataclass


@dataclass
class _W:
    text: str
    start: float
    end: float


def _make_words(n: int, gap: float = 0.5) -> list[_W]:
    """Generate n words spaced gap seconds apart."""
    words = []
    t = 0.0
    for i in range(n):
        words.append(_W(text=f"w{i+1}", start=round(t, 3), end=round(t + 0.4, 3)))
        t += gap
    return words


def _make_segments(words: list[_W]) -> list[dict]:
    """One Whisper segment covering all words (no sentence boundaries)."""
    return [{
        "words": [{"text": w.text, "start": w.start, "end": w.end} for w in words]
    }]


def _make_segments_with_boundaries(words: list[_W], boundary_indices: list[int]) -> list[dict]:
    """Multiple Whisper segments; boundary_indices are the first word of each new segment."""
    segs = []
    current = []
    for i, w in enumerate(words):
        if i in boundary_indices and current:
            segs.append({"words": current})
            current = []
        current.append({"text": w.text, "start": w.start, "end": w.end})
    if current:
        segs.append({"words": current})
    return segs


# Import the function under test
from app.engine.storyboard import _segment_captions, _MAX_WORDS

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        print(f"  PASS  {name}")
        PASS += 1
    else:
        print(f"  FAIL  {name}{': ' + detail if detail else ''}")
        FAIL += 1


# ── Test 1: Short format — 20 words, no sentence boundaries ─────────────────
print("\n── Test 1: short format, 20 words, no boundaries ──")
words = _make_words(20)
segments = _make_segments(words)
cards = _segment_captions(
    remapped_words=words,
    transcript_segments=segments,
    timing_map=None,
    emphasis_words=[],
    word_categories={},
    max_words=4,
)
for c in cards:
    wc = len(c["words"])
    check(
        f"card {c['id']} has ≤4 words ({wc})",
        wc <= 4,
        f"got {wc} words: {[w['text'] for w in c['words']]}",
    )
check(
    f"all 20 words covered (got {sum(len(c['words']) for c in cards)})",
    sum(len(c["words"]) for c in cards) == 20,
)

# ── Test 2: Short format — sentence boundaries split mid-run ────────────────
print("\n── Test 2: short format, 12 words, sentence boundaries at 0, 5, 9 ──")
words = _make_words(12)
segments = _make_segments_with_boundaries(words, boundary_indices=[0, 5, 9])
cards = _segment_captions(
    remapped_words=words,
    transcript_segments=segments,
    timing_map=None,
    emphasis_words=[],
    word_categories={},
    max_words=4,
)
for c in cards:
    wc = len(c["words"])
    check(f"card {c['id']} ≤4 words ({wc})", wc <= 4)
check(
    f"all 12 words covered (got {sum(len(c['words']) for c in cards)})",
    sum(len(c["words"]) for c in cards) == 12,
)

# ── Test 3: Long format (default, max 7) — 20 words, no boundaries ──────────
print("\n── Test 3: long format (max_words=7 default), 20 words, no boundaries ──")
words = _make_words(20)
segments = _make_segments(words)
cards7 = _segment_captions(
    remapped_words=words,
    transcript_segments=segments,
    timing_map=None,
    emphasis_words=[],
    word_categories={},
    max_words=7,   # explicit, same as _MAX_WORDS default
)
for c in cards7:
    wc = len(c["words"])
    check(f"card {c['id']} ≤7 words ({wc})", wc <= 7)
check(
    f"all 20 words covered (got {sum(len(c['words']) for c in cards7)})",
    sum(len(c["words"]) for c in cards7) == 20,
)

# ── Test 4: Long format — no card with more than 7 words ───────────────────
print("\n── Test 4: orphan merge doesn't exceed 4 for short format ──")
# 3 words → seg boundary → 1 word (orphan) → 3 words
words = _make_words(7)
segs = _make_segments_with_boundaries(words, boundary_indices=[0, 3, 4])
cards = _segment_captions(
    remapped_words=words,
    transcript_segments=segs,
    timing_map=None,
    emphasis_words=[],
    word_categories={},
    max_words=4,
)
for c in cards:
    wc = len(c["words"])
    check(f"card {c['id']} ≤4 words ({wc})", wc <= 4)
check(
    f"all 7 words covered (got {sum(len(c['words']) for c in cards)})",
    sum(len(c["words"]) for c in cards) == 7,
)

# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"Result: {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
