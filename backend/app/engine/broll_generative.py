"""
Generative B-roll engine — LLM-powered gap-fill for uncovered strong beats.

Architecture:
  1. Scan script_structure for strong beats (role in STRONG_ROLES, score >= 6)
     that have no accepted card within ±4.5s.
  2. For each candidate, extract label/kicker DETERMINISTICALLY from transcript
     words nearest the beat midpoint (no LLM text generation).
  3. Call Haiku ONCE with all candidates in one prompt.
     LLM chooses: shape / entry / content_type / (emoji_value if emoji) / zone.
     LLM never outputs label, kicker, colors, fonts, or animation params.
  4. Validate each output card against the whitelist. Reject individually on
     any invalid field. Reject the entire call output on JSON parse failure.
  5. Insert accepted cards post-merge (gap-fill only — guaranteed no collision
     with already-accepted cards from the greedy merge).
"""
from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.engine.captions import WordTiming


# ── Allowed primitive vocabulary ──────────────────────────────────────────────

ALLOWED_SHAPES        = {"circle", "bar", "badge", "grid"}
ALLOWED_ENTRIES       = {"pop", "trace", "fade", "slide_up"}
ALLOWED_CONTENT_TYPES = {"number", "text", "emoji"}
ALLOWED_ZONES         = {"upper-data", "lower-third"}

STRONG_ROLES = {
    "payoff", "realization", "principle", "climax",
    "hook", "stat", "emotional_end", "amplify",
}
STRONG_SCORE_MIN = 6
MIN_GAP_S        = 4.5
DEFAULT_DURATION = 5.0


# ── Label / kicker extraction (deterministic, from transcript) ─────────────────

def _words_in_window(
    remapped_words: list,
    start: float,
    end: float,
    pad: float = 0.5,
) -> list:
    return [
        w for w in remapped_words
        if start - pad <= float(getattr(w, "start", 0)) <= end + pad
    ]


def _extract_label(remapped_words: list, beat_start: float, beat_end: float,
                   max_words: int = 4) -> str:
    """Pick up to max_words words nearest the beat midpoint, in spoken order."""
    mid = (beat_start + beat_end) / 2.0
    window = _words_in_window(remapped_words, beat_start, beat_end)
    if not window:
        window = _words_in_window(remapped_words, beat_start, beat_end, pad=2.0)
    if not window:
        return ""
    window_sorted = sorted(window, key=lambda w: abs(float(getattr(w, "start", 0)) - mid))
    chosen = sorted(window_sorted[:max_words], key=lambda w: float(getattr(w, "start", 0)))
    return " ".join(getattr(w, "text", "").strip() for w in chosen).strip()


def _extract_kicker(remapped_words: list, beat_start: float,
                    max_words: int = 2) -> str:
    """Pick up to max_words words immediately before the beat start."""
    before = [
        w for w in remapped_words
        if beat_start - 3.0 <= float(getattr(w, "start", 0)) < beat_start
    ]
    if not before:
        return ""
    before_sorted = sorted(before, key=lambda w: float(getattr(w, "start", 0)))
    chosen = before_sorted[-max_words:]
    return " ".join(getattr(w, "text", "").strip() for w in chosen).strip()


def _extract_number(remapped_words: list, beat_start: float,
                    beat_end: float) -> str | None:
    """Return the first numeral found in the beat window, or None."""
    window = _words_in_window(remapped_words, beat_start, beat_end, pad=1.0)
    _num = re.compile(r'\b\d+(?:[.,]\d+)?(?:\s*[kKmM%])?\b')
    for w in sorted(window, key=lambda w: float(getattr(w, "start", 0))):
        m = _num.search(getattr(w, "text", ""))
        if m:
            return m.group().strip()
    return None


# ── Gap helper ────────────────────────────────────────────────────────────────

def _interval_gap(s1: float, e1: float, s2: float, e2: float) -> float:
    """Time gap between two [s, e] intervals. Returns 0.0 if they overlap."""
    return max(0.0, max(s1, s2) - min(e1, e2))


def _card_intervals(cards: list[dict]) -> list[tuple[float, float]]:
    return [
        (float(c.get("startSec", 0)), float(c.get("endSec", c.get("startSec", 0) + DEFAULT_DURATION)))
        for c in cards
    ]


# ── Candidate identification ──────────────────────────────────────────────────

