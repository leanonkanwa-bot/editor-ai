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


# ── Composition vocabulary (each axis validated independently) ────────────────

ALLOWED_FRAMES     = {"none", "phone", "card", "none-fullbleed"}
ALLOWED_VISUALS    = {"icon", "chart_trace", "number_counter", "chat_bubbles", "quote_mark"}
ALLOWED_TEXT_SLOTS = {"kicker", "label", "quote_text", "none"}
ALLOWED_LAYOUTS    = {"stacked", "side_by_side", "centered_overlay"}
ALLOWED_ENTRIES    = {"pop", "trace", "fade", "slide_up", "sequential"}
ALLOWED_ICONS      = {"spark", "stop", "growth", "alert", "heart", "star", "check"}
ALLOWED_N_BUBBLES  = {1, 2, 3}
ALLOWED_ZONES      = {"upper-data", "lower-third"}

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
    """Return beats that are strong but have no accepted card within MIN_GAP_S.

    Set env BROLL_GENERATIVE_MIN_SCORE to override STRONG_SCORE_MIN without
    a code push (useful for Railway one-shot testing: set to 0 to force all
    STRONG_ROLES beats through regardless of score; remove to restore default).
    """
    import os
    score_min = int(os.environ.get("BROLL_GENERATIVE_MIN_SCORE", STRONG_SCORE_MIN))
    accepted_ivs = _card_intervals(accepted_cards)

    candidates: list[dict] = []
    n_weak = n_range = n_covered = 0

    for beat in script_structure:
        role  = beat.get("beat", "")
        score = int(beat.get("score", 0))

        if role not in STRONG_ROLES and score < score_min:
            n_weak += 1
            continue

        src_s = float(beat.get("start", 0))
        src_e = float(beat.get("end", src_s + 3.0))
        out_s = timing_map.source_to_output(src_s)
        out_e = timing_map.source_to_output(src_e)

        if out_e <= out_s or out_s >= trimmed_duration:
            n_range += 1
            continue

        new_end = min(out_s + DEFAULT_DURATION, trimmed_duration)

        # Identify the first accepted interval that blocks this beat.
        blocking = next(
            ((a_s, a_e) for a_s, a_e in accepted_ivs
             if _interval_gap(out_s, new_end, a_s, a_e) < MIN_GAP_S),
            None,
        )
        if blocking:
            a_s, a_e = blocking
            print(
                f"[BROLL-GENERATIVE] beat role={role} score={score}"
                f" t={out_s:.1f}s → covered by [{a_s:.1f},{a_e:.1f}s]"
                f" gap={_interval_gap(out_s, new_end, a_s, a_e):.2f}s",
                flush=True,
            )
            n_covered += 1
            continue

        label    = _extract_label(remapped_words, out_s, out_e)
        kicker   = _extract_kicker(remapped_words, out_s)
        ctx_words = _words_in_window(remapped_words, out_s, out_e, pad=3.0)
        ctx_text  = " ".join(getattr(w, "text", "") for w in sorted(
            ctx_words, key=lambda w: float(getattr(w, "start", 0))
        ))[:200]

        print(
            f"[BROLL-GENERATIVE] beat role={role} score={score}"
            f" t={out_s:.1f}s → UNCOVERED — queued for LLM",
            flush=True,
        )
        candidates.append({
            "beat_role": role,
            "score":     score,
            "out_start": round(out_s, 3),
            "out_end":   min(round(out_e, 3), trimmed_duration),
            "label":     label,
            "kicker":    kicker,
            "ctx_text":  ctx_text,
        })

    print(
        f"[BROLL-GENERATIVE] scan complete:"
        f" {len(script_structure)} beats"
        f" | {n_weak} weak/skipped"
        f" | {n_range} out-of-range"
        f" | {n_covered} covered"
        f" | {len(candidates)} uncovered (score_min={score_min})",
        flush=True,
    )
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
You assign visual composition parameters for animated graphic card overlays.

STYLE PACK (do NOT output these — the renderer injects them automatically):
  bg={bg}  accent={accent}  text={text}

VOCABULARY — each axis is independent, validate individually:
  frame:     none | phone | card | none-fullbleed
  visual:    icon | chart_trace | number_counter | chat_bubbles | quote_mark
  icon_name: (when visual=icon only) spark | stop | growth | alert | heart | star | check
  n_bubbles: (when visual=chat_bubbles only) 1 | 2 | 3
  text_slot: kicker | label | quote_text | none
  layout:    stacked | side_by_side | centered_overlay
  entry:     pop | trace | fade | slide_up | sequential
  zone:      upper-data | lower-third

