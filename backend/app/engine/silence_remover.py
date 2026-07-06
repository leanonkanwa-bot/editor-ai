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


def _is_filler(word: str) -> bool:
    """Return True if *word* is a filler, ignoring attached punctuation."""
    return bool(_FILLERS_RE.match(word.strip(".,!?;:'\"()")))

# Pause-isolation guard: a filler is only physically cut when it has a pause
# on at least one side. Fillers embedded in normal speech flow are left in place —
# a missed filler sounds natural; a spurious mid-word cut sounds broken.
_FILLER_PAUSE_GUARD_PRE  = 0.20   # seconds gap required before the filler
_FILLER_PAUSE_GUARD_POST = 0.15   # seconds gap required after the filler
_FILLER_CUT_PAD          = 0.040  # seconds padding for Whisper ±50ms timing imprecision
_MIN_WORD_DUR_S          = 0.030  # words shorter than this are Whisper artifacts, not speech

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

        # Drop Whisper zero-duration artefacts before any detection.
        # These arise when Whisper transcribes non-speech noise or reads
        # instructions aloud (e.g. "pause courte") — the token gets a
        # timestamp but no actual duration. They create phantom bigrams that
        # fool the false-start and stutter detectors.
        _n_before = len(words)
        words = [(t, s, e) for t, s, e in words if e - s >= _MIN_WORD_DUR_S]
        _n_artifacts = _n_before - len(words)
        if _n_artifacts:
            print(
                f"[WHISPER-ARTIFACT] ignored {_n_artifacts} zero-duration words "
                f"(duration < {_MIN_WORD_DUR_S}s)",
                flush=True,
            )

        print(
            f"[SILENCE] process() vb590f87 — {len(words)} words "
            f"({_n_before} raw, {_n_artifacts} artifacts removed), "
            f"{len(segments)} segments",
            flush=True,
        )

        if len(words) < 2:
            print("[SILENCE] process() aborted: fewer than 2 words", flush=True)
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
            if _is_filler(cur_text) and gap_dur > _PAUSE_AFTER_FILLER:
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

        # Pause drops are already in `drops` (added by the loop above).
        # They must also go into physical_drops so _subtract_fillers() in
        # pretrim excises them from the source video.  Without this, the
        # virtual path (apply_drops_to_transcript) compresses transcript
        # timestamps making the gap invisible, so _smart_pause_cuts() always
        # sees 0-gap words and logs "none" even when pauses exist in the video.
        pause_drops = list(drops)

        # Remove standalone filler words.
        filler_drops = self._find_filler_word_drops(words, segment_ends)
        # Detect word-repetition stutters (physical cuts like fillers).
        stutter_drops = _find_stutter_drops(words)
        # Detect and cut false-start phrases (abandoned phrase + restart).
        false_start_drops = _find_false_start_drops(words)

        # Physical drops = all categories that get excised from the source video.
        physical_drops = pause_drops + filler_drops + stutter_drops + false_start_drops

        # Extend master drops with non-pause categories (pause_drops already there).
        drops.extend(filler_drops + stutter_drops + false_start_drops)
        drops.sort(key=lambda d: d.start)

        _log_stutter_near_misses(words)

        print(
            f"[SILENCE] detected: {len(pause_drops)} pause-excess, "
            f"{len(filler_drops)} fillers, {len(stutter_drops)} stutters, "
            f"{len(false_start_drops)} false-starts "
            f"({len(drops)} raw drops total, {len(physical_drops)} physical)",
            flush=True,
        )

        return _merge_drops(drops), physical_drops

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
            if not _is_filler(text):
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
            if text.lower() == "so" and i > 0:
                prev_end = words[i - 1][2]
                if start - prev_end > 0.3:
                    continue

            # Gap before the filler: beginning of recording counts as infinite pre-gap
            # (not just word.start — a filler at t=0.05s would give pre_gap=0.05 < guard).
            pre_gap = start - words[i - 1][2] if i > 0 else float("inf")

            # Gap after the filler: end of recording counts as infinite post-gap.
            post_gap = words[i + 1][1] - end if i < len(words) - 1 else float("inf")

            # Pause-isolation guard: only cut if there's a pause on at least one side.
            if not (pre_gap > _FILLER_PAUSE_GUARD_PRE or post_gap > _FILLER_PAUSE_GUARD_POST):
                continue

            # Clamp cut boundaries to adjacent word edges (or recording start/end).
            cut_start = max(start - _FILLER_CUT_PAD, words[i - 1][2] if i > 0 else 0.0)
            cut_end   = min(end   + _FILLER_CUT_PAD, words[i + 1][1] if i < len(words) - 1 else end + _FILLER_CUT_PAD)

            print(
                f"[FILLER] cut {text!r} at {start:.2f}s "
                f"(pre_gap {pre_gap:.2f}s, post_gap {post_gap:.2f}s)",
                flush=True,
            )
            drops.append(DropSegment(cut_start, cut_end, f"filler_word:{text}"))

        return drops