def _find_candidates(
    script_structure: list[dict],
    accepted_cards: list[dict],
    remapped_words: list,
    timing_map,
    trimmed_duration: float,
) -> list[dict]:
    """Return beats that are strong but have no accepted card within MIN_GAP_S."""
    accepted_ivs = _card_intervals(accepted_cards)

    candidates = []
    for beat in script_structure:
        role  = beat.get("beat", "")
        score = int(beat.get("score", 0))
        if role not in STRONG_ROLES and score < STRONG_SCORE_MIN:
            continue

        src_s = float(beat.get("start", 0))
        src_e = float(beat.get("end", src_s + 3.0))
        out_s = timing_map.source_to_output(src_s)
        out_e = timing_map.source_to_output(src_e)

        if out_e <= out_s or out_s >= trimmed_duration:
            continue

        new_end = min(out_s + DEFAULT_DURATION, trimmed_duration)
        if any(_interval_gap(out_s, new_end, a_s, a_e) < MIN_GAP_S for a_s, a_e in accepted_ivs):
            continue

        label  = _extract_label(remapped_words, out_s, out_e)
        kicker = _extract_kicker(remapped_words, out_s)

        # Context window: ~30 words around the beat for the LLM prompt
        ctx_words = _words_in_window(remapped_words, out_s, out_e, pad=3.0)
        ctx_text  = " ".join(getattr(w, "text", "") for w in sorted(
            ctx_words, key=lambda w: float(getattr(w, "start", 0))
        ))[:200]

        candidates.append({
            "beat_role":    role,
            "score":        score,
            "out_start":    round(out_s, 3),
            "out_end":      min(round(out_e, 3), trimmed_duration),
            "label":        label,
            "kicker":       kicker,
            "ctx_text":     ctx_text,
        })

    return candidates


# ── Haiku call ────────────────────────────────────────────────────────────────

def _build_prompt(candidates: list[dict], pack: dict, language: str) -> str:
    accent = pack.get("accent", "#4cc9f0")
    bg     = pack.get("bg", "#1a1a1a")
    text   = pack.get("text", "#f1f1f1")

    items = []
    for i, c in enumerate(candidates):
        items.append(
            f'  [{i}] role={c["beat_role"]} score={c["score"]}'
            f' t={c["out_start"]:.1f}-{c["out_end"]:.1f}s'
            f' ctx="{c["ctx_text"][:120]}"'
        )
    items_str = "\n".join(items)

    return f"""\
You assign visual primitive parameters for animated graphic card overlays.

STYLE PACK (do NOT output these — the renderer uses them automatically):
  bg={bg}  accent={accent}  text={text}

VOCABULARY (only these values are valid):
  shape:        circle | bar | badge | grid
  entry:        pop | trace | fade | slide_up
  content_type: number | text | emoji
  zone:         upper-data | lower-third

RULES (strict):
  - Output ONLY the JSON array below. No prose, no explanation.
  - One object per candidate, same order as input.
  - Allowed fields per object: shape, entry, content_type, emoji_value, zone
  - emoji_value: required only when content_type="emoji". 1-2 chars. Otherwise omit.
  - Do NOT output: label, kicker, colors, fonts, animation params, or any other field.
  - Prefer content_type="text" when the beat is a statement/principle.
  - Prefer content_type="emoji" for emotional/climax beats.
  - Prefer content_type="number" only when the context text contains a numeral.
  - Prefer entry="trace" for bar shape. Prefer entry="pop" for badge and circle.
  - Language of context: {language}

CANDIDATES:
{items_str}

OUTPUT FORMAT (array, exactly {len(candidates)} objects):
[{{"shape":"badge","entry":"pop","content_type":"emoji","emoji_value":"🎯","zone":"upper-data"}},...]"""


def _call_haiku(candidates: list[dict], pack: dict, language: str) -> list[dict] | None:
    """One Haiku call for all candidates. Returns raw parsed list or None on failure."""
    import os
    from anthropic import Anthropic

    model = os.environ.get("BROLL_GENERATIVE_MODEL", "claude-haiku-4-5-20251001")
    client = Anthropic()
    prompt = _build_prompt(candidates, pack, language)

    t0 = time.perf_counter()
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        print(f"[BROLL-GENERATIVE] Haiku API error: {exc}", flush=True)
        return None

    latency = time.perf_counter() - t0
    in_tok  = resp.usage.input_tokens
    out_tok = resp.usage.output_tokens
    cost    = (in_tok * 0.80 + out_tok * 4.00) / 1_000_000
    print(
        f"[BROLL-GENERATIVE] model={model} latency={latency:.1f}s"
        f" tokens={in_tok}in+{out_tok}out cost=${cost:.5f}",
        flush=True,
    )

    raw = resp.content[0].text.strip()
    # Extract JSON array
    m = re.search(r'\[.*\]', raw, re.DOTALL)
    if not m:
        print(f"[BROLL-GENERATIVE] no JSON array in response: {raw[:200]}", flush=True)
        return None

    try:
        parsed = json.loads(m.group())
    except json.JSONDecodeError:
        # Try _repair_json from planner
        try:
            from app.agent.planner import _repair_json
            parsed = json.loads(_repair_json(m.group()))
        except Exception as repair_exc:
            print(f"[BROLL-GENERATIVE] JSON repair failed: {repair_exc}", flush=True)
            return None

    if not isinstance(parsed, list):
        print(f"[BROLL-GENERATIVE] output is not a list: {type(parsed)}", flush=True)
        return None

    return parsed


# ── Validation ────────────────────────────────────────────────────────────────

