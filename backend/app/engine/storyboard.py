"""
Storyboard generation: graphic overlay cards + deterministic captions.

Stage 2 of the HyperFrames pipeline. Takes the pre-trimmed video's
timing map and transcript, produces a storyboard JSON that compose.py
(Stage 3) assembles into a HyperFrames composition.

Two distinct card types in one storyboard:
  - Graphic cards: designed by Claude API, sparse, beat-driven
  - Caption cards: mechanically generated from transcript words,
    dense, every spoken word guaranteed, no LLM variability
"""
from __future__ import annotations

import json
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
_ANCHOR_SEARCH_FORWARD_S = 6.0        # how far ahead to scan for trigger keyword position
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

    # Merge apostrophe-split tokens (French elisions: m'a, l'équipe, j'ai, etc.)
    # Whisper often splits these into ["m'", "a"] or ["m", "'a"] as separate words.
    # Both U+0027 (straight) and U+2019 (right single quotation mark) are handled.
    _STORYBOARD_APOS = ("'", "'")  # U+0027 + U+2019
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

    return cards


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
    # watch-time retention. Long format keeps its original pace tiers unchanged.
    if trimmed_duration < 60:
        base_pace = 4.5 if format_hint == "short" else 7
    elif trimmed_duration < 180:
        base_pace = 10
    elif trimmed_duration < 600:
        base_pace = 16
    elif trimmed_duration < 1800:
        base_pace = 28
    else:
        base_pace = 45

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
        beat_summary.append({
            "beat": seg.get("beat", ""),
            "outStart": round(out_start, 2),
            "outEnd": round(out_end, 2),
            "reason": seg.get("reason", ""),
            "retention_note": seg.get("retention_note", ""),
            "score": seg.get("score", 0),
        })

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
    "number": "<if a stat/number is featured>",
    "style": "stat"|"key_phrase"|"quote"|"callout"|"comparison"|"list"|"question"|"timeline"|"dialogue"|"trend"|"attributed_quote"|"carousel"|"definition"|"checklist"|"score"|"mindmap"|"data_chart"|"instagram-follow"|"tiktok-follow"|"yt-lower-third"|"news_ticker"|"rating"|"map_location"|"progress_bar"|"before_after_image"|"countdown"|"poll_question"|"myth_vs_fact"|"step_number"|"quote_carousel"|"emoji_reaction"|"price_tag"|"warning_soft"|"testimonial"|"versus_battle"|"recap_summary"|"location_journey"|"formula_equation"|"roadmap_milestone"|"pros_cons"|"star_rating_review"|"income_reveal"|"question_answer_pair"|"chapter_marker"|"secret_reveal"|"objection_response"|"data_bar_chart"|"cause_effect"|"number_ranking"|"hand_written_note"|"speech_bubble_thought"|"calendar_date_highlight"|"percentage_split"|"red_flag_list"|"success_metric_badge"|"client_avatar_persona"|"book_recommendation"|"tool_stack"|"revenue_breakdown"|"age_milestone"|"contrarian_take"|"action_step_cta"|"story_chapter_transition"|"live_reaction_split"|"hidden_cost_reveal"|"social_proof_counter"|"timeline_prediction"|"red_thread_connector"|"silent_beat_pause"|"comment_reply_style"|"before_you_scroll"|"traffic_light_status"|"day_in_life_schedule"|"skill_tree_unlock"|"audience_poll_result"|"broken_promise_tracker"|"ingredient_list"|"resource_allocation"|"fill_in_the_blank",
    "left_label": "<comparison: left side label>",
    "left_value": "<comparison: left side value>",
    "right_label": "<comparison: right side label>",
    "right_value": "<comparison: right side value>",
    "items": ["<list/checklist: item 1>", "<list/checklist: item 2>", ...],
    "steps": ["<timeline: step 1>", "<timeline: step 2>", ...],
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
    "blank_word": "<fill_in_the_blank: the single word or short phrase that fills the blank>"
  }}
}}