def _normalize_word(w: str) -> str:
    """Normalize a word token for stutter comparison.

    Converts typographic apostrophes to straight (Whisper uses U+2019 in FR),
    strips edge punctuation, lowercases. Language-agnostic by construction.
    """
    w = re.sub(r"[‘’ʼ]", "'", w)
    return w.strip(".,!?;:'\"").lower()


def _find_stutter_drops(
    words: list[tuple[str, float, float]],
) -> list[DropSegment]:
    """Detect adjacent repeated-word stutters and return them as physical drop segments.

    A run of exactly 2 identical normalized words is a stutter — cut the gap
    plus the second occurrence, keeping the first. A run of 3+ is treated as
    intentional rhetorical repetition and logged but not cut.
    """
    drops: list[DropSegment] = []
    i = 0
    while i < len(words) - 1:
        cur_text, cur_start, cur_end = words[i]
        norm = _normalize_word(cur_text)
        if len(norm) < 2:   # skip single-character tokens ("à", "a", etc.)
            i += 1
            continue

        # Count consecutive identical normalized words starting at i.
        # Punctuation-only tokens (e.g. "," as a standalone Whisper token) have
        # norm == "" and are treated as transparent separators so they don't
        # prematurely break a run of 3 like "jamais , jamais , jamais".
        run_end = i + 1
        while run_end < len(words):
            rn = _normalize_word(words[run_end][0])
            if rn == norm:
                run_end += 1
            elif not rn:
                run_end += 1
            else:
                break
        run_len = sum(1 for k in range(i, run_end) if _normalize_word(words[k][0]) == norm)

        if run_len < 2:
            i += 1
            continue

        if run_len >= 3:
            print(
                f"[STUTTER] rhetorical? {run_len}x '{cur_text}' at {cur_start:.2f}s - not cut",
                flush=True,
            )
            i = run_end
            continue

        # run_len == 2: stutter — keep first occurrence, drop gap + second.
        _, nxt_start, nxt_end = words[i + 1]
        prev_end   = words[i - 1][2] if i > 0 else 0.0
        next_start = words[run_end][1] if run_end < len(words) else nxt_end

        cut_start = max(cur_end - _FILLER_CUT_PAD, prev_end)
        cut_end   = min(nxt_end + _FILLER_CUT_PAD, next_start)

        print(
            f"[STUTTER] cut repeat '{cur_text}' at {nxt_start:.2f}s "
            f"(gap={nxt_start - cur_end:.2f}s)",
            flush=True,
        )
        drops.append(DropSegment(cut_start, cut_end, f"stutter:{cur_text}"))
        i = run_end

    return drops


# Near-miss scan window: look this many word positions ahead.
_STUTTER_NEAR_MISS_WINDOW = 8