def _validate(obj: dict, idx: int) -> dict | None:
    """Whitelist validation. Returns cleaned dict or None on rejection."""
    shape        = obj.get("shape", "")
    entry        = obj.get("entry", "")
    content_type = obj.get("content_type", "")
    zone         = obj.get("zone", "upper-data")

    if shape not in ALLOWED_SHAPES:
        print(
            f"[BROLL-GENERATIVE] skip candidate[{idx}]"
            f" — invalid shape={shape!r} (allowed: {sorted(ALLOWED_SHAPES)})",
            flush=True,
        )
        return None
    if entry not in ALLOWED_ENTRIES:
        print(
            f"[BROLL-GENERATIVE] skip candidate[{idx}]"
            f" — invalid entry={entry!r} (allowed: {sorted(ALLOWED_ENTRIES)})",
            flush=True,
        )
        return None
    if content_type not in ALLOWED_CONTENT_TYPES:
        print(
            f"[BROLL-GENERATIVE] skip candidate[{idx}]"
            f" — invalid content_type={content_type!r} (allowed: {sorted(ALLOWED_CONTENT_TYPES)})",
            flush=True,
        )
        return None
    if zone not in ALLOWED_ZONES:
        zone = "upper-data"

    # Sanitise emoji_value: only if content_type=="emoji", max 2 chars
    emoji_value = ""
    if content_type == "emoji":
        ev = str(obj.get("emoji_value", "")).strip()
        # Accept at most 2 Unicode code points (single or compound emoji)
        graphemes = list(ev)[:2]
        emoji_value = "".join(graphemes) if graphemes else "✨"

    # Strip every field not in whitelist (colours, labels, etc.)
    return {
        "shape":        shape,
        "entry":        entry,
        "content_type": content_type,
        "emoji_value":  emoji_value,
        "zone":         zone,
    }


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_generative_broll(
    script_structure: list[dict],
    accepted_cards: list[dict],
    remapped_words: list,
    timing_map,
    trimmed_duration: float,
    pack: dict,
    language: str = "en",
    card_id_offset: int = 0,
) -> list[dict]:
    """
    Find uncovered strong beats, call Haiku once, return validated gap-fill cards.
    The caller inserts these AFTER the greedy merge (no confidence competition).
    """
    candidates = _find_candidates(
        script_structure, accepted_cards, remapped_words,
        timing_map, trimmed_duration,
    )

    if not candidates:
        print("[BROLL-GENERATIVE] no uncovered strong beats — skipping LLM call", flush=True)
        return []

    print(
        f"[BROLL-GENERATIVE] {len(candidates)} uncovered beat(s):"
        f" {[c['beat_role'] for c in candidates]}",
        flush=True,
    )

    llm_outputs = _call_haiku(candidates, pack, language)
    if llm_outputs is None:
        return []

    if len(llm_outputs) != len(candidates):
        print(
            f"[BROLL-GENERATIVE] output length mismatch:"
            f" expected {len(candidates)}, got {len(llm_outputs)} — rejecting all",
            flush=True,
        )
        return []

    new_cards: list[dict] = []
    accepted_ivs = _card_intervals(accepted_cards)

    for idx, (cand, raw_out) in enumerate(zip(candidates, llm_outputs)):
        validated = _validate(raw_out, idx)
        if validated is None:
            continue

        out_s = cand["out_start"]
        out_e = cand["out_end"]
        end_s = min(round(out_s + DEFAULT_DURATION, 3), trimmed_duration)

        # Final gap check — compare full intervals, not just start times.
        # new_cards may have grown during this loop so rebuild each iteration.
        all_ivs = accepted_ivs + _card_intervals(new_cards)
        if any(_interval_gap(out_s, end_s, a_s, a_e) < MIN_GAP_S for a_s, a_e in all_ivs):
            print(
                f"[BROLL-GENERATIVE] skip candidate[{idx}] at {out_s:.2f}s"
                f" — gap closed before insertion",
                flush=True,
            )
            continue

        # Resolve content value deterministically
        content_type  = validated["content_type"]
        content_value = ""
        if content_type == "emoji":
            content_value = validated["emoji_value"] or "✨"
        elif content_type == "number":
            num = _extract_number(remapped_words, out_s, out_e)
            if num:
                content_value = num
            else:
                # No numeral found — fall back to text
                content_type  = "text"
                content_value = cand["label"]
        else:  # text
            content_value = cand["label"]

        cid = f"gen-{card_id_offset + idx + 1:03d}"

        params = {
            "shape":         validated["shape"],
            "entry":         validated["entry"],
            "content_type":  content_type,
            "content_value": content_value,
            "label":         cand["label"],
            "kicker":        cand["kicker"],
        }

        card = {
            "id":           cid,
            "startSec":     out_s,
            "endSec":       end_s,
            "zone":         validated["zone"],
            "contentHints": {"style": "__broll__"},
            "_broll_type":  "generative_primitive",
            "_broll_params": params,
            "_confidence":  0.65,
            "_beat_role":   cand["beat_role"],
        }
        new_cards.append(card)
        print(
            f"[BROLL-GENERATIVE] inserted {cid} at {out_s:.2f}s"
            f" shape={validated['shape']} entry={validated['entry']}"
            f" content={content_type}:{content_value!r}"
            f" beat={cand['beat_role']}",
            flush=True,
        )

    print(
        f"[BROLL-GENERATIVE] {len(new_cards)}/{len(candidates)} candidate(s) → cards",
        flush=True,
    )
    return new_cards
