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
from typing import Any

from app.engine.captions import WordTiming
from app.engine.pretrim import TimingMap

# ── Caption segmentation constants ────────────────────────────────────
_PAUSE_GAP = 0.15       # Seconds gap between words to trigger a caption break
_MAX_WORDS = 7           # Maximum words per caption card
_ORPHAN_MIN_DUR = 0.25   # 1-word groups shorter than this merge into neighbors


def _segment_captions(
    remapped_words: list[WordTiming],
    transcript_segments: list[dict],
    timing_map: TimingMap,
    emphasis_words: list[str],
    word_categories: dict[str, str],
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
    merged_words: list[dict] = []
    for w in all_words:
        if merged_words and (
            w["text"].startswith("'") or
            merged_words[-1]["text"].endswith("'")
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

    # Step 1: group by sentence boundary OR word count
    raw_groups: list[list[dict]] = []
    current: list[dict] = []
    for w in all_words:
        if current:
            if w.get("seg_start") or len(current) >= _MAX_WORDS:
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
            if i + 1 < len(raw_groups) and len(raw_groups[i + 1]) + len(g) <= _MAX_WORDS + 1:
                raw_groups[i + 1] = g + raw_groups[i + 1]
                i += 1
                continue
            # Try backward merge
            if merged and len(merged[-1]) + len(g) <= _MAX_WORDS + 1:
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
) -> list[dict]:
    """Generate graphic overlay cards via Claude API call.

    Uses the narrative context (beat spine, key lines, retention notes)
    to design cards that reinforce the emotional arc.
    """
    from anthropic import Anthropic
    from app.core.config import settings

    # Compute card density per graphic-overlays formula
    if trimmed_duration < 60:
        base_pace = 7
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
    "style": "stat"|"key_phrase"|"quote"|"callout"|"comparison"|"list"|"question"|"timeline"|"dialogue"|"trend"|"attributed_quote"|"carousel"|"definition"|"checklist"|"score"|"mindmap",
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
    "branches": ["<mindmap: branch 1>", "<mindmap: branch 2>", ...]
  }}
}}

ZONES — where the card sits on screen:
  fullscreen    — covers whole canvas (hero moments, big statements)
  side-panel    — left or right portion (data, comparisons)
  video-overlay — full canvas but transparent (glass effect over video)
  NEVER use "lower-third" — that zone is reserved for captions only.

RULES:
- Target {target_cards} cards for a {trimmed_duration:.0f}s video
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
  "comparison" — speaker contrasts two specific things (before/after,
    old/new, us/them). Exactly 2 sides required.
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
  "score" — a score, ranking, or rating (e.g. "3-1", "Top 5", "8/10").
    NOT a stat (stat is for metrics that count up). Provide "score_text".
  "mindmap" — a central concept with 2-3 branching related ideas.
    Provide "center" + "branches" array.
  "callout" — supplementary context or aside (catch-all, use only
    when no other type fits).
  "dialogue" — speaker recounts an exchange between two people.
  "trend" — speaker describes a directional change (growth/decline).
  "question" — speaker poses a question and then answers it.
- TIMING: startSec should match when the speaker BEGINS saying the
  words the card references — synchronous with speech, like captions.
- Place cards at NARRATIVELY IMPORTANT moments — not evenly spaced

LANGUAGE: {language}
- ALL card text (kicker, title, detail, items, steps, line_a/line_b,
  attribution) MUST be in {language} — match the speaker's language exactly.

BRAND: accent color {brand_color}, content type: {content_type}, style: {editing_style}

Reply with ONLY a JSON array, no explanation."""

    user_msg = f"""VIDEO DURATION: {trimmed_duration:.1f}s

BEAT SPINE (the narrative structure):
{json.dumps(script_out, indent=2)}

SEGMENT DETAILS (scores, reasons, retention notes):
{json.dumps(beat_summary, indent=2)}

KEY LINES (most memorable moments):
{json.dumps(key_lines)}

Design {target_cards} graphic overlay cards for this video."""

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
        print(f"[STORYBOARD] Generated {len(cards)} graphic cards", flush=True)
        return cards
    except Exception as e:
        print(f"[STORYBOARD] Claude API error: {e}", flush=True)
        return []


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

    # Timing audit — after snap, log CRITICAL if card still has no speech nearby.
    for _gc in graphic_cards:
        _start = float(_gc.get("startSec", 0))
        _near = [w for w in remapped_words if _start - 0.3 <= w.start <= _start + 1.0]
        if not _near:
            _closest = min(remapped_words, key=lambda w: abs(w.start - _start), default=None)
            _cl_str = (f"nearest='{_closest.text}'@{_closest.start:.2f}s"
                       if _closest else "no words")
            print(
                f"[STORYBOARD] CRITICAL card {_gc.get('id','?')} startSec={_start:.2f}s "
                f"has NO speech in [{_start-0.3:.2f},{_start+1.0:.2f}] — {_cl_str}",
                flush=True,
            )
        else:
            print(
                f"[STORYBOARD] card {_gc.get('id','?')} startSec={_start:.2f}s "
                f"first nearby word='{_near[0].text}'@{_near[0].start:.2f}s",
                flush=True,
            )

    # Generate caption cards mechanically
    caption_cards = _segment_captions(
        remapped_words=remapped_words,
        transcript_segments=transcript_segments,
        timing_map=timing_map,
        emphasis_words=caption_emphasis_words,
        word_categories=word_categories,
    )

    print(f"[STORYBOARD] {len(graphic_cards)} graphic + {len(caption_cards)} caption cards", flush=True)

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