def _log_stutter_near_misses(words: list[tuple[str, float, float]]) -> None:
    """Log word repetitions that were NOT caught as stutters, with rejection reason.

    Scans all words for same-normalized-form pairs within _STUTTER_NEAR_MISS_WINDOW
    positions. A pair that was already cut by _find_stutter_drops (adjacent with
    only transparent separators between them) is skipped. All others get a
    [STUTTER] near-miss line explaining why they were not cut.
    """
    norms = [_normalize_word(w[0]) for w in words]

    for i in range(len(words)):
        if len(norms[i]) < 2:
            continue
        for j in range(i + 1, min(i + _STUTTER_NEAR_MISS_WINDOW + 1, len(words))):
            if norms[j] != norms[i]:
                continue

            # Same normalized word at i and j.
            bridge_norms = norms[i + 1: j]
            non_transparent = [norms[k] for k in range(i + 1, j) if norms[k]]

            if not non_transparent:
                # Adjacent (possibly with transparent separators) — should have
                # been caught by _find_stutter_drops; skip.
                pass
            else:
                gap = words[j][1] - words[i][2]
                bridge_tokens = [words[k][0] for k in range(i + 1, j)]
                print(
                    f"[STUTTER] near-miss: '{words[i][0]}' x2"
                    f" at {words[i][1]:.2f}s / {words[j][1]:.2f}s"
                    f" gap={gap:.2f}s"
                    f" bridge={bridge_tokens!r}"
                    f" reason=not_adjacent",
                    flush=True,
                )
            break  # one report per i position


# French and English function words: determiners, prepositions, pronouns,
# conjunctions, auxiliaries. Groups of only these words (+ symbols) are never
# the start of a meaningful false-start — they carry no propositional content.
_FALSE_START_FUNCTION_WORDS = frozenset({
    # French articles / determiners
    "le", "la", "les", "l", "un", "une", "des", "du", "au", "aux",
    "ce", "cet", "cette", "ces", "mon", "ton", "son", "ma", "ta", "sa",
    "nos", "vos", "leur", "leurs",
    # French prepositions
    "de", "à", "en", "dans", "sur", "sous", "par", "pour",
    "avec", "sans", "entre", "vers", "chez", "dont", "où",
    # French conjunctions
    "et", "ou", "mais", "donc", "or", "ni", "car", "que", "quand",
    "si", "comme", "lorsque", "bien",
    # French pronouns / clitics
    "il", "elle", "on", "nous", "vous", "ils", "elles",
    "je", "tu", "me", "te", "se", "lui", "y", "en", "qui",
    # French negation / particles
    "ne", "pas", "plus", "jamais", "rien", "très", "aussi",
    # English articles / determiners
    "the", "a", "an", "this", "that", "these", "those", "my", "your",
    "his", "her", "its", "our", "their",
    # English prepositions
    "of", "in", "on", "at", "to", "for", "with", "by", "from",
    "up", "out", "as", "about", "into", "over", "after",
    # English conjunctions / pronouns
    "and", "or", "but", "so", "yet", "nor",
    "i", "you", "he", "she", "it", "we", "they",
    # English auxiliaries
    "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did",
    "will", "would", "can", "could", "shall", "should", "may", "might",
    "not",
})


def _has_lexical_word(bigram_words: list[str]) -> bool:
    """Return True if the bigram contains at least one full lexical word.

    Symbols (%, #, $, …) and function words (determiners, prepositions, etc.)
    don't count — a phrase made only of these is never a meaningful false-start.
    """
    for w in bigram_words:
        norm = _normalize_word(w)
        if not norm or not norm[0].isalpha():
            continue
        if norm in _FALSE_START_FUNCTION_WORDS:
            continue
        return True
    return False


