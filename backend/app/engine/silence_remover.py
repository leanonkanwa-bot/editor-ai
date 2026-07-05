"""Rhythm-Aware Silence Remover — Feature 2.

Processes word timestamps and segments to identify pauses and filler words
that should be removed without disrupting natural speech rhythm.

Rules:
  - Remove pauses > 0.5s mid-sentence.
  - Remove pauses > 0.3s after filler words (um, uh, like, basically, etc.).
  - Keep authority pauses before periods/full-stops.
  - Keep emphasis pauses before PRINCIPLE/PAYOFF keywords.
  - Keep question beats (pause before rhetorical questions).
  - Keep last 0.3s of each segment.
  - Remove filler words when they are standalone (not used as comparisons).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# V1 safe list — very low false-positive risk in natural French speech.
_FILLER_WORDS_FR_V1 = frozenset({
    "euh", "euhh", "heu",      # vocalic hesitations
    "bah", "ba",               # discourse fillers
    "hein",                    # confirmation-seeking
    "bref",                    # empty summary
    "du coup",                 # empty connector
    "tu vois", "t'vois",      # confirmation-seeking
})
# V2 candidates disabled — too much legitimate use in natural French:
# "donc", "voilà", "genre", "en fait", "quoi", "bon", "ouais bon"

_FILLER_WORDS_EN = frozenset({
    "um", "uh", "uhh", "umm", "hmm", "hm", "er", "erm",
    "like", "basically", "literally", "actually", "honestly",
    "you know", "i mean", "right", "okay", "so", "well",
    "kind of", "sort of", "you know what i mean",
    "seriously", "obviously",
})

_FILLER_WORDS = _FILLER_WORDS_FR_V1 | _FILLER_WORDS_EN

_FILLERS_RE = re.compile(
    r"^(" + "|".join(re.escape(f) for f in sorted(_FILLER_WORDS, key=len, reverse=True)) + r")$",
    re.IGNORECASE,
)

# Pause-isolation guard: a filler is only physically cut when it has a pause
# on at least one side. Fillers embedded in normal speech flow are left in place —
# a missed filler sounds natural; a spurious mid-word cut sounds broken.
_FILLER_PAUSE_GUARD_PRE  = 0.20   # seconds gap required before the filler
_FILLER_PAUSE_GUARD_POST = 0.15   # seconds gap required after the filler
_FILLER_CUT_PAD          = 0.040  # seconds padding for Whisper ±50ms timing imprecision

_PRINCIPLE_PAYOFF = re.compile(
    r"\b(the truth is|the key is|the secret is|the reason is|the point is|"
    r"what that means|what this means|here is|this is why|the answer is|"
    r"the reality is|remember this|never forget|most people|nobody talks|"
    r"this changed|this is the|the biggest|the real|the only)\b",
    re.IGNORECASE,
)

_PAUSE_AFTER_FILLER    = 0.3    # remove pauses after filler words longer than this (s)

# Intelligent pause shortening — calibrate after first production renders.
_PAUSE_TOUCH_THRESHOLD = 0.70   # pauses shorter than this are never touched (s)
_PAUSE_LONG_THRESHOLD  = 2.00   # above this: "long" category regardless of position (s)
_PAUSE_MID_SHORT_KEEP  = 0.45   # mid-sentence, 0.70–2.00 s: shorten to this (s)
_PAUSE_EOL_LONG_KEEP   = 0.80   # end-of-sentence, >2.00 s: shorten to this (s)
_PAUSE_MID_LONG_KEEP   = 0.40   # mid-sentence, >2.00 s: shorten to this (s)


@dataclass
class DropSegment:
    """A time range to drop from the audio/video timeline."""
    start: float
    end: float
    reason: str


class RhythmAwareSilenceRemover:
    """Analyses word timestamps and returns segments to drop."""

    def process(
        self,
        word_timestamps: list[dict[str, Any]],
        segments: list[dict[str, Any]],
    ) -> tuple[list[DropSegment], list[DropSegment]]:
        """
        Returns a list of DropSegment objects representing time ranges to cut.

        Args:
            word_timestamps: flat list of {text, start, end} word dicts
            segments: list of {start, end, text, words} segment dicts

        Returns:
            (all_drops, filler_drops) — all_drops is the merged list used for
            timestamp shifting; filler_drops is the unmerged filler-word subset
            passed to pretrim for physical cutting.
        """
        drops: list[DropSegment] = []

        # Build a flat list of (text, start, end) from segments.
        words: list[tuple[str, float, float]] = []
        for seg in segments:
            for w in seg.get("words", []):
                try:
                    words.append((
                        str(w.get("text", "")).strip(),
                        float(w["start"]),
                        float(w["end"]),
                    ))
                except (KeyError, TypeError, ValueError):
                    continue

        if not words:
            # Fall back to word_timestamps list.
            for w in word_timestamps:
                try:
                    words.append((
                        str(w.get("text", "")).strip(),
                        float(w["start"]),
                        float(w["end"]),
                    ))
                except (KeyError, TypeError, ValueError):
                    continue

        if len(words) < 2:
            return drops, []

        # Build segment end times for boundary detection.
        segment_ends: set[float] = set()
        for seg in segments:
            try:
                segment_ends.add(float(seg["end"]))
            except (KeyError, TypeError, ValueError):
                pass

        for i in range(len(words) - 1):
            cur_text,  cur_start,  cur_end  = words[i]
            next_text, next_start, next_end = words[i + 1]

            gap_start = cur_end
            gap_end   = next_start
            gap_dur   = gap_end - gap_start

            if gap_dur <= 0:
                continue

            # Remove: pause after a filler word (checked before touch threshold).
            if _FILLERS_RE.match(cur_text) and gap_dur > _PAUSE_AFTER_FILLER:
                drops.append(DropSegment(gap_start, gap_end, f"pause_after_filler:{cur_text}"))
                continue

            # Pauses below the touch threshold are never modified.
            if gap_dur < _PAUSE_TOUCH_THRESHOLD:
                continue

            # Never touch the final gap before the last word (tail of recording).
            if i == len(words) - 2:
                continue

            # Segment-boundary gaps preserve cross-cut rhythm.
            if any(abs(cur_end - se) < 0.1 for se in segment_ends):
                continue

            # Preserve rhetorical beats before PRINCIPLE/PAYOFF keywords.
            if _PRINCIPLE_PAYOFF.search(next_text):
                continue

            # End-of-sentence: punctuation attached to the word before the gap.
            is_sentence_end = cur_text.rstrip().endswith((".", "?", "!"))

            if is_sentence_end:
                if gap_dur <= _PAUSE_LONG_THRESHOLD:
                    continue                              # 0.70–2.00s EOS: untouched
                keep_s = _PAUSE_EOL_LONG_KEEP             # >2.00s EOS: shorten to 0.80s
            else:
                if gap_dur <= _PAUSE_LONG_THRESHOLD:
                    keep_s = _PAUSE_MID_SHORT_KEEP        # 0.70–2.00s mid: shorten to 0.45s
                else:
                    keep_s = _PAUSE_MID_LONG_KEEP         # >2.00s mid: shorten to 0.40s

            cut_start = gap_start + keep_s
            if cut_start < gap_end - 0.05:
                drops.append(DropSegment(
                    cut_start, gap_end,
                    f"pause_shortened:{gap_dur:.2f}s→{keep_s:.2f}s",
                ))

        # Remove standalone filler words (when confidence is implied by position).
        filler_drops = self._find_filler_word_drops(words, segment_ends)
        drops.extend(filler_drops)

        # Sort and merge overlapping drops.
        drops.sort(key=lambda d: d.start)
        return _merge_drops(drops), filler_drops

    def _find_filler_word_drops(
        self,
        words: list[tuple[str, float, float]],
        segment_ends: set[float],
    ) -> list[DropSegment]:
        """Drop isolated filler words that serve no semantic purpose.

        A filler is only cut when isolated by a pause on at least one side
        (pre_gap > _FILLER_PAUSE_GUARD_PRE OR post_gap > _FILLER_PAUSE_GUARD_POST).
        Fillers embedded in normal speech flow are left intact.
        """
        drops: list[DropSegment] = []
        for i, (text, start, end) in enumerate(words):
            if not _FILLERS_RE.match(text):
                continue

            if i == 0 or i == len(words) - 1:
                continue

            # Don't drop if it's at a segment boundary.
            if any(abs(end - se) < 0.1 for se in segment_ends):
                continue

            # Don't drop "like" if used as comparison.
            if text.lower() == "like":
                prev_text = words[i - 1][0] if i > 0 else ""
                if _is_comparison_like(prev_text):
                    continue

            # Don't drop "so" if it's a meaningful sentence-start connector.
            if text.lower() == "so":
                prev_end = words[i - 1][2]
                if start - prev_end > 0.3:
                    continue

            # Pause-isolation guard: only cut if there's a pause on at least one side.
            pre_gap  = start - words[i - 1][2]
            post_gap = words[i + 1][1] - end
            if not (pre_gap > _FILLER_PAUSE_GUARD_PRE or post_gap > _FILLER_PAUSE_GUARD_POST):
                continue

            # Apply padding to absorb Whisper timing imprecision; clamp to adjacent words.
            cut_start = max(start - _FILLER_CUT_PAD, words[i - 1][2])
            cut_end   = min(end   + _FILLER_CUT_PAD, words[i + 1][1])

            print(
                f"[FILLER] cut {text!r} at {start:.2f}s "
                f"(pre_gap {pre_gap:.2f}s, post_gap {post_gap:.2f}s)",
                flush=True,
            )
            drops.append(DropSegment(cut_start, cut_end, f"filler_word:{text}"))

        return drops


def _is_comparison_like(preceding_word: str) -> bool:
    """Heuristic: 'like' is a comparison if preceded by a verb or 'just'."""
    comparison_triggers = {"just", "look", "works", "acts", "feels", "sounds", "seems"}
    return preceding_word.lower().rstrip(".,!?") in comparison_triggers


def _merge_drops(drops: list[DropSegment]) -> list[DropSegment]:
    """Merge overlapping or adjacent drop segments."""
    if not drops:
        return drops
    merged: list[DropSegment] = [drops[0]]
    for d in drops[1:]:
        last = merged[-1]
        if d.start <= last.end + 0.05:
            # Extend the last segment.
            merged[-1] = DropSegment(last.start, max(last.end, d.end), last.reason)
        else:
            merged.append(d)
    return merged


def apply_drops_to_transcript(
    transcript: dict[str, Any],
    drops: list[DropSegment],
) -> dict[str, Any]:
    """
    Returns a copy of the transcript with word timestamps adjusted to
    remove the dropped time ranges. Does NOT modify the original dict.

    The output timestamps reflect what the audio would look like if the
    drop ranges were spliced out — used to keep the caption/plan timings
    consistent with silence-removed audio.
    """
    if not drops:
        return transcript

    import copy
    t = copy.deepcopy(transcript)

    def _adjust_time(original_t: float) -> float:
        adjusted = original_t
        shift = 0.0
        for d in drops:
            if original_t <= d.start:
                break
            if original_t >= d.end:
                shift += d.end - d.start
            else:
                # Time falls inside a drop — clamp to drop start.
                shift += original_t - d.start
                break
        return original_t - shift

    for seg in t.get("segments", []):
        seg["start"] = _adjust_time(float(seg.get("start", 0)))
        seg["end"]   = _adjust_time(float(seg.get("end", 0)))
        for w in seg.get("words", []):
            w["start"] = _adjust_time(float(w.get("start", 0)))
            w["end"]   = _adjust_time(float(w.get("end", 0)))

    return t
