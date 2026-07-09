"""End-to-end pipeline runner — two-phase:
  Phase 1 (run_job):  transcribe → silence removal → energy → speakers →
                      plan → hook rewrite → broll specs → ready_for_review
  Phase 2 (run_render_phase): render → done
"""

from __future__ import annotations

import re
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# One heavy render at a time — prevents N concurrent Chrome worker pools from
# exhausting cgroup RAM.  Phase-1 (transcription) is light enough to overlap.
_RENDER_SEM = threading.Semaphore(1)

from app.agent.planner import FormatHint, analyze_subject_position, plan_edit, rewrite_hook
from app.api.jobs import store
from app.core.config import settings
from app.core.plans import has_4k_access
from app.engine.analytics_engine import build_insights_instructions, load_insights
from app.engine.brand_engine import BrandEngine, load_brand
from app.engine.broll_generator import BrollGenerator
from app.engine.energy_detector import EnergyDetector
from app.engine.graphics_engine import GraphicSelector, build_video_context, detect_content_type
from app.engine.render import render
from app.engine.silence_remover import RhythmAwareSilenceRemover, apply_drops_to_transcript
from app.engine.speaker_detector import SpeakerDetector
from app.engine.template_engine import apply_template, get_template
from app.engine.transcribe import AudioMissingError, transcribe, unload_model

# Purge work_dirs older than 2 days at module load (i.e. server startup).
def _purge_old_work_dirs() -> None:
    _work_root = settings.work_dir
    if not _work_root.exists():
        return
    _cutoff = time.time() - 172800  # 2 days
    for _d in _work_root.iterdir():
        if _d.is_dir() and _d.stat().st_mtime < _cutoff:
            shutil.rmtree(_d, ignore_errors=True)

_purge_old_work_dirs()


def verify_caption_sync(remapped_words: list, edited_duration: float) -> list:
    """Filter caption words to only those that fall within the edited video.

    This is a utility wrapper around render._verify_caption_sync() exposed
    here for testing and external use. The render pipeline calls the private
    version directly before build_ass().
    """
    import logging
    log = logging.getLogger(__name__)
    issues = []
    valid = []
    for w in remapped_words:
        start = getattr(w, "start", w.get("start", 0) if isinstance(w, dict) else 0)
        if start < 0:
            issues.append(f"'{getattr(w, 'text', '')}' negative start={start:.3f}s")
            continue
        if start > edited_duration:
            issues.append(f"'{getattr(w, 'text', '')}' start={start:.3f}s > duration={edited_duration:.3f}s")
            continue
        valid.append(w)
    if issues:
        log.warning("caption sync issues (%d): %s", len(issues), issues[:10])
    return valid