def _find_false_start_drops(
    words: list[tuple[str, float, float]],
) -> list[DropSegment]:
    """Detect false-start phrases and return them as physical drop segments.

    A false-start is a bigram [A B] abandoned mid-phrase (followed by a pause
    or filler) and then restarted. Guards:
      (a) First occurrence contains no sentence-ending punctuation (. ? !)
      (b) Bridge between first occurrence and restart < 2.0s
      (c) Restart must extend beyond the bigram (word at j+MIN_NGRAM exists)
      (d) No two adjacent false-start cuts
      (e) First occurrence must contain at least one lexical word
    """
    if len(words) < 4:
        return []

    WINDOW_S    = 10.0  # max gap first→restart (s)
    MIN_NGRAM   = 2
    GAP_TRIGGER = 0.35  # pause threshold for bridge detection (s)
    MAX_BRIDGE  = 2.0   # guard (b): reject if bridge ≥ this (s)

    norms = [_normalize_word(w[0]) for w in words]

    drops: list[DropSegment] = []
    last_cut_end = -1.0  # guard (d): end timestamp of most recent false-start cut

    for i in range(len(words) - MIN_NGRAM):
        if not norms[i]:
            continue

        for j in range(i + MIN_NGRAM, len(words) - MIN_NGRAM + 1):
            if words[j][1] - words[i][1] > WINDOW_S:
                break
            if norms[j] != norms[i]:
                continue
            if norms[j + 1] != norms[i + 1]:
                continue

            # Matching bigram at i and j.
            bridge_gap = words[j][1] - words[i + MIN_NGRAM - 1][2]
            has_filler = any(_is_filler(words[k][0]) for k in range(i + MIN_NGRAM, j))
            has_pause = bridge_gap > GAP_TRIGGER or any(
                words[k + 1][1] - words[k][2] > GAP_TRIGGER
                for k in range(i + MIN_NGRAM - 1, min(j, i + MIN_NGRAM + 3))
                if k + 1 < j
            )

            if not (has_filler or has_pause):
                break

            first_phrase = " ".join(w[0] for w in words[i: i + MIN_NGRAM])

            # Guard (a): no sentence-ending punctuation in first occurrence
            if any(re.search(r"[.?!]", words[k][0]) for k in range(i, i + MIN_NGRAM)):
                print(
                    f"[FALSE-START] rejected '{first_phrase}' at {words[i][1]:.2f}s"
                    f" — sentence boundary in first occurrence",
                    flush=True,
                )
                break

            # Guard (b): bridge < 2.0s
            if bridge_gap >= MAX_BRIDGE:
                print(
                    f"[FALSE-START] rejected '{first_phrase}' at {words[i][1]:.2f}s"
                    f" — bridge={bridge_gap:.2f}s >= {MAX_BRIDGE:.1f}s",
                    flush=True,
                )
                break

            # Guard (c): restart extends beyond the repeated bigram
            if j + MIN_NGRAM >= len(words):
                break

            # Guard (d): no adjacent false-start cuts
            if words[i][1] < last_cut_end:
                print(
                    f"[FALSE-START] rejected '{first_phrase}' at {words[i][1]:.2f}s"
                    f" — adjacent to previous cut (last_cut_end={last_cut_end:.2f}s)",
                    flush=True,
                )
                break

            # Guard (e): must contain at least one lexical word
            if not _has_lexical_word([words[k][0] for k in range(i, i + MIN_NGRAM)]):
                print(
                    f"[FALSE-START] rejected '{first_phrase}' at {words[i][1]:.2f}s"
                    f" — no lexical word (pure function-word group)",
                    flush=True,
                )
                break

            # All guards passed — cut first occurrence + bridge up to restart.
            cut_start = words[i][1]
            cut_end   = words[j][1]
            last_cut_end = cut_end
            print(
                f"[FALSE-START] cut: '{first_phrase}' at {words[i][1]:.2f}s"
                f" -> restart at {words[j][1]:.2f}s (bridge={bridge_gap:.2f}s)",
                flush=True,
            )
            drops.append(DropSegment(cut_start, cut_end, f"false_start:{first_phrase}"))
            break  # one cut per i position

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