RULES (strict):
  - Output ONLY the JSON array. No prose, no explanation.
  - One object per candidate, same order as input.
  - Allowed output fields: frame, visual, icon_name, n_bubbles, text_slot, layout, entry, zone
  - icon_name: required when visual=icon. Must be from the list above. Otherwise omit.
  - n_bubbles: required when visual=chat_bubbles (default 2). Otherwise omit.
  - Do NOT output: label, kicker, colors, fonts, SVG, HTML, or any other field.
  - visual guide — evaluated in PRIORITY ORDER (first match wins, overrides beat role):
      PRIORITY 1: chat_bubbles
        Use whenever the scene involves two distinct people exchanging, even indirectly:
        reported speech ("elle me disait que..."), narrative exchange ("on s'est parlé et il
        a fini par avouer que..."), contrasted stances ("j'essayais d'expliquer / il ne voulait
        pas comprendre"), confession, confrontation, revelation between two parties.
        Does NOT require explicit speech verbs — two perspectives in tension = chat_bubbles.
        ALWAYS pair with: frame=phone  entry=sequential
      PRIORITY 2: number_counter
        ONLY when ctx contains a digit (numeral). Not for vague amounts.
      PRIORITY 3: chart_trace
        Growth, stat, amplify beats with an upward trend in ctx.
      PRIORITY 4: quote_mark
        A single-voice principle or wisdom statement. Pair with text_slot=quote_text.
      PRIORITY 5: icon  (default — use when none of the above match)
        Emotional/realization/payoff/principle beats.
  - frame guide:
      phone          → ONLY with chat_bubbles (two-person exchange). Never with other visuals.
      card           → default for all other visuals
      none-fullbleed → climax/bold moments with one large visual (only when NOT a dialogue scene)
  - layout guide: stacked→default | side_by_side→icon+label | centered_overlay→fullbleed text
  - entry guide: sequential→chat_bubbles+phone only | trace→chart_trace | pop→icon/badge | fade→quote
  - icon_name guide: heart→emotion/climax | spark→realization/hook | star→payoff/success |
    stop→principle/boundary | growth→stat/amplify | alert→urgency | check→confirmation
  - Language of context: {language}

CANDIDATES:
{items_str}

OUTPUT FORMAT (array, exactly {len(candidates)} objects):
[{{"frame":"card","visual":"icon","icon_name":"spark","text_slot":"label","layout":"stacked","entry":"pop","zone":"upper-data"}},...]"""


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
            max_tokens=768,
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


# ── Validation + compatibility ────────────────────────────────────────────────

def _validate(obj: dict, idx: int) -> dict | None:
    """Per-axis whitelist validation. Hard-rejects only the two core axes
    (visual, frame). Soft-corrects soft axes (text_slot, layout, entry, zone).
    Returns a clean dict ready for _compat_fix, or None on hard rejection.
    """
    frame     = obj.get("frame",     "card")
    visual    = obj.get("visual",    "")
    text_slot = obj.get("text_slot", "label")
    layout    = obj.get("layout",    "stacked")
    entry     = obj.get("entry",     "pop")
    zone      = obj.get("zone",      "upper-data")

    # Hard reject: unknown visual (the primary semantic choice)
    if visual not in ALLOWED_VISUALS:
        print(
            f"[BROLL-GENERATIVE] skip candidate[{idx}]"
            f" — invalid visual={visual!r} (allowed: {sorted(ALLOWED_VISUALS)})",
            flush=True,
        )
        return None

    # Hard reject: unknown frame (structural choice)
    if frame not in ALLOWED_FRAMES:
        print(
            f"[BROLL-GENERATIVE] skip candidate[{idx}]"
            f" — invalid frame={frame!r} (allowed: {sorted(ALLOWED_FRAMES)})",
            flush=True,
        )
        return None

    # Soft corrections: unknown value → safe default
    if text_slot not in ALLOWED_TEXT_SLOTS:
        text_slot = "label"
    if layout not in ALLOWED_LAYOUTS:
        layout = "stacked"
    if entry not in ALLOWED_ENTRIES:
        entry = "pop"
    if zone not in ALLOWED_ZONES:
        zone = "upper-data"

    # Visual sub-fields
    icon_name = ""
    if visual == "icon":
        raw = str(obj.get("icon_name", "")).strip().lower()
        icon_name = raw if raw in ALLOWED_ICONS else "spark"
        if raw not in ALLOWED_ICONS and raw:
            print(
                f"[BROLL-GENERATIVE] candidate[{idx}] icon_name={raw!r}"
                f" not in whitelist — defaulted to 'spark'",
                flush=True,
            )

    n_bubbles = 2
    if visual == "chat_bubbles":
        try:
            n_bubbles = int(obj.get("n_bubbles", 2))
        except (ValueError, TypeError):
            n_bubbles = 2
        if n_bubbles not in ALLOWED_N_BUBBLES:
            n_bubbles = 2

    return _compat_fix({
        "frame":     frame,
        "visual":    visual,
        "icon_name": icon_name,
        "n_bubbles": n_bubbles,
        "text_slot": text_slot,
        "layout":    layout,
        "entry":     entry,
        "zone":      zone,
    }, idx)


def _compat_fix(params: dict, idx: int) -> dict:
    """Cross-axis compatibility pass — silent correction, never rejection.

    Rules are evaluated in declaration order. Order is intentional and encodes
    priority when two rules could apply to the same input:

      PRIORITY: frame wins over visual when they conflict.
      Example: frame=phone + visual=number_counter
        → Rule 1 fires first: visual forced to chat_bubbles.
        → Rule 4 (number_counter excludes phone) then sees visual=chat_bubbles
          and is a no-op. The phone-frame intent is preserved because a phone
          frame showing chat bubbles is coherent; showing a number counter is not.
        To add a new rule: append it here + document the priority interaction
        with any existing rule it could conflict with.

    Compatibility table:
      Rule 1  frame=phone          → visual forced to chat_bubbles
      Rule 2  visual=chat_bubbles  → frame must be phone or card
      Rule 3  visual=quote_mark    → text_slot forced to quote_text
      Rule 4  visual=number_counter→ frame must not be phone  [no-op if Rule 1 fired]
      Rule 5  entry=sequential     → only valid with chat_bubbles; else demoted to fade
    """
    p = dict(params)

    # Rule 1 — PRIORITY: frame=phone preserves phone intent; visual is corrected to fit.
    # When frame=phone + visual=number_counter, this fires before Rule 4, making Rule 4 a no-op.
    if p["frame"] == "phone" and p["visual"] != "chat_bubbles":
        print(
            f"[BROLL-GENERATIVE] compat[{idx}] Rule1:"
            f" frame=phone requires visual=chat_bubbles"
            f" (was {p['visual']!r}) — corrected",
            flush=True,
        )
        p["visual"]    = "chat_bubbles"
        p["n_bubbles"] = p.get("n_bubbles", 2) or 2

    # Rule 2
    if p["visual"] == "chat_bubbles" and p["frame"] not in ("phone", "card"):
        print(
            f"[BROLL-GENERATIVE] compat[{idx}] Rule2:"
            f" visual=chat_bubbles requires frame=phone|card"
            f" (was {p['frame']!r}) — corrected to 'card'",
            flush=True,
        )
        p["frame"] = "card"

    # Rule 3
    if p["visual"] == "quote_mark" and p["text_slot"] != "quote_text":
        print(
            f"[BROLL-GENERATIVE] compat[{idx}] Rule3:"
            f" visual=quote_mark requires text_slot=quote_text"
            f" (was {p['text_slot']!r}) — corrected",
            flush=True,
        )
        p["text_slot"] = "quote_text"

    # Rule 4 — no-op when Rule 1 already changed visual away from number_counter
    if p["visual"] == "number_counter" and p["frame"] == "phone":
        print(
            f"[BROLL-GENERATIVE] compat[{idx}] Rule4:"
            f" visual=number_counter excludes frame=phone — corrected to 'card'",
            flush=True,
        )
        p["frame"] = "card"

    # Rule 5
    if p["entry"] == "sequential" and p["visual"] != "chat_bubbles":
        print(
            f"[BROLL-GENERATIVE] compat[{idx}] Rule5:"
            f" entry=sequential requires visual=chat_bubbles"
            f" (was {p['visual']!r}) — entry demoted to 'fade'",
            flush=True,
        )
        p["entry"] = "fade"

    return p


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

        # Resolve number content deterministically for number_counter visual
        content_value = ""
        if validated["visual"] == "number_counter":
            num = _extract_number(remapped_words, out_s, out_e)
            if num:
                content_value = num
            else:
                # No numeral in transcript — demote to icon:spark
                print(
                    f"[BROLL-GENERATIVE] candidate[{idx}]"
                    f" visual=number_counter but no numeral found"
                    f" — demoted to icon:spark",
                    flush=True,
                )
                validated = dict(validated, visual="icon", icon_name="spark")

        cid = f"gen-{card_id_offset + idx + 1:03d}"

        params = {
            "frame":         validated["frame"],
            "visual":        validated["visual"],
            "icon_name":     validated["icon_name"],
            "n_bubbles":     validated["n_bubbles"],
            "content_value": content_value,
            "text_slot":     validated["text_slot"],
            "layout":        validated["layout"],
            "entry":         validated["entry"],
            "label":         cand["label"],
            "kicker":        cand["kicker"],
        }

        card = {
            "id":            cid,
            "startSec":      out_s,
            "endSec":        end_s,
            "zone":          validated["zone"],
            "contentHints":  {"style": "__broll__"},
            "_broll_type":   "generative_primitive",
            "_broll_params": params,
            "_confidence":   0.65,
            "_beat_role":    cand["beat_role"],
        }
        new_cards.append(card)
        print(
            f"[BROLL-GENERATIVE] inserted {cid} at {out_s:.2f}s"
            f" frame={validated['frame']} visual={validated['visual']}"
            f" entry={validated['entry']} beat={cand['beat_role']}",
            flush=True,
        )

    print(
        f"[BROLL-GENERATIVE] {len(new_cards)}/{len(candidates)} candidate(s) → cards",
        flush=True,
    )
    return new_cards
