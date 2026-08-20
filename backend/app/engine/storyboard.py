"""
Storyboard generation: graphic overlay cards + deterministic captions.

Stage 2 of the HyperFrames pipeline. Takes the pre-trimmed video's
timing map and transcript, produces a storyboard JSON that compose.py
(Stage 3) assembles into a HyperFrames composition.

Two distinct card types in one storyboard:
  - Graphic cards: designed by Claude API, narrative-driven LLM selection
  - Caption cards: mechanically generated from transcript words,
    dense, every spoken word guaranteed, no LLM variability
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

from app.engine.captions import WordTiming
from app.engine.pretrim import TimingMap

# ── Caption segmentation constants ────────────────────────────────────
_PAUSE_GAP = 0.15       # Seconds gap between words to trigger a caption break
_MAX_WORDS = 7           # Maximum words per caption card
_ORPHAN_MIN_DUR = 0.25   # 1-word groups shorter than this merge into neighbors

# ── Grounding guard constants ──────────────────────────────────────────
# Trigger-style cards require explicit verbal signals ("voici ce que personne ne
# dit", "attention", "fais ça maintenant", …). When the LLM misclassifies a card
# by paraphrasing from the beat spine instead of literal speech, these guards
# catch the mismatch and reclassify to a safe generic fallback before render.
_TRIGGER_STYLES: frozenset[str] = frozenset({
    "contrarian_take",
    "warning_soft",
    "red_flag_list",
    "action_step_cta",
    "myth_vs_fact",
    "secret_reveal",
    "objection_response",
    # Wave 7 trigger types
    "live_reaction_split",
    "hidden_cost_reveal",
    "comment_reply_style",
    "before_you_scroll",
    # Wave 8 trigger types
    "broken_promise_tracker",
})
_GROUNDING_OVERLAP_THRESHOLD = 0.40   # fraction of trigger content-words that must match speech
_GROUNDING_WINDOW_PRE_S  = 0.5        # seconds before startSec included in the speech window
_GROUNDING_WINDOW_POST_S = 3.0        # seconds after  startSec included in the speech window
_ANCHOR_SEARCH_FORWARD_S      = 6.0    # how far ahead to scan for trigger keyword position
_DATA_ANCHOR_SEARCH_FORWARD_S = 12.0  # wider window for data cards (number/age/result) whose
                                       # LLM startSec can be 4-9s before the spoken value
_ANCHOR_LEAD_S           = 0.20       # card appears this many seconds before the trigger word

# French stopwords stripped before grounding overlap computation so that invented phrases
# sharing only function words with genuine speech (e.g. "je vais dire que…" vs "je vais
# vous montrer…") do not inflate the score above the rejection threshold.
_FR_STOPWORDS: frozenset[str] = frozenset({
    # subject pronouns
    "je", "tu", "il", "elle", "on", "nous", "vous", "ils", "elles",
    # object / reflexive pronouns
    "me", "te", "se", "lui", "leur", "en", "le", "la", "les", "y",
    # determiners & partitives
    "un", "une", "des", "de", "du", "au", "aux", "le", "la", "les",
    # possessives
    "mon", "ton", "son", "ma", "ta", "sa", "mes", "tes", "ses",
    "notre", "votre", "nos", "vos", "leurs",
    # demonstratives
    "ce", "cet", "cette", "ces",
    # conjunctions
    "et", "ou", "mais", "donc", "or", "ni", "car",
    "que", "qui", "quoi", "dont",
    "comme", "si", "parce",
    # prepositions
    "dans", "pour", "avec", "sur", "sous", "par", "en",
    "vers", "chez", "entre", "avant", "après", "pendant", "depuis",
    # high-frequency verbs (low content value)
    "est", "sont", "être", "avoir", "va", "vais", "vas",
    "aller", "dire", "faire", "dit", "fait", "ont", "été", "était",
    # negation & common adverbs
    "ne", "pas", "plus", "très", "bien", "aussi", "même",
    "tout", "tous", "toute", "toutes", "peu", "trop", "beaucoup",
    # discourse particles
    "ça", "voilà", "voici", "alors", "ainsi", "donc",
    "déjà", "encore", "là", "ici",
    # filler conjunctions
    "quand", "ensemble",
})

# Maps each trigger style to the contentHints field that holds its key claim.
# This field is what the speaker must have literally said for the style to be valid.
_TRIGGER_TEXT_FIELD: dict[str, str] = {
    "contrarian_take":    "take_text",
    "warning_soft":       "warning_text",
    "red_flag_list":      "flags",         # list → joined into one string
    "action_step_cta":    "cta_text",
    "myth_vs_fact":       "myth_text",     # the debunked claim is the most trigger-specific piece
    "secret_reveal":      "secret_text",
    "objection_response": "objection_text",
    # Wave 7
    "live_reaction_split": "reality_text",  # the surprising outcome is the distinctive spoken content
    "hidden_cost_reveal":  "real_cost",     # the revealed price is what the speaker literally states
    "comment_reply_style": "reply_text",    # the speaker's reply is their own literal words
    "before_you_scroll":   "hook_text",     # the hook phrase is what must be verbatim in speech
    # Wave 8
    "broken_promise_tracker": "promises",  # promise list joined — speaker must name these literally
}

# Maps data-card styles to the primary contentHints field used for Whisper anchor search.
# These styles don't populate 'title' — their key spoken content lives in a type-specific
# field. Without this, TITLE-ANCHOR skips them (title="") and they stay at the LLM's
# original startSec, which can be 3-8s before the content is actually spoken.
# Confirmed cases from job 45bf7899: number_hero 8.54s early, age_milestone 3.94s,
# client_result_number 3.76s — all fixed by anchoring to the primary data field.
_DATA_ANCHOR_FIELDS: dict[str, str] = {
    "number_hero":          "nh_number",       # e.g. "12 000 €/mois"
    "age_milestone":        "age_value",        # e.g. "3 ans", "34"
    "client_result_number": "result_value",     # e.g. "+340%", "10k abonnés"
    "income_reveal":        "ir_amount",        # primary revenue number
    "prim_stat_counter":    "stat_value",       # animated counter target
}

# Styles that legitimately occupy video-overlay / fullscreen in landscape (full-canvas heroes).
# All other non-data-panel cards with these zones are remapped to landscape-tl so
# compose.py sees a side-panel zone and derives compact=True.
# full_cover primitives (prim_split_compare, prim_journey_map) MUST be here —
# they require the full 1920×1080 canvas and must never be compacted to landscape-tl.
_LANDSCAPE_HERO_STYLES: frozenset[str] = frozenset({
    "key_phrase", "quote", "question", "definition",
    "chapter_marker", "callout",
    "prim_split_compare", "prim_journey_map", "prim_cinematic_reveal",
    "prim_ascension_reveal", "prim_shatter_truth", "prim_split_stage",
    "prim_confession_frame",
})

# Styles whose catalogue _family is "full_cover": consume the entire canvas.
# Injected onto card objects after LLM generation so the full-cover exclusion
# pass and backdrop-dim dispatch can operate without importing catalogue.py.
_FULL_COVER_STYLES: frozenset[str] = frozenset({
    "prim_split_compare", "prim_journey_map", "prim_cinematic_reveal",
    "prim_ascension_reveal", "prim_shatter_truth", "prim_split_stage",
    "prim_confession_frame",
})


def _segment_captions(
    remapped_words: list[WordTiming],
    transcript_segments: list[dict],
    timing_map: TimingMap,
    emphasis_words: list[str],
    word_categories: dict[str, str],
    max_words: int = _MAX_WORDS,
) -> list[dict]:
    """Build caption cards from remapped words using sentence boundaries.

    Uses remapped_words directly (already in the output timeline via
    pretrim.py's proven direct-offset math). Sentence boundaries come
    from transcript_segments (Whisper's segment structure) — used only
    as boundary markers, NOT for timestamp re-remapping.

    Algorithm:
      1. Build a set of source-timestamp word starts that begin a new
         Whisper segment (sentence boundary markers)
      2. Convert remapped_words to dicts with emphasis/category/boundary flags
      3. Group by: sentence boundary OR max 7 words — mid-sentence
         pauses do NOT break a card
      4. Merge orphans (<=2 words) forward or backward
    """
    emphasis_set = {ew.lower() for ew in emphasis_words}

    # Build sentence-boundary markers from transcript_segments.
    # A word is a segment starter if it's the FIRST word in any Whisper segment.
    seg_start_times: set[float] = set()
    for seg in transcript_segments:
        seg_words = seg.get("words", [])
        if seg_words:
            seg_start_times.add(round(float(seg_words[0].get("start", 0)), 3))

    _MIN_WORD_DUR = 0.1  # clamp zero-duration words to this minimum

    # Build all_words from remapped_words (correct timing) with seg_start
    # tags from transcript_segments (sentence boundaries). Both lists have
    # the same words in the same order (pretrim preserves order), just
    # with different grouping structure. Walk them in lockstep.
    all_words: list[dict] = []
    _skipped_empty = 0
    _clamped_zero_dur = []

    # Flatten transcript_segments into a parallel list of (text, is_seg_start)
    seg_tags: list[tuple[str, bool]] = []
    for seg in transcript_segments:
        first_in_seg = True
        for sw in seg.get("words", []):
            text = sw.get("text", "").strip()
            if not text:
                continue
            seg_tags.append((text, first_in_seg))
            first_in_seg = False

    _MIN_ARTIFACT_DUR = 0.030  # Whisper artifacts: duration below this are noise

    # Walk remapped_words and seg_tags in lockstep by position
    tag_idx = 0
    _skipped_short_dur = 0
    for w in remapped_words:
        text = w.text.strip()
        if not text:
            _skipped_empty += 1
            continue

        # Exclude Whisper artifacts (< 30ms).  Advance tag_idx to keep the
        # seg_tags lockstep aligned — these words ARE in transcript_segments.
        if w.end - w.start < _MIN_ARTIFACT_DUR:
            _skipped_short_dur += 1
            if tag_idx < len(seg_tags):
                tag_idx += 1
            continue

        # Get seg_start from the parallel tag list
        is_seg_start = False
        if tag_idx < len(seg_tags):
            is_seg_start = seg_tags[tag_idx][1]
            tag_idx += 1

        w_start = w.start
        w_end = w.end
        if w_end <= w_start:
            w_end = w_start + _MIN_WORD_DUR
            _clamped_zero_dur.append(f"\"{text}\" at {w_start:.2f}s")

        text_lower = text.lower().strip(".,!?;:'\"")
        all_words.append({
            "text": text,
            "start": round(w_start, 4),
            "end": round(w_end, 4),
            "emphasis": text_lower in emphasis_set,
            "category": word_categories.get(text_lower, ""),
            "seg_start": is_seg_start,
        })

    print(f"[CAPTION AUDIT] remapped_words: {len(remapped_words)} total, "
          f"{len(all_words)} kept, {_skipped_empty} empty, "
          f"{_skipped_short_dur} short-dur (<{_MIN_ARTIFACT_DUR}s) filtered, "
          f"{len(_clamped_zero_dur)} zero-dur clamped to {_MIN_WORD_DUR}s")
    if _clamped_zero_dur:
        print(f"[CAPTION AUDIT] Clamped zero-dur words: {_clamped_zero_dur[:10]}")
    if tag_idx != len(seg_tags):
        print(f"[CAPTION AUDIT] WARNING: seg_tags has {len(seg_tags)} entries "
              f"but only {tag_idx} consumed (mismatch with remapped_words)")

    # Diagnostic: log every apostrophe-boundary word so we can inspect raw Whisper tokens.
    _APOS_CHARS = ("'", "’", "‘", "ʼ")
    _apos_dbg: list[str] = []
    for _ai, _aw in enumerate(all_words):
        _t = _aw["text"]
        _prev_t = all_words[_ai - 1]["text"] if _ai > 0 else ""
        if any(_t.startswith(c) or _t.endswith(c) for c in _APOS_CHARS) or \
                any(_prev_t.endswith(c) for c in _APOS_CHARS):
            _apos_dbg.append(
                f"  [{_ai}] prev={repr(_prev_t) if _ai > 0 else 'N/A'}"
                f" | cur={repr(_t)} | seg_start={_aw['seg_start']}"
                f" | t={_aw['start']:.3f}s"
            )
    if _apos_dbg:
        print(f"[CAPTION APOS] {len(_apos_dbg)} apostrophe-boundary entries (raw Whisper tokens):")
        for _dl in _apos_dbg[:40]:
            print(_dl, flush=True)
    else:
        print("[CAPTION APOS] No apostrophe-boundary words found", flush=True)

    # Merge apostrophe-split tokens (French elisions: m'a, l'équipe, j'ai, etc.)
    # Whisper often splits these into ["m'", "a"] or ["m", "'a"] as separate words.
    # Both U+0027 (straight) and U+2019 (right single quotation mark) are handled.
    _STORYBOARD_APOS = ("'", "’")  # U+0027 + U+2019
    merged_words: list[dict] = []
    for w in all_words:
        if merged_words and (
            any(w["text"].startswith(a) for a in _STORYBOARD_APOS) or
            any(merged_words[-1]["text"].endswith(a) for a in _STORYBOARD_APOS)
        ):
            prev = merged_words[-1]
            prev["text"] = prev["text"] + w["text"]
            prev["end"] = w["end"]
        else:
            merged_words.append(dict(w))
    if len(merged_words) != len(all_words):
        print(f"[CAPTION] Merged {len(all_words) - len(merged_words)} apostrophe-split tokens "
              f"({len(all_words)} -> {len(merged_words)} words)")
    all_words = merged_words

    # Merge decimal-split tokens (European notation: "8,5" → Whisper ["8", " ,5"])
    _decimal_merged: list[dict] = []
    for w in all_words:
        _wt = w["text"].lstrip()
        if (
            _decimal_merged
            and len(_wt) >= 2
            and _wt[0] in (",", ".")
            and _wt[1].isdigit()
            and _decimal_merged[-1]["text"].rstrip()[-1:].isdigit()
        ):
            prev = _decimal_merged[-1]
            prev["text"] = prev["text"].rstrip() + _wt
            prev["end"] = w["end"]
        else:
            _decimal_merged.append(dict(w))
    if len(_decimal_merged) != len(all_words):
        print(f"[CAPTION] Merged {len(all_words) - len(_decimal_merged)} decimal-split tokens "
              f"({len(all_words)} -> {len(_decimal_merged)} words)")
    all_words = _decimal_merged

    # Step 1: group by sentence boundary OR word count
    raw_groups: list[list[dict]] = []
    current: list[dict] = []
    for w in all_words:
        if current:
            if w.get("seg_start") or len(current) >= max_words:
                raw_groups.append(current)
                current = []
        current.append(w)
    if current:
        raw_groups.append(current)

    # Step 2: merge orphans
    merged: list[list[dict]] = []
    i = 0
    while i < len(raw_groups):
        g = raw_groups[i]
        dur = g[-1]["end"] - g[0]["start"] if g else 0
        is_orphan = len(g) <= 2

        if is_orphan:
            # Try forward merge
            if i + 1 < len(raw_groups) and len(raw_groups[i + 1]) + len(g) <= max_words:
                raw_groups[i + 1] = g + raw_groups[i + 1]
                i += 1
                continue
            # Try backward merge
            if merged and len(merged[-1]) + len(g) <= max_words:
                merged[-1].extend(g)
                i += 1
                continue

        merged.append(g)
        i += 1

    # Step 3: build caption card dicts
    cards: list[dict] = []
    for idx, group in enumerate(merged):
        cards.append({
            "id": f"cap-{idx + 1:03d}",
            "type": "caption",
            "startSec": group[0]["start"],
            "endSec": group[-1]["end"],
            "zone": "lower-third",
            "words": group,
        })

    # ── BOUNDARY INVARIANT — every seg_start word must begin a card ────
    _boundary_violations = []
    for c in cards:
        for wi, w in enumerate(c["words"]):
            if w.get("seg_start") and wi > 0:
                prev = c["words"][wi - 1]["text"]
                _boundary_violations.append(
                    f"Card {c['id']}: \"{w['text']}\" is seg_start but at "
                    f"position {wi} (after \"{prev}\"), not at card start"
                )
    if _boundary_violations:
        print(f"[CAPTION AUDIT] CRITICAL — {len(_boundary_violations)} boundary violations:")
        for v in _boundary_violations[:10]:
            print(f"  {v}")
    else:
        print(f"[CAPTION AUDIT] BOUNDARY CHECK: all segment-start words begin their cards")

    # ── COVERAGE AUDIT — log every discrepancy ──────────────────────────
    input_texts = [w["text"] for w in all_words]
    output_texts = []
    for c in cards:
        output_texts.extend(w["text"] for w in c["words"])

    if input_texts != output_texts:
        missing = []
        extra = []
        inp_set = list(enumerate(input_texts))
        out_set = list(enumerate(output_texts))

        # Find words in input but not in output (missing)
        j = 0
        for i, word in enumerate(input_texts):
            if j < len(output_texts) and output_texts[j] == word:
                j += 1
            else:
                ctx_before = " ".join(input_texts[max(0, i-2):i])
                ctx_after = " ".join(input_texts[i+1:i+3])
                w_data = all_words[i]
                missing.append(
                    f"  [{i}] \"{word}\" at {w_data['start']:.2f}s "
                    f"(context: ...{ctx_before} >>>{word}<<< {ctx_after}...)"
                )

        print(f"[CAPTION AUDIT] MISMATCH: {len(input_texts)} input words, {len(output_texts)} output words")
        if missing:
            print(f"[CAPTION AUDIT] MISSING {len(missing)} word(s):")
            for m in missing[:20]:
                print(m)
        if len(output_texts) > len(input_texts):
            print(f"[CAPTION AUDIT] EXTRA {len(output_texts) - len(input_texts)} word(s) in output")
    else:
        print(f"[CAPTION AUDIT] PASS: {len(input_texts)}/{len(input_texts)} words, 0 missing")

    # Post-merge apostrophe audit: detect unmerged spans surviving into final cards.
    # "card starts with apos" = 'j' ended previous card, "'ai" starts this one (merge failed at boundary).
    # "unmerged span inside card" = merge should have joined these but didn't.
    _apos_start_warns: list[str] = []
    _apos_span_warns: list[str] = []
    for c in cards:
        for wi, w in enumerate(c["words"]):
            wt = w["text"]
            if any(wt.startswith(a) for a in _STORYBOARD_APOS):
                if wi == 0:
                    _apos_start_warns.append(
                        f"  Card {c['id']} starts with: {repr(wt)} at {w['start']:.3f}s"
                    )
                else:
                    prev_wt = c["words"][wi - 1]["text"]
                    _apos_span_warns.append(
                        f"  Card {c['id']} [{wi}]: prev={repr(prev_wt)} | cur={repr(wt)} at {w['start']:.3f}s"
                    )
    if _apos_start_warns:
        print(f"[CAPTION APOS] CARD-BOUNDARY: {len(_apos_start_warns)} card(s) open with apostrophe word (merge missed boundary):")
        for _l in _apos_start_warns:
            print(_l, flush=True)
    if _apos_span_warns:
        print(f"[CAPTION APOS] INTRA-CARD: {len(_apos_span_warns)} unmerged apostrophe span(s) inside card(s):")
        for _l in _apos_span_warns:
            print(_l, flush=True)
    if not _apos_start_warns and not _apos_span_warns:
        print("[CAPTION APOS] OK: no unmerged apostrophe spans in final cards", flush=True)

    return cards


# ── Prosodic emphasis helpers ──────────────────────────────────────────────────

def _prost_median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def _prost_std(values: list[float]) -> float:
    if len(values) < 2:
        return 1.0
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5


def _compute_prosodic_scores(
    keep_segments: list[dict],
    transcript_words: list[dict],
    energy_profile: list[dict],
) -> dict[float, float]:
    """Compute 0-1 prosodic emphasis score per keep_segment (source timeline).

    Combines three signals — all zero-dependency, source-space:
      pause_before (0.40) — gap before segment's first word vs speaker median
      slowdown     (0.35) — avg word duration in segment vs speaker median
      rms_delta    (0.25) — RMS level in energy window vs median (from energy_detector)

    Returns {src_start: score}.
    """
    if not keep_segments or not transcript_words:
        return {}

    words = sorted(transcript_words, key=lambda w: float(w.get("start", 0)))

    # Global baseline: median word duration
    durs = [float(w.get("end", 0)) - float(w.get("start", 0))
            for w in words
            if float(w.get("end", 0)) - float(w.get("start", 0)) > 0.02]
    if not durs:
        return {}
    median_word_dur = _prost_median(durs)

    raw: dict[float, dict] = {}
    for seg in keep_segments:
        s_start = float(seg.get("start", 0))
        s_end   = float(seg.get("end", s_start + 1))

        seg_words = [w for w in words
                     if float(w.get("start", 0)) >= s_start - 0.1
                     and float(w.get("start", 0)) < s_end]
        if not seg_words:
            raw[s_start] = {"pause": 0.0, "slowdown": 1.0, "rms": None}
            continue

        # Pause before first word of this segment
        first_ws = float(seg_words[0].get("start", s_start))
        prev_ends = [float(w.get("end", 0)) for w in words
                     if float(w.get("end", 0)) <= first_ws + 0.05
                     and float(w.get("end", 0)) > first_ws - 5.0]
        pause = max(0.0, first_ws - max(prev_ends)) if prev_ends else 0.0

        # Slowdown: avg word duration in segment vs global median
        sdurs = [float(w.get("end", 0)) - float(w.get("start", 0))
                 for w in seg_words
                 if float(w.get("end", 0)) - float(w.get("start", 0)) > 0.02]
        avg_dur  = sum(sdurs) / len(sdurs) if sdurs else median_word_dur
        slowdown = avg_dur / max(median_word_dur, 0.001)

        # RMS from energy_profile 3s window covering s_start
        rms = None
        for ep in energy_profile:
            ep_at  = float(ep.get("at", 0))
            ep_dur = float(ep.get("duration", 3.0))
            if ep_at <= s_start < ep_at + ep_dur:
                rms = float(ep.get("rms_db", -40.0))
                break

        raw[s_start] = {"pause": pause, "slowdown": slowdown, "rms": rms}

    pauses    = [v["pause"]    for v in raw.values()]
    slowdowns = [v["slowdown"] for v in raw.values()]
    rms_vals  = [v["rms"]      for v in raw.values() if v["rms"] is not None]

    p_med, p_std = _prost_median(pauses),    _prost_std(pauses)
    s_med, s_std = _prost_median(slowdowns), _prost_std(slowdowns)
    r_med        = _prost_median(rms_vals) if rms_vals else -40.0
    r_std        = _prost_std(rms_vals)    if rms_vals else 5.0

    def _z(val: float, med: float, std: float) -> float:
        return max(-2.5, min(2.5, (val - med) / max(std, 0.001)))

    result: dict[float, float] = {}
    for s_start, v in raw.items():
        zp = _z(v["pause"],    p_med, p_std)
        zs = _z(v["slowdown"], s_med, s_std)
        zr = _z(v["rms"] if v["rms"] is not None else r_med, r_med, r_std)
        combined = 0.40 * zp + 0.35 * zs + 0.25 * zr
        result[s_start] = round(1.0 / (1.0 + math.exp(-combined)), 3)

    return result


def _generate_graphic_cards(
    trimmed_duration: float,
    script_structure: list[dict],
    keep_segments: list[dict],
    key_lines: list[str],
    brand_color: str,
    content_type: str,
    editing_style: str,
    format_hint: str,
    timing_map: TimingMap,
    language: str = "en",
    subject_side: str | None = None,
) -> list[dict]:
    """Generate graphic overlay cards via Claude API call.

    Uses the narrative context (beat spine, key lines, retention notes)
    to design cards that reinforce the emotional arc.
    """
    from anthropic import Anthropic
    from app.core.config import settings

    # Compute card density per graphic-overlays formula.
    # Short format (<60s) gets a denser ceiling (~1 card per 4.5s) to improve
    # watch-time retention. Original pace tiers — only LLM rich cards, no beat layer.
    if trimmed_duration < 60:
        base_pace = 4.5 if format_hint == "short" else 7
    elif trimmed_duration < 180:
        base_pace = 10
    elif trimmed_duration < 600:
        # Linear interpolation 10→16 across 180–600s: eliminates the hard
        # drop at 180s (179s→18 cards vs 181s→11 cards with flat pace=16).
        # At 300s (5 min): pace≈11.7 → ~26 cards; at 599s: pace≈16 → ~37.
        base_pace = 10 + 6 * (trimmed_duration - 180) / 420
    elif trimmed_duration < 1800:
        # Linear interpolation 16→28 across 600–1800s: eliminates the hard
        # drop at 600s (599s→37 cards vs 601s→21 cards with flat pace=28).
        # At 660s (11 min): pace≈16.6 → ~40 cards; at 1200s: pace≈22 → ~54.
        base_pace = 16 + 12 * (trimmed_duration - 600) / 1200
    else:
        base_pace = 28

    density_mult = 1.0
    target_cards = max(3, round(trimmed_duration / (base_pace * density_mult)))

    # Build beat summary for the prompt
    beat_summary = []
    for seg in keep_segments:
        src_start = float(seg.get("start", 0))
        src_end = float(seg.get("end", 0))
        out_start = timing_map.source_to_output(src_start)
        out_end = timing_map.source_to_output(src_end)
        if out_end <= out_start:
            continue
        _beat_entry: dict = {
            "beat": seg.get("beat", ""),
            "outStart": round(out_start, 2),
            "outEnd": round(out_end, 2),
            "reason": seg.get("reason", ""),
            "retention_note": seg.get("retention_note", ""),
            "score": seg.get("score", 0),
        }
        # Prosodic enrichment — only present when computed (zero-cost otherwise)
        if seg.get("emphasis_score") is not None:
            _beat_entry["emphasis_score"] = seg["emphasis_score"]
        if seg.get("prosodic_peak"):
            _beat_entry["prosodic_peak"] = True
        if seg.get("energy_level"):
            _beat_entry["energy_level"] = seg["energy_level"]
        beat_summary.append(_beat_entry)

    # Remap script_structure to output timeline
    script_out = []
    for entry in script_structure:
        src_s = float(entry.get("start", 0))
        src_e = float(entry.get("end", 0))
        out_s = timing_map.source_to_output(src_s)
        out_e = timing_map.source_to_output(src_e)
        if out_e > out_s:
            script_out.append({
                "beat": entry.get("beat", ""),
                "lines": entry.get("lines", []),
                "start": round(out_s, 2),
                "end": round(out_e, 2),
            })

    system_prompt = f"""\
You design graphic overlay cards for edited talking-head videos.

OUTPUT: a JSON array of card objects. Each card:
{{
  "id": "card-01",
  "beat": "<from the beat spine below>",
  "intent": "<1 sentence: what this card communicates>",
  "startSec": <seconds in the edited video>,
  "endSec": <seconds>,
  "accentIndex": <0-4>,
  "zone": "fullscreen"|"side-panel"|"video-overlay",
  "contentHints": {{
    "kicker": "<optional short label>",
    "title": "<main text>",
    "accent_word": "<optional: one word/phrase from title to emphasize via highlight swipe>",
    "detail": "<optional supporting text>",
    "number": "<if a stat/number is featured — for prim_stat_counter use numeric string only, e.g. '46.2' not '46,2 M€'>",
    "prefix": "<prim_stat_counter only — currency/unit BEFORE the number, e.g. '$'. Convention FR: laisser vide, mettre la devise dans suffix>",
    "suffix": "<prim_stat_counter only — unit AFTER the number, e.g. 'M€', 'K', '%'. Convention FR: suffix='€' ou 'M€', prefix vide>",
    "style": "stat"|"key_phrase"|"quote"|"callout"|"comparison"|"list"|"question"|"timeline"|"dialogue"|"trend"|"attributed_quote"|"carousel"|"definition"|"checklist"|"score"|"mindmap"|"instagram-follow"|"tiktok-follow"|"yt-lower-third"|"news_ticker"|"rating"|"map_location"|"progress_bar"|"before_after_image"|"countdown"|"poll_question"|"myth_vs_fact"|"step_number"|"quote_carousel"|"emoji_reaction"|"price_tag"|"warning_soft"|"testimonial"|"versus_battle"|"recap_summary"|"location_journey"|"formula_equation"|"roadmap_milestone"|"pros_cons"|"star_rating_review"|"income_reveal"|"question_answer_pair"|"chapter_marker"|"secret_reveal"|"objection_response"|"data_bar_chart"|"cause_effect"|"number_ranking"|"hand_written_note"|"speech_bubble_thought"|"calendar_date_highlight"|"percentage_split"|"red_flag_list"|"success_metric_badge"|"client_avatar_persona"|"book_recommendation"|"tool_stack"|"revenue_breakdown"|"age_milestone"|"contrarian_take"|"action_step_cta"|"story_chapter_transition"|"live_reaction_split"|"hidden_cost_reveal"|"social_proof_counter"|"timeline_prediction"|"red_thread_connector"|"silent_beat_pause"|"comment_reply_style"|"before_you_scroll"|"traffic_light_status"|"day_in_life_schedule"|"skill_tree_unlock"|"audience_poll_result"|"broken_promise_tracker"|"ingredient_list"|"resource_allocation"|"fill_in_the_blank"|"streak_counter"|"before_now_later"|"platform_stats"|"cost_comparison"|"decision_matrix"|"habit_tracker"|"income_vs_expense"|"milestone_recap"|"content_calendar"|"client_result_number"|"mistake_lesson"|"tool_comparison"|"weekly_review"|"audience_question"|"prim_stat_counter"|"prim_split_compare"|"prim_journey_map"|"number_hero"|"prim_cinematic_reveal"|"prim_ascension_reveal"|"prim_shatter_truth"|"prim_split_stage"|"prim_confession_frame",
    "left_label": "<comparison / prim_split_compare: left side label>",
    "left_value": "<comparison: left side value (prim_split_compare does not use this)>",
    "right_label": "<comparison / prim_split_compare: right side label>",
    "right_value": "<comparison: right side value (prim_split_compare does not use this)>",
    "from_city": "<prim_journey_map REQUIRED — departure city name, e.g. 'Paris'>",
    "to_city": "<prim_journey_map REQUIRED — arrival city name, e.g. 'Bangkok'>",
    "from_country": "<prim_journey_map optional — departure country, e.g. 'France'>",
    "to_country": "<prim_journey_map optional — arrival country, e.g. 'Thaïlande'>",
    "distance_km": "<prim_journey_map optional — numeric distance string, e.g. '9560'>",
    "items": ["<list/checklist: item 1>", "<list/checklist: item 2>", ...],
    "steps": ["<timeline/prim_split_stage mode=steps: step 1>", "<step 2>", ...],
    "slides": ["<carousel: slide 1>", "<carousel: slide 2>", ...],
    "line_a": "<dialogue: first speaker's line>",
    "line_b": "<dialogue: second speaker's line>",
    "speaker_a": "<dialogue: optional first speaker label>",
    "speaker_b": "<dialogue: optional second speaker label>",
    "trend_direction": "up"|"down",
    "attribution": "<attributed_quote: who said it>",
    "term": "<definition: the word/concept>",
    "definition": "<definition: explanation text>",
    "score_text": "<score: e.g. 3-1, Top 5>",
    "center": "<mindmap: central concept>",
    "branches": ["<mindmap: branch 1>", "<mindmap: branch 2>", ...],
    "rating_value": "<rating: numeric score, e.g. 8.5>",
    "rating_max": "<rating: max of the scale, e.g. 10>",
    "location_name": "<map_location: city, country, or place name>",
    "location_context": "<map_location: optional context e.g. country or region>",
    "progress_percent": "<progress_bar: integer 0-100>",
    "progress_label": "<progress_bar: what is being measured>",
    "before_label": "<before_after_image: description of the BEFORE state>",
    "after_label": "<before_after_image: description of the AFTER state>",
    "countdown_from": "<countdown: starting integer, counts down to 0>",
    "countdown_label": "<countdown: what is being counted down>",
    "poll_question": "<poll_question: the question text>",
    "poll_options": ["<poll_question: option 1>", "<poll_question: option 2>"],
    "myth_text": "<myth_vs_fact: the incorrect belief to debunk>",
    "fact_text": "<myth_vs_fact: the corrected truth>",
    "truth_text": "<prim_shatter_truth: the truth that replaces the shattered myth, max 60 chars>",
    "side": "<prim_split_stage: which side the speaker video slides to — 'left' or 'right'>",
    "mode": "<prim_split_stage: content type on the opposite side — 'steps' or 'diagram'>",
    "nodes": [{{"label": "<prim_split_stage mode=diagram: node label, short phrase>"}}],
    "step_num": "<step_number: the step number or label, e.g. '01', '3', 'Étape 2'>",
    "step_label": "<step_number: short description of this step>",
    "quotes": ["<quote_carousel: quote 1>", "<quote_carousel: quote 2>", "<quote_carousel: quote 3>"],
    "emoji_label": "<emoji_reaction: the reaction as a short punchy phrase, e.g. 'On s\'emballe !', 'C\'est impressionnant', 'Voilà le résultat'>",
    "price": "<price_tag: price string, e.g. '29€', '$199/mo', 'Gratuit'>",
    "price_context": "<price_tag: optional context, e.g. 'par mois', 'one-time', 'paiement unique'>",
    "warning_text": "<warning_soft: the warning message text>",
    "testimonial_text": "<testimonial: the quote from the customer or client>",
    "person_name": "<testimonial: name of the person>",
    "person_role": "<testimonial: role or context, e.g. 'CEO at Acme', 'Client depuis 2 ans'>",
    "side_a": "<versus_battle: first side label or name>",
    "side_b": "<versus_battle: second side label or name>",
    "recap_items": ["<recap_summary: first bullet point>", "<second point>", "<third point>"],
    "journey_points": ["<location_journey: first location/stop>", "<second stop>", "<third stop>"],
    "formula_parts": ["<formula_equation: first term>", "×", "<second term>", "=", "<result term>"],
    "milestone_label": "<roadmap_milestone: the milestone title or achievement>",
    "milestone_context": "<roadmap_milestone: brief context or date/stage>",
    "pros": ["<pros_cons: first advantage>", "<second advantage>"],
    "cons": ["<pros_cons: first drawback>", "<second drawback>"],
    "stars": 4,
    "review_text": "<star_rating_review: the review quote>",
    "reviewer_name": "<star_rating_review: reviewer name or handle>",
    "income_value": "<income_reveal: the income/number to reveal, e.g. '12 000 €/mois'>",
    "income_context": "<income_reveal: brief context, e.g. 'revenu passif en 6 mois'>",
    "qa_question": "<question_answer_pair: the question text>",
    "qa_answer": "<question_answer_pair: the answer text>",
    "chapter_num": "<chapter_marker: chapter number or label, e.g. '01', 'II', 'Partie 3'>",
    "chapter_title": "<chapter_marker: chapter title or subject>",
    "secret_text": "<secret_reveal: the text to reveal from blur, e.g. the key insight or secret>",
    "objection_text": "<objection_response: the objection or pushback being addressed>",
    "response_text": "<objection_response: the speaker's response or rebuttal>",
    "bar_labels": ["<data_bar_chart: label for bar 1>", "<label for bar 2>"],
    "bar_values": [0.0, 0.0],
    "cause_text": "<cause_effect: the cause or trigger>",
    "effect_text": "<cause_effect: the resulting effect or outcome>",
    "rankings": ["<number_ranking: first place label>", "<second place label>", "<third place label>"],
    "note_text": "<hand_written_note: the aside or note text to display>",
    "thought_text": "<speech_bubble_thought: the internal thought or reflection text>",
    "date_value": "<calendar_date_highlight: the date or period to highlight, e.g. 'Lundi 14 Jan', '2025', 'Semaine 3'>",
    "date_context": "<calendar_date_highlight: short context label for the date, e.g. 'Lancement officiel', 'Objectif atteint'>",
    "split_labels": ["<percentage_split: label for segment 1>", "<label for segment 2>"],
    "split_values": [0.0, 0.0],
    "flags": ["<red_flag_list: warning signal 1>", "<signal 2>", "<signal 3>"],
    "badge_label": "<success_metric_badge: the achievement or metric headline, e.g. '10 000 abonnés', '+47% de CA'>",
    "badge_context": "<success_metric_badge: brief supporting context, e.g. 'en 90 jours', 'objectif Q1 atteint'>",
    "persona_name": "<client_avatar_persona: the persona or client archetype name, e.g. 'Sophie, 34 ans'>",
    "persona_traits": ["<client_avatar_persona: trait or pain point 1>", "<trait 2>", "<trait 3>"],
    "book_title": "<book_recommendation: the book title>",
    "book_author": "<book_recommendation: the author name>",
    "tools": ["<tool_stack: tool or software name 1>", "<tool 2>", "<tool 3>"],
    "revenue_sources": ["<revenue_breakdown: source label 1>", "<source label 2>"],
    "revenue_values": [0.0, 0.0],
    "age_value": "<age_milestone: the age or duration as a number or string, e.g. '34', '10 ans', '90 jours'>",
    "age_context": "<age_milestone: short context, e.g. 'à laquelle j\'ai lancé mon business', 'de travail pour y arriver'>",
    "take_text": "<contrarian_take: the contrarian or provocative statement>",
    "cta_text": "<action_step_cta: the imperative call-to-action text>",
    "transition_label": "<story_chapter_transition: the short narrative beat label, e.g. 'Mais voilà ce qui s\'est passé', 'La suite…', 'Et maintenant ?'>",
    "expected_text": "<live_reaction_split: what was expected or assumed>",
    "reality_text": "<live_reaction_split: what actually happened — the surprising outcome>",
    "sticker_price": "<hidden_cost_reveal: the advertised or displayed price>",
    "real_cost": "<hidden_cost_reveal: the actual full cost being revealed>",
    "counter_final_value": "<social_proof_counter: the final number to settle on, e.g. '12 847', '1M+'>",
    "counter_label": "<social_proof_counter: what the number represents, e.g. 'abonnés', 'clients satisfaits'>",
    "confirmed_steps": ["<timeline_prediction: confirmed/past step 1>", "<step 2>"],
    "predicted_steps": ["<timeline_prediction: predicted/future step 1>", "<step 2>"],
    "connector_points": ["<red_thread_connector: concept 1 being tied together>", "<concept 2>", "<optional concept 3>"],
    "pause_symbol": "<silent_beat_pause: optional — symbol or short pause marker, defaults to '…' if omitted>",
    "comment_text": "<comment_reply_style: the comment or question being shown>",
    "reply_text": "<comment_reply_style: the speaker's reply or response>",
    "hook_text": "<before_you_scroll: the pattern-interrupt hook text — must be punchy and direct>",
    "status_color": "<traffic_light_status: 'red' | 'yellow' | 'green' — must match what the speaker implies>",
    "status_label": "<traffic_light_status: label describing what the status means, e.g. 'Stratégie validée', 'À optimiser', 'Abandonne ça'>",
    "schedule_items": ["<day_in_life_schedule: time-anchored item, e.g. '6h - Réveil', '9h - Deep work', '12h - Pause'>"],
    "unlocked_milestones": ["<skill_tree_unlock: milestone or skill unlocked in sequence, e.g. 'Maîtrise de Notion', 'Premier client signé'>"],
    "poll_percentages": [0.0, 0.0],
    "promises": ["<broken_promise_tracker: promise 1 as stated>", "<promise 2>"],
    "kept_status": [true, false],
    "ingredients": ["<ingredient_list: required item or material 1>", "<item 2>"],
    "resource_labels": ["<resource_allocation: label for resource 1, e.g. 'Temps', 'Énergie', 'Budget'>"],
    "resource_values": [0.0, 0.0],
    "sentence_with_blank": "<fill_in_the_blank: the sentence with a blank placeholder, e.g. 'La clé du succès c\\'est ___'>",
    "blank_word": "<fill_in_the_blank: the single word or short phrase that fills the blank>",
    "streak_count": "<streak_counter: the accumulation number, e.g. '42', '100'>",
    "streak_unit": "<streak_counter: unit of the streak, e.g. 'jours', 'semaines', 'posts'>",
    "streak_label": "<streak_counter: short label describing the streak, e.g. 'de publication quotidienne', 'sans interruption'>",
    "before_label": "<before_now_later: description of the BEFORE state>",
    "now_label": "<before_now_later: description of the CURRENT / NOW state>",
    "later_label": "<before_now_later: description of the FUTURE / LATER state>",
    "platforms": ["<platform_stats: platform name 1, e.g. 'TikTok', 'YouTube', 'Instagram'>"],
    "values": ["<platform_stats: metric value for platform 1, e.g. '50k', '20k abonnés'>"],
    "option_labels": ["<cost_comparison: label for option 1, e.g. 'Basique', 'Pro', 'Enterprise'>"],
    "option_prices": ["<cost_comparison: price string for option 1, e.g. '0€', '29€/mois', '99€/mois'>"],
    "best_index": "<cost_comparison: 0-based index of the recommended/best option; omit to default to last>",
    "quadrant_labels": ["<decision_matrix: label for quadrant 1 (top-left)>", "<quadrant 2 (top-right)>", "<quadrant 3 (bottom-left)>", "<quadrant 4 (bottom-right)>"],
    "habit_label": "<habit_tracker: name of the habit being tracked, e.g. 'Sport matinal', 'Lecture 30 min'>",
    "days_completed": [true, false],
    "income_value": "<income_vs_expense: the income figure as a string, e.g. '12 000€', '8k'>",
    "expense_value": "<income_vs_expense: the expense figure as a string, e.g. '7 500€', '5k'>",
    "income_label": "<income_vs_expense: label for the income bar, defaults to 'Revenus'>",
    "expense_label": "<income_vs_expense: label for the expense bar, defaults to 'Dépenses'>",
    "milestones": ["<milestone_recap: milestone text 1, e.g. '2020 — Premier client'>", "<milestone 2>"],
    "calendar_items": ["<content_calendar: calendar entry, e.g. 'Lundi — Post produit'>", "<entry 2>"],
    "result_value": "<client_result_number: the transformation result, e.g. '+340%', '10k abonnés', 'x3 CA'>",
    "result_context": "<client_result_number: time or context frame, e.g. 'en 60 jours', 'en 3 mois'>",
    "client_label": "<client_result_number: optional client identifier, e.g. 'Marie D.', 'Client e-commerce'>",
    "mistake_text": "<mistake_lesson: the mistake that was made, in the speaker's own words>",
    "lesson_text": "<mistake_lesson: the lesson learned from that mistake>",
    "tool_names": ["<tool_comparison: name of tool 1, e.g. 'Notion'>", "<tool 2>"],
    "tool_features": ["<tool_comparison: feature comparison row 1, e.g. 'Prix: Gratuit | 5€/mois'>", "<row 2>"],
    "review_categories": ["<weekly_review: category name 1, e.g. 'Contenu', 'Prospection', 'Santé'>"],
    "review_scores": ["<weekly_review: score or assessment for category 1, e.g. '8/10', '✓', '⚠️'>"],
    "question_text": "<audience_question: the single question posed to the audience — no answer included>",
    "nh_number": "<number_hero: the main number/value to display as-is, e.g. '12 000 €/mois', '500K abonnés', '×3 CA'>",
    "nh_kicker": "<number_hero: small caps label above the lines, e.g. 'RÉSULTAT DU MOIS', 'CLIENT #47', 'EN 90 JOURS'>",
    "nh_detail": "<number_hero: muted context below the lines, e.g. 'en affiliation organique', 'de chiffre d\\'affaires net'>"
  }}
}}

ZONES — where the card sits on screen:
  fullscreen    — covers whole canvas (hero moments, big statements)
  side-panel    — left or right portion (data, comparisons)
  video-overlay — full canvas but transparent (glass effect over video)
  NEVER use "lower-third" — that zone is reserved for captions only.
{f"SUBJECT POSITION: the speaker occupies the {subject_side} side of the frame. Place data-heavy cards (stat, list, comparison) on the OPPOSITE side so they don't obscure the face." if subject_side and subject_side != "center" else ""}
RULES:
- CARD COUNT BUDGET: {target_cards} cards maximum for a {trimmed_duration:.0f}s video. Ceiling means: never pad or force cards beyond what the content genuinely deserves. It does NOT mean: aim for the minimum. On longer videos, under-coverage is its own quality failure — a viewer watching 10+ minutes without visual reinforcement loses engagement. Treat the budget as the number of genuine moments this video contains: most well-structured videos will use 70–90% of it. Place a card whenever the speaker teaches, reveals, contrasts, enumerates, or drives home a point worth remembering. If you are at 40% of the budget with half the video left and no good reason to stop, look harder for moments you may have missed.
- DEAD ZONE RULE: Any 60+ second stretch of active speech with no card is a failure mode — even on pure narrative passages with no explicit enumeration, statistic, or comparison. On reflective, elaborative, or storytelling content, use these types: contrarian_take (the speaker challenges what the audience likely assumes), cause_effect (explicit or implied causal chain: "X → Y", "parce que X, donc Y"), quote (powerful first-person statement about the speaker's own experience or perspective), callout (conceptual anchor for the section — what the viewer must grasp to follow what comes next), question (rhetorical question the speaker poses and then answers). "No obvious structural peak" is not a reason to skip — it is a reason to look harder for the underlying insight.
- DUAL-CARD BEATS: When a single speech segment contains BOTH (a) a vivid, memorable, or funny formulation that stands on its own as a key_phrase AND (b) one or more distinct numeric facts (stat), generate TWO separate cards with startSec offset by 1-2s: the stat card anchors to when the number is spoken, the key_phrase card anchors to the memorable phrase. Only split when both elements are genuinely strong independently — do not split weak content.
- Card startSec/endSec must be within [0, {trimmed_duration:.1f}]
- Cards should NOT overlap each other in time
- Most cards should last 3-8 seconds
- "question" cards may last up to 15s (they stay while the speaker answers)
- "timeline" cards: set endSec to AFTER the speaker finishes narrating
  the LAST step — use the beat spine timestamps to find when the final
  step's words end, then set endSec = that timestamp + 1s. Up to 20s.
- Vary accentIndex (0-4) across cards for visual rhythm
- Content must come from what the speaker actually says
- CONTENT STYLE RULES (follow strictly, do not improvise):
  "list" — speaker names 3+ distinct items, reasons, examples, or ideas
    as a PARALLEL SET meant to be read as a complete group. Use when items
    are interchangeable in position and none depends on the prior.
    NOT for sequential process steps that build on each other → use timeline.
    NOT for required materials/prerequisites → use ingredient_list.
    NOT for completed actions → use checklist.
  "timeline" — speaker narrates a PROCESS FLOW or CHRONOLOGICAL SEQUENCE
    where each step leads to or follows the next ("d'abord X, ensuite Y, puis Z"
    OR "en 2020 X, en 2021 Y, en 2022 Z" as a continuous narrative arc).
    PRIORITY over list whenever sequence/order is essential to meaning.
    DISTINCTION FROM milestone_recap: timeline is a CONTINUOUS PROGRESSION
    (process, journey, narrative arc); milestone_recap is a RETROSPECTIVE
    listing of discrete past ACHIEVEMENTS anchored by year/date (speaker looks
    back on isolated milestones). Rule: year-anchored past achievements → use
    milestone_recap. Ordered steps / narrative flow → use timeline.
    DISTINCTION FROM list: list items are parallel and interchangeable;
    timeline items have strict before/after dependency (order cannot be reversed).
    IMPLICIT CAUSAL SEQUENCES: Also triggers when the speaker recounts a chain
    of past events in causal order WITHOUT explicit "d'abord/ensuite/puis"
    markers — detectable when each clause in past tense directly CAUSES or
    ENABLES the next ("j'avais investi dans X → c'est comme ça qu'on s'est
    rencontré → en l'observant j'ai réalisé → on en est venu à créer Y").
    Criterion: would reversing two steps break the story? If yes → timeline.
    DISTINCTION FROM cause_effect: cause_effect describes a SINGLE BINARY
      relationship — exactly ONE cause and ONE effect (A → B, two elements).
      timeline is for 3+ ordered steps forming a full chain or progression.
      DECISIVE TEST: count the distinct elements in the sequence.
      If 2 elements (one cause, one effect) → cause_effect.
      If 3+ elements forming a chain → timeline, even if each step causes the next.
    DISTINCTION FROM prim_split_stage: When the speaker presents 2–5 steps
      as an explicitly named METHOD, FRAMEWORK, or PROCESS the viewer should
      memorize and reuse, prim_split_stage TAKES PRIORITY over timeline.
      Decisive markers for prim_split_stage: "ma méthode", "le process", "les
      étapes de X", "les piliers", "la structure", "la formule", "voilà comment
      je fais / comment ça marche" + a short labelable list of 2-5 items.
      timeline = what happened in chronological / narrative order (story).
      prim_split_stage = framework the viewer should learn and apply.
      Rule: if you can reframe the sequence as "MA MÉTHODE : étape 1, 2, 3"
      without changing its meaning → use prim_split_stage, NOT timeline.
    Provide "steps" array (2-6 items).
  "comparison" — speaker contrasts two distinct things (old/new, us/them,
    method A vs method B). Exactly 2 sides required. NOT for the same
    thing before vs after a change (use before_after_image for that).
    ABSOLUTE EXCLUSION: NEVER use when the two sides are GEOGRAPHIC LOCATIONS
    (cities, countries, regions) connected by travel or relocation. That is
    prim_journey_map. Example wrong: "AVANT: Paris / APRÈS: Thaïlande" on a
    relocation story → use prim_journey_map instead.
  "stat" — a specific number or metric cited as EVIDENCE, CONTEXT, or
    SUPPORTING DATA that illustrates a point (e.g. "le taux d'abandon est à
    72%", "93% du contenu viral contient un visage", "en moyenne 4x plus
    de portée"). The number backs up an argument; it is not the headline.
    NOT prim_stat_counter: when the number is the speaker's OWN result or
    achievement AND a structural reveal signal is present → use prim_stat_counter.
  "prim_stat_counter" — animated count-up card. Use when BOTH conditions hold:
    CONDITION 1 — PERSONAL METRIC: the number is the speaker's OWN result,
      achievement, or metric (not a general/cited statistic from an external source).
    CONDITION 2 — STRUCTURAL REVEAL SIGNAL (at least one must be present):
      a. BREAK before the number: ellipsis, dramatic colon, or suspense phrasing
         immediately before the number ("Le mois de novembre... 43 000 euros",
         "le résultat : 8,7%", "et là, 46 millions").
      b. ANNOUNCEMENT VERB: "vient d'atteindre", "a franchi", "a fait", "a généré",
         "je vais te dire exactement ce que j'ai gagné".
      c. DRAMATIC QUALIFIER after the number: a clause that amplifies the scale or
         impact immediately after the number ("— le meilleur résultat depuis le
         lancement", "en un seul mois", "en 30 jours seulement", "je n'en revenais
         pas").
      d. DRAMATIC REPETITION: the number is echoed or spelled out ("je répète :
         cinq cent mille euros").
    If neither condition 2a/b/c/d is present → use stat instead.
    EXCLUSIONS — do NOT use prim_stat_counter when:
      — The number is income or revenue → income_reveal takes priority.
      — All three number_hero conditions hold (singular dominance + personal result
        + climactic placement) → number_hero takes priority.
    NUMBER FORMATTING RULES (strict — no exceptions):
    • Always split the magnitude into number + suffix. NEVER put the unit
      inside number. Always use decimal point "." not comma.
    • Millions   → suffix "M"  : "1 million" → number="1", suffix="M"
                                  "46,2 millions d'euros" → number="46.2", suffix="M€"
    • Thousands  → suffix "K"  : "500 000" or "500 000 euros" → number="500", suffix="K€"
                                  Always abbreviate to K when ≥ 100 000; never leave raw 6-digit number.
    • Milliards  → suffix "Md" : "2,3 milliards" → number="2.3", suffix="Md"
                                  "2,3 milliards d'euros" → number="2.3", suffix="Md€"
    • Percentages → suffix "%" : "87 %" → number="87", suffix="%"
    • Dollars (before number) → prefix="$", suffix="" or suffix="M"/"K"
    • Convention FR: currency symbol always in suffix, prefix always empty for euros.
    Fields: "number" (mandatory, numeric string), "title" (mandatory, kicker label),
    "suffix" (recommended), "prefix" (optional, USD only).
    Zone: upper-right. Duration: 1.2–1.8 s.
    Do NOT use for percentages inside a comparative sentence (use "stat" or "comparison")
    or for lists of numbers.
  "prim_split_compare" — fullscreen animated split card: two panels slide from opposite
    edges to a central divider. STRICT TRIGGER — use ONLY for CONCEPTUAL oppositions:
    rich vs poor, before vs after a non-geographic state change (mindset, status, method),
    X vs Y qualitative contrast.
    ABSOLUTE EXCLUSION — NEVER use if ANY of the following apply:
      (a) Either label contains or quotes a number, currency, percentage, or revenue amount
          (e.g. "500€", "+12 000€/mois", "+47%", "0 → 500K") → use before_after_image instead.
      (b) Speaker mentions two geographic locations connected by travel → prim_journey_map.
    Test: if either left_label or right_label IS or CONTAINS a number/amount/metric
    → before_after_image, not prim_split_compare.
    Provide "left_label" (e.g. "AVANT") and "right_label" (e.g. "APRÈS"). No left_value/right_value.
    Optional "title" for a kicker above the card. Full-cover (fullscreen), duration 2.0–2.5s.
  "prim_journey_map" — fullscreen flight-tracker card. Bezier arc animates from departure to
    arrival city; trail draws progressively with glow; plane chevron follows with auto-rotation.
    TRIGGER PATTERN (ALL of these must be present):
      1. Movement verb — partir, s'envoler, décoller, atterrir, déménager, s'installer,
         voyager, traverser, rallier, rejoindre, quitter [destination], fly, travel, move, relocate
      2. TWO identifiable geographic places (cities, countries, regions)
      3. Directionality — the speaker goes FROM one TO the other
    PRIORITY RULE: prim_journey_map WINS over comparison, prim_split_compare, and
    location_journey whenever a movement verb + two geographic places are present.
    Even if the sentence also implies a life before/after contrast ("ça a tout changé"),
    the geographic-movement pattern takes absolute priority.
    WRONG: Paris + Thaïlande as "AVANT/APRÈS" labels → this is prim_journey_map, not prim_split_compare.
    RIGHT: "j'ai tout quitté pour aller m'installer en Thaïlande depuis Paris" → prim_journey_map.
    Provide "from_city" (REQUIRED), "to_city" (REQUIRED).
    Optional: "from_country", "to_country", "distance_km".
    Full-cover (fullscreen overlay), duration 3.5–6.0s.
  "key_phrase" — a transferable PRINCIPLE or insight the speaker states as a
    standalone truth with pedagogical intent (e.g. "la régularité bat le
    talent", "vends la transformation pas le produit"). Speaker is sharing
    a lesson meant to be adopted by the viewer. TRIGGER: reads as a principle
    that could stand as a lesson title or inspirational poster caption.
    ALSO TRIGGERS on contrastive business insights that name what something
    does vs. does not do ("tu crées du contenu et donnes de la valeur SANS
    jamais vendre", "la vente te PERMET de capturer la valeur", "ce n'est
    pas X qui compte, c'est Y", "X ne suffit pas — il faut Y"). If a
    contrast has two explicitly NAMED sides → use versus_battle instead.
    Distinct from quote (quote is a personal declaration specific to the
    speaker's experience; key_phrase is a universal principle).
    MANDATORY QUALITY GATE — all three must be true before creating this card:
    (a) COMPLETE THOUGHT: the phrase makes sense in total isolation, without
        any surrounding context. A subordinate clause is never a complete
        thought ("pour créer de la richesse" ✗, "afin de réussir" ✗).
    (b) SPECIFIC CONTENT: the phrase states WHAT or HOW, not merely the topic.
        "la richesse vient d'un système qui crée et capture de la valeur" ✓
        "pour créer de la richesse" ✗ — topic announcement, no actual claim.
    (c) MEMORABLE AS-IS: a viewer seeing only this phrase (no video context)
        immediately understands the lesson. Could stand on an inspirational
        poster unchanged.
    DISQUALIFIERS — do NOT create a key_phrase card for:
    — Purpose clauses: "pour [infinitive]…", "afin de…"
    — Topic announcements: "voici comment", "je vais vous expliquer", "parlons de"
    — Paraphrases of common beliefs the speaker is about to refute:
      "beaucoup pensent que…", "la plupart croient que…"
    DIVERSITY WARNING: key_phrase must not become the default escape hatch for
    all well-phrased moments. If more than 2 of every 5 cards placed so far
    are key_phrase, stop — those remaining moments should be: callout
    (conceptual anchor), quote (personal declaration), contrarian_take
    (challenged assumption), or cause_effect (narrative causal link).
  "quote" — a memorable first-person DECLARATION or observation specific to
    the speaker's personal experience or perspective — something lived, not
    taught (e.g. "ce jour-là m'a changé pour toujours", "je n'aurais jamais
    cru que c'était possible", "c'était la décision la plus difficile de
    ma vie"). The speaker is recounting or asserting something personal, not
    extracting a universal principle.
    DISCRIMINATION TEST: "is this a transferable lesson anyone could adopt?" →
    key_phrase. "is this a personal statement tied to the speaker's own story
    or moment?" → quote.
    NOT attributed_quote (attributed_quote has a named external source).
  "attributed_quote" — quote with a named source ("X said...").
  "carousel" — 2-4 short, self-contained statements that CYCLE VISUALLY
    within one card window as individual rotating slides — each item appears
    alone on its own slide in sequence.
    TRIGGER: numbered tips, quick insights, or related points each short enough
    to fill one slide in isolation (e.g. "Conseil 1 : ... Conseil 2 : ...
    Conseil 3 : ..."). Use carousel when each item naturally stands alone on
    its own slide.
    DISTINCTION FROM list: list renders all items simultaneously as a visible
    stacked group; carousel displays items one at a time in rotation. If the
    items are numbered quick tips → carousel. If they form a complete set meant
    to be read all at once → list. Do NOT use for items with a causal chain or
    chronological sequence → use timeline instead.
    Provide "slides" array (2-4 items, each ≤ 12 words).
  "instagram-follow" — speaker explicitly directs viewers to their Instagram
    profile with a follow or join CTA. REQUIRES: the word "Instagram" AND a
    follow/subscribe action ("suis-moi", "abonne-toi", "rejoins", "lien dans
    la bio", "follow"). Distinct from action_step_cta (action_step_cta is a
    generic imperative; instagram-follow is platform-branded). Provide "title"
    (handle, e.g. "@moncompte") + optional "detail".
  "tiktok-follow" — speaker explicitly directs viewers to their TikTok profile
    with a follow CTA. REQUIRES: the word "TikTok" AND a follow action
    ("abonne-toi sur TikTok", "follow moi sur TikTok"). Distinct from
    action_step_cta and instagram-follow. Provide "title" (handle) + optional "detail".
  "yt-lower-third" — speaker asks viewers to subscribe to their YouTube channel,
    activate bell notifications, or like the video. TRIGGER: subscribe/cloche/
    pouce-bleu directive in a YouTube video context — does NOT require the word
    "YouTube" to be literally spoken if the context is clearly a YouTube channel.
    Distinct from action_step_cta (that is a generic content CTA, not a
    platform-branded subscribe widget). Provide "title" (channel handle or name).
  "news_ticker" — speaker delivers an URGENT, breaking-news style announcement
    or time-sensitive alert. TRIGGER: explicit urgency markers ("breaking",
    "alerte", "dernière minute", "urgent", "c'est officiel", "flash") OR a
    statement formatted like a broadcast headline. Distinct from callout
    (callout is neutral context; news_ticker has explicit urgency/alert energy
    and a ticker visual). Provide "title" (the headline or alert text).
  "definition" — speaker introduces a term, concept, or everyday word and
    explains what it means. Applies to BOTH business jargon (SEO, niche,
    churn) AND everyday concepts redefined in context (e.g. "un service,
    c'est quand tu utilises tes muscles et ton temps pour donner de la
    valeur"). TRIGGER PATTERNS (FR + EN): "un X, c'est [quand / le fait
    de / l'art de / la capacité de]", "X, c'est en gros / simplement /
    littéralement", "par X j'entends", "quand je dis X, je veux dire",
    "ce qu'on appelle X c'est", "the definition of X is", "X means".
    Provide "term" + "definition" fields.
  "checklist" — completed/verified action items ("things I checked",
    "requirements met"). Use checklist, not list, when items imply
    done/verified status. Use "items" array.
  "score" — a competitive score, ranking, or leaderboard position (e.g.
    "3-1", "Top 5", "ranked #2"). Use when the number reflects a
    competition or relative standing. NOT stat (stat is a raw metric).
    NOT rating (rating is the speaker's own subjective assessment on a
    scale). Provide "score_text".
  "mindmap" — a central concept with 2-3 branching related ideas.
    Provide "center" + "branches" array.
  "callout" — the speaker pauses to ensure the viewer grasps a building
    block that makes what follows easier to understand. Two functional
    signatures: (1) PARENTHETICAL ASIDE — speaker interrupts to add a
    clarification or caveat ("note importante", "petite précision", "juste
    pour être clair ici", "quick note here"); (2) CONCEPTUAL ANCHOR —
    speaker states a model, rule, or mechanism the viewer must hold in
    mind to follow the next section ("le principe clé c'est", "si tu
    retiens une chose de cette partie", "comprends bien ce mécanisme",
    "et c'est là que tout s'explique").
    FUNCTION TEST: would removing this sentence make the next 30 seconds
    harder to follow? If yes → callout. If the statement stands alone and
    the speaker moves on without depending on it → consider key_phrase.
    DISCRIMINATORS:
    - vs key_phrase: key_phrase stands alone as a transferable lesson;
      callout is conceptual glue that holds the surrounding explanation.
    - vs secret_reveal: secret_reveal reveals surprising information the
      viewer never would have found independently; callout explains a
      mechanism the viewer NEEDS to follow the argument.
    - vs stat: when a number or ratio is cited as a working model or
      allocation principle ("80% de tes résultats viennent de 20% de tes
      efforts" as a rule for how to prioritise) rather than as a factual
      data point about a specific real-world scenario → use callout, not stat.
  "dialogue" — speaker recounts an exchange between two people.
  "trend" — speaker describes a directional change (growth/decline).
  "question" — speaker poses a question and then answers it.
  "rating" — speaker gives their OWN personal assessment on a scale
    (e.g. "I'd give this 8 out of 10", "je lui mets 9/10"). Provide
    "rating_value" + "rating_max". NOT stat (stat is a raw metric).
    NOT score (score is competitive rankings). NOT star_rating_review
    (that is a third-party review with star count, not the speaker's own
    live assessment).
  "map_location" — speaker references a specific geographic location.
    Provide "location_name" + optional "location_context".
  "progress_bar" — speaker describes a percentage, completion level,
    or how far along something is ("we're 70% there"). Provide
    "progress_percent" (0-100 integer) + "progress_label". NOT stat.
  "before_after_image" — speaker describes how ONE thing transformed
    (same entity, two points in time: "avant / après"). Use for
    transformations of a single subject. NOT for two different things
    side by side (use comparison for that). Provide "before_label" +
    "after_label".
  "countdown" — speaker counts DOWN from a number (urgency, steps
    remaining, limited time). Numbers DECREASE. NOT stat. Provide
    "countdown_from" (integer) + "countdown_label".
  "poll_question" — speaker poses a question WITH explicit
    multiple-choice options. Distinct from question (question has no
    options). Provide "poll_question" + "poll_options" array (2-4).
  "myth_vs_fact" — speaker calmly debunks a myth and states the corrected fact.
    Distinct from callout (callout adds context, not a correction).
    DISTINCTION FROM prim_shatter_truth: myth_vs_fact is the measured, informational
      version — the speaker corrects a false belief without high confrontational energy.
      For a DRAMATIC, emphatic destruction of a false belief (electric "Faux." energy,
      the speaker attacks the belief head-on) → use prim_shatter_truth instead.
    Provide "myth_text" + "fact_text".
  "step_number" — speaker highlights a single focal step, phase, or
    pivotal narrative moment (e.g. "step 1", "première chose",
    "moment charnière", "c'est là que tout a changé"). Use when the
    speaker wants to emphasize ONE moment or action in isolation.
    Distinct from timeline (timeline shows the full sequence; step_number
    is a single spotlight). Distinct from roadmap_milestone (milestone is
    a past achievement reached in an ongoing journey; step_number is an
    active focal emphasis, numbered or not). Provide "step_num" (can be
    a label like "01", "Clé n°1", or "?" if unnamed) + optional
    "step_label". NOT versus_battle (versus_battle requires two named
    opposing sides; step_number has only ONE focal subject).
  "quote_carousel" — speaker delivers 2-4 short quotes or phrases in
    rapid succession that should cycle visually. Distinct from carousel
    (carousel is for varied tips/content); use quote_carousel only for
    multiple pure quotes. Distinct from attributed_quote (attributed_quote
    is one quote + source). Provide "quotes" array.
  "emoji_reaction" — speaker expresses a strong reaction, emotion, or
    exclamation (hype, celebration, surprise, emphasis). Shows as a large
    bold callout — no emoji glyph, text only. Provide "emoji_label" (a
    short punchy phrase capturing the reaction, e.g. "On s'emballe !",
    "C'est incroyable", "Voilà le résultat"). Do NOT provide an "emoji"
    field. Distinct from key_phrase (key_phrase is a neutral statement;
    emoji_reaction is an exclamatory reaction moment).
  "price_tag" — speaker mentions a specific price point or cost. Provide
    "price" string + optional "price_context".
  "warning_soft" — speaker signals a caution, trap, or common mistake using
    EXPLICIT WARNING LANGUAGE (soft register — not a crisis, no alarm). The
    speaker alerts the viewer to avoid or be careful of something.
    TRIGGER: at least one explicit warning marker must be present:
      FR: "attention", "méfie-toi", "évite", "piège", "danger", "risque",
        "erreur classique", "ne fais pas ça", "à ne surtout pas faire",
        "beaucoup font l'erreur de", "le piège c'est", "garde-toi de",
        "sois prudent avec", "ce que les gens ratent", "le problème c'est que"
      EN: "warning", "watch out", "avoid", "trap", "mistake", "be careful"
    DISTINCTION FROM contrarian_take: contrarian_take challenges a widespread BELIEF
      or conventional wisdom (provocative stance); warning_soft alerts about a
      concrete RISK or MISTAKE to avoid (practical caution, not a belief battle).
    DISTINCTION FROM mistake_lesson: mistake_lesson tells a PAST story of a mistake
      the speaker MADE and learned from (narrative, retrospective). warning_soft is a
      FORWARD-LOOKING alert to the viewer — avoid this, don't do that.
    DISTINCTION FROM callout: callout is neutral context-setting. warning_soft has
      explicit cautionary register and an action the viewer must avoid.
    Provide "warning_text".
  "testimonial" — speaker quotes a customer, client, or user with their
    name and role context. Distinct from attributed_quote (attributed_quote
    is for public figures or named sources); testimonial is for end-user
    social proof with role context. Distinct from star_rating_review
    (star_rating_review requires a star count; testimonial does not).
    Provide "testimonial_text" + "person_name" + "person_role".
  "versus_battle" — speaker explicitly pits TWO named opponents, options,
    or philosophies against each other ("employé VS freelance", "X contre
    Y"). REQUIRES both sides to be explicitly named by the speaker — do
    NOT use for a single dramatic moment, pivotal event, or turning point
    (use step_number or roadmap_milestone for those). Do NOT use when the
    speaker is only describing one thing dramatically. More dynamic than
    comparison (comparison is sober data; versus_battle has a VS badge).
    Provide "side_a" + "side_b" — both must come directly from the
    speaker's words, not be invented.
  "recap_summary" — speaker does a structured recap or summary of key
    points (e.g. "three things to remember"). Distinct from list (list is
    ad-hoc; recap_summary has a "what we covered" narrative feel). Provide
    "recap_items" list (2-5 bullet strings).
  "location_journey" — speaker describes a geographic or spatial journey
    between multiple places. Distinct from timeline (timeline is temporal
    event sequence; location_journey is spatial/geographic). Provide
    "journey_points" list (2-5 location names).
  "formula_equation" — speaker presents a formula, equation, or
    mathematical relationship. Use when parts are connected by operators
    (×, ÷, +, =, →). Provide "formula_parts" list alternating terms and
    operators (e.g. ["Temps", "×", "Effort", "=", "Résultat"]).
  "roadmap_milestone" — speaker celebrates a concrete past achievement
    reached in an ongoing journey (e.g. "on a atteint 1 000 abonnés",
    "après 6 mois on a signé notre premier client"). Use when the
    milestone is a COMPLETED checkpoint in a longer progression. Distinct
    from step_number (step_number is active focal emphasis on one thing,
    numbered or not; roadmap_milestone is a completed achievement in a
    journey). Distinct from timeline (timeline shows the full sequence).
    Provide "milestone_label" + "milestone_context".
  "pros_cons" — speaker explicitly lists ADVANTAGES and DRAWBACKS of the
    SAME subject ("les avantages et inconvénients de X"). Distinct from
    comparison (comparison contrasts two different things; pros_cons
    evaluates one thing from two angles). Distinct from versus_battle
    (versus_battle pits two named opponents against each other; pros_cons
    evaluates a single subject). Provide "pros" list + "cons" list
    (2-4 items each).
  "star_rating_review" — speaker cites a THIRD-PARTY review that includes
    an explicit star count ("4 étoiles sur 5", "rated 4.8/5"). Distinct
    from testimonial (testimonial has no star count; use testimonial when
    only a quote and name are present). Distinct from rating (rating is
    the SPEAKER's own live assessment, not a cited review). Provide
    "stars" (int 0-5), "review_text", "reviewer_name".
  "income_reveal" — speaker dramatically reveals a personal INCOME, REVENUE, or
    FINANCIAL EARNINGS figure (their own money: salary, monthly revenue, passive
    income, business CA). Has suspense/reveal energy — the number lands like a
    punchline. Distinct from stat (stat is third-party data; income_reveal is
    the speaker's OWN financial result with emotional weight).
    DISTINCTION FROM prim_stat_counter: income_reveal is SPECIFIC TO MONEY EARNED
      by the speaker (€/mois, CA, salaire, revenus passifs, résultat financier).
      prim_stat_counter covers ALL OTHER personal metrics (subscriber count, client
      count, conversion rate, non-financial achievements). Rule: if the number
      represents income or revenue → income_reveal, not prim_stat_counter, even
      when a structural reveal signal is present.
    Provide "income_value" (the number string) + "income_context" (brief qualifier).
  "number_hero" — the SINGLE most important number in the entire video, given
    full visual staging: centered spotlight, mirror accent lines above and below
    the number, cinematic 3-act reveal animation.
    BUDGET: ONE per video maximum. If several numbers compete, use stat or
    prim_stat_counter for the others; reserve number_hero for the one number
    the speaker would put on a book cover.
    TRIGGER — all three must hold simultaneously:
      (a) SINGULAR DOMINANCE: this number is the credibility anchor of the whole video.
      (b) PERSONAL RESULT: the speaker's own achievement or a direct client outcome.
      (c) CLIMACTIC PLACEMENT: the speaker treats it as a peak moment — dramatic pause,
          "voilà le résultat", "ce que ça m'a rapporté", not a passing figure in a list.
    DISTINCTION FROM income_reveal: income_reveal is a blur-reveal text card (no spotlight,
      no mirror lines). number_hero is a full visual spectacle — use income_reveal when
      the reveal energy is present but the number is not the dominant climax of the video.
    OVERRIDE RULE: when ALL THREE trigger conditions hold simultaneously, number_hero
      takes priority over prim_stat_counter, income_reveal, and stat — even when the
      number would also qualify for those styles. Decisive test: if you removed this
      single number from the video, does the video lose its central credibility proof?
      If YES → number_hero. If no → prim_stat_counter or income_reveal.
    DISTINCTION FROM prim_stat_counter: prim_stat_counter renders as a count-up badge
      in the UPPER-RIGHT CORNER (zone: upper-right). number_hero gives the number the
      ENTIRE SCREEN with spotlight + mirror accent lines + 3-act cinematic reveal
      (zone: fullscreen). When all three trigger conditions are met → number_hero wins;
      prim_stat_counter is for every other personal metric that doesn't qualify.
    DISTINCTION FROM income_reveal: income_reveal is a blur-reveal text card for any
      personal income/revenue figure. number_hero is for the ONE dominant climax number
      of the entire video regardless of whether it is income or any other metric.
    DISTINCTION FROM stat: stat is informational data without personal climactic energy.
    Provide "nh_number" (display string, e.g. "12 000 €/mois") +
    "nh_kicker" (e.g. "RÉSULTAT DU MOIS") + "nh_detail" (e.g. "en affiliation organique").
    Zone: fullscreen. Duration: 2.0–3.0s.
  "prim_cinematic_reveal" — the single most important PHRASE of the entire video,
    staged as a full-screen cinematic multi-layer depth reveal on a pure black canvas.
    Three layers enter with distinct premium easings (sine.inOut / power3.out / expo.out)
    at staggered offsets — no filter:blur, depth created purely via 3D transforms.
    BUDGET: HARD LIMIT — exactly ONE of {{prim_cinematic_reveal, prim_ascension_reveal}}
      per video, no exceptions. These two primitives share a single "climax slot".
      If multiple moments seem to qualify, pick the single strongest by these criteria
      (in priority order):
        1. Climactic placement — the moment where the video's full argument converges
        2. Highest declarative conviction — absolute truth, creed-level claim, not a tip
        3. Best stand-alone power — phrase is self-contained, needs no context, ≤60 chars
      Tiebreak between hook and payoff: ALWAYS prefer the PAYOFF — it lands with the
      accumulated weight of everything the video has built. Only choose the hook when
      its phrase is demonstrably stronger than any payoff moment in the script.
      NEVER generate two cards from {{prim_cinematic_reveal, prim_ascension_reveal}} in the
      same video. If both a conviction phrase AND a result phrase compete for this slot,
      compare their climactic weight by the criteria above.
    PLACEMENT — two valid contexts (choose based on script structure, not position):
      OPTION A — STRONG HOOK (typically 0–30s): the speaker opens with the video's
        core thesis as a direct declaration — bold, quotable, self-contained. The
        viewer grasps the full promise from this one phrase alone without any build-up.
        Hook signals: "ce que j'ai mis X ans à comprendre", "la seule chose qui change
        vraiment tout", "si tu ne retiens qu'une chose de cette vidéo", "la vérité que
        personne ne dit", "je vais te donner la seule chose qui a vraiment changé mon
        parcours", phrasing where the THESIS itself is the hook, not a description of it.
        NOT a valid hook: generic open questions ("vous allez voir pourquoi…") or pure
        content announcements ("je vais vous montrer comment…") — those are callout.
      OPTION B — PAYOFF MOMENT (typically 60–90% through the video): the culminating
        revelation the entire narrative was building toward. The speaker delivers their
        definitive, quotable conviction after the evidence has been fully laid out.
        Payoff signals: "voilà ce qui a tout changé", "et là j'ai compris", "le résultat ?",
        "si je devais résumer tout ça en une phrase", "c'est ça qui change tout",
        "en fin de compte", "tout repose sur [X]", "le vrai levier c'est [X]",
        conclusion of a transformation arc that ends in a first-person creed or
        a universal truth stated with personal conviction after lived evidence.
        NOT a valid payoff: an intermediate lesson, a tip within a sequence,
        or a general principle without revelation energy → key_phrase instead.
    TRIGGER — all FOUR must hold simultaneously:
      (a) PINNACLE STATEMENT: this phrase is THE one takeaway the speaker wants
          imprinted. It functions as the video's tagline or thesis, not a tip
          or intermediate observation.
      (b) DECLARATIVE CONVICTION: speaker delivers it as an absolute truth,
          a personal creed, or a transformational declaration — "tout repose
          sur la confiance", "c'est ça qui change tout", "le vrai levier c'est X".
      (c) STAND-ALONE POWER: phrase works as full-screen text with no chart or
          stat needed. Maximum 60 characters.
      (d) EXPLICIT LINGUISTIC MARKER (at least one MUST be present in the phrase
          itself — this is the hard discriminator against key_phrase):
          Revelation/payoff markers (FR):
            "voilà ce qui a (tout) changé", "c'est ça qui a tout changé",
            "et là j'ai compris / réalisé", "j'ai compris ce jour-là",
            "le résultat :", "en fin de compte", "après tout ce chemin",
            "si je devais résumer tout ça en une phrase",
            "tout repose sur [X]", "le vrai levier c'est [X]",
            "ma conviction (profonde / après X ans)", "voilà ce que ça m'a appris",
            "ce que j'ai mis X ans à comprendre", "le levier final",
            "résultat de X ans", "j'ai tout compris", "au bout de X ans",
            "voilà la vérité après tout ça", "une seule conviction"
          Hook markers (when used as a thesis in the opening):
            "si tu ne retiens qu'une chose", "la seule chose / vérité qui change vraiment",
            "je vais te partager la conviction qui a tout changé",
            "ce que j'aurais voulu qu'on me dise au départ",
            "je vais te donner la seule chose qui a vraiment changé mon parcours",
            "la conviction qui a tout changé"
          WITHOUT at least one of these markers → use key_phrase instead of
          prim_cinematic_reveal, even if the phrase is very strong.
          OVERRIDE RULE: when an explicit marker IS present, prim_cinematic_reveal
          takes priority over key_phrase even if the underlying principle is transferable.
    DISQUALIFIERS — do NOT use prim_cinematic_reveal for:
      — Questions or teasers: "vous allez comprendre pourquoi…", "saviez-vous que…"
        (→ question or callout instead)
      — Content announcements without a conviction attached: "je vais vous montrer
        comment…", "voici la méthode en 3 étapes" (→ callout)
      — Tips within a sequence: "conseil n°2", "voici ce qu'il faut faire…" (→ key_phrase)
      — Number-anchored climaxes: phrase contains or requires a figure to land
        (→ number_hero or prim_stat_counter)
      — Structural transitions without personal conviction: "passons à la suite"
        (→ chapter_marker or story_chapter_transition)
    DISTINCTION FROM key_phrase: key_phrase is a recurring pedagogical principle that
      can appear 3-5× per video; prim_cinematic_reveal is the ONE capstone moment —
      the thesis the whole video serves. Decisive test: if removing this one card left
      the viewer without the video's central message → prim_cinematic_reveal. If other
      key_phrases already cover that core message → this is just another key_phrase.
    DISTINCTION FROM number_hero: number_hero is for the climactic NUMBER;
      prim_cinematic_reveal is for the climactic PHRASE with no figure required.
    DISTINCTION FROM chapter_marker: chapter_marker introduces a section;
      prim_cinematic_reveal delivers the video's ultimate manifesto.
    Provide "title" (REQUIRED — the phrase, max 60 chars),
      "kicker" (optional — eyebrow label, e.g. "MA CONVICTION", max 30 chars),
      "detail" (optional — one-line amplifier below the title).
    Zone: fullscreen. Duration: 2.0–3.5s.
  "prim_ascension_reveal" — the single most important RESULT PHRASE in the entire
    video: a statement where the speaker's proven result — expressed as a concrete
    figure embedded in a full phrase — functions as the climactic proof. Staged as
    a full-screen depth reveal with a 5-layer choreography: halo glow, horizon line,
    3D title entry (back.out overshoot), ring pulse, kicker. No filter:blur.
    BUDGET: SHARED with prim_cinematic_reveal — exactly ONE of these two per video.
      NEVER generate prim_ascension_reveal if prim_cinematic_reveal is already used.
    TRIGGER — all FOUR must hold simultaneously:
      (a) RESULT ANCHOR: the phrase contains a concrete figure (client count, revenue
          amount, percentage, timeframe) that is the climactic proof of the video.
          The number is embedded inside a full sentence — it is the anchor, not the
          whole message. "j'ai accompagné 47 entrepreneurs vers leur 1er 10k€/mois"
          is PAR; "12 000 €" alone is number_hero.
      (b) SOCIAL PROOF / RESULT ENERGY: this is the culminating revelation of what the
          speaker has concretely achieved or enabled — the "voilà ce que ça a produit"
          of the video. The phrase radiates accomplished fact, not theoretical principle.
      (c) PHRASE FORMAT: the figure is embedded in a longer statement (subject + verb +
          result). Maximum 60 characters total.
      (d) EXPLICIT RESULT MARKER (at least one must be present in the phrase or its
          immediate context — this is the hard discriminator against key_phrase):
          Social proof / result markers (FR):
            "j'ai accompagné X", "X clients / entrepreneurs / personnes ont…",
            "X% de mes clients ont atteint", "le bilan : X",
            "voilà ce que ça a donné concrètement", "au total X",
            "depuis le lancement : X", "le chiffre que j'ai atteint",
            "résultat : X [unité]", "X en [durée]",
            "j'ai généré X", "X abonnés / ventes / euros en [durée]",
            "le taux de réussite : X%", "X succès sur Y tentatives"
    DISQUALIFIERS — do NOT use prim_ascension_reveal for:
      — Conviction/manifesto phrases without a figure → prim_cinematic_reveal
      — Single standalone number as the hero → number_hero
      — Count-up animation reveal → prim_stat_counter
      — Intermediate lesson or data point without climax energy → stat or key_phrase
    DISTINCTION FROM prim_cinematic_reveal: PCR is for pure conviction ("tout repose
      sur la confiance", "c'est ça qui change tout") — no figure required or central.
      PAR is for conviction anchored in a concrete proven result figure. When both a
      conviction phrase and a result phrase exist, compare climactic weight: the one
      that delivers the video's ULTIMATE message wins the shared climax slot.
    DISTINCTION FROM number_hero: number_hero spotlights a SINGLE number on a visual
      spotlight (e.g. "12 000 €"). PAR reveals a FULL SENTENCE where the figure is
      the proof inside a phrase (e.g. "j'ai accompagné 47 entrepreneurs…").
    DISTINCTION FROM prim_stat_counter: prim_stat_counter animates a count-up for any
      speaker metric; PAR is a static depth reveal for the ultimate result phrase.
    Provide "title" (REQUIRED — the result phrase, max 60 chars),
      "kicker" (optional — eyebrow label, e.g. "LE BILAN", "RÉSULTAT FINAL", max 30 chars).
    Zone: fullscreen. Duration: 2.0–3.5s.
  "prim_shatter_truth" — the ONE full-screen confrontation card where the speaker actively
    DESTROYS a false belief held by the audience. The myth text appears, micro-vibrates,
    then shatters into fragments with a white flash — the truth imposes itself on a pure
    black canvas. No filter:blur, depth via transforms only.
    BUDGET: INDEPENDENT — exactly 1 per video. Does NOT consume the climax slot
      (prim_cinematic_reveal / prim_ascension_reveal may still be used in the same video).
    TRIGGER — ALL THREE must hold simultaneously:
      (a) FALSE BELIEF TARGET: the speaker names or implies a specific false belief,
          excuse, or myth that the AUDIENCE holds. The myth has a face — it is a belief
          the viewer might currently hold, not a general misconception in the abstract.
          "Tu penses que X → Faux." is the prototypical form.
      (b) CONFRONTATIONAL ENERGY: the speaker actively demolishes it — not a calm
          correction but a decisive, emphatic rejection. The tone is combative, electric.
      (c) EXPLICIT MARKER (at least one must be present in the spoken segment):
          FR: "Faux.", "C'est faux", "c'est un mensonge", "arrêtons de croire que",
            "stop de penser que", "cette idée reçue", "démystifions", "tu te trompes",
            "cette croyance est fausse", "c'est exactement le contraire",
            "il faut déconstruire", "personne ne vous dit que c'est faux mais"
          EN: "False.", "That's wrong", "Stop believing that", "Myth vs reality"
    HARD GATE: if none of the exact expressions from (c) appear verbatim in the
      spoken segment → do NOT use prim_shatter_truth. Weaker expressions such as
      "c'est totalement inexact", "c'est une illusion", "Absolument pas", "Non," alone
      are NOT sufficient — they do not carry the explicit shattering energy required.
      When the marker is absent: use myth_vs_fact (measured correction) or
      contrarian_take (counterintuitive opinion), never prim_shatter_truth.
    DISQUALIFIERS — do NOT use prim_shatter_truth for:
      — Factual corrections without a marker from (c) → myth_vs_fact
      — Opinions or soft nuances → contrarian_take or callout
      — Multiple myths listed at once → red_flag_list or checklist
      — Moments where the speaker acknowledges both sides → versus_battle or comparison
    DISTINCTION FROM myth_vs_fact: myth_vs_fact is the informational version
      (speaker corrects, explains, provides the real fact — measured tone). It applies
      whenever the correction lacks an explicit marker from (c).
      prim_shatter_truth is the DRAMATIC version — confrontational, electric — and
      requires one of the exact markers from (c) to be spoken.
      Rule: if the exact spoken words could be paraphrased as "En réalité, [fact]"
      without losing emotional truth → myth_vs_fact. Only "FAUX. [Truth]" all-caps
      energy with a verbatim marker → prim_shatter_truth.
    Provide "myth_text" (REQUIRED — the false belief being shattered, max 50 chars,
      framed from the audience's perspective: e.g. "travailler dur suffit pour réussir"),
      "truth_text" (REQUIRED — the truth that replaces it, max 60 chars, declarative,
      confident: e.g. "Ce qui compte c'est travailler intelligemment"),
      "kicker" (optional — e.g. "IDÉE REÇUE", "MYTHE #1", max 25 chars).
    Zone: fullscreen. Duration: 2.0–3.0s.
  "prim_split_stage" — the speaker's face shrinks to 50% and slides to one side of the
    screen while the opposite half reveals structured content: numbered steps
    (mode='steps') or an icon+label vertical diagram (mode='diagram'). The speaker
    remains visible throughout — no blackout. Used when the speaker walks through a
    FRAMEWORK, PROCESS, or STRUCTURE the viewer should memorize.
    BUDGET: 1–2 per video. Alternate "side" (left / right) between occurrences.
    PRIORITY OVER timeline: when trigger (a) holds AND the speaker explicitly
      frames the sequence as a method/framework (markers: "ma méthode", "le
      process", "les étapes de", "les piliers", "la formule", "voilà comment
      je fais"), use prim_split_stage INSTEAD OF timeline — even if timeline
      could also match the sequential pattern. timeline is for chronological
      narrative flow (what happened); prim_split_stage is for teachable
      frameworks (what to do / how it works). When in doubt: can the viewer
      apply these steps themselves after watching? If yes → prim_split_stage.
    TRIGGER — at least one must hold:
      (a) PROCESS / WORKFLOW: speaker explains 2–5 sequential or parallel steps
          that form a reusable framework ("voilà les 3 étapes de ma méthode",
          "le process est simple : A, puis B, puis C"). Use mode = 'steps'.
      (b) DIAGRAM / RELATIONSHIP: speaker names 2–4 linked concepts, pillars,
          or components that form a visual structure or chain ("les 3 piliers de X",
          "la relation entre A, B et C"). Use mode = 'diagram'.
    DISQUALIFIERS:
      — Content with >5 steps → checklist or list instead
      — Single isolated point → key_phrase or callout
      — Pure storytelling without a reusable framework → key_phrase or anecdote
    Provide "side" (REQUIRED — 'left' or 'right'; alternate between occurrences
      for visual variety), "mode" (REQUIRED — 'steps' or 'diagram'),
      "kicker" (optional — short eyebrow label, e.g. 'MA MÉTHODE', 'LE PROCESS',
      max 25 chars), "steps" (mode='steps' REQUIRED — list of 2–5 short step strings,
      max ~30 chars each), "nodes" (mode='diagram' REQUIRED — list of 2–4 objects,
      each with "icon" (emoji) and "label" (short text, max 25 chars)).
    Zone: fullscreen. Duration: 3.5–6.0s.
  "prim_confession_frame" — a full-screen fragility reveal for the single personal
    confession moment: the speaker admits a weakness, a doubt, a moment of shame,
    or a personal low point they actually lived through. Staged as a desaturation
    overlay (mix-blend-mode:saturation drains colour) + subtle radial vignette +
    discrete bottom-left text entry + thin accent line that draws slowly left→right.
    Register: intimate and understated — no fanfare, no punchline.
    BUDGET: HARD LIMIT — exactly ONE per video, in its own independent slot.
      The climax slot (prim_cinematic_reveal / prim_ascension_reveal) and the
      confession slot (prim_confession_frame) are DISTINCT — a video CAN contain
      both a prim_cinematic_reveal AND a prim_confession_frame simultaneously,
      because they occupy different emotional registers. Never two prim_confession_frame
      in the same video.
    PLACEMENT — most often mid-video or at the opening of a transformation arc:
      the confession introduces the "before" state, the personal struggle that
      gives the speaker's advice credibility. May also appear near the end as a
      raw honest moment after the main content has been delivered.
    TRIGGER — all FOUR must hold simultaneously:
      (a) PERSONAL FIRST-PERSON: the speaker speaks about themselves specifically —
          "j'ai", "je me suis", "j'avais", "je faisais", "je n'osais pas".
          NOT a general lesson about failure or a hypothetical: "beaucoup de gens
          doutent" or "on traverse tous des périodes difficiles" → key_phrase instead.
      (b) PAST SUFFERING / FRAGILITY: the content names a lived weakness, shame,
          doubt, or difficult personal state — not a challenge overcome with ease,
          not a lesson framed as a tip. The speaker is admitting something difficult,
          not celebrating their resilience after the fact.
      (c) OPENLY ADMITTED: the speaker consciously surfaces something they could have
          hidden — the very act of naming it is the gesture: "j'ai caché", "je
          faisais semblant", "j'avais honte", "j'ai douté", "j'aurais voulu qu'on
          me dise d'arrêter". A vague mention of "a hard period" without naming the
          inner state does not qualify.
      (d) SELF-CONTAINED: the confession names the fragile state clearly enough that
          it works as a standalone card — a viewer reading it alone understands what
          the speaker felt. An incomplete thought or a confession buried mid-sentence
          does not qualify.
    LINGUISTIC MARKERS (at least one MUST be present in the segment):
      "j'ai douté", "j'avais honte", "j'avais peur", "j'étais perdu(e)",
      "je ne savais plus", "je faisais semblant", "j'ai caché", "je n'osais pas",
      "j'aurais voulu qu'on me dise", "j'ai pleuré", "je voulais tout arrêter",
      "j'étais épuisé(e)", "j'avais l'impression de tout rater",
      "j'avais perdu confiance", "je me suis senti(e) seul(e)",
      "j'ai failli tout abandonner", "ce que j'ai caché", "ma pire période",
      "je ne comprenais plus pourquoi"
      WITHOUT at least one of these (or an equivalent direct first-person admission
      of fragility in the speaker's own words) → use key_phrase or anecdote instead.
    DISQUALIFIERS — do NOT use prim_confession_frame for:
      — Lessons framed as takeaways: "ce que j'ai appris de mes échecs, c'est…"
        (focus on the lesson, not the emotional state) → key_phrase
      — Overcome-with-strength framing: "j'ai traversé ça et voilà ce que j'ai fait"
        (focus is on the overcoming, not the suffering itself) → key_phrase
      — General statements about struggle without personal admission: "beaucoup
        d'entrepreneurs vivent ça" → callout or key_phrase
      — Pivot to solution: "j'ai douté, mais j'ai compris que…" — if the sentence
        immediately resolves the doubt into a lesson, the payoff dominates;
        the confession clause alone is too short → key_phrase
      — Future-facing anxiety without a past lived experience: "j'ai peur de…"
        (present fear without the retrospective "I lived through it" dimension)
    CRITICAL DISTINCTION FROM prim_cinematic_reveal:
      prim_cinematic_reveal = strength, manifesto, conviction —
        "voilà ce qui a tout changé", "ma conviction profonde", "le vrai levier c'est X"
      prim_confession_frame = fragility, aveu, personal low point —
        "j'avais honte", "je faisais semblant", "j'ai failli tout arrêter"
      DECISIVE TEST: is the speaker standing tall (certainty, direction, arrival)
        or speaking from a place of inner fragility (doubt, shame, exhaustion)?
        Standing tall → prim_cinematic_reveal. Fragility → prim_confession_frame.
        A phrase CAN contain both a past confession and a current conviction —
        assign to whichever emotion DOMINATES in the segment.
    Provide "confession_text" (REQUIRED — the raw first-person past-tense confession,
      max 80 chars, must contain the speaker's admitted fragile state),
      "kicker" (optional — quiet eyebrow label, max 30 chars, e.g.
      "CE QUE JE N'AI JAMAIS DIT", "MON POINT BAS", "CE QUE J'AI CACHÉ").
    Zone: fullscreen. Duration: 2.5–4.0s.
  "question_answer_pair" — speaker poses a question AND immediately answers
    it in the same breath (e.g. "Qu'est-ce que c'est ? C'est une méthode
    en 3 étapes"). BOTH question and answer are present in the same segment.
    Distinct from question (question leaves the answer to the viewer or to
    a later beat). NOT poll_question (poll is an open vote). Provide
    "qa_question" + "qa_answer".
  "chapter_marker" — speaker introduces a new major section or chapter of
    a longer video. REQUIRES an EXPLICIT STRUCTURAL MARKER: a chapter number,
    section number, part label, or module name must be present or strongly
    implied (e.g. "on passe à la partie 2", "chapitre 3", "section suivante",
    "module 4", "deuxième bloc", "next chapter"). The transition must be
    announced as a named structural unit, not just a topic pivot.
    DISTINCTION FROM story_chapter_transition: story_chapter_transition is a
    narrative beat or pivot without explicit structural labelling ("et là tout
    a changé", "voilà ce qui s'est passé ensuite"). chapter_marker explicitly
    names or numbers the new section. If no section label or number is present,
    use story_chapter_transition instead.
    Distinct from step_number (step_number is a numbered item within a process
    sequence; chapter_marker is a top-level section break in a long video).
    Provide "chapter_num" + "chapter_title".
  "secret_reveal" — speaker reveals a hidden insight, secret, or surprising
    answer after building suspense ("le secret c'est…", "ce que personne ne
    dit c'est…", "la réponse surprenante c'est…"). Requires REVEAL ENERGY —
    the content was withheld then unveiled. Distinct from key_phrase (key_phrase
    is a strong statement without suspense buildup). Provide "secret_text".
  "objection_response" — speaker voices a common objection or pushback and
    then immediately rebutts it ("mais tu vas me dire X… eh bien en réalité Y").
    REQUIRES both the objection AND the speaker's response in the same beat.
    Distinct from myth_vs_fact (myth_vs_fact debunks a common belief;
    objection_response is a direct dialogue rebuttal with first-person objection
    voice). Provide "objection_text" + "response_text".
  "data_bar_chart" — speaker cites MULTIPLE numeric values that directly
    compare to each other (2-4 values with labels), suitable for a bar chart
    visualization. Distinct from stat (stat is a single number). Distinct from
    score (score is competitive ranking). Distinct from trend (trend is a
    directional curve).
    EXCLUSIONS (use these more specific styles instead):
    NOT platform_stats: when the bar labels ARE social media platform names
      (Instagram, TikTok, YouTube, LinkedIn, Facebook, Pinterest, X/Twitter,
      Snapchat, Meta, Google) → use platform_stats.
    NOT revenue_breakdown: when the values represent income or revenue
      broken down by source, product, or stream → use revenue_breakdown.
    data_bar_chart is for GENERIC comparative data (costs, rates, volumes,
    durations, counts) whose labels are NOT platform or revenue-source names.
    Provide "bar_labels" list + "bar_values" list of float (same length, 2-4 items).
  "cause_effect" — speaker explicitly states a SINGLE cause-and-effect
    relationship ("parce que X, donc Y", "X entraîne Y", "si X alors Y").
    REQUIRES exactly two elements: one named cause and one named effect.
    Distinct from callout (callout is one point). Distinct from comparison
    (comparison contrasts two things; cause_effect shows a directional link).
    DISTINCTION FROM timeline: timeline has 3+ sequential steps; cause_effect
      has exactly 2 elements (one cause, one effect). If the speaker names 3 or
      more events in sequence — even with causal language between each step —
      use timeline, not cause_effect.
    Provide "cause_text" + "effect_text".
  "number_ranking" — speaker names a ranked ordered list (top 3, podium,
    leaderboard). REQUIRES explicit ordering/ranking. Distinct from list
    (list is unordered or loosely ordered; number_ranking has explicit
    rank positions). Distinct from score (score is a competitive result;
    number_ranking is an ordered catalog). Provide "rankings" list (2-5 items,
    ordered 1st to last).
  "hand_written_note" — speaker shares a personal aside, a quick side note, a
    parenthetical remark, or a "pro tip" that feels informal and spontaneous.
    Renders as a sticky-note or handwritten-style card. Distinct from callout
    (callout is a formal highlight; hand_written_note is an informal aside).
    Distinct from key_phrase (key_phrase is a main statement; hand_written_note
    is a margin note). Provide "note_text".
  "speech_bubble_thought" — speaker voices an internal thought, rhetorical
    inner monologue, or imagined audience reaction ("you're probably thinking…",
    "in your head right now…"). Renders as a thought-bubble. Distinct from
    dialogue (dialogue is two people talking; speech_bubble_thought is one
    person's internal monologue). Distinct from question (question is posed
    outward to the audience; speech_bubble_thought is a voiced inner thought).
    Provide "thought_text".
  "calendar_date_highlight" — speaker references a specific date, deadline,
    launch, or milestone moment ("le 14 janvier", "en 2025", "dans 90 jours").
    Renders as a calendar cell or date badge. Distinct from countdown (countdown
    is a timer running down; calendar_date_highlight is a fixed date reference).
    Distinct from roadmap_milestone (roadmap_milestone is a progress point;
    calendar_date_highlight is just the date itself). Provide "date_value" +
    "date_context".
  "percentage_split" — speaker describes how a total is divided proportionally
    ("60% de mon temps va à X, 40% à Y"). REQUIRES two or more segments that
    sum to 100%. Distinct from comparison (comparison is qualitative A vs B;
    percentage_split is a proportional numeric division). Distinct from
    data_bar_chart (data_bar_chart compares absolute values; percentage_split
    shows shares of a whole). Provide "split_labels" list + "split_values" list
    of floats (same length; values should sum to ~100).
  "red_flag_list" — speaker enumerates warning signs, mistakes to avoid, or
    danger signals ("les red flags à surveiller", "les erreurs classiques").
    REQUIRES at least 2 negative/warning items. Distinct from checklist
    (checklist is positive to-dos; red_flag_list is warnings). Distinct from
    warning_soft (warning_soft is one single caution; red_flag_list is a
    multi-item danger list). Provide "flags" list (2-5 items).
  "success_metric_badge" — speaker calls out a concrete achievement, a result
    milestone, or a proof-of-success number ("j'ai atteint 10 000 abonnés",
    "on a fait +47% de CA"). Renders as a badge or medal. Distinct from stat
    (stat is a raw number; success_metric_badge frames it as an achievement).
    Distinct from income_reveal (income_reveal is specifically about earnings;
    success_metric_badge is any success metric). Provide "badge_label" +
    "badge_context".
  "client_avatar_persona" — speaker describes a target client, customer
    archetype, or ideal buyer persona ("mon client idéal, c'est Sophie, 34 ans…").
    Renders as an avatar with traits pills. Distinct from testimonial (testimonial
    is a real person's review; client_avatar_persona is a composite archetype).
    Distinct from versus_battle (versus contrasts two options; client_avatar_persona
    profiles one person). Provide "persona_name" + "persona_traits" list (2-4 items).
  "book_recommendation" — speaker references, recommends, or quotes a book
    ("je te conseille de lire X", "le livre qui a changé ma vie", "il y a
    un livre que j'ai lu, ça disait…", "dans son livre X il explique",
    "j'ai lu quelque chose qui disait"). Preferred when title OR author is
    identifiable from the transcript; if NEITHER can be extracted, use
    key_phrase for the book's central claim instead of leaving the window
    empty. Distinct from testimonial (testimonial is a person's review of a
    product/service, not a book). Provide "book_title" (or "–" if untitled
    in transcript) + "book_author" (or "" if unnamed).
  "tool_stack" — speaker enumerates a set of tools, apps, or software they use
    ("mes outils du quotidien", "la stack que j'utilise", "les logiciels que…").
    REQUIRES at least 2 named tools. Distinct from list (list is any enumeration;
    tool_stack is specifically a set of named software/tools). Distinct from
    checklist (checklist is to-dos; tool_stack is an inventory of tools).
    Provide "tools" list (2-6 items).
  "revenue_breakdown" — speaker details multiple revenue streams with values
    ("mon CA se répartit entre X€ de Y et Z€ de W"). REQUIRES both source labels
    and numeric values. Distinct from stat (stat is a single number). Distinct
    from income_reveal (income_reveal is a single total earnings figure;
    revenue_breakdown is the breakdown by source). Distinct from data_bar_chart
    (data_bar_chart is generic numeric comparison; revenue_breakdown is
    specifically revenue by source). Provide "revenue_sources" list +
    "revenue_values" list of floats (same length, 2-5 items).
  "age_milestone" — speaker discloses or references an age or duration as a
    dramatic personal reveal ("j'avais 24 ans quand…", "ça m'a pris 3 ans",
    "à 34 ans j'ai…"). REQUIRES a number or duration value. Distinct from
    stat (stat is a business/data number; age_milestone is a personal age or
    elapsed time). Distinct from calendar_date_highlight (calendar_date_highlight
    is a specific date; age_milestone is an age or duration). Distinct from
    step_number (step_number is a process step; age_milestone is a personal
    milestone age). Provide "age_value" + "age_context".
  "contrarian_take" — speaker voices a provocative opinion, either by EXPLICITLY
    flagging it ("voici ce que personne ne dit", "j'ai une opinion impopulaire",
    "la vérité que tu ne veux pas entendre", "je vais dire quelque chose que
    personne n'ose dire", or equivalent meta-commentary), OR by using a clearly
    IRONIC framing where a positive word is deployed sarcastically in a context
    that makes its literal reading absurd. IRONY RULE: triggers when (1) the
    speaker uses a manifestly positive term (paradis, idéal, génial, parfait,
    formidable, c'est super) AND (2) the immediately preceding sentence contained
    a negative fact or figure that makes the positive reading impossible (e.g.,
    "un chef d'entreprise passe 190h par an à faire des papiers… c'est vraiment
    un paradis la France"). In the irony case, set take_text = the sarcastic
    positive statement and expected_text = the negative fact it responds to.
    Do NOT use for bold but sincere claims without irony or explicit framing
    (use callout or key_phrase for those). Do NOT use for warnings or cautions
    (use warning_soft or red_flag_list). Distinct from warning_soft (warning_soft
    is a caution without opinion framing). Distinct from callout (callout is neutral
    context). Provide "take_text".
  "action_step_cta" — speaker gives a direct imperative call to action or a
    concrete next step for the viewer ("maintenant voici ce que tu dois faire",
    "passe à l'action", "fais X dès aujourd'hui"). Distinct from callout
    (callout is a statement; action_step_cta is an imperative directive).
    Distinct from step_number (step_number is one step in a process;
    action_step_cta is a standalone final CTA). Distinct from question (question
    poses a query; action_step_cta is a command). Provide "cta_text".
  "story_chapter_transition" — marks a narrative beat transition between two
    story parts, a pivot moment, or a scene break ("mais voilà ce qui s'est
    passé…", "et là tout a changé", "la suite m'a surpris"). Distinct from
    chapter_marker (chapter_marker is a structured numbered chapter; this is a
    fluid narrative beat without numbering). Distinct from timeline
    (timeline is a sequence of events; story_chapter_transition is one
    pivot-beat separator). Provide "transition_label".
  "live_reaction_split" — speaker contrasts what was expected or believed with
    what actually turned out to be true. REQUIRES both sides to be stated. Works
    with collective OR first-person framing: "on pensait que X… mais en réalité Y"
    AND "je pensais que X… en réalité Y" AND "j'imaginais X mais maintenant je
    réalise Y" AND "j'aurais pas cru que X, et pourtant en réalité Y". The
    expected belief may be the speaker's own past misconception. Distinct from
    before_after_image (that is a transformation over time; live_reaction_split is
    expectation vs outcome). Distinct from versus_battle (versus contrasts two
    options; live_reaction_split is expected-vs-reality). Trigger-style: the reveal
    of reality must be literally spoken. Provide "expected_text" (what was believed
    before) + "reality_text" (what the speaker now knows is true).
  "hidden_cost_reveal" — speaker reveals a hidden or total cost that differs
    from the advertised price ("le prix affiché c'est X… mais le coût réel
    c'est Y"). REQUIRES both prices. Distinct from income_reveal (single number;
    hidden_cost_reveal shows two contrasting prices). Distinct from stat
    (stat is informational; hidden_cost_reveal has reveal/shock energy).
    Trigger-style: the real cost must be literally spoken. Provide
    "sticker_price" + "real_cost".
  "social_proof_counter" — speaker cites a rapidly-accumulating or high-volume
    social metric ("on est passé à 12 000 abonnés", "déjà 50 000 téléchargements").
    Renders as a large number with slot-machine-settling animation. Distinct from
    stat (stat is static data; social_proof_counter has kinetic scroll-settle
    energy, specifically for social/community metrics). Distinct from
    success_metric_badge (badge frames a milestone achievement; counter emphasizes
    the live-accumulating number itself). Provide "counter_final_value" +
    "counter_label".
  "timeline_prediction" — speaker presents a timeline that mixes confirmed past
    steps with projected future steps, explicitly distinguishing between what has
    happened and what is planned ("jusqu'ici on a fait X et Y… et voici ce qu'on
    prévoit pour Z"). REQUIRES at least one confirmed step AND at least one
    predicted/future step — both lists must be non-empty.
    HARD TRIGGER: at least one step must be in the FUTURE or expressed as a plan —
      detectable by future tense ("on va", "on prévoit", "notre prochain objectif",
      "d'ici X mois", "l'étape suivante"), conditional, or explicit planning language
      ("l'objectif est de", "on compte", "ce qu'on prépare").
    HARD RULE: if ALL steps are in the PAST with no forward projection →
      milestone_recap or timeline, NOT timeline_prediction.
    DISTINCTION FROM milestone_recap: milestone_recap lists PAST-ONLY confirmed
      achievements the speaker looks back on. timeline_prediction REQUIRES a
      FORWARD-LOOKING component. No future step → not timeline_prediction.
    Distinct from timeline (timeline is a fully confirmed sequence with no future
      projection). Provide "confirmed_steps" list + "predicted_steps" list (1-4 each).
  "red_thread_connector" — speaker explicitly ties together 2-3 concepts
    mentioned at different points in the video ("tu te souviens de X ? Et de Y ?
    Eh bien les deux sont liés…"). The connector energy — calling back to
    earlier-mentioned ideas — is mandatory. Distinct from mindmap (mindmap is
    one center + branches; red_thread_connector is a narrative callback linking
    previously-mentioned distinct concepts). Distinct from list (list is
    ad-hoc enumeration; red_thread_connector is an explicit narrative tie).
    Provide "connector_points" list (2-3 items naming the concepts).
  "silent_beat_pause" — a deliberately minimal near-empty card for a dramatic
    silence beat or reflective pause moment. Use sparingly, only when the speaker
    goes silent for effect or invites the viewer to sit with a thought. NOT a
    trigger-style type. Provide optional "pause_symbol" (defaults to "…").
  "comment_reply_style" — speaker reads or voices a written comment/question
    and then gives their reply ("j'ai reçu ce commentaire… voici ma réponse").
    Renders as a social-media comment + reply visual. REQUIRES both comment and
    reply to be present. Distinct from testimonial (testimonial is endorsement;
    comment_reply is a Q&A exchange). Distinct from dialogue (dialogue is two
    spoken voices; comment_reply_style is a written comment + spoken reply).
    Trigger-style: the reply must be literally spoken. Provide "comment_text" +
    "reply_text".
  "before_you_scroll" — a direct pattern-interrupt addressed to the viewer,
    designed to stop them from scrolling ("attends avant de partir", "lis ça
    avant de scroller", "avant que tu continues"). REQUIRES direct second-person
    address to viewer. Distinct from action_step_cta (action_step_cta is a
    directive to DO something; before_you_scroll is a plea to STAY and READ).
    Distinct from callout (callout is neutral context; before_you_scroll has
    urgency/interruption energy). Trigger-style: the hook phrase must be literally
    spoken. Provide "hook_text".
  "traffic_light_status" — speaker explicitly assigns a go/no-go or health
    status to a strategy, project, or metric ("c'est rouge pour cette
    tactique", "c'est vert, on valide", "encore en jaune"). REQUIRES an
    explicit color-coded or status signal. Provide "status_color"
    ("red"|"yellow"|"green") + "status_label". Distinct from stat (stat is
    a raw metric; traffic_light_status is a status verdict). Distinct from
    score (score is competitive ranking; traffic_light is a go/no-go).
    Distinct from warning_soft (warning_soft is a caution text without
    color-coded framing; traffic_light has explicit RED/YELLOW/GREEN
    structure). NOT to be used when the speaker mentions a color
    incidentally without assigning a status label.
  "day_in_life_schedule" — speaker walks through their day or routine in
    clock-anchored time slots ("je me lève à 6h", "à 9h je fais mon deep
    work", "12h pause"). REQUIRES at least 3 time-anchored items with
    explicit hour/time markers. Provide "schedule_items" list. Distinct
    from timeline (timeline is temporal event sequence without clock
    anchors; day_in_life_schedule is a daily routine with explicit hours).
    Distinct from checklist (checklist is completed to-dos; schedule is
    time-of-day slots). Distinct from list (list is unordered enumeration;
    schedule is clock-ordered with time references mandatory).
  "skill_tree_unlock" — speaker describes a sequence of discrete skill
    unlocks, capability gates, or achievement badges they progressed
    through in order ("d'abord j'ai maîtrisé X, ensuite Y s'est débloqué,
    puis Z"). REQUIRES at least 2 ordered unlocks framed as a progression.
    Distinct from success_metric_badge (badge is a single isolated
    achievement; skill_tree is a chained unlock sequence). Distinct from
    roadmap_milestone (milestone is one completed checkpoint; skill_tree is
    a set of discrete levelled unlocks). Distinct from checklist (checklist
    is to-dos; skill_tree has game-like unlock/progression energy). Provide
    "unlocked_milestones" list (2-5 items in unlock order).
  "audience_poll_result" — speaker cites the result of a vote or poll,
    including ACTUAL percentages and a winning option ("j'ai posé la
    question à ma communauté — 67% ont répondu X, 33% Y"). REQUIRES both
    options AND their numeric percentages AND a clear winner. Distinct from
    poll_question (poll_question is an open interactive vote with NO
    results yet; audience_poll_result shows the completed result with
    percentages and a winner). Distinct from percentage_split (percentage_split
    is a neutral proportional breakdown; audience_poll_result has a winning
    option, a poll framing, and explicit vote counts). Distinct from
    data_bar_chart (data_bar_chart is generic numeric comparison;
    audience_poll_result is specifically vote results with a winner).
    Provide "poll_options" list + "poll_percentages" list of floats
    (same length, 2-4 items; values should sum to ~100).
  "broken_promise_tracker" — speaker enumerates a mixed list of promises
    or commitments, explicitly flagging which were KEPT and which were
    BROKEN ("j'avais promis X — tenu. J'avais promis Y — pas tenu.").
    REQUIRES at least one kept AND at least one broken item — pure kept is
    checklist, pure broken is red_flag_list. Trigger-style: the speaker
    must literally name these promises. Distinct from checklist (all-positive
    verified items; broken_promise_tracker has BOTH ✓ and ✗). Distinct
    from red_flag_list (all-negative warnings; broken_promise_tracker
    explicitly tracks a MIXED record). Distinct from myth_vs_fact
    (myth_vs_fact debunks one claim; broken_promise_tracker maps a list of
    commitments). Provide "promises" list + "kept_status" list of booleans
    (same length; must contain at least one true AND one false).
  "ingredient_list" — speaker enumerates the required components, materials,
    inputs, or prerequisites needed for something ("pour réussir ça il te
    faut X, Y et Z", "les ingrédients de ma méthode sont…"). Use when the
    framing is REQUIRED MATERIALS, not completed tasks or software tools.
    Distinct from tool_stack (tool_stack is specifically named software
    apps; ingredient_list is any required material, concept, or input).
    Distinct from checklist (checklist is completed actions; ingredient_list
    is required inputs not yet verified). Distinct from list (list is
    general ad-hoc enumeration; ingredient_list has explicit required-
    materials framing). Provide "ingredients" list (2-6 items).
  "resource_allocation" — speaker describes how a LIMITED resource (time,
    budget, energy, attention) is distributed across uses with an
    "emptying envelope" feel ("j'alloue 40% de mon budget à X, 30% à Y,
    30% à Z"). REQUIRES labeled resource categories AND numeric values
    that represent shares of a constrained total. Distinct from
    revenue_breakdown (revenue_breakdown is specifically financial income
    by stream; resource_allocation is ANY limited resource with depletion
    framing — not just money). Distinct from percentage_split (percentage_split
    is a neutral proportional breakdown without depletion framing;
    resource_allocation has explicit limited-envelope / allocation energy).
    Distinct from data_bar_chart (data_bar_chart is absolute value
    comparison; resource_allocation is shares of a finite total). Provide
    "resource_labels" list + "resource_values" list of floats (same length,
    2-5 items; values should sum to ~100 if percentages, or share a common unit).
  "fill_in_the_blank" — speaker constructs a sentence with a deliberate gap
    and then reveals the missing word for rhetorical or pedagogical effect
    ("la clé du succès c'est ___ — c'est la régularité"). REQUIRES an
    explicit sentence-with-gap structure AND the single reveal word to be
    literally spoken. Distinct from secret_reveal (secret_reveal is a whole
    content block blurred then revealed; fill_in_the_blank is ONE WORD
    within an already-visible sentence). Distinct from key_phrase (key_phrase
    is a complete statement; fill_in_the_blank has an intentional structural
    gap). Distinct from question (question asks outward to the audience;
    fill_in_the_blank is a structured completion format). Provide
    "sentence_with_blank" (use ___ for the gap) + "blank_word".
  "streak_counter" — speaker highlights a running streak or accumulating count
    ("ça fait 42 jours que je poste chaque jour", "100 jours de suite"). REQUIRES
    an explicit count AND a unit of continuity. The streak must be ONGOING and
    still accumulating — not a retrospective duration used as context. Distinct from
    progress_bar (progress_bar tracks progress toward a goal; streak_counter celebrates
    unbroken continuity). Distinct from countdown (countdown is a DECREASING timer;
    streak_counter INCREASES). Distinct from stat (stat is generic data; streak_counter
    has streak/series energy). NOT when the speaker references a past duration as
    context ("il y a deux ans", "il y a X mois") — a retrospective duration anchoring
    a BEFORE state is NOT a streak; if paired with a current and future state in the
    same segment, use before_now_later instead.
    Provide "streak_count" + "streak_unit" + optional "streak_label".
  "before_now_later" — speaker explicitly maps THREE temporal states of the same
    subject: past state, current state, and future state ("avant j'étais X, maintenant
    je suis Y, et demain je vise Z"; "il y a deux ans je galérais, aujourd'hui je
    dirige une entreprise, dans le futur je vise l'indépendance"). REQUIRES all three
    states to be named in the same segment. PRIORITY — when a segment names all three
    states (past context + present reality + future goal/aspiration), before_now_later
    WINS over streak_counter (even if the past state mentions a duration like "il y a
    deux ans"), key_phrase, before_after_image, and comparison. Trigger pattern:
    past anchor ("il y a X / avant / jadis / j'étais") + present state ("aujourd'hui /
    maintenant / je suis / je dirige") + future goal ("demain / dans le futur / je vise /
    l'objectif est / je serai"). Distinct from comparison (comparison is exactly 2 sides;
    before_now_later always has 3). Distinct from timeline_prediction (timeline_prediction
    is a sequence of steps; before_now_later is a 3-point temporal snapshot of ONE
    subject). Distinct from before_after_image (before_after_image is 2 states of one
    subject; before_now_later adds an explicit FUTURE/LATER state).
    Provide "before_label" + "now_label" + "later_label".
  "platform_stats" — speaker cites metrics for MULTIPLE platforms or channels
    simultaneously ("sur TikTok j'ai 50k, sur YouTube 20k, sur Insta 30k").
    REQUIRES at least 2 named platforms each with a numeric value. Distinct from
    social_proof_counter (social_proof_counter is ONE rapidly-accumulating number;
    platform_stats shows multiple platforms side-by-side). Distinct from stat (stat
    is a single metric; platform_stats is a multi-platform grid). Provide "platforms"
    list + "values" list (same length, 2-5 items).
  "cost_comparison" — speaker presents MULTIPLE options side-by-side with an
    associated cost, tax rate, or financial value for each. Canonical use case:
    pricing tiers ("le plan basique à 0€, le pro à 29€/mois, l'enterprise à 99€").
    EXTENDED USE: also triggers for COUNTRY-BY-COUNTRY FISCAL COMPARISONS where
    the speaker names N>=2 countries each with a stated tax rate, net amount, or
    cost — e.g., "en France tu prends 300 000, tu vas en Grèce tu prends 100 000,
    au Portugal c'était zéro, en Belgique capé à 10%". In this case option_labels =
    country names in order of citation, option_prices = the stated fiscal amount or
    rate (use values as spoken: "300 000€", "100 000€", "0€", "10%"), best_index =
    index of the lowest-cost / most favourable option. Values may be heterogeneous
    (mix of absolute amounts and percentages) — use them as spoken.
    REQUIRES at least 2 named options each with a stated value. Distinct from
    price_tag (price_tag is a SINGLE price point; cost_comparison shows 2+ options).
    Distinct from comparison (comparison is qualitative, exactly 2 sides; cost_comparison
    is a pricing/fiscal grid with 2-4 options). Distinct from income_vs_expense
    (income_vs_expense is binary income/outflow; cost_comparison is 2+ options).
    Distinct from data_bar_chart (data_bar_chart requires homogeneous float values;
    cost_comparison handles heterogeneous units). Provide "option_labels" list +
    "option_prices" list (same length, 2-4 items). Optionally set "best_index"
    (0-based) to highlight the recommended option — defaults to the last option.
  "decision_matrix" — speaker introduces a 2×2 framework to classify actions or
    choices ("urgent/important, urgent/pas important, pas urgent/important, pas urgent/
    pas important"). REQUIRES exactly 4 quadrant labels. Distinct from comparison
    (comparison contrasts 2 sides; decision_matrix is a 2×2 grid). Distinct from
    pros_cons (pros_cons evaluates one subject from two angles; decision_matrix is a
    classification grid for actions). Provide "quadrant_labels" list of exactly 4 strings
    AND a short "title" (e.g. "Matrice Eisenhower") to appear as a header above the
    grid — always provide a title so the card has context beyond the 4 quadrant labels.
  "habit_tracker" — speaker describes a recurring daily habit and its completion
    status over recent days ("voici ma streak de sport — lundi oui, mardi non, …").
    REQUIRES a named habit AND a boolean completion list. Distinct from checklist
    (checklist is one-time completed tasks; habit_tracker is recurring daily tracking
    with a visual grid). Distinct from skill_tree_unlock (skill_tree_unlock is a
    progression sequence; habit_tracker is a repeating daily binary done/not-done).
    Provide "habit_label" + "days_completed" list of booleans (7-14 days typical).
  "income_vs_expense" — speaker contrasts their total income against their total
    expenses ("je gagne 12 000€ par mois et mes dépenses sont à 7 500€"). REQUIRES
    both an income value AND an expense value. Distinct from revenue_breakdown
    (revenue_breakdown shows multiple income sources; income_vs_expense is binary
    income-vs-outflow). Distinct from comparison (comparison is qualitative; income_vs_expense
    is a binary financial bars). Distinct from cost_comparison (cost_comparison is
    buying options; income_vs_expense is total inflow vs total outflow). Provide
    "income_value" + "expense_value" + optional "income_label" / "expense_label".
  "milestone_recap" — speaker reviews MULTIPLE key milestones or achievements in
    sequence ("en 2021 j'ai décroché mon premier client, en 2022 j'ai passé les 10k
    abonnés, en 2024 j'atteins les 6 chiffres"). REQUIRES at least 2 milestones listed
    together. Distinct from recap_summary (recap_summary is a general conclusion
    summary in unordered bullet points; milestone_recap is a chronological retrospective
    of specific achievements). Distinct from roadmap_milestone (roadmap_milestone is a
    SINGLE isolated milestone; milestone_recap always lists SEVERAL together). Distinct
    from timeline (timeline is a progression of arbitrary events; milestone_recap focuses
    exclusively on ACHIEVEMENTS and past accomplishments). Provide "milestones" list
    (2-6 items, each formatted as "Année — Réalisation" or similar).
  "content_calendar" — speaker shows or describes a content planning schedule by day
    or week slot ("lundi je poste un produit, mercredi une story behind-the-scenes,
    vendredi un reel viral"). REQUIRES multiple days or time slots each with a distinct
    content assignment. Distinct from day_in_life_schedule (day_in_life_schedule is a
    SINGLE day's hourly routine — "9h réveil, 10h gym"; content_calendar is a MULTI-DAY
    weekly or monthly publishing plan). Distinct from roadmap_milestone (roadmap_milestone
    is a project phase; content_calendar is a publishing schedule). Provide
    "calendar_items" list (3-7 items, each formatted as "Jour — Contenu").
  "client_result_number" — speaker reveals a TRANSFORMATION RESULT achieved by a
    client or customer ("mon client a gagné +340% en 60 jours", "elle est passée de 0
    à 10k abonnés en 3 mois"). REQUIRES a result value AND a time or context frame.
    Distinct from income_reveal (income_reveal is the SPEAKER'S OWN revenue; this is
    a CLIENT'S transformation result). Distinct from social_proof_counter (that is a
    community headcount like followers; this is a specific performance transformation).
    Distinct from stat (stat is generic data with no client-transformation framing).
    Provide "result_value" + "result_context" + optional "client_label".
  "mistake_lesson" — speaker explicitly names a mistake they made AND the lesson
    they drew from it ("j'ai fait l'erreur de X... et j'en ai retenu que Y"). REQUIRES
    both a mistake AND a lesson stated in the same segment. Distinct from
    objection_response (objection_response is a CLIENT'S objection + speaker's
    defensive counter; mistake_lesson is the SPEAKER'S OWN error + personal reflection).
    Distinct from cause_effect (cause_effect is a neutral causal chain, no personal
    error acknowledgment; mistake_lesson has explicit self-critique and retrospective
    tone). Distinct from warning_soft (warning_soft is an advisory warning about
    something to avoid; mistake_lesson is a POST-HOC reflection on something already
    done wrong). Provide "mistake_text" + "lesson_text".
  "tool_comparison" — speaker compares MULTIPLE tools or software against each other
    on specific criteria ("Notion vs Trello vs Asana — voici les différences clés").
    REQUIRES at least 2 named tools AND a feature-by-feature comparison angle. Distinct
    from cost_comparison (cost_comparison is purely a pricing grid; tool_comparison is
    multi-criteria feature analysis). Distinct from tool_stack (tool_stack is a simple
    list of tools used; tool_comparison evaluates tools head-to-head). Distinct from
    versus_battle (versus_battle is a stylized duel framing; tool_comparison is a
    structured multi-criteria grid). Provide "tool_names" list (2-3 items) +
    "tool_features" list of comparison rows (2-5 items).
  "weekly_review" — speaker evaluates MULTIPLE categories of a period (week, month)
    each with a separate score or assessment ("ma semaine : contenu 8/10, prospection
    6/10, santé 9/10"). REQUIRES at least 2 categories each with a distinct
    score/assessment. Distinct from star_rating_review (star_rating_review is a
    SINGLE overall rating for ONE item; weekly_review has MULTIPLE categories each
    assessed separately). Distinct from audience_poll_result (that is an external
    audience vote; weekly_review is a personal self-assessment). Provide
    "review_categories" list + "review_scores" list (same length, 2-6 items).
  "audience_question" — speaker poses a SINGLE question to the audience with NO answer
    shown on the card — the question hangs in suspense to invite participation
    ("dis-moi en commentaire...", "et toi, tu sais vraiment ce que veut ton audience?").
    REQUIRES exactly one question with no accompanying answer in the same segment.
    Distinct from question_answer_pair (question_answer_pair shows BOTH question AND
    answer on the same card; audience_question shows ONLY the question). Distinct from
    question (question is a rhetorical hook asked to create tension; audience_question
    is specifically an ENGAGEMENT question directed at the audience asking for their
    participation or input). Provide "question_text".
- VERBATIM GROUNDING — mandatory check before assigning any explicit-signal
  card type (contrarian_take, warning_soft, red_flag_list, action_step_cta,
  myth_vs_fact, secret_reveal, objection_response, live_reaction_split,
  hidden_cost_reveal, comment_reply_style, before_you_scroll,
  broken_promise_tracker): the trigger phrase MUST
  appear verbatim in the KEY LINES or be unambiguously present in the beat
  description. The BEAT SPINE "lines" field is editorial context synthesised
  by a planning model — it may paraphrase, editorially rephrase, or invent
  punchier wording that was never literally spoken. Do NOT assign a
  trigger-phrase type based on beat-spine lines alone when those lines are
  absent from KEY LINES. When uncertain between a trigger-phrase type and a
  generic type (callout, key_phrase, quote), always prefer the generic type.
- TIMING: startSec should match when the speaker BEGINS saying the
  words the card references — synchronous with speech, like captions.
- Place cards at NARRATIVELY IMPORTANT moments — not evenly spaced

LANGUAGE: {language}
- ALL card text (kicker, title, detail, items, steps, line_a/line_b,
  attribution) MUST be in {language} — match the speaker's language exactly.
- PUNCTUATION: Never use the em-dash character (—) in any card text. Use a comma, colon, or period instead.

BRAND: accent color {brand_color}, content type: {content_type}, style: {editing_style}

Reply with ONLY a JSON array, no explanation."""

    # Append prosodic block when signal is available (branchement fix + Option B)
    if any(b.get("emphasis_score") is not None for b in beat_summary):
        system_prompt += """

## Prosodic signal (audio-derived — source-independent of transcript text)
Some beats carry "emphasis_score" (0-1): computed from pause before the segment,
speech slowdown, and RMS volume delta relative to the speaker's baseline.
Higher = the speaker physically paused, slowed down, and spoke louder there.
"prosodic_peak":true = strongest prosodic signal in the second half of the video.
"energy_level":"HIGH"|"MEDIUM"|"LOW" = 3-second window energy classification.

USE FOR CLIMAX CARD SELECTION (prim_cinematic_reveal / prim_ascension_reveal):
When multiple beats satisfy the linguistic trigger conditions, prefer the beat with
the highest emphasis_score / prosodic_peak:true. The prosodic signal confirms the
speaker's physical emphasis — it does NOT replace linguistic requirements. Never
assign a climax card based on prosodic_peak alone if the linguistic conditions fail."""

    user_msg = f"""VIDEO DURATION: {trimmed_duration:.1f}s

BEAT SPINE (the narrative structure):
{json.dumps(script_out, indent=2)}

SEGMENT DETAILS (scores, reasons, retention notes):
{json.dumps(beat_summary, indent=2)}

KEY LINES (most memorable moments):
{json.dumps(key_lines)}

Design graphic overlay cards for this video — up to {target_cards} maximum. Place a card only at moments that genuinely earn one: a key claim, a surprising stat, a narrative turning point, or a concept the viewer needs to see to understand. Skip the moment if no card adds value. Quality and narrative relevance always take priority over reaching the card count ceiling."""

    # Scale max_tokens with target_cards so long-video responses are never truncated.
    # ~150 tokens/card average (JSON overhead + complex types like list/tool_comparison).
    # Minimum 4096 preserves short-video behaviour; cap at 16384 (claude-opus-4-7 limit is 32k).
    _max_tok = max(4096, min(16384, target_cards * 150))

    client = Anthropic()
    try:
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=_max_tok,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        if response.stop_reason == "max_tokens":
            print(
                f"[STORYBOARD] WARN: LLM hit max_tokens={_max_tok} "
                f"(target_cards={target_cards}) — response truncated",
                flush=True,
            )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        try:
            cards = json.loads(raw)
        except json.JSONDecodeError:
            from app.agent.planner import _repair_json
            cards = json.loads(_repair_json(raw))
        if not isinstance(cards, list):
            cards = cards.get("cards", [])
        # Clamp to video duration; 0.5s floor prevents zero-duration cards only.
        # Per-category minimums (2.5/3.0/3.5s) are applied below with anti-overlap clamp.
        for card in cards:
            card["startSec"] = max(0, min(float(card.get("startSec", 0) or 0), trimmed_duration - 1))
            card["endSec"] = max(card["startSec"] + 0.5, min(float(card.get("endSec", 0) or 0), trimmed_duration))
        # Defensive duration cap: contrarian_take is a brief verbal signal, not a
        # long window. Cap at 2.5s to prevent swallowing adjacent content types
        # if classification fired on paraphrased planning output rather than
        # literal speech.
        for card in cards:
            if card.get("contentHints", {}).get("style") == "contrarian_take":
                _start = float(card["startSec"])
                _end = float(card["endSec"])
                if _end - _start > 2.5:
                    card["endSec"] = round(_start + 2.5, 2)
                    print(f"[STORYBOARD] contrarian_take cap: {_start:.2f}-{_end:.2f}s → {_start:.2f}-{card['endSec']}s", flush=True)
        # Duration extension: lift short cards to per-category minimums.
        # Sorted first so the anti-overlap clamp (next_card.startSec) is accurate.
        _MULTI_ITEM_STYLES = frozenset({
            "list", "timeline", "checklist", "pros_cons", "data_bar_chart",
            "carousel", "mindmap", "roadmap_milestone", "tool_stack",
            "revenue_breakdown", "tool_comparison", "decision_matrix",
            "day_in_life_schedule", "milestone_recap", "content_calendar",
            "weekly_review", "ingredient_list", "resource_allocation",
            "platform_stats", "cost_comparison", "broken_promise_tracker",
            "percentage_split", "habit_tracker",
        })
        _ANIMATED_STYLES = frozenset({
            "prim_stat_counter", "prim_split_compare", "prim_journey_map",
            "prim_cinematic_reveal", "prim_ascension_reveal",
            "social_proof_counter", "before_after_image", "countdown",
            "progress_bar", "traffic_light_status",
        })
        _sorted_dur = sorted(cards, key=lambda c: float(c.get("startSec", 0)))
        for _di, _dc in enumerate(_sorted_dur):
            _style = _dc.get("contentHints", {}).get("style", "")
            _ds = float(_dc["startSec"])
            _de = float(_dc["endSec"])
            _dur = _de - _ds
            if _style in _MULTI_ITEM_STYLES:
                _min_dur = 3.5
            elif _style in _ANIMATED_STYLES:
                _min_dur = 3.0
            else:
                _min_dur = 2.5
            if _dur < _min_dur:
                _next_s = float(_sorted_dur[_di + 1]["startSec"]) if _di + 1 < len(_sorted_dur) else trimmed_duration
                _new_end = min(_ds + _min_dur, _next_s - 0.05, trimmed_duration)
                if _new_end > _de:
                    _dc["endSec"] = round(_new_end, 3)
                    print(
                        f"[STORYBOARD] DUR-EXT {_dc.get('id','?')} style={_style!r} "
                        f"{_dur:.2f}s→{_new_end - _ds:.2f}s (floor {_min_dur}s)",
                        flush=True,
                    )
        # Budget=1 guard: PCR + PAR share a single "climax slot" — keep at most one.
        # Tiebreak: prefer the card with the latest startSec (payoff > hook).
        _CLIMAX_PRIMS = frozenset({"prim_cinematic_reveal", "prim_ascension_reveal"})
        _climax_cards = [c for c in cards if c.get("contentHints", {}).get("style", "") in _CLIMAX_PRIMS]
        if len(_climax_cards) > 1:
            _climax_cards.sort(key=lambda c: float(c.get("startSec", 0)), reverse=True)
            _keep_climax = _climax_cards[0]
            for _rc in _climax_cards[1:]:
                cards.remove(_rc)
                print(
                    f"[STORYBOARD] CLIMAX-BUDGET evicted {_rc.get('id','?')}"
                    f" style={_rc.get('contentHints',{}).get('style','?')!r}"
                    f" t={_rc.get('startSec','?')}s"
                    f" (budget=1 PCR+PAR shared, kept {_keep_climax.get('id','?')})",
                    flush=True,
                )
        # Budget=1 guard: prim_confession_frame — independent of climax slot, at most one per video.
        # Tiebreak: keep the card with the latest startSec (same policy as climax budget).
        _confess_cards = [c for c in cards if c.get("contentHints", {}).get("style", "") == "prim_confession_frame"]
        if len(_confess_cards) > 1:
            _confess_cards.sort(key=lambda c: float(c.get("startSec", 0)), reverse=True)
            _keep_confess = _confess_cards[0]
            for _rc in _confess_cards[1:]:
                cards.remove(_rc)
                print(
                    f"[STORYBOARD] CONFESS-BUDGET evicted {_rc.get('id','?')}"
                    f" style='prim_confession_frame'"
                    f" t={_rc.get('startSec','?')}s"
                    f" (budget=1 PCF, kept {_keep_confess.get('id','?')})",
                    flush=True,
                )
        # Landscape zone guard: video-overlay and fullscreen in landscape leave compact=False
        # for any card not in _DATA_PANEL_TYPES (those are rotated later by _remap_zone in
        # compose.py). Hero styles (key_phrase, quote, etc.) legitimately need the full canvas;
        # everything else is remapped to landscape-tl so compose derives compact=True.
        if format_hint != "short":  # landscape (16:9)
            for card in cards:
                _zone = card.get("zone", "")
                if _zone in ("video-overlay", "fullscreen"):
                    _cs = card.get("contentHints", {}).get("style", "")
                    if _cs not in _LANDSCAPE_HERO_STYLES:
                        card["zone"] = "landscape-tl"
                        print(
                            f"[STORYBOARD] ZONE-REMAP {card.get('id', '?')}"
                            f" style={_cs!r} {_zone!r} -> 'landscape-tl'"
                            f" (landscape non-hero guard)",
                            flush=True,
                        )
            # Reverse guard: full-cover primitives MUST be fullscreen — if the LLM
            # assigned a compact zone, force it back. The forward guard above only handles
            # the inverse direction (fullscreen → compact for non-hero cards).
            for card in cards:
                _cs = card.get("contentHints", {}).get("style", "")
                if _cs in _FULL_COVER_STYLES and card.get("zone", "") not in ("fullscreen", "video-overlay"):
                    _old_zone = card.get("zone", "?")
                    card["zone"] = "fullscreen"
                    print(
                        f"[STORYBOARD] ZONE-REMAP {card.get('id', '?')}"
                        f" style={_cs!r} {_old_zone!r} -> 'fullscreen'"
                        f" (full-cover landscape guard)",
                        flush=True,
                    )
        print(f"[STORYBOARD] Generated {len(cards)} graphic cards", flush=True)
        return cards
    except Exception as e:
        print(f"[STORYBOARD] Claude API error: {e}", flush=True)
        return []


def _tokenize_text(text: str) -> frozenset[str]:
    """Lowercase + split at punctuation → frozen token set. Skips 1-char tokens.

    Replaces punctuation (including apostrophes) with spaces so that French
    contractions like "d'impopulaire" → ["d", "impopulaire"] are handled
    correctly: the prefix (d, l, j, c, n, m) is filtered by the ≥2-char guard
    and the root word is kept.
    """
    return frozenset(
        t for t in re.sub(r"[^\w\s]", " ", text.lower()).split()
        if len(t) >= 2
    )


def _content_words(text: str) -> frozenset[str]:
    """Tokenize then remove French stopwords, keeping only substantive content tokens."""
    return _tokenize_text(text) - _FR_STOPWORDS


def _card_trigger_text(card: dict) -> str:
    """Extract the primary trigger-text string from a trigger-style card's contentHints.

    Only reads the type-specific field (take_text, warning_text, …) — NOT the generic
    'title'. Title is a display label, not a verbatim trigger phrase; checking it would
    cause false-positive reclassifications on cards whose title is a generic header.
    Returns "" when the field is absent or empty, which yields overlap=1.0 (pass-through).
    """
    hints  = card.get("contentHints", {})
    style  = hints.get("style", "")
    field  = _TRIGGER_TEXT_FIELD.get(style, "")
    if not field:
        return ""
    val = hints.get(field) or ""
    if isinstance(val, list):
        val = " ".join(str(x) for x in val)
    return str(val)


def _find_trigger_anchor(card: dict, remapped_words: list[WordTiming]) -> float | None:
    """Return corrected startSec anchored to the first Whisper word that matches a
    trigger content-word, scanning [startSec - PRE, startSec + ANCHOR_SEARCH_FORWARD_S].

    Returns None if no matching word is found (caller keeps original startSec).
    Never moves startSec backward beyond the current value.
    """
    trigger_cw = _content_words(_card_trigger_text(card))
    if not trigger_cw:
        return None
    start = float(card.get("startSec", 0))
    lo = start - _GROUNDING_WINDOW_PRE_S
    hi = start + _ANCHOR_SEARCH_FORWARD_S
    for w in remapped_words:
        if w.start < lo:
            continue
        if w.start > hi:
            break
        if _content_words(w.text) & trigger_cw:
            anchored = round(max(w.start - _ANCHOR_LEAD_S, start), 3)
            return anchored
    return None


def _grounding_overlap(card: dict, remapped_words: list[WordTiming]) -> float:
    """Return fraction of trigger content-words present in speech near startSec.

    Stopwords (French function words, pronouns, high-frequency verbs) are stripped
    from both the trigger text and the Whisper window before computing overlap.
    This prevents invented phrases that share only function words with genuine speech
    (e.g. "je vais dire que c'est une mauvaise idée" near "je vais vous montrer ça")
    from crossing the rejection threshold.

    Window: [startSec - _GROUNDING_WINDOW_PRE_S, startSec + _GROUNDING_WINDOW_POST_S].
    Returns 1.0 (always passes) when the card has no extractable trigger text.
    """
    trigger_tokens = _content_words(_card_trigger_text(card))
    if not trigger_tokens:
        return 1.0
    start = float(card.get("startSec", 0))
    spoken: frozenset[str] = frozenset()
    for w in remapped_words:
        if start - _GROUNDING_WINDOW_PRE_S <= w.start <= start + _GROUNDING_WINDOW_POST_S:
            spoken |= _content_words(w.text)
    return len(trigger_tokens & spoken) / len(trigger_tokens)


def _apply_segment_clamp(
    graphic_cards: list[dict],
    seg_out: list[tuple[float, float]],
) -> int:
    """Hard deterministic floor: clamp cards that land before speech or in a silence gap.

    Takes output-timeline segment bounds (already remapped from source via
    timing_map.source_to_output).  Returns the number of cards clamped.

    Two cases trigger a clamp:
      1. card.startSec is before the first speech segment
      2. card.startSec falls in a silence gap between two segments → moved to the
         start of the NEXT segment

    A card already inside a speech segment is not moved (it may still be a few hundred
    milliseconds early within that segment, but that is the title-anchor's job).
    """
    if not seg_out:
        return 0
    clamped = 0
    for _gc in graphic_cards:
        _start = float(_gc.get("startSec", 0))
        _floor: float | None = None
        if _start < seg_out[0][0]:
            _floor = seg_out[0][0]
        else:
            for _i in range(len(seg_out)):
                _ss, _se = seg_out[_i]
                if _ss <= _start <= _se:
                    break  # inside this segment — no clamp needed
                if _i + 1 < len(seg_out):
                    _ns = seg_out[_i + 1][0]
                    if _se < _start < _ns:
                        _floor = _ns
                        break
        if _floor is not None and _floor > _start:
            _orig = float(_gc["startSec"])
            _gc["startSec"] = round(_floor, 3)
            if float(_gc.get("endSec", 0)) < _gc["startSec"] + 1.5:
                _gc["endSec"] = round(_gc["startSec"] + 3.0, 3)
            print(
                f"[STORYBOARD] SEGMENT-CLAMP card {_gc.get('id','?')} "
                f"startSec {_orig:.2f}→{_gc['startSec']:.2f}s "
                f"(segment boundary enforced)",
                flush=True,
            )
            clamped += 1
    return clamped


def _find_fill_gaps(
    sorted_cards: list[dict],
    remapped_words: list[WordTiming],
    trimmed_duration: float,
    threshold_s: float = 20.0,
    min_words: int = 25,
    max_gaps: int = 8,
) -> list[tuple[float, float, list[WordTiming]]]:
    """Identify speech-filled gaps above threshold_s between existing cards.

    Returns list of (gap_start, gap_end, words_in_gap) sorted by descending duration,
    capped at max_gaps entries (largest first so the budget goes to the most impactful gaps).
    """
    gaps: list[tuple[float, float, list[WordTiming]]] = []
    prev_end = 0.0
    for card in sorted_cards:
        gap_start = prev_end
        gap_end = float(card["startSec"])
        if gap_end - gap_start >= threshold_s:
            words = [w for w in remapped_words if gap_start <= w.start < gap_end]
            if len(words) >= min_words:
                gaps.append((gap_start, gap_end, words))
        prev_end = max(prev_end, float(card["endSec"]))
    # Tail gap
    if trimmed_duration - prev_end >= threshold_s:
        words = [w for w in remapped_words if w.start >= prev_end]
        if len(words) >= min_words:
            gaps.append((prev_end, trimmed_duration, words))
    gaps.sort(key=lambda g: g[1] - g[0], reverse=True)
    return gaps[:max_gaps]


# Narrative types allowed in gap-fill: require no special structural signals.
# Excludes stat / prim_stat_counter / income_reveal / number_hero (need explicit data).
_GF_ALLOWED_STYLES: frozenset[str] = frozenset({
    "callout", "key_phrase", "quote", "cause_effect",
    "contrarian_take", "question", "attributed_quote",
})


def _gap_fill_call(
    gap_start: float,
    gap_end: float,
    gap_words: list[WordTiming],
    trimmed_duration: float,
    language: str,
    card_idx: int,
) -> dict | None:
    """Launch a focused second LLM call to find the best card moment in a speech gap.

    Returns a single card dict or None when the LLM abstains ({}) or the call fails.
    Each returned card still needs to pass the full quality chain in generate_storyboard().
    """
    from anthropic import Anthropic
    from app.core.config import settings

    gap_dur = gap_end - gap_start
    n_words = len(gap_words)

    # Build timestamped passage: group words in 10s chunks for temporal reference
    passage_lines: list[str] = []
    chunk_start: float | None = None
    chunk_words: list[str] = []
    for w in gap_words:
        if chunk_start is None:
            chunk_start = w.start
        if w.start - chunk_start >= 10.0:
            if chunk_words:
                passage_lines.append(f"[{chunk_start:.1f}s] {' '.join(chunk_words)}")
            chunk_start = w.start
            chunk_words = [w.text]
        else:
            chunk_words.append(w.text)
    if chunk_words and chunk_start is not None:
        passage_lines.append(f"[{chunk_start:.1f}s] {' '.join(chunk_words)}")
    passage_text = "\n".join(passage_lines)

    lang_label = "French" if language.startswith("fr") else ("English" if language.startswith("en") else language)

    system_prompt = f"""You are a video card classifier. A specific passage from a coaching/entrepreneur video has NO graphic overlay card despite having substantive spoken content. Your task: find THE SINGLE BEST card moment in this passage — the moment with the highest insight density.

AVAILABLE TYPES (narrative-focused, no special structural signal required):
- "callout"         : conceptual anchor — the one idea the viewer must grasp to follow the next 30s
- "key_phrase"      : transferable principle stated as a standalone truth
- "quote"           : powerful first-person statement about the speaker's own experience or conviction
- "cause_effect"    : explicit causal chain (X → Y, "because X, so Y")
- "contrarian_take" : speaker challenges what the audience likely assumes
- "question"        : rhetorical question the speaker poses and immediately answers
- "attributed_quote": citation from a named book, person, or external source

STRICT RULES:
1. anchor_word: ONE word that appears LITERALLY in the passage text
2. startSec / endSec: must be within [{gap_start:.1f}, {gap_end:.1f}]
3. Duration: 3–8s recommended
4. title: in {lang_label}, extracted from what the speaker actually says — no invention
5. kicker: optional short UPPERCASE label (e.g. "L'INSIGHT CLÉ", "RETENIR ÇA")

ABSTAIN RULE: If no moment in this passage genuinely earns a card (content too thin,
purely transitional, no extractable insight), return exactly: {{}}

Return ONE JSON object — a card or {{}}, nothing else:
{{"startSec": X, "endSec": Y, "contentHints": {{"style": "...", "title": "...", "kicker": "...", "anchor_word": "..."}}}}"""

    user_msg = (
        f"PASSAGE [{gap_start:.1f}s–{gap_end:.1f}s | {gap_dur:.0f}s | {n_words} words]:\n"
        f"{passage_text}\n\n"
        f"Find the single best card moment in this passage, or return {{}} if none earns a card."
    )

    client = Anthropic(api_key=settings.anthropic_api_key)
    try:
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=256,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        data = json.loads(raw)

        # Abstain: LLM returned {} or non-dict
        if not data or not isinstance(data, dict) or "contentHints" not in data:
            print(
                f"[GAP-FILL] Abstained on gap {gap_start:.1f}–{gap_end:.1f}s ({gap_dur:.0f}s)",
                flush=True,
            )
            return None

        # Clamp times to gap bounds
        _s = max(gap_start, min(float(data.get("startSec", gap_start)), gap_end - 2.0))
        _e = max(_s + 2.5, min(float(data.get("endSec", _s + 4.0)), gap_end, trimmed_duration))

        card: dict = {
            "id": f"gf-{card_idx:02d}",
            "type": "graphic",
            "startSec": round(_s, 3),
            "endSec": round(_e, 3),
            "zone": "fullscreen",
            "contentHints": data["contentHints"],
        }

        # Enforce allowed-style list; reclassify unknown types to callout
        _style = card["contentHints"].get("style", "")
        if _style not in _GF_ALLOWED_STYLES:
            print(
                f"[GAP-FILL] Style {_style!r} not in allowed list — reclassified to callout",
                flush=True,
            )
            card["contentHints"]["style"] = "callout"

        print(
            f"[GAP-FILL] card {card['id']} style={card['contentHints'].get('style')!r}"
            f" title={str(card['contentHints'].get('title',''))[:40]!r}"
            f" anchor={card['contentHints'].get('anchor_word','?')!r}"
            f" startSec={_s:.2f}s",
            flush=True,
        )
        return card

    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
        print(f"[GAP-FILL] Parse error gap {gap_start:.1f}–{gap_end:.1f}s: {e}", flush=True)
        return None
    except Exception as e:
        print(f"[GAP-FILL] API error gap {gap_start:.1f}–{gap_end:.1f}s: {e}", flush=True)
        return None


def _inject_rhythm_split_stage(
    graphic_cards: list[dict],
    remapped_words: list[WordTiming],
    trimmed_duration: float,
    style_pack: str,
    subject_side: str | None,
    layout: str,
    rhythm_s: float = 3.0,
    min_words: int = 4,
    card_dur: float = 4.5,
    exclusion_pad: float = 0.5,
    **_deprecated,  # absorbs old threshold_s / min_gap_s kwargs
) -> list[dict]:
    """Inject prim_split_stage(mode=caption) on a 3s grid, skipping slots covered by a rich card.

    Walk the timeline every rhythm_s seconds. At each slot check whether it overlaps
    any existing graphic card (with exclusion_pad buffer). If not → inject a caption
    card with word-by-word sync. Works for both portrait and landscape layouts.
    (Was landscape-only before — that restriction was the root cause of zero triggers on
    portrait videos, since the reference test file is 9:16 portrait.)
    """

    _side = "left" if subject_side == "left" else "right"

    # Build exclusion intervals: graphic card windows + padding
    exclusion: list[tuple[float, float]] = [
        (float(c.get("startSec", 0)) - exclusion_pad,
         float(c.get("endSec", 0)) + exclusion_pad)
        for c in graphic_cards
    ]

    def _overlaps(ws: float, we: float) -> bool:
        return any(es < we and ee > ws for es, ee in exclusion)

    # Anchor the grid to the first spoken word
    _first_word_t = remapped_words[0].start if remapped_words else 0.0
    grid_origin = max(0.5, _first_word_t - 0.2)

    new_cards: list[dict] = []
    cursor = grid_origin
    _slot = 0

    while cursor + card_dur <= trimmed_duration - 0.3:
        card_start = round(cursor, 3)
        card_end   = round(min(card_start + card_dur, trimmed_duration - 0.3), 3)

        if card_end - card_start >= 2.5 and not _overlaps(card_start, card_end):
            # Gather spoken words in this window
            span_words = [w for w in remapped_words if card_start <= w.start < card_end]

            if len(span_words) >= min_words:
                # Find best syntactic start (sentence > clause > word boundary)
                _start_idx = 0
                _clause_idx = 0
                _found_clause = False
                for _wi in range(1, len(span_words)):
                    _pt = span_words[_wi - 1].text.rstrip()
                    if _pt and _pt[-1] in ".?!":
                        _start_idx = _wi
                        break
                    if not _found_clause and _pt and _pt[-1] in ",;:":
                        _clause_idx = _wi
                        _found_clause = True
                else:
                    if _found_clause:
                        _start_idx = _clause_idx
                if len(span_words) - _start_idx < min_words:
                    _start_idx = 0

                caption_words = [
                    {"text": w.text, "start": float(w.start), "end": float(w.end)}
                    for w in span_words[_start_idx:_start_idx + 16]
                ]

                _card_side = _side if len(new_cards) % 2 == 0 else (
                    "right" if _side == "left" else "left"
                )
                _slot += 1
                card_id = f"card-rhythm-sst-{_slot:02d}"
                new_cards.append({
                    "id": card_id,
                    "type": "graphic",
                    "zone": "fullscreen",
                    "startSec": card_start,
                    "endSec": card_end,
                    "_family": "full_cover",
                    "contentHints": {
                        "style": "prim_split_stage",
                        "mode": "caption",
                        "side": _card_side,
                        "caption_words": caption_words,
                    },
                })
                # Mark this slot as occupied so it doesn't self-conflict
                exclusion.append((card_start - exclusion_pad, card_end + exclusion_pad))
                print(
                    f"[RHYTHM-SPLIT] {card_id} [{card_start:.1f}–{card_end:.1f}s]"
                    f" side={_card_side!r} words={len(caption_words)}"
                    f" text={' '.join(w['text'] for w in caption_words)!r}",
                    flush=True,
                )

        cursor = round(cursor + rhythm_s, 3)

    return new_cards


def generate_storyboard(
    trimmed_duration: float,
    remapped_words: list[WordTiming],
    transcript_segments: list[dict],
    script_structure: list[dict],
    keep_segments: list[dict],
    key_lines: list[str],
    caption_emphasis_words: list[str],
    word_categories: dict[str, str],
    brand_color: str,
    content_type: str,
    editing_style: str,
    format_hint: str,
    timing_map: TimingMap,
    language: str = "en",
    style_pack: str = "lean_glass",
    subject_side: str | None = None,
    energy_profile: list | None = None,
    prosodic_keep_segments: list | None = None,
) -> dict:
    """Generate a complete storyboard: graphic cards + caption cards.

    Returns a storyboard dict matching the graphic-overlays schema.
    """
    width, height = (1080, 1920) if format_hint == "short" else (1920, 1080)
    layout = "portrait" if format_hint == "short" else "landscape"

    # Compute prosodic emphasis scores and enrich keep_segments in-place.
    # Use prosodic_keep_segments when provided (original planner segments, source-time)
    # so passthrough-mode override (1 full-coverage segment) doesn't kill the signal.
    _segs_for_prosodic = prosodic_keep_segments if prosodic_keep_segments else keep_segments
    # Flatten transcript_segments → individual word dicts (source time) for prosodic scoring.
    # transcript_segments is a list of Whisper segments with nested "words"; passing segments
    # directly would make pause/slowdown operate on segment boundaries (≈0 pause for all).
    _transcript_words_flat = [
        w for seg in transcript_segments for w in seg.get("words", [])
    ]
    if energy_profile:
        _scores = _compute_prosodic_scores(_segs_for_prosodic, _transcript_words_flat, energy_profile)
        if _scores:
            _top_score = max(_scores.values())
            _top_start = next(k for k, v in _scores.items() if v == _top_score)
            _half = trimmed_duration * 0.5
            for seg in keep_segments:
                _sc = _scores.get(seg.get("src_start", seg.get("start", -1)))
                if _sc is not None:
                    seg["emphasis_score"] = _sc
                    seg["prosodic_peak"] = (
                        _sc == _top_score and seg.get("src_start", seg.get("start")) >= _half
                    )
            _log_seg = next(
                (s for s in keep_segments if s.get("src_start", s.get("start")) == _top_start),
                None,
            )
            if _log_seg:
                print(
                    f"[PROSODIC] top candidate:"
                    f" t={_top_start:.2f} score={_top_score:.3f}"
                    f" text={_log_seg.get('text', '')[:60]}",
                    flush=True,
                )

    # Attach energy_level from energy_profile to keep_segments
    if energy_profile:
        _ep_index = {round(e.get("at", 0), 1): e for e in energy_profile if isinstance(e, dict)}
        for seg in keep_segments:
            _t = round(seg.get("src_start", seg.get("start", 0)), 1)
            _ep_entry = _ep_index.get(_t)
            if _ep_entry and "energy_level" not in seg:
                seg["energy_level"] = _ep_entry.get("energy_level", "MEDIUM")

    # Generate graphic overlay cards via Claude
    graphic_cards = _generate_graphic_cards(
        trimmed_duration=trimmed_duration,
        script_structure=script_structure,
        keep_segments=keep_segments,
        key_lines=key_lines,
        brand_color=brand_color,
        content_type=content_type,
        editing_style=editing_style,
        format_hint=format_hint,
        timing_map=timing_map,
        language=language,
        subject_side=subject_side,
    )

    # Inject _family metadata from catalogue onto full_cover primitives.
    # catalogue.py is not imported here; this mapping mirrors its _family field
    # so the full-cover exclusion pass and backdrop-dim dispatch work correctly.
    for _gc in graphic_cards:
        if _gc.get("contentHints", {}).get("style", "") in _FULL_COVER_STYLES:
            _gc["_family"] = "full_cover"

    # Fix 4/5: Snap graphic card startSec to the first spoken word at or after
    # startSec — if the LLM placed the card >0.3s before any speech it references,
    # pull it forward so it arrives synchronously with speech (not as a spoiler).
    # Audit logs come after the snap so they reflect the corrected times.
    for _gc in graphic_cards:
        _start = float(_gc.get("startSec", 0))
        _ahead = [w for w in remapped_words if _start <= w.start <= _start + 2.5]
        if _ahead and _ahead[0].start - _start > 0.3:
            _orig = _start
            _gc["startSec"] = round(_ahead[0].start, 3)
            # Keep endSec >= new startSec
            if float(_gc.get("endSec", 0)) < _gc["startSec"]:
                _gc["endSec"] = round(_gc["startSec"] + 3.0, 3)
            print(
                f"[STORYBOARD] card {_gc.get('id','?')} startSec snapped "
                f"{_orig:.2f}→{_gc['startSec']:.2f}s (first word: '{_ahead[0].text}')",
                flush=True,
            )

    # Keyword-position anchoring: trigger-style cards are often anchored by the LLM to
    # the first word of the sentence that CONTAINS the trigger phrase, which can be 3-5s
    # before the trigger phrase itself ("Aujourd'hui je vais vous dire quelque chose
    # d'impopulaire" → startSec=1.00s, "impopulaire" at 4.0s → 3s early offset).
    # Scan the Whisper stream for the earliest match of any trigger content-word within
    # [startSec-0.5, startSec+6.0] and re-anchor to that word (minus a 200ms lead).
    # Runs before the grounding guard so the corrected startSec is what gets checked.
    for _gc in graphic_cards:
        _style = _gc.get("contentHints", {}).get("style", "")
        if _style not in _TRIGGER_STYLES:
            continue
        _anchor = _find_trigger_anchor(_gc, remapped_words)
        if _anchor is not None and _anchor > float(_gc.get("startSec", 0)):
            _orig_start = float(_gc["startSec"])
            _gc["startSec"] = _anchor
            if float(_gc.get("endSec", 0)) < _anchor + 1.5:
                _gc["endSec"] = round(_anchor + 3.0, 3)
            print(
                f"[STORYBOARD] ANCHOR card {_gc.get('id','?')} style={_style!r} "
                f"startSec {_orig_start:.2f}→{_anchor:.2f}s "
                f"(trigger keyword found in Whisper)",
                flush=True,
            )

    # Title-based semantic anchoring — non-trigger cards only.
    # Root cause: the Fix-4/5 snap gate (0.3s threshold) never fires in short-format
    # videos where speech density is ~3 words/sec — a transitional word like "en" or
    # "auprès" is almost always within 0.3s of any timestamp, keeping the gate shut even
    # when the card's actual content (e.g. the skill names in a skill_tree_unlock) isn't
    # spoken until 1–4s later.  Trigger-style cards are already protected by
    # _find_trigger_anchor(); this pass extends the same keyword-match logic to all
    # non-trigger cards using contentHints.title as the search text.
    # Only fires when the matched word is > 0.5s later than current startSec so that
    # already-correct placements (card already at or after the title word) are not shifted.
    for _gc in graphic_cards:
        _style = _gc.get("contentHints", {}).get("style", "")
        if _style in _TRIGGER_STYLES:
            continue  # already handled by _find_trigger_anchor above
        _hints = _gc.get("contentHints", {})
        _title = _hints.get("title", "")
        # Data cards (number_hero, age_milestone, …) store their spoken content in
        # a style-specific field, not in 'title'. Fall back to that field so these
        # cards get anchored to when their number/value is actually spoken, not just
        # to the segment start (which can be 4-9s before the content is stated).
        _used_data_fallback = False
        if not _title:
            _data_field = _DATA_ANCHOR_FIELDS.get(_style, "")
            if _data_field:
                _data_val = _hints.get(_data_field) or ""
                if isinstance(_data_val, list):
                    _data_val = " ".join(str(x) for x in _data_val)
                _title = str(_data_val)
                _used_data_fallback = bool(_title)
        if not _title:
            continue
        _title_cw = _content_words(_title)
        if not _title_cw:
            continue
        _start_s = float(_gc.get("startSec", 0))
        _lo = _start_s - _GROUNDING_WINDOW_PRE_S
        # Data-field fallback uses a wider search window: the LLM startSec for
        # data cards can be 4-9s before the spoken value (confirmed 8.54s on
        # number_hero in job 45bf7899), which is outside the standard 6s window.
        _hi = _start_s + (_DATA_ANCHOR_SEARCH_FORWARD_S if _used_data_fallback else _ANCHOR_SEARCH_FORWARD_S)
        _matched: float | None = None
        _matched_word: str = ""
        for _w in remapped_words:
            if _w.start < _lo:
                continue
            if _w.start > _hi:
                break
            if _content_words(_w.text) & _title_cw:
                _matched = _w.start
                _matched_word = _w.text
                break
        if _matched is not None and _matched - _start_s > 0.5:
            _orig_start = float(_gc["startSec"])
            _gc["startSec"] = round(max(_matched - _ANCHOR_LEAD_S, _start_s), 3)
            if float(_gc.get("endSec", 0)) < _gc["startSec"] + 1.5:
                _gc["endSec"] = round(_gc["startSec"] + 3.0, 3)
            _anchor_tag = "DATA-ANCHOR" if _used_data_fallback else "TITLE-ANCHOR"
            print(
                f"[STORYBOARD] {_anchor_tag} card {_gc.get('id','?')} style={_style!r} "
                f"startSec {_orig_start:.2f}→{_gc['startSec']:.2f}s "
                f"(keyword '{_matched_word}'@{_matched:.2f}s matched in Whisper)",
                flush=True,
            )

    # Grounding guard — code-level backstop for trigger-style cards.
    # The LLM prompt contains a verbatim-grounding rule, but it's a soft constraint
    # that Claude can violate under paraphrase pressure from the beat spine. This loop
    # cross-references each trigger-style card's key claim against actual Whisper words
    # in a ±window around startSec. Cards that fail are reclassified to a safe generic
    # fallback (key_phrase if a title exists, otherwise callout) so they remain as
    # dead-zone fillers rather than disappearing entirely.
    for _gc in graphic_cards:
        _style = _gc.get("contentHints", {}).get("style", "")
        if _style not in _TRIGGER_STYLES:
            continue
        _overlap = _grounding_overlap(_gc, remapped_words)
        _pct = int(_overlap * 100)
        if _overlap < _GROUNDING_OVERLAP_THRESHOLD:
            _orig = _style
            _title = _gc.get("contentHints", {}).get("title", "")
            if not _title:
                # Promote type-specific trigger field → title so key_phrase always has text.
                # Without this, cards with no title field render as empty callouts (two blue
                # bars, no text) because the reclassified callout has nothing to display.
                _tf = _TRIGGER_TEXT_FIELD.get(_orig, "")
                _tv = _gc.get("contentHints", {}).get(_tf, "")
                if isinstance(_tv, list):
                    _tv = " ".join(str(x) for x in _tv)
                if _tv:
                    _gc["contentHints"]["title"] = str(_tv).strip()
                    _title = _gc["contentHints"]["title"]
            _gc["contentHints"]["style"] = "key_phrase" if _title else "callout"
            print(
                f"[STORYBOARD] GROUNDING REJECT card {_gc.get('id','?')} "
                f"style={_orig!r}→{_gc['contentHints']['style']!r} overlap={_pct}%",
                flush=True,
            )
        else:
            print(
                f"[STORYBOARD] GROUNDING OK card {_gc.get('id','?')} "
                f"style={_style!r} overlap={_pct}%",
                flush=True,
            )

    # Segment-boundary clamp — hard deterministic floor after ALL anchoring passes.
    # Closes the paraphrase edge case: title-anchor finds nothing when card content is
    # fully paraphrased, leaving startSec at the LLM's (potentially early) value.
    # transcript_segments are in SOURCE timeline → remap to output timeline first.
    _seg_out: list[tuple[float, float]] = []
    for _seg in transcript_segments:
        _ss = timing_map.source_to_output(float(_seg.get("start", 0)))
        _se = timing_map.source_to_output(float(_seg.get("end", 0)))
        if _se > _ss:
            _seg_out.append((_ss, _se))
    _seg_out.sort()
    _apply_segment_clamp(graphic_cards, _seg_out)

    # Timing audit — after all anchoring passes, log per-card timing with title context.
    # Includes card title so logs can confirm semantic alignment, not just proximity.
    # A nearby transitional word ("en", "mais") with title far from speech still looks
    # "fine" in proximity-only logs — title in the log makes the blind spot visible.
    for _gc in graphic_cards:
        _start  = float(_gc.get("startSec", 0))
        _end    = float(_gc.get("endSec", _start + 3))
        _hints  = _gc.get("contentHints", {})
        _style  = _hints.get("style", "?")
        _title  = str(_hints.get("title", "") or "").strip()
        _title_snippet = (_title[:40] + "…") if len(_title) > 40 else _title
        _near = [w for w in remapped_words if _start - 0.3 <= w.start <= _start + 1.0]
        if not _near:
            _closest = min(remapped_words, key=lambda w: abs(w.start - _start), default=None)
            _cl_str = (f"nearest='{_closest.text}'@{_closest.start:.2f}s"
                       if _closest else "no words")
            print(
                f"[STORYBOARD] CRITICAL card {_gc.get('id','?')} style={_style!r} "
                f"title={_title_snippet!r} "
                f"startSec={_start:.2f}s endSec={_end:.2f}s "
                f"has NO speech in [{_start-0.3:.2f},{_start+1.0:.2f}] — {_cl_str}",
                flush=True,
            )
        else:
            print(
                f"[STORYBOARD] card {_gc.get('id','?')} style={_style!r} "
                f"title={_title_snippet!r} "
                f"startSec={_start:.2f}s endSec={_end:.2f}s "
                f"first nearby word='{_near[0].text}'@{_near[0].start:.2f}s",
                flush=True,
            )

    # Generative B-roll auto-injection is disabled.
    # The engine (broll_generative.py + broll_primitive.py) drove card selection via internal
    # narrative beat roles (HOOK, REALIZATION, PRINCIPLE, PAYOFF, EMOTIONAL_END, AMPLIFY).
    # These roles exist for structural analysis only and were never designed to drive on-screen
    # visuals directly. Beat-driven injection caused several classes of bugs:
    #   1. Data mixing: kicker extracted from pre-beat context, content_value from beat text —
    #      two separate script moments mislabelled as a single stat card.
    #   2. Label duplication on certain primitive types (progress_bar fixed separately, others
    #      may still be affected).
    #   3. Internal role names ("HOOK", "PAYOFF") could appear as visible on-screen text when
    #      propagated through param pipelines that used beat role as a title fallback.
    # What remains active and untouched:
    #   - 3 deterministic scanners (money_counter, calendar_date, growth_curve) — match real
    #     transcript words and amounts, not beat roles.
    #   - 16 LLM narrative card styles from the storyboard (stat, quote, comparison, list,
    #     timeline, dialogue, trend, etc.) — independent of this engine.
    #   - Density budget (BROLL_MAX_PER_MINUTE) and greedy merge — still applied to all cards.
    # Re-enable and redesign once beat roles are fully decoupled from visual param extraction.

    # Lower-third auto-injection is disabled.
    # The component (broll_lower_third.py) is designed for explicit speaker
    # identification (real name + title), not for beat-driven auto-injection.
    # Auto-injection caused two classes of bugs:
    #   1. Collisions with semantic/generative cards at the same timestamp,
    #      silently dropped by _clamp_overlaps() in compose.py.
    #   2. Internal beat role names ("HOOK", "PAYOFF") leaked as visible
    #      on-screen text since _beat.get("beat") was used as the "title" param.
    # Re-enable and redesign when a real speaker-ID data source is available.

    # Generate caption cards mechanically (long format: no captions)
    if format_hint == "long":
        caption_cards = []
    else:
        caption_cards = _segment_captions(
            remapped_words=remapped_words,
            transcript_segments=transcript_segments,
            timing_map=timing_map,
            emphasis_words=caption_emphasis_words,
            word_categories=word_categories,
            max_words=4 if format_hint == "short" else _MAX_WORDS,
        )

    print(f"[STORYBOARD] {len(graphic_cards)} graphic + {len(caption_cards)} caption cards", flush=True)

    # Dead-zone audit: log gaps > 12s for monitoring (no fallback injection)
    _sorted_gc = sorted(graphic_cards, key=lambda c: float(c.get("startSec", 0)))
    _dz_gaps: list[float] = []
    _prev_end = 0.0

    for _gc2 in _sorted_gc:
        _cs = float(_gc2.get("startSec", 0))
        _ce = float(_gc2.get("endSec", _cs))
        _gap = _cs - _prev_end
        if _gap > 12.0:
            _gap_words = [w for w in remapped_words if _prev_end <= w.start <= _cs]
            _gap_text = " ".join(w.text for w in _gap_words[:20])
            print(
                f"[DEAD-ZONE] card gap {_prev_end:.1f}→{_cs:.1f}s ({_gap:.1f}s): '{_gap_text}'",
                flush=True,
            )
            _dz_gaps.append(_gap)
        _prev_end = max(_prev_end, _ce)

    _tail_gap = trimmed_duration - _prev_end
    if _tail_gap > 12.0:
        _tail_words = [w for w in remapped_words if _prev_end <= w.start]
        _tail_text = " ".join(w.text for w in _tail_words[:20])
        print(
            f"[DEAD-ZONE] tail gap {_prev_end:.1f}→{trimmed_duration:.1f}s ({_tail_gap:.1f}s): '{_tail_text}'",
            flush=True,
        )
        _dz_gaps.append(_tail_gap)

    if _dz_gaps:
        print(
            f"[DEAD-ZONE] {len(_dz_gaps)} gap(s) > 12s | max={max(_dz_gaps):.1f}s avg={sum(_dz_gaps)/len(_dz_gaps):.1f}s",
            flush=True,
        )
    else:
        print("[DEAD-ZONE] No card gaps > 12s — full coverage OK", flush=True)

    # ── Gap-fill: targeted second LLM calls for speech deserts ───────────────
    # After the dead-zone audit we know exactly which spans lack card coverage.
    # For each qualifying gap (≥ 20s, ≥ 25 speech words) we fire a focused call
    # that sees only that passage and may abstain (returns None/{}). Each survivor
    # runs the full quality chain identical to main cards.
    from math import ceil as _math_ceil

    _gf_max = _math_ceil(trimmed_duration / 90)  # proportional — no hard cap after 12 min
    _gf_gaps = _find_fill_gaps(
        _sorted_gc, remapped_words, trimmed_duration,
        threshold_s=12.0, min_words=15, max_gaps=_gf_max,
    )
    print(
        f"[GAP-FILL] {len(_gf_gaps)} qualifying gap(s) (max={_gf_max} calls)",
        flush=True,
    )

    _gf_new: list[dict] = []
    _gf_idx = 0

    for _gf_start, _gf_end, _gf_words in _gf_gaps:
        _gf_idx += 1
        print(
            f"[GAP-FILL] Evaluating gap [{_gf_start:.1f}–{_gf_end:.1f}s]"
            f" ({_gf_end - _gf_start:.0f}s | {len(_gf_words)} words)",
            flush=True,
        )
        _gf_card = _gap_fill_call(
            _gf_start, _gf_end, _gf_words, trimmed_duration, language, _gf_idx
        )
        if _gf_card is None:
            continue  # abstained or error

        # 1. Trigger-style anchor ─────────────────────────────────────────────
        _gf_style = _gf_card.get("contentHints", {}).get("style", "")
        if _gf_style in _TRIGGER_STYLES:
            _gf_anchor = _find_trigger_anchor(_gf_card, remapped_words)
            if _gf_anchor is not None and _gf_anchor > float(_gf_card.get("startSec", 0)):
                _gf_orig = float(_gf_card["startSec"])
                _gf_card["startSec"] = _gf_anchor
                if float(_gf_card.get("endSec", 0)) < _gf_anchor + 1.5:
                    _gf_card["endSec"] = round(_gf_anchor + 3.0, 3)
                print(
                    f"[GAP-FILL] TRIGGER-ANCHOR {_gf_card['id']}"
                    f" {_gf_orig:.2f}→{_gf_card['startSec']:.2f}s",
                    flush=True,
                )

        # 2. Title-based semantic anchor (non-trigger cards) ──────────────────
        else:
            _gf_title = _gf_card.get("contentHints", {}).get("title", "")
            _gf_title_cw = _content_words(_gf_title) if _gf_title else frozenset()
            if _gf_title_cw:
                _gf_s0 = float(_gf_card.get("startSec", 0))
                _gf_lo = _gf_s0 - _GROUNDING_WINDOW_PRE_S
                _gf_hi = _gf_s0 + _ANCHOR_SEARCH_FORWARD_S
                _gf_matched: float | None = None
                _gf_matched_word = ""
                for _gfw in remapped_words:
                    if _gfw.start < _gf_lo:
                        continue
                    if _gfw.start > _gf_hi:
                        break
                    if _content_words(_gfw.text) & _gf_title_cw:
                        _gf_matched = _gfw.start
                        _gf_matched_word = _gfw.text
                        break
                if _gf_matched is not None and _gf_matched - _gf_s0 > 0.5:
                    _gf_prev = _gf_s0
                    _gf_card["startSec"] = round(
                        max(_gf_matched - _ANCHOR_LEAD_S, _gf_s0), 3
                    )
                    if float(_gf_card.get("endSec", 0)) < _gf_card["startSec"] + 1.5:
                        _gf_card["endSec"] = round(_gf_card["startSec"] + 3.0, 3)
                    print(
                        f"[GAP-FILL] TITLE-ANCHOR {_gf_card['id']}"
                        f" {_gf_prev:.2f}→{_gf_card['startSec']:.2f}s"
                        f" ('{_gf_matched_word}'@{_gf_matched:.2f}s)",
                        flush=True,
                    )

        # 3. Grounding guard ───────────────────────────────────────────────────
        _gf_style = _gf_card.get("contentHints", {}).get("style", "")
        if _gf_style in _TRIGGER_STYLES:
            _gf_overlap = _grounding_overlap(_gf_card, remapped_words)
            if _gf_overlap < _GROUNDING_OVERLAP_THRESHOLD:
                _gf_title = _gf_card.get("contentHints", {}).get("title", "")
                _gf_card["contentHints"]["style"] = "key_phrase" if _gf_title else "callout"
                print(
                    f"[GAP-FILL] GROUNDING REJECT {_gf_card['id']}"
                    f" {_gf_style!r}→{_gf_card['contentHints']['style']!r}"
                    f" overlap={int(_gf_overlap * 100)}%",
                    flush=True,
                )

        # 4. Segment-boundary clamp ────────────────────────────────────────────
        _apply_segment_clamp([_gf_card], _seg_out)

        # 5. Re-clamp card to its originating gap ─────────────────────────────
        # Ensures _apply_segment_clamp hasn't moved the card past the gap boundary.
        _gf_card["startSec"] = round(
            max(_gf_start, min(float(_gf_card["startSec"]), _gf_end - 2.0)), 3
        )
        _gf_card["endSec"] = round(
            min(
                _gf_end,
                max(float(_gf_card["endSec"]), float(_gf_card["startSec"]) + 2.5),
            ),
            3,
        )

        # 6. Anti-overlap — must not touch any placed card (main or gap-fill) ─
        _gf_all_placed = graphic_cards + _gf_new
        _gf_conflict = [
            c for c in _gf_all_placed
            if float(c.get("startSec", 0)) < float(_gf_card["endSec"])
            and float(c.get("endSec", 0)) > float(_gf_card["startSec"])
        ]
        if _gf_conflict:
            _gfc0 = _gf_conflict[0]
            print(
                f"[GAP-FILL] OVERLAP REJECT {_gf_card['id']}"
                f" [{_gf_card['startSec']:.2f}–{_gf_card['endSec']:.2f}s]"
                f" overlaps {_gfc0.get('id','?')}"
                f" [{_gfc0.get('startSec','?')}–{_gfc0.get('endSec','?')}s]",
                flush=True,
            )
            continue

        _gf_new.append(_gf_card)
        print(
            f"[GAP-FILL] ACCEPTED {_gf_card['id']}"
            f" style={_gf_card['contentHints'].get('style')!r}"
            f" [{_gf_card['startSec']:.2f}–{_gf_card['endSec']:.2f}s]",
            flush=True,
        )

    if _gf_new:
        graphic_cards = sorted(
            graphic_cards + _gf_new,
            key=lambda c: float(c.get("startSec", 0)),
        )
        print(
            f"[GAP-FILL] Merged {len(_gf_new)} card(s) — total: {len(graphic_cards)}",
            flush=True,
        )
    else:
        print("[GAP-FILL] No new cards inserted", flush=True)

    # ── Rhythm split-stage injection ─────────────────────────────────────────
    # ── Rhythm split-stage injection — 6s grid, skip slots covered by rich cards ──
    _rhythm_splits = _inject_rhythm_split_stage(
        graphic_cards=graphic_cards,
        remapped_words=remapped_words,
        trimmed_duration=trimmed_duration,
        style_pack=style_pack,
        subject_side=subject_side,
        layout=layout,
    )
    if _rhythm_splits:
        graphic_cards = sorted(
            graphic_cards + _rhythm_splits,
            key=lambda c: float(c.get("startSec", 0)),
        )
        print(
            f"[RHYTHM-SPLIT] Merged {len(_rhythm_splits)} card(s) — total: {len(graphic_cards)}",
            flush=True,
        )
    else:
        print("[RHYTHM-SPLIT] No slots available (all covered by rich cards or silence)", flush=True)

    # ── Full-cover exclusion pass ─────────────────────────────────────────────
    # Drop card_overlay cards that overlap a full_cover window. full_cover cards
    # consume the entire canvas; any overlay card behind them is invisible and
    # would waste GSAP budget.
    _fc_windows = [
        (c["startSec"], c["endSec"])
        for c in graphic_cards
        if c.get("_family") == "full_cover"
    ]
    if _fc_windows:
        _fc_kept, _fc_dropped = [], []
        for _c in graphic_cards:
            if _c.get("_family") == "full_cover":
                _fc_kept.append(_c)
            elif any(_c["startSec"] < _we and _c["endSec"] > _ws for _ws, _we in _fc_windows):
                _fc_dropped.append(_c)
            else:
                _fc_kept.append(_c)
        graphic_cards = _fc_kept
        if _fc_dropped:
            print(
                f"[FULL-COVER] Dropped {len(_fc_dropped)} overlapping card_overlay card(s): "
                f"{[_c['id'] for _c in _fc_dropped]}",
                flush=True,
            )
        # Assertion: no card_overlay must overlap a full_cover window after the pass.
        for _c in graphic_cards:
            if _c.get("_family") != "full_cover":
                for _ws, _we in _fc_windows:
                    assert not (_c["startSec"] < _we and _c["endSec"] > _ws), (
                        f"[FULL-COVER] Overlap remains after exclusion pass: "
                        f"{_c['id']} [{_c['startSec']}, {_c['endSec']}] overlaps [{_ws}, {_we}]"
                    )

    storyboard = {
        "composition": {
            "fps": 30,
            "width": width,
            "height": height,
            "durationSeconds": round(trimmed_duration, 3),
            "layout": layout,
            "themeId": "noir",
        },
        "videoTrack": {
            "sourcePath": "input-video.mp4",
            "startSec": 0,
            "endSec": round(trimmed_duration, 3),
        },
        "cards": graphic_cards + caption_cards,
    }

    return storyboard