ZONES — where the card sits on screen:
  fullscreen    — covers whole canvas (hero moments, big statements)
  side-panel    — left or right portion (data, comparisons)
  video-overlay — full canvas but transparent (glass effect over video)
  NEVER use "lower-third" — that zone is reserved for captions only.
{f"SUBJECT POSITION: the speaker occupies the {subject_side} side of the frame. Place data-heavy cards (stat, list, comparison) on the OPPOSITE side so they don't obscure the face." if subject_side and subject_side != "center" else ""}
RULES:
- CARD COUNT CEILING: {target_cards} cards maximum for a {trimmed_duration:.0f}s video. This is a hard ceiling, not a target — only place a card when the moment genuinely deserves one. A video with 5 high-quality cards is better than one with 10 forced cards. Never invent or pad cards just to approach the ceiling.
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
  "list" — speaker names 3+ distinct items/reasons/steps/fears/goals
    in sequence. ALWAYS use list for enumerated content, never collapse
    multiple items into a single callout or quote card.
  "timeline" — sequential/temporal progression (events along a path
    with dates or temporal ordering). Use timeline, not list, when
    items have a clear chronological sequence.
  "comparison" — speaker contrasts two distinct things (old/new, us/them,
    method A vs method B). Exactly 2 sides required. NOT for the same
    thing before vs after a change (use before_after_image for that).
  "stat" — a specific number or metric is featured.
  "key_phrase" — a single impactful statement (not enumerated).
  "quote" — unattributed statement the speaker emphasizes.
  "attributed_quote" — quote with a named source ("X said...").
  "carousel" — 2-4 short related statements that cycle within one
    card window (e.g. multiple quick tips, rotating perspectives).
  "definition" — speaker introduces a term/concept and explains it.
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
  "callout" — supplementary context or aside (catch-all, use only
    when no other type fits).
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
  "myth_vs_fact" — speaker debunks a myth and states the real fact.
    Distinct from callout (callout adds context, not a correction).
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
  "warning_soft" — speaker flags a caution, common mistake, or risk (soft
    register — not a crisis). Uses pack accent color only, NOT orange/red.
    Distinct from callout (callout is neutral context). Provide "warning_text".
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
  "income_reveal" — speaker dramatically reveals an income, revenue, or
    financial figure. Distinct from stat (stat is informational; income_reveal
    has suspense/reveal energy). Provide "income_value" (the number string)
    + "income_context" (brief qualifier).
  "question_answer_pair" — speaker poses a question AND immediately answers
    it in the same breath (e.g. "Qu'est-ce que c'est ? C'est une méthode
    en 3 étapes"). BOTH question and answer are present in the same segment.
    Distinct from question (question leaves the answer to the viewer or to
    a later beat). NOT poll_question (poll is an open vote). Provide
    "qa_question" + "qa_answer".
  "chapter_marker" — speaker introduces a new major section or chapter of
    a longer video (e.g. "on passe maintenant à la partie 2", "chapitre 3").
    Use for structural dividers that announce a topic shift. Distinct from
    step_number (step_number is a numbered item within a sequence; chapter_marker
    is a top-level section break). Provide "chapter_num" + "chapter_title".
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
    compare to each other (2-4 values with labels). Distinct from stat (stat is
    a single number). Distinct from score (score is competitive ranking).
    Distinct from trend (trend is a directional curve). Distinct from data_chart
    (data_chart is the pre-existing hand-coded bar chart; data_bar_chart is
    the pack-styled Wave 4 version — prefer data_bar_chart for new cards).
    Provide "bar_labels" list + "bar_values" list of float (same length, 2-4 items).
  "cause_effect" — speaker explicitly states a cause-and-effect relationship
    ("parce que X, donc Y", "X entraîne Y", "si X alors Y"). REQUIRES both
    cause and effect to be named. Distinct from callout (callout is one point).
    Distinct from comparison (comparison contrasts two things; cause_effect
    shows a directional causal link). Provide "cause_text" + "effect_text".
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
  "book_recommendation" — speaker explicitly names a book they recommend or
    reference ("je te conseille de lire X", "le livre qui a changé ma vie…").
    REQUIRES both title and author to be identifiable. Distinct from testimonial
    (testimonial is a person's review; book_recommendation is a titled work).
    Distinct from key_phrase (key_phrase is a statement; book_recommendation
    frames a specific book). Provide "book_title" + "book_author".
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
  "contrarian_take" — speaker EXPLICITLY flags that they are voicing an unpopular
    or taboo opinion by stepping outside the content to comment on its provocativeness:
    "voici ce que personne ne dit", "j'ai une opinion impopulaire", "la vérité que
    tu ne veux pas entendre", "je vais dire quelque chose que personne n'ose dire",
    or equivalent meta-commentary framing. REQUIRES this explicit signaling — do NOT
    use when the speaker makes a bold, surprising, or counter-intuitive claim without
    first announcing it as controversial (use callout or key_phrase for those). Do NOT
    use for warnings or cautions (use warning_soft or red_flag_list). Uses pack accent
    color unchanged — signals tension through typography only. Distinct from warning_soft
    (warning_soft is a caution without opinion framing; contrarian_take requires the
    speaker to call out the take as controversial). Distinct from callout (callout is
    neutral context; contrarian_take has explicit editorial tension framing). Provide "take_text".
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
  "live_reaction_split" — speaker contrasts what people expected with what
    actually happened ("on pensait que X… mais en réalité Y"). REQUIRES both
    sides to be stated. Distinct from before_after_image (that is a transformation
    over time; live_reaction_split is expectation vs outcome). Distinct from
    versus_battle (versus contrasts two options; live_reaction_split is
    expected-vs-reality). Trigger-style: the reveal of reality must be literally
    spoken. Provide "expected_text" + "reality_text".
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
    prévoit pour Z"). REQUIRES at least one confirmed step and one predicted step.
    Distinct from timeline (timeline is all-confirmed events; timeline_prediction
    explicitly separates confirmed from predicted). Provide "confirmed_steps"
    list + "predicted_steps" list (1-4 each).
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
- SCENE PRIMITIVE NOTE — a deterministic semantic scanner runs after your
  storyboard and auto-inserts animated scene primitives when it detects
  exact trigger phrases (e.g. "première vente", "mon mentor m'a dit",
  "j'ai investi", "mis à l'échelle"). When the scanner fires, its card
  (confidence 0.88) takes priority over yours (confidence 0.70) and your
  card is suppressed. You do NOT need to avoid these moments — just assign
  the best normal card type you would otherwise choose (key_phrase, callout,
  stat, quote, etc.) and the scanner will upgrade it automatically when the
  exact phrase is present. Never output a card with style "__broll__" —
  that is reserved for scanner output only.
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

    user_msg = f"""VIDEO DURATION: {trimmed_duration:.1f}s

BEAT SPINE (the narrative structure):
{json.dumps(script_out, indent=2)}

SEGMENT DETAILS (scores, reasons, retention notes):
{json.dumps(beat_summary, indent=2)}

KEY LINES (most memorable moments):
{json.dumps(key_lines)}

Design graphic overlay cards for this video — up to {target_cards} maximum. Place a card only at moments that genuinely earn one: a key claim, a surprising stat, a narrative turning point, or a concept the viewer needs to see to understand. Skip the moment if no card adds value. Quality and narrative relevance always take priority over reaching the card count ceiling."""

    client = Anthropic()
    try:
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
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
        # Clamp to video duration
        for card in cards:
            card["startSec"] = max(0, min(float(card.get("startSec", 0)), trimmed_duration - 1))
            card["endSec"] = max(card["startSec"] + 1, min(float(card.get("endSec", 0)), trimmed_duration))
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
        print(f"[STORYBOARD] Generated {len(cards)} graphic cards", flush=True)
        return cards
    except Exception as e:
        print(f"[STORYBOARD] Claude API error: {e}", flush=True)
        return []


_MAX_BROLL_PER_MINUTE = 3   # hard cap: no more than 3 B-roll cards per 60s

def _merge_cards(
    llm_cards: list[dict],
    semantic_cards: list[dict],
    min_gap_s: float = 4.5,
    video_duration_s: float = 0.0,
) -> list[dict]:
    """Greedy-by-confidence merge of LLM graphic cards + semantic B-roll cards.

    LLM cards are assigned implicit confidence 0.70.
    Semantic cards carry their own confidence value.
    All cards are sorted descending by confidence; we walk in that order,
    accept a card, and immediately suppress any remaining card whose window
    overlaps within min_gap_s (both directions).

    Density cap: _MAX_BROLL_PER_MINUTE (default 3) — prevents overlay saturation
    on content-rich videos. Cap is proportional to video duration.
    Set env BROLL_MAX_PER_MINUTE to override without a code push.
    """
    import os
    _effective_cap = float(os.environ.get("BROLL_MAX_PER_MINUTE", _MAX_BROLL_PER_MINUTE))
    max_cards = (
        max(1, int(_effective_cap * video_duration_s / 60.0))
        if video_duration_s > 0
        else 999
    )

    # Attach confidence to LLM cards (implicit 0.70)
    annotated: list[tuple[float, dict]] = []
    for c in llm_cards:
        annotated.append((float(c.get("_confidence", 0.70)), c))
    for c in semantic_cards:
        annotated.append((float(c.get("_confidence", 0.88)), c))

    # Sort descending by confidence; ties broken by earliest timestamp.
    # Guard against LLM cards where startSec was emitted as "" (non-fatal edge case).
    annotated.sort(
        key=lambda t: (t[0], -float(t[1].get("startSec", 0) or 0)),
        reverse=True,
    )

    accepted: list[dict] = []
    accepted_ivs: list[tuple[float, float]] = []

    def _iv_gap(s1: float, e1: float, s2: float, e2: float) -> float:
        return max(0.0, max(s1, s2) - min(e1, e2))

    for conf, card in annotated:
        if len(accepted) >= max_cards:
            print(
                f"[BROLL-MERGE] density cap reached ({max_cards} cards"
                f" for {video_duration_s:.0f}s video) — dropping remaining",
                flush=True,
            )
            break
        cstart = float(card.get("startSec", 0) or 0)
        cend   = float(card.get("endSec", "") or cstart + 5.0)
        suppressed = any(
            _iv_gap(cstart, cend, a_s, a_e) < min_gap_s
            for a_s, a_e in accepted_ivs
        )
        if suppressed:
            print(
                f"[BROLL-MERGE] suppressed card {card.get('id','?')} "
                f"at {cstart:.2f}s (conf={conf:.2f}) — within {min_gap_s}s of accepted card",
                flush=True,
            )
            continue
        accepted.append(card)
        accepted_ivs.append((cstart, cend))

    # Restore display order (chronological)
    accepted.sort(key=lambda c: float(c.get("startSec", 0)))
    print(
        f"[BROLL-MERGE] {len(llm_cards)} LLM + {len(semantic_cards)} semantic → "
        f"{len(accepted)} accepted after merge (cap={max_cards})",
        flush=True,
    )
    return accepted


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
) -> dict:
    """Generate a complete storyboard: graphic cards + caption cards.

    Returns a storyboard dict matching the graphic-overlays schema.
    """
    width, height = (1080, 1920) if format_hint == "short" else (1920, 1080)
    layout = "portrait" if format_hint == "short" else "landscape"

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
        _title = _gc.get("contentHints", {}).get("title", "")
        if not _title:
            continue
        _title_cw = _content_words(_title)
        if not _title_cw:
            continue
        _start_s = float(_gc.get("startSec", 0))
        _lo = _start_s - _GROUNDING_WINDOW_PRE_S
        _hi = _start_s + _ANCHOR_SEARCH_FORWARD_S
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
            print(
                f"[STORYBOARD] TITLE-ANCHOR card {_gc.get('id','?')} style={_style!r} "
                f"startSec {_orig_start:.2f}→{_gc['startSec']:.2f}s "
                f"(title keyword '{_matched_word}'@{_matched:.2f}s matched in Whisper)",
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

    # Semantic B-roll scan + greedy merge
    try:
        from app.engine.semantic_scanner import scan_words
        from app.engine import broll_registry as _broll_reg
        _sem_hits = scan_words(remapped_words)
        _sem_cards: list[dict] = []
        _card_counter = len(graphic_cards) + 1
        for _hit in _sem_hits:
            _btype = _broll_reg.REGISTRY.get(_hit.broll_type)
            if _btype is None:
                continue
            _cid = f"broll-{_card_counter:03d}"
            _card_counter += 1
            _end_sec = min(
                round(_hit.start_sec + _btype.default_duration, 3),
                trimmed_duration,
            )
            _sem_cards.append({
                "id": _cid,
                "startSec": round(_hit.start_sec, 3),
                "endSec": _end_sec,
                "zone": _btype.preferred_zone,
                "contentHints": {"style": "__broll__"},
                "_broll_type": _hit.broll_type,
                "_broll_params": _hit.params,
                "_confidence": _hit.confidence,
                "_text_span": _hit.text_span,
            })
            print(
                f"[BROLL-MERGE] semantic hit: {_hit.broll_type} "
                f"'{_hit.text_span}' @{_hit.start_sec:.2f}s conf={_hit.confidence:.2f}",
                flush=True,
            )
        graphic_cards = _merge_cards(graphic_cards, _sem_cards, video_duration_s=trimmed_duration)
    except Exception as _broll_exc:
        print(f"[BROLL-MERGE] non-fatal error in semantic scan/merge: {_broll_exc}", flush=True)

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

    # Generate caption cards mechanically
    caption_cards = _segment_captions(
        remapped_words=remapped_words,
        transcript_segments=transcript_segments,
        timing_map=timing_map,
        emphasis_words=caption_emphasis_words,
        word_categories=word_categories,
        max_words=4 if format_hint == "short" else _MAX_WORDS,
    )

    print(f"[STORYBOARD] {len(graphic_cards)} graphic + {len(caption_cards)} caption cards", flush=True)

    # Dead-zone audit + fill: log gaps > 12s; inject fallback key_phrase cards for gaps > 20s
    _sorted_gc = sorted(graphic_cards, key=lambda c: float(c.get("startSec", 0)))
    _dz_gaps: list[float] = []
    _fallback_cards: list[dict] = []
    _prev_end = 0.0

    def _dz_fill(gap_start: float, gap_end: float) -> None:
        gap = gap_end - gap_start
        if gap <= 20.0:
            return
        n_ideal = max(1, int(gap / 40.0))
        n = min(3, n_ideal)
        if n_ideal > n:
            print(
                f"[DEAD-ZONE-FILL] skipped (cap reached): gap at {gap_start:.1f}s-{gap_end:.1f}s"
                f" needs {n_ideal} cards, injecting {n}",
                flush=True,
            )
        step = gap / (n + 1)
        for _i in range(n):
            pos = gap_start + step * (_i + 1)
            c_start = round(max(gap_start, pos - 3.0), 3)
            c_end = round(min(gap_end, pos + 3.0), 3)
            ws = [w for w in remapped_words if c_start - 2.0 <= w.start <= c_end + 2.0]
            phrase = " ".join(w.text for w in ws[:20])
            if not phrase:
                ws = [w for w in remapped_words if gap_start <= w.start <= gap_end]
                phrase = " ".join(w.text for w in ws[:20])
            phrase = phrase.replace("—", " ").replace("–", " ").strip()
            _fallback_cards.append({
                "id": f"dz-fill-{len(_fallback_cards) + 1:03d}",
                "type": "key_phrase",
                "zone": "video-overlay",
                "startSec": c_start,
                "endSec": c_end,
                "contentHints": {"phrase": phrase or "…"},
                "_fallback": True,
                "_confidence": 0.50,
            })
            print(
                f"[DEAD-ZONE-FILL] injected key_phrase {c_start:.1f}→{c_end:.1f}s"
                f" in gap {gap_start:.1f}→{gap_end:.1f}s ({gap:.1f}s)",
                flush=True,
            )

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
        _dz_fill(_prev_end, _cs)
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
    _dz_fill(_prev_end, trimmed_duration)

    if _dz_gaps:
        print(
            f"[DEAD-ZONE] {len(_dz_gaps)} gap(s) > 12s | max={max(_dz_gaps):.1f}s avg={sum(_dz_gaps)/len(_dz_gaps):.1f}s",
            flush=True,
        )
    else:
        print("[DEAD-ZONE] No card gaps > 12s — full coverage OK", flush=True)

    if _fallback_cards:
        graphic_cards.extend(_fallback_cards)
        graphic_cards.sort(key=lambda c: float(c.get("startSec", 0)))
        print(f"[DEAD-ZONE-FILL] {len(_fallback_cards)} fallback card(s) injected total", flush=True)

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