def _guard_drops_against_key_content(
    drops: list,
    plan,
    transcript: dict,
) -> list:
    """Remove any physical drop that would excise words belonging to key_lines.

    Operates in SOURCE timestamp space: both `drops` (from silence_remover)
    and `transcript` (original, pre-virtual-drop) share the same coordinate
    system, so the overlap check is exact.

    Only meaningful lexical words (length >= 3, not pure function words) are
    used as anchors to avoid over-protecting common short tokens.
    """
    key_lines: list[str] = plan.key_lines or []
    if not key_lines:
        return drops

    _FUNCTION_WORDS = frozenset({
        "le", "la", "les", "de", "du", "des", "et", "est", "il", "elle",
        "on", "un", "une", "ce", "se", "sa", "son", "leur", "pas", "ne",
        "en", "au", "aux", "je", "tu", "nous", "vous", "ils", "elles",
        "the", "a", "an", "of", "in", "is", "it", "to", "for", "and", "or",
    })

    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9àâäéèêëîïôùûüç]", "", s.lower())

    # Build set of meaningful normalized words that appear in any key_line.
    protected_norms: set[str] = set()
    for line in key_lines:
        for token in line.split():
            n = _norm(token)
            if len(n) >= 3 and n not in _FUNCTION_WORDS:
                protected_norms.add(n)

    if not protected_norms:
        return drops

    # Find source-space timestamps of real words that match protected_norms.
    # "Real" = duration >= _MIN_WORD_DUR_S (same threshold as artifact filter).
    from app.engine.silence_remover import _MIN_WORD_DUR_S
    protected_intervals: list[tuple[float, float, str]] = []
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []):
            try:
                ws = float(w["start"])
                we = float(w["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if we - ws < _MIN_WORD_DUR_S:
                continue
            n = _norm(str(w.get("text", "")).strip())
            if n in protected_norms:
                protected_intervals.append((ws, we, str(w.get("text", "")).strip()))

    if not protected_intervals:
        return drops

    # Reject any drop whose interval overlaps a protected word interval.
    filtered: list = []
    for drop in drops:
        offenders = [
            wtext for ws, we, wtext in protected_intervals
            if drop.start < we and drop.end > ws
        ]
        if offenders:
            print(
                f"[CUT-GUARD] rejected {drop.reason} ({drop.start:.2f}-{drop.end:.2f}s): "
                f"would remove key content {offenders}",
                flush=True,
            )
        else:
            filtered.append(drop)

    n_rejected = len(drops) - len(filtered)
    if n_rejected:
        print(
            f"[CUT-GUARD] {n_rejected} drop(s) rejected — key_line content protection "
            f"(protected words: {sorted(protected_norms)[:8]}{'…' if len(protected_norms) > 8 else ''})",
            flush=True,
        )
    return filtered


def _dedup_drops(drops: list) -> list:
    """Merge drops with >50% overlap — keep the narrower (more precise) interval.

    Called after LLM + lexical drops are merged to prevent double-cuts where
    both layers identify the same filler word (e.g. 'Bah' by LLM and regex).
    """
    from app.engine.silence_remover import DropSegment as _DS
    if len(drops) <= 1:
        return drops
    ordered = sorted(drops, key=lambda d: d.start)
    result: list = []
    for d in ordered:
        if not result:
            result.append(d)
            continue
        prev = result[-1]
        overlap = min(prev.end, d.end) - max(prev.start, d.start)
        if overlap <= 0:
            result.append(d)
            continue
        span_prev = prev.end - prev.start
        span_d    = d.end - d.start
        threshold = 0.5 * min(span_prev, span_d)
        if overlap > threshold:
            # >50% overlap relative to the smaller drop → dedup, keep narrower
            if span_d < span_prev:
                print(
                    f"[DEDUP] {prev.reason} [{prev.start:.2f}-{prev.end:.2f}]"
                    f" superseded by narrower {d.reason} [{d.start:.2f}-{d.end:.2f}]"
                    f" (overlap={overlap:.3f}s)",
                    flush=True,
                )
                result[-1] = d
            else:
                print(
                    f"[DEDUP] {d.reason} [{d.start:.2f}-{d.end:.2f}]"
                    f" merged into {prev.reason} [{prev.start:.2f}-{prev.end:.2f}]"
                    f" (overlap={overlap:.3f}s)",
                    flush=True,
                )
        else:
            result.append(d)
    return result


def _llm_editorial_cuts(
    transcript: dict,
    key_lines: list[str],
) -> list:
    """Call Claude Haiku with the verbatim numbered transcript → list of DropSegments to cut.

    Rules encoded in the prompt:
    - Fillers (Euh, Bah, Ben, Hein, Hm), accidental repetitions, false starts → cut
    - Rhetorical repetitions (3+ identical consecutive) → keep
    - Last occurrence of a stutter repetition → keep, cut the earlier ones
    - Never touch key_lines content → passed explicitly in prompt
    - When in doubt → do NOT cut
    """
    import json as _json
    import os as _os
    import re as _re
    import time as _time

    from anthropic import Anthropic as _Anthropic
    from app.engine.silence_remover import DropSegment as _DS

    words = [w for seg in transcript.get("segments", []) for w in seg.get("words", [])]
    if not words:
        return []

    # Build numbered transcript (cap at 600 words — Haiku context is large)
    _MAX_WORDS = 600
    numbered = "\n".join(
        f"[{i}] {str(w.get('text', '')).strip()}"
        for i, w in enumerate(words[:_MAX_WORDS])
    )
    _truncated = len(words) > _MAX_WORDS
    _max_idx = min(len(words), _MAX_WORDS) - 1

    key_lines_str = "\n".join(f"- {l}" for l in key_lines[:10]) if key_lines else "(aucun)"

    prompt = f"""Tu es un éditeur vidéo expert. Transcription VERBATIM numérotée (un indice = un mot) :

{numbered}{"...(tronqué après [{_max_idx}])" if _truncated else ""}

CONSIGNES :
0. SCAN SYSTÉMATIQUE : examine CHAQUE indice de [0] à [{_max_idx}] sans en sauter aucun.
1. Coupe uniquement : fillers isolés (Euh, Bah, Ben, Hein, Hm, ouais isolé), répétitions accidentelles (même mot/groupe répété consécutivement), faux départs (phrase relancée immédiatement).
2. Répétitions simples : garde LA DERNIÈRE occurrence, coupe les précédentes. Ex : "[5] il [6] il [7] faut" → coupe [5,5], garde [6,7].
3. Répétitions multi-mots : identifie les groupes de mots répétés. Ex : "[12] parce [13] qu'ils [14] parce [15] qu'ils" → coupe [12,13] (premier groupe), garde [14,15].
4. Répétitions rhétoriques VOLONTAIRES (3+ occurrences identiques, effet stylistique) = NE PAS TOUCHER.
5. NE JAMAIS toucher ces extraits clés :
{key_lines_str}
6. En cas de doute → NE PAS COUPER.

Réponds UNIQUEMENT en JSON strict (aucun texte avant ni après).
"cuts" = ce qui doit être coupé. "kept" = candidats que tu as examinés mais décidé de GARDER (liste tous pour audit) :
{{"cuts": [{{"indices": [debut, fin_inclus], "reason": "filler|repetition|false_start|premature_conclusive"}}], "kept": [{{"indices": [i, j], "reason": "kept — explication"}}]}}

Exemple — [2]="Euh", [5]-[6]="il il", [12]-[15]="parce qu'ils parce qu'ils", [20]-[22] répétition rhétorique gardée :
{{"cuts": [{{"indices": [2, 2], "reason": "filler"}}, {{"indices": [5, 5], "reason": "repetition"}}, {{"indices": [12, 13], "reason": "repetition"}}], "kept": [{{"indices": [20, 22], "reason": "kept — répétition rhétorique volontaire 3× stylistique"}}]}}

Si rien à couper : {{"cuts": [], "kept": []}}"""

    # Model: default Haiku, override with LLM_EDITORIAL_MODEL for deeper analysis
    _model = _os.getenv("LLM_EDITORIAL_MODEL", "claude-haiku-4-5-20251001")

    _t0 = _time.perf_counter()
    try:
        client = _Anthropic()
        response = client.messages.create(
            model=_model,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        print(f"[LLM-EDIT] API error: {e}", flush=True)
        return []

    _latency = _time.perf_counter() - _t0
    _in_tok = response.usage.input_tokens
    _out_tok = response.usage.output_tokens
    # Haiku 4.5: $0.80/1M input, $4.00/1M output  |  Sonnet 4.5: $3/$15
    if "haiku" in _model:
        _cost = (_in_tok * 0.80 + _out_tok * 4.00) / 1_000_000
    else:
        _cost = (_in_tok * 3.00 + _out_tok * 15.00) / 1_000_000
    print(
        f"[LLM-EDIT] model={_model} latency={_latency:.1f}s "
        f"tokens={_in_tok}in+{_out_tok}out cost=${_cost:.4f}",
        flush=True,
    )

    raw = response.content[0].text.strip()
    m = _re.search(r'\{.*\}', raw, _re.DOTALL)
    if not m:
        print(f"[LLM-EDIT] no JSON in response: {raw[:200]}", flush=True)
        return []

    try:
        data = _json.loads(m.group())
    except _json.JSONDecodeError as e:
        print(f"[LLM-EDIT] JSON parse error: {e} | raw: {raw[:200]}", flush=True)
        return []

    # Log "kept" items for exhaustiveness audit
    for kept in data.get("kept", []):
        k_idx = kept.get("indices", [])
        k_reason = kept.get("reason", "")
        if len(k_idx) == 2:
            k0, k1 = int(k_idx[0]), int(k_idx[1])
            if 0 <= k0 <= k1 < len(words):
                k_text = " ".join(str(words[k].get("text", "")).strip() for k in range(k0, k1 + 1))
                print(f"[LLM-EDIT] kept [{k0},{k1}] text={k_text!r} reason={k_reason!r}", flush=True)

    drops = []
    for cut in data.get("cuts", []):
        idx = cut.get("indices", [])
        reason = cut.get("reason", "llm_editorial")
        if len(idx) != 2:
            continue
        i0, i1 = int(idx[0]), int(idx[1])
        if i0 < 0 or i1 >= len(words) or i0 > i1:
            print(f"[LLM-EDIT] skip invalid indices [{i0},{i1}] (words={len(words)})", flush=True)
            continue
        t_start = float(words[i0].get("start", 0))
        t_end   = float(words[i1].get("end", 0))
        if t_end <= t_start:
            continue
        cut_text = " ".join(str(words[k].get("text", "")).strip() for k in range(i0, i1 + 1))
        print(
            f"[LLM-EDIT] cut [{i0},{i1}] t={t_start:.2f}-{t_end:.2f}s "
            f"reason={reason} text={cut_text!r}",
            flush=True,
        )
        # target_intervals: the exact word spans being cut so word_safe
        # treats them as intentional targets, not collateral bystanders.
        _target_ivs = tuple(
            (float(words[k].get("start", 0)), float(words[k].get("end", 0)))
            for k in range(i0, i1 + 1)
        )
        drops.append(_DS(start=t_start, end=t_end, reason=f"llm_{reason}",
                         target_intervals=_target_ivs))

    print(
        f"[LLM-EDIT] {len(drops)} cut(s) from {len(data.get('cuts', []))} suggestion(s) "
        f"| {len(data.get('kept', []))} kept item(s) audited",
        flush=True,
    )
    return drops


def run_job(
    job_id: str,
    src: Path,
    instructions: str,
    format_hint: FormatHint,
    *,
    caption_font: str = "Poppins Bold",
    caption_color: str = "white",
    caption_position: str = "center",
    caption_style: str = "impact",
    brand_color: str | None = None,
    aesthetic: str = "dark-pro",
    editing_style: str = "viral",
    # Content brief fields (Feature 6)
    target_audience: str = "",
    main_message: str = "",
    desired_emotion: str = "",
    platform: str = "",
    content_type_hint: str = "",
    # Template Memory (Feature 1)
    template_id: str = "",
    # Style pack
    style_pack: str = "lean_glass",
    # Coach Profile IA (Feature 3)
    coach_profile: dict | None = None,
    # Absorbed from stored params (re-detected internally — ignored here)
    content_type: str = "",
    **kwargs,
) -> None:
    """Phase 1: transcription, analysis, planning → status: ready_for_review."""
    try:
        _t0 = time.perf_counter()
        # ── Step 1: Transcribe + Vision (concurrent) ──────────────────────
        store.update(job_id, status="transcribing", progress=10,
                     message="Transcribing audio + analysing subject position…")
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_transcript = pool.submit(lambda: transcribe(src).to_dict())
            f_subject    = pool.submit(lambda: analyze_subject_position(src))
            transcript  = f_transcript.result()
            subject_pos = f_subject.result()
        print(f"[TIMING] transcription+vision: {time.perf_counter()-_t0:.1f}s", flush=True)

        # ── Step 2: Silence removal (Feature 2) ───────────────────────────
        _t = time.perf_counter()
        store.update(job_id, status="transcribing", progress=20,
                     message="Removing silences and filler words…")
        from app.core.config import settings as _cfg
        drops: list = []
        filler_drops: list = []
        if _cfg.disable_cuts:
            # DISABLE_CUTS: skip timestamp adjustment — the video plays
            # uncut, so drop-shifted timestamps would cause progressive
            # drift (captions ahead of speech, gap growing toward the end).
            transcript_clean = transcript
            print("[PIPELINE] DISABLE_CUTS=true — skipping silence removal timestamp shift", flush=True)
        else:
            remover = RhythmAwareSilenceRemover()
            word_timestamps = [
                w for seg in transcript.get("segments", [])
                for w in seg.get("words", [])
            ]
            drops, filler_drops = remover.process(word_timestamps, transcript.get("segments", []))
            transcript_clean = apply_drops_to_transcript(transcript, drops)
        print(f"[TIMING] silence_removal: {time.perf_counter()-_t:.1f}s", flush=True)

        # ── Step 3: Energy detection (Feature 4) ──────────────────────────
        _t = time.perf_counter()
        store.update(job_id, status="transcribing", progress=25,
                     message="Detecting speaker energy…")
        try:
            detector = EnergyDetector()
            word_ts  = [
                w for seg in transcript_clean.get("segments", [])
                for w in seg.get("words", [])
            ]
            energy_profile = detector.analyze(src, word_ts)
            energy_dicts   = [
                {"at": ep.at, "duration": ep.duration,
                 "rms_db": ep.rms_db, "speech_rate": ep.speech_rate,
                 "level": ep.level}
                for ep in energy_profile
            ]
        except Exception:
            energy_dicts = []
        print(f"[TIMING] energy_detection: {time.perf_counter()-_t:.1f}s", flush=True)

        # ── Step 4: Multi-speaker detection (Feature 8) ───────────────────
        _t = time.perf_counter()
        store.update(job_id, status="transcribing", progress=30,
                     message="Detecting speakers…")
        try:
            spk_detector   = SpeakerDetector()
            speaker_segs   = spk_detector.detect(src, transcript_clean.get("segments", []))
            speaker_dicts  = [
                {"start": ss.start, "end": ss.end, "speaker_id": ss.speaker_id,
                 "camera_pos": ss.camera_pos, "lower_third": ss.lower_third}
                for ss in speaker_segs
            ]
        except Exception:
            speaker_dicts = []
        print(f"[TIMING] speaker_detection: {time.perf_counter()-_t:.1f}s", flush=True)

        # ── Step 5a: Load template if specified (Feature 1) ───────────────
        template: dict = {}
        if template_id:
            try:
                t = get_template(template_id)
                if t:
                    template = t
                    # Apply template caption overrides now so they propagate.
                    style = t.get("style", {})
                    cap_style_map = {"one_word": "impact", "phrase": "impact", "full_sentence": "kinetic"}
                    caption_style = cap_style_map.get(style.get("caption_style", ""), caption_style)
                    caption_position = style.get("caption_position", caption_position)
            except Exception:
                pass

        # ── Step 5b: Load brand kit (Feature 2) ───────────────────────────
        brand_kit: dict = {}
        try:
            brand_kit = load_brand()
            brand_engine = BrandEngine()
            brand_primary = brand_kit.get("colors", {}).get("primary", "")
            if brand_primary and not brand_color:
                brand_color = brand_primary
            cap_color_override = brand_kit.get("font", {}).get("caption_color", "")
            if cap_color_override:
                from app.engine.brand_engine import _hex_to_name
                caption_color = _hex_to_name(cap_color_override)
        except Exception:
            brand_kit = {}

        # ── Step 5b+: Override from coach_profile (highest priority) ──────
        _FONT_EXPAND = {
            "Poppins":     "Poppins Bold",
            "Inter":       "Inter Bold",
            "Montserrat":  "Montserrat Bold",
            "Bebas":       "Bebas Neue",
            "Anton":       "Anton",
            "DM Sans":     "DM Sans Bold",
            "Quicksand":   "Quicksand Bold",
            "Roboto":      "Roboto Bold",
        }
        if coach_profile:
            _profile_color = coach_profile.get("primaryColor", "")
            if _profile_color and not brand_color:
                brand_color = _profile_color
            _profile_font = coach_profile.get("font", "")
            if _profile_font and caption_font == "Poppins Bold":
                caption_font = _FONT_EXPAND.get(_profile_font, _profile_font)
            print(
                f"[BRAND] Loaded profile: font={coach_profile.get('font', '')!r} "
                f"color={coach_profile.get('primaryColor', '')!r} "
                f"secondary={coach_profile.get('secondaryColor', '')!r}"
            )
        print(f"[BRAND] Passing to render: caption_font={caption_font!r} brand_color={brand_color!r}")

        # ── Step 5c: Load analytics insights (Feature 4) ──────────────────
        insights_instructions = ""
        try:
            insights = load_insights()
            insights_instructions = build_insights_instructions(insights)
            # Bias content_type_hint from analytics if not user-set.
            if not content_type_hint:
                content_type_hint = insights.get("recommended_settings", {}).get(
                    "preferred_content_type", ""
                )
        except Exception:
            pass

        # ── Step 5d: Build enriched instructions from content brief ────────
        enriched_instructions = _build_instructions(
            instructions, target_audience, main_message,
            desired_emotion, platform, content_type_hint,
            insights_instructions=insights_instructions,
            template=template,
        )

        # ── Step 6: Planning ───────────────────────────────────────────────
        _t = time.perf_counter()
        store.update(job_id, status="planning", progress=40,
                     message="Asking the agent for an edit plan…")
        plan = plan_edit(
            transcript_clean,
            enriched_instructions,
            format_hint=format_hint,
            brand_color=brand_color,
            caption_color=caption_color,
            caption_position=caption_position,
            caption_font=caption_font,
            subject_position=subject_pos,
            coach_profile=coach_profile,
            editing_style=editing_style,
        )
        print(f"[TIMING] planning: {time.perf_counter()-_t:.1f}s", flush=True)

        # ── Step 6.5: LLM editorial layer ────────────────────────────────────
        # Claude receives the verbatim transcript numbered word-by-word and returns
        # the indices to cut (fillers, accidental repetitions, false starts).
        # Runs AFTER planning so key_lines can be passed to the prompt.
        # The CUT-GUARD below is a second safety net in case the LLM overshoots.
        _t = time.perf_counter()
        try:
            _llm_drops = _llm_editorial_cuts(transcript, plan.key_lines or [])
            if _llm_drops:
                _pre_merge = len(filler_drops)
                filler_drops = _dedup_drops(filler_drops + _llm_drops)
                print(
                    f"[LLM-EDIT] merged {len(_llm_drops)} cut(s):"
                    f" {_pre_merge} lexical + {len(_llm_drops)} LLM"
                    f" → {len(filler_drops)} after dedup",
                    flush=True,
                )
        except Exception as _llm_exc:
            print(f"[LLM-EDIT] error (skipping): {_llm_exc}", flush=True)
        print(f"[TIMING] llm_editorial: {time.perf_counter()-_t:.1f}s", flush=True)

        # ── Semantic guard: reject drops that overlap key_line words ───────
        _pre_guard_ranges = {(d.start, d.end) for d in filler_drops}
        filler_drops = _guard_drops_against_key_content(filler_drops, plan, transcript)
        _post_guard_ranges = {(d.start, d.end) for d in filler_drops}
        _rejected_ranges = _pre_guard_ranges - _post_guard_ranges

        if _rejected_ranges:
            # Guard removed physical drop(s). Remove the matching virtual drops
            # too and rebuild transcript_clean so captions and video stay in sync.
            # Without this, transcript_clean would be compressed by the rejected
            # drop but the video wouldn't be cut → 2s+ caption/video desync.
            #
            # Handles two cases:
            # (A) exact match: drop (rs, re) is exactly the rejected interval
            # (B) merge case: a pause was merged by _merge_drops with the false-start
            #     → keep only the pause portion [d.start, rs), drop the rest
            from app.engine.silence_remover import DropSegment as _DS
            drops_filtered = []
            n_removed = 0
            for d in drops:
                absorbed = False
                for rs, re in _rejected_ranges:
                    if abs(d.start - rs) < 0.01 and abs(d.end - re) < 0.01:
                        # Case A: exact match — drop entirely
                        absorbed = True
                        n_removed += 1
                        break
                    if abs(d.end - re) < 0.01 and d.start < rs - 0.01:
                        # Case B: merged drop — keep the leading pause portion
                        drops_filtered.append(_DS(d.start, rs, d.reason))
                        absorbed = True
                        n_removed += 1
                        break
                if not absorbed:
                    drops_filtered.append(d)
            transcript_clean = apply_drops_to_transcript(transcript, drops_filtered)
            print(
                f"[CUT-GUARD] rebuilt transcript_clean: {n_removed} virtual drop(s) adjusted "
                f"for {len(_rejected_ranges)} rejected physical cut(s)",
                flush=True,
            )

        # ── Word-safe: ensure no physical drop overlaps real speech ──────
        from app.engine.silence_remover import word_safe_drops as _word_safe_drops
        _source_words = [
            w for seg in transcript.get("segments", [])
            for w in seg.get("words", [])
        ]
        filler_drops = _word_safe_drops(filler_drops, _source_words)

        # ── Acoustic-stutter detection (logging only) ─────────────────────
        _STUTTER_MAX_LEN = 4    # token chars — short function words only
        _STUTTER_MIN_DUR = 0.55 # seconds — anomalously long for short tokens
        _STUTTER_GAP_MIN = 0.08 # seconds — must be adjacent to a gap

        for _si, _sw in enumerate(_source_words):
            _sw_text = str(_sw.get("text", "")).strip().lower().rstrip(".,!?;:'\"")
            if not _sw_text or len(_sw_text) > _STUTTER_MAX_LEN:
                continue
            _sw_dur = float(_sw.get("end", 0)) - float(_sw.get("start", 0))
            if _sw_dur <= _STUTTER_MIN_DUR:
                continue
            _prev_end   = float(_source_words[_si - 1].get("end", 0)) if _si > 0 else 0.0
            _next_start = float(_source_words[_si + 1].get("start", 0)) if _si < len(_source_words) - 1 else float("inf")
            _gap_b = float(_sw.get("start", 0)) - _prev_end
            _gap_a = _next_start - float(_sw.get("end", 0))
            if _gap_b > _STUTTER_GAP_MIN or _gap_a > _STUTTER_GAP_MIN:
                _ctx_b = str(_source_words[_si - 1].get("text", "")).strip() if _si > 0 else ""
                _ctx_a = str(_source_words[_si + 1].get("text", "")).strip() if _si < len(_source_words) - 1 else ""
                print(
                    f"[ACOUSTIC-STUTTER] '{_sw_text}' dur={_sw_dur:.2f}s"
                    f" at {float(_sw.get('start', 0)):.2f}s"
                    f" | ctx: '{_ctx_b}' … '{_ctx_a}'"
                    f" | gap_before={_gap_b:.2f}s gap_after={_gap_a:.2f}s",
                    flush=True,
                )

        # Effective virtual drops (post-guard rebuild) for Phase 2 c2s mapping.
        _effective_virtual_drops: list = drops_filtered if _rejected_ranges else drops  # type: ignore[name-defined]

        # ── Diagnostic: log planner gaps that overlap speech ─────────────
        _keep_raw = plan.raw.get("keep_segments", [])
        _vd_sorted = sorted(_effective_virtual_drops, key=lambda d: d.start)

        def _c2s_diag(tc: float) -> tuple[float, float]:
            """Return (source_ts, accumulated_offset) for the given compressed ts."""
            offset = 0.0
            for _d in _vd_sorted:
                c_ds = _d.start - offset
                if tc <= c_ds:
                    break
                offset += _d.end - _d.start
            return tc + offset, offset

        _gap_pairs: list[tuple[str, float, float]] = []
        if _keep_raw:
            if float(_keep_raw[0].get("start", 0)) > 0.5:
                _gap_pairs.append(("lead-in", 0.0, float(_keep_raw[0]["start"])))
            for _gi in range(len(_keep_raw) - 1):
                _gap_pairs.append((
                    f"gap-{_gi}",
                    float(_keep_raw[_gi].get("end", 0)),
                    float(_keep_raw[_gi + 1].get("start", 0)),
                ))
        for _gid, _gs, _ge in _gap_pairs:
            if _ge <= _gs + 0.001:
                continue  # zero-width gap: segments share a boundary, nothing lost
            _gs_src, _gs_offset = _c2s_diag(_gs)
            _ge_src, _ge_offset = _c2s_diag(_ge)
            # Always log the c2s offset so we can prove whether conversion fired
            print(
                f"[C2S] {_gid}: compressed {_gs:.3f}-{_ge:.3f}"
                f" → source {_gs_src:.3f}-{_ge_src:.3f}"
                f" (offset {_gs_offset:+.3f}s, {len(_vd_sorted)} virtual drops)",
                flush=True,
            )
            _gap_words = [
                w for w in _source_words
                if float(w.get("start", 0)) < _ge_src - 0.005
                and float(w.get("end", 0)) > _gs_src + 0.005
                and (float(w.get("end", 0)) - float(w.get("start", 0))) >= 0.030
            ]
            if _gap_words:
                _wtxt = " ".join(str(w.get("text", "")).strip() for w in _gap_words[:8])
                print(
                    f"[PLAN-GAP] {_gid} compressed {_gs:.2f}-{_ge:.2f}"
                    f" → source {_gs_src:.2f}-{_ge_src:.2f}: overlaps speech '{_wtxt}'",
                    flush=True,
                )

        # ── GAP-RESCUE: extend keep_segments to recover speech lost in gaps ─
        # Any real word in a planner gap that is NOT covered by a validated
        # filler drop is "lost by non-selection".  Extend the preceding segment
        # to include it — after this point, a word can only vanish via an
        # explicit, word-safe-validated drop, never by a silent planning hole.

        # Fix 6: build occurrence index so we can trace repeated words (e.g. 'jamais' x3)
        from collections import Counter as _Counter
        _src_word_counts = _Counter(
            str(w.get("text", "")).strip().lower() for w in _source_words
        )
        _repeated_vocab = {t for t, c in _src_word_counts.items() if c > 1}

        _n_rescued = 0
        for _gi in range(len(_keep_raw) - 1):
            _gs_c = float(_keep_raw[_gi].get("end", 0))
            _ge_c = float(_keep_raw[_gi + 1].get("start", 0))
            if _ge_c <= _gs_c + 0.050:
                continue  # gap too narrow to host real speech
            _gs_src, _gs_off = _c2s_diag(_gs_c)
            _ge_src, _ge_off = _c2s_diag(_ge_c)

            # Words in source space that land FULLY inside the gap (start >= gap
            # start, end <= gap end).  Straddling words are excluded: a word
            # that starts in the gap but ends inside the next segment would be
            # captured by two FFmpeg clips → audio duplicate.
            _gap_uncov = [
                w for w in _source_words
                if float(w.get("start", 0)) >= _gs_src - 0.005
                and float(w.get("start", 0)) < _ge_src - 0.005
                and float(w.get("end", 0)) > _gs_src + 0.005
                and float(w.get("end", 0)) <= _ge_src + 0.005
                and (float(w.get("end", 0)) - float(w.get("start", 0))) >= 0.030
                and not any(
                    d.start < float(w.get("end", 0)) and d.end > float(w.get("start", 0))
                    for d in filler_drops
                )
            ]
            if not _gap_uncov:
                continue

            # Clamp: 10ms before the first real word of the next segment
            # (start >= _ge_src), so no word can belong to two segments.
            _next_words_src = sorted(
                [
                    w for w in _source_words
                    if float(w.get("start", 0)) >= _ge_src - 0.005
                    and (float(w.get("end", 0)) - float(w.get("start", 0))) >= 0.030
                ],
                key=lambda w: float(w["start"]),
            )
            _next_first_src = float(_next_words_src[0]["start"]) if _next_words_src else _ge_src
            # In a gap there are no virtual drops, so src delta == compressed delta.
            _next_first_c = _gs_c + (_next_first_src - _gs_src)

            _wtxt = " ".join(str(w.get("text", "")).strip() for w in _gap_uncov[:8])
            _last_w_end_src = float(_gap_uncov[-1].get("end", 0)) + 0.150
            _new_end_c = _gs_c + (_last_w_end_src - _gs_src)
            _new_end_c = min(_new_end_c, _ge_c - 0.010, _next_first_c - 0.010)
            if _new_end_c <= _gs_c + 0.050:
                continue

            _keep_raw[_gi]["end"] = round(_new_end_c, 3)
            _n_rescued += 1
            print(
                f"[GAP-RESCUE] gap-{_gi} extended seg[{_gi}].end"
                f" {_gs_c:.2f}->{_new_end_c:.2f}"
                f" (src {_gs_src:.2f}->{_last_w_end_src:.2f}): '{_wtxt}'",
                flush=True,
            )
            # Fix 6: flag repeated words being rescued so we can trace occurrences
            _rep_rescued = [
                (str(w.get("text", "")).strip(), round(float(w.get("start", 0)), 2))
                for w in _gap_uncov
                if str(w.get("text", "")).strip().lower() in _repeated_vocab
            ]
            if _rep_rescued:
                _rep_detail = [
                    f"'{t}'@{ts}s (×{_src_word_counts[t.lower()]} in transcript)"
                    for t, ts in _rep_rescued
                ]
                print(
                    f"[GAP-RESCUE] gap-{_gi} rescued REPEATED word(s): {_rep_detail}",
                    flush=True,
                )
        if _n_rescued:
            print(
                f"[GAP-RESCUE] {_n_rescued} segment(s) extended to recover lost speech",
                flush=True,
            )

        # ── Step 7: Hook rewrite (Feature 3) ──────────────────────────────
        _t = time.perf_counter()
        store.update(job_id, status="planning", progress=50,
                     message="Rewriting hook for maximum retention…")
        hook_overlay: dict = {}
        try:
            keep_segs = plan.keep_segments
            if keep_segs:
                first_seg_text = keep_segs[0].get("summary", "") or ""
                if not first_seg_text:
                    # Pull text from transcript segments that overlap the first keep segment.
                    fs_start = float(keep_segs[0].get("start", 0))
                    fs_end   = float(keep_segs[0].get("end", fs_start + 10))
                    first_seg_text = " ".join(
                        w.get("text", "")
                        for seg in transcript_clean.get("segments", [])
                        for w in seg.get("words", [])
                        if fs_start <= float(w.get("start", 0)) <= fs_end
                    )[:200]
                full_text = transcript_clean.get("text", "")[:500]
                hook_result = rewrite_hook(full_text, first_seg_text, brand_color or "#FF7751")
                if hook_result.get("confidence", 0.0) >= 0.7:
                    hook_overlay = hook_result
        except Exception:
            hook_overlay = {}
        print(f"[TIMING] hook_rewrite: {time.perf_counter()-_t:.1f}s", flush=True)

        # ── Step 8: B-roll generation (Feature 1) ─────────────────────────
        _t = time.perf_counter()
        store.update(job_id, status="planning", progress=55,
                     message="Generating b-roll overlays…")
        broll_specs_dicts: list[dict] = []
        try:
            broll_gen = BrollGenerator()
            keep_segs = plan.keep_segments or []

            # Build edit_timeline_map: source_start → cumulative_edit_start.
            edit_map: dict[float, float] = {}
            cum = 0.0
            for seg in keep_segs:
                ss = float(seg.get("start", 0))
                ee = float(seg.get("end", ss))
                edit_map[ss] = cum
                cum += max(0.0, ee - ss)
            total_edit_dur = cum

            transcript_segs = transcript_clean.get("segments", [])
            broll_specs = broll_gen.generate(
                transcript_segs, edit_map, total_edit_dur, subject_pos
            )
            broll_specs_dicts = [
                {"kind": bs.kind, "at": bs.at, "duration": bs.duration, "params": bs.params}
                for bs in broll_specs
            ]
        except Exception:
            broll_specs_dicts = []
        print(f"[TIMING] broll_generation: {time.perf_counter()-_t:.1f}s", flush=True)

        # ── Step 9: Detect content type for color grade ────────────────────
        detected_content_type = content_type_hint.lower() if content_type_hint else ""
        if not detected_content_type:
            detected_content_type = detect_content_type(transcript_clean.get("text", ""))

        # ── Step 10: Build adaptive graphic specs ─────────────────────────
        selector  = GraphicSelector()
        video_ctx = build_video_context(transcript_clean, plan)
        selector.configure(video_ctx["content_type"])
        graphic_specs_objs = []
        for seg in (plan.script_structure or []):
            seg_text  = " ".join(seg.get("lines", []))
            seg_role  = seg.get("beat", "")
            try:
                seg_start = float(seg.get("start", 0.0))
                seg_end   = float(seg.get("end", seg_start + 3.0))
            except (TypeError, ValueError):
                continue
            seg_dur = max(1.0, seg_end - seg_start)
            spec = selector.select(seg_text, seg_role, seg_start, seg_dur, video_ctx)
            if spec is not None:
                graphic_specs_objs.append(spec)

        # ── Step 11: Build preview for frontend ───────────────────────────
        preview = _build_preview(
            plan, transcript_clean, detected_content_type,
            hook_overlay, broll_specs_dicts, speaker_dicts,
        )

        # ── Persist everything; set status → ready_for_review ─────────────
        print(f"[TIMING] phase1_total: {time.perf_counter()-_t0:.1f}s", flush=True)
        store.update(
            job_id,
            status="ready_for_review",
            progress=65,
            message="Edit plan ready — review before rendering.",
            plan_data={
                "raw": plan.raw,
                "content_type": detected_content_type,
                "graphic_specs": [
                    {"kind": gs.kind, "at": gs.at, "duration": gs.duration}
                    for gs in graphic_specs_objs
                ],
                "broll_specs": broll_specs_dicts,
                "speaker_segments": speaker_dicts,
                "hook_overlay": hook_overlay,
                "brand_kit": brand_kit,
                "template": template,
                "filler_drops": [
                    {"start": d.start, "end": d.end, "reason": d.reason}
                    for d in filler_drops
                ],
                "virtual_drops": [
                    {"start": d.start, "end": d.end, "reason": d.reason}
                    for d in _effective_virtual_drops
                ],
                "source_words": _source_words,
            },
            transcript=transcript_clean,
            subject_pos=subject_pos,
            energy_profile=energy_dicts,
            hook_overlay=hook_overlay,
            preview=preview,
            params=store.get(job_id).params | dict(
                caption_font=caption_font,
                caption_color=caption_color,
                caption_position=caption_position,
                caption_style=caption_style,
                brand_color=brand_color,
                aesthetic=aesthetic,
                editing_style=editing_style,
                style_pack=style_pack,
                content_type=detected_content_type,
            ),
        )

    except AudioMissingError as e:
        store.update(
            job_id,
            status="error",
            error=str(e),
            message=str(e),
        )
    except Exception as e:
        store.update(
            job_id,
            status="error",
            error=f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
            message="Phase 1 (analysis) failed.",
        )


def quality_check(plan, result: dict) -> list[str]:
    """Post-render quality gate — returns a list of warning/error strings.

    Called automatically after every render. Issues are logged at WARNING
    level and surfaced in Railway logs so they are easy to spot.
    """
    issues: list[str] = []
    edited_duration = float(result.get("duration", 0.0))

    # Hook strength
    segs = plan.keep_segments or []
    if segs and segs[0].get("score", 5) < 10:
        issues.append(
            f"WARN: Hook segment score={segs[0].get('score','?')} — retention risk"
        )

    # Duration guard for short-form
    if edited_duration > 90 and plan.format == "short":
        issues.append(f"WARN: Short-form is {edited_duration:.1f}s — exceeds 90s target")

    # Minimum segment count
    if len(segs) < 3:
        issues.append(f"WARN: Only {len(segs)} segment(s) — may feel incomplete")

    # No empty output
    if edited_duration < 5:
        issues.append(f"ERROR: Output duration {edited_duration:.1f}s is suspiciously short")

    return issues


def run_render_phase(job_id: str, src: Path) -> None:
    """Phase 2: render the approved edit plan → status: done."""
    job = store.get(job_id)
    if not job:
        return
    # Serialize heavy renders: Chrome worker pools (8+ processes × 700 MB each)
    # exhaust the cgroup limit when two renders overlap.  The semaphore queues
    # the second render until the first is complete — no job is dropped.
    _sem_acquired = _RENDER_SEM.acquire(blocking=False)
    if not _sem_acquired:
        print(
            f"[PIPELINE] job {job_id}: render semaphore busy — queuing"
            " (another render is in progress)",
            flush=True,
        )
        store.update(job_id, message="En file d'attente (rendu précédent en cours)…")
        _RENDER_SEM.acquire(blocking=True)
        print(f"[PIPELINE] job {job_id}: render semaphore acquired — starting", flush=True)
    try:
        _t_phase2 = time.perf_counter()
        plan_data   = job.plan_data or {}
        transcript  = job.transcript or {}
        subject_pos = job.subject_pos
        params      = job.params or {}

        from app.agent.planner import EditPlan, _guard_plan_inplace, _fallback_keep_all
        _raw = plan_data.get("raw", {})
        _src_duration = float((job.transcript or {}).get("duration", 0.0))

        # Guard: also applied here in case Phase 2 replays a plan stored
        # before the guard was deployed (plan_edit guards only at Phase 1 time).
        _guard_plan_inplace(_raw, job.transcript or {}, _src_duration)
        _kept_s = sum(
            max(0.0, float(s.get("end", 0)) - float(s.get("start", 0)))
            for s in _raw.get("keep_segments", [])
            if isinstance(s, dict)
        )
        _drop_pct = 100.0 * (1.0 - _kept_s / max(_src_duration, 0.01))
        print(
            f"[PLAN-GUARD] phase2 ratio: kept={_kept_s:.1f}s / {_src_duration:.1f}s "
            f"({100-_drop_pct:.0f}% kept, {_drop_pct:.0f}% dropped)",
            flush=True,
        )
        if _drop_pct > 40.0 and _src_duration > 0:
            print(
                f"[PLAN-GUARD] phase2: {_drop_pct:.0f}% dropped > 40% — applying fallback (keep all)",
                flush=True,
            )
            _raw = _fallback_keep_all(_raw, job.transcript or {})

        plan = EditPlan(raw=_raw)

        content_type  = plan_data.get("content_type", "coaching")
        broll_specs_d = plan_data.get("broll_specs", [])
        speaker_segs  = plan_data.get("speaker_segments", [])
        hook_overlay  = plan_data.get("hook_overlay", {})

        from app.engine.silence_remover import DropSegment as _DropSegment
        filler_drops = [
            _DropSegment(start=d["start"], end=d["end"], reason=d["reason"])
            for d in plan_data.get("filler_drops", [])
        ]
        virtual_drops = [
            _DropSegment(start=d["start"], end=d["end"], reason=d["reason"])
            for d in plan_data.get("virtual_drops", [])
        ]
        source_words: list[dict] = plan_data.get("source_words", [])

        # Rebuild GraphicSpec objects from stored dicts.
        from app.engine.graphics_engine import GraphicSelector, build_video_context
        selector  = GraphicSelector()
        video_ctx = build_video_context(transcript, plan)
        selector.configure(video_ctx["content_type"])
        graphic_specs = []
        for seg in (plan.script_structure or []):
            seg_text  = " ".join(seg.get("lines", []))
            seg_role  = seg.get("beat", "")
            try:
                seg_start = float(seg.get("start", 0.0))
                seg_end   = float(seg.get("end", seg_start + 3.0))
            except (TypeError, ValueError):
                continue
            seg_dur = max(1.0, seg_end - seg_start)
            spec = selector.select(seg_text, seg_role, seg_start, seg_dur, video_ctx)
            if spec is not None:
                graphic_specs.append(spec)

        # Rebuild BrollSpec objects.
        from app.engine.broll_generator import BrollSpec
        broll_specs = [
            BrollSpec(
                kind=bd["kind"], at=bd["at"],
                duration=bd["duration"], params=bd.get("params", {}),
            )
            for bd in broll_specs_d
        ]

        # Free Whisper RAM before FFmpeg + HyperFrames Chrome workers.
        unload_model()
        print("[PIPELINE] Whisper model unloaded — RAM freed before render", flush=True)

        store.update(job_id, status="rendering", progress=70,
                     message="Rendering with FFmpeg…")

        out_path = settings.outputs_dir / f"{job_id}.mp4"
        work_dir = settings.work_dir / job_id

        _t = time.perf_counter()
        result = render(
            src,
            transcript,
            plan,
            work_dir,
            out_path,
            caption_font=params.get("caption_font", "Poppins Bold"),
            caption_color=params.get("caption_color", "white"),
            caption_position=params.get("caption_position", "center"),
            caption_style=params.get("caption_style", "impact"),
            brand_color=params.get("brand_color"),
            aesthetic=params.get("aesthetic", "dark-pro"),
            editing_style=params.get("editing_style", "viral"),
            style_pack=params.get("style_pack", "lean_glass"),
            subject_position=subject_pos,
            graphic_specs=graphic_specs,
            content_type=content_type,
            allow_4k=has_4k_access(params.get("coach_profile")),
            filler_drops=filler_drops,
            virtual_drops=virtual_drops,
            source_words=source_words,
        )

        print(f"[TIMING] render: {time.perf_counter()-_t:.1f}s", flush=True)

        # Post-render quality check — logs warnings so they surface in Railway logs.
        import logging as _logging
        _qlog = _logging.getLogger(__name__)
        _issues = quality_check(plan, result)
        for _issue in _issues:
            _qlog.warning("quality_check: %s", _issue)

        # ── Brand: apply intro/outro bumpers (Feature 2) ──────────────────
        _t = time.perf_counter()
        brand_kit  = plan_data.get("brand_kit", {})
        final_path = out_path
        try:
            if brand_kit:
                be = BrandEngine()
                final_path = be.prepend_intro(out_path, brand_kit, work_dir)
                final_path = be.append_outro(final_path, brand_kit, work_dir)
                if final_path != out_path:
                    import shutil as _shutil
                    _shutil.move(str(final_path), str(out_path))
                    final_path = out_path
        except Exception:
            final_path = out_path
        print(f"[TIMING] brand_bumpers: {time.perf_counter()-_t:.1f}s", flush=True)
        print(f"[TIMING] phase2_total: {time.perf_counter()-_t_phase2:.1f}s", flush=True)

        store.update(
            job_id,
            status="done",
            progress=100,
            message="Done.",
            result={
                "video_url": f"/api/download/{job_id}",
                "packaging": result["packaging"],
                "format": result["format"],
                "duration": result["duration"],
                "plan": result["plan"],
                "titres_ctr": plan.titres_ctr,
                "thumbnail_mot": plan.thumbnail_mot,
                "script_structure": plan.script_structure,
                "content_type": content_type,
                "hook_overlay": hook_overlay,
                "brand_applied": bool(brand_kit.get("name")),
            },
        )
        # Video-ready email — fire-and-forget, never blocks render delivery
        try:
            import json as _json
            from app import emails as _emails
            if job.profile_id:
                _prof_path = settings._data_root / "profiles" / f"{job.profile_id}.json"
                if _prof_path.exists():
                    _emails.send_video_ready(_json.loads(_prof_path.read_text(encoding="utf-8")))
        except Exception as _email_exc:
            print(f"[email] video_ready hook failed for job {job_id}: {_email_exc}")
    except Exception as e:
        store.update(
            job_id,
            status="error",
            error=f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
            message="Phase 2 (render) failed.",
        )
    finally:
        _RENDER_SEM.release()


def _build_instructions(
    instructions: str,
    target_audience: str,
    main_message: str,
    desired_emotion: str,
    platform: str,
    content_type_hint: str,
    insights_instructions: str = "",
    template: dict | None = None,
) -> str:
    """Append content brief, template, and analytics hints to the user's instructions."""
    parts = [instructions] if instructions.strip() else []
    if target_audience:
        parts.append(f"TARGET AUDIENCE: {target_audience}")
    if main_message:
        parts.append(f"CORE MESSAGE: {main_message}")
    if desired_emotion:
        parts.append(f"DESIRED EMOTION: Make the viewer feel {desired_emotion}.")
    if platform:
        parts.append(f"PLATFORM: Optimise for {platform}.")
    if content_type_hint:
        parts.append(f"CONTENT TYPE: {content_type_hint}")
    if insights_instructions:
        parts.append(insights_instructions)
    if template:
        style = template.get("style", {})
        name  = template.get("name", "reference video")
        parts.append(
            f"TEMPLATE STYLE (from '{name}'): "
            f"pacing={style.get('pacing','medium')}, "
            f"zoom={style.get('zoom_intensity','medium')}, "
            f"captions={style.get('caption_style','one_word')}, "
            f"energy={style.get('energy_level','medium')}, "
            f"cuts/min={style.get('avg_cuts_per_minute',12):.0f}. "
            f"Match this editing fingerprint as closely as possible."
        )
    return "\n".join(parts) or "(none — apply default high-retention edit)"


def _build_preview(
    plan,
    transcript: dict,
    content_type: str,
    hook_overlay: dict,
    broll_specs: list[dict],
    speaker_segments: list[dict],
) -> dict:
    """Build the structured preview object sent to the frontend."""
    keep_segs = plan.keep_segments or []
    total_original = float(transcript.get("duration", 0.0))
    total_edited   = sum(
        max(0.0, float(s.get("end", 0)) - float(s.get("start", 0)))
        for s in keep_segs
    )

    edit_segments = []
    for i, seg in enumerate(keep_segs):
        try:
            s = float(seg.get("start", 0))
            e = float(seg.get("end", s))
        except (TypeError, ValueError):
            continue
        edit_segments.append({
            "order":   i + 1,
            "role":    seg.get("role", ""),
            "score":   seg.get("score", 0),
            "retention_note": seg.get("retention_note", ""),
            "original_time": f"{s:.1f}s–{e:.1f}s",
            "edit_dur": f"{e - s:.1f}s",
            "note":    seg.get("reason", ""),
        })

    return {
        "hook_rewrite":        hook_overlay.get("rewritten_hook", ""),
        "hook_confidence":     hook_overlay.get("confidence", 0.0),
        "total_duration_original": round(total_original, 1),
        "total_duration_edited":   round(total_edited, 1),
        "segments_kept":   len(keep_segs),
        "segments_cut":    max(0, len(transcript.get("segments", [])) - len(keep_segs)),
        "content_type":    content_type,
        "color_grade":     content_type,
        "edit_plan":       edit_segments,
        "graphics_planned": len(broll_specs),
        "speakers_detected": len(set(ss.get("speaker_id", "A") for ss in speaker_segments)),
        "packaging": plan.packaging,
    }
