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
from concurrent.futures import TimeoutError as _FutureTimeoutError
from pathlib import Path

# One heavy render at a time — prevents N concurrent Chrome worker pools from
# exhausting cgroup RAM.  Phase-1 (transcription) is light enough to overlap.
_RENDER_SEM = threading.Semaphore(1)
_TRANSCRIPTION_TIMEOUT_S = 1200  # 20 min — Whisper hang guard; sets status=error + is_retry=True

# Set by the SIGTERM handler in main.py.  When set:
#   - run_render_phase() refuses to start a new render (returns immediately so
#     Layer C recovery picks it up on the next boot)
#   - queued renders waiting on the semaphore abort rather than block forever
_shutdown_event = threading.Event()

from app.agent.planner import FormatHint, analyze_narrative_map, analyze_subject_position, plan_edit, rewrite_hook
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
from app.engine.tracking import track_subject

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
            # llm_false_start cuts target a false-start phrase whose restart begins
            # at drop.end; any protected word entirely within the cut is the false-start
            # occurrence (not the key-line content which starts at drop.end) — exempt it.
            # Other reasons (llm_repetition, etc.) do NOT get this exemption: a wrong
            # repetition cut that happens to target a key-line word must remain blocked.
            and not (
                drop.reason == "llm_false_start"
                and drop.start <= ws + 0.005
                and we <= drop.end + 0.005
            )
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
            # For filler drops: use min-end (conservative) — the rule-based post-pad
            # is always ≤ the LLM's boundary (next word start), so min preserves the
            # acoustic tail without letting an over-wide LLM cut eat into content words.
            # For all other drop types: keep union (wider boundaries preserve padding).
            _both_filler = (
                prev.reason.startswith("filler") or prev.reason.startswith("llm_filler")
            ) and (
                d.reason.startswith("filler") or d.reason.startswith("llm_filler")
            )
            merged = _DS(
                start=min(prev.start, d.start),
                end=min(prev.end, d.end) if _both_filler else max(prev.end, d.end),
                reason=prev.reason,
                target_intervals=prev.target_intervals + d.target_intervals,
            )
            print(
                f"[DEDUP] {d.reason} [{d.start:.2f}-{d.end:.2f}]"
                f" merged into {prev.reason} [{prev.start:.2f}-{prev.end:.2f}]"
                f" -> {'min-end' if _both_filler else 'union'}"
                f" [{merged.start:.2f}-{merged.end:.2f}]"
                f" (overlap={overlap:.3f}s)",
                flush=True,
            )
            result[-1] = merged
        else:
            result.append(d)
    return result


def _stable_ts_refine_cuts(src: "Path", drops: list, transcript: dict) -> list:
    """Re-analyse repetition-cut time windows with stable-ts to correct word boundaries.

    Only runs when STABLE_TS_REPAIR=true (default: false — no RAM cost on normal path).
    Unloads the main Whisper model first so stable-ts never double-loads it.
    Only operates on drops whose reason starts with 'llm_repetition'.
    """
    import os as _os2
    if _os2.getenv("STABLE_TS_REPAIR", "false").strip().lower() != "true":
        return drops

    _rep = [d for d in drops if d.reason.startswith("llm_repetition")]
    if not _rep:
        return drops

    import gc as _gc2, re as _re2, subprocess as _sp2, tempfile as _tmp2, time as _t2
    from pathlib import Path as _Path2

    print(f"[STABLE-TS] refining {len(_rep)} repetition cut(s)…", flush=True)
    _t_start2 = _t2.perf_counter()

    try:
        import stable_whisper as _sw2
    except ImportError:
        print("[STABLE-TS] stable-ts not installed — skipping refinement", flush=True)
        return drops

    from app.engine.transcribe import unload_model as _unload2, FFMPEG_PATH as _FF2
    from app.engine.silence_remover import DropSegment as _DS2

    # Unload main Whisper before loading stable-ts (RAM constraint on 1 GB dyno).
    # run_render_phase() will call unload_model() again — safe, already None.
    _unload2()
    print("[STABLE-TS] Whisper unloaded before stable-ts", flush=True)

    _model_name = _os2.getenv("WHISPER_MODEL", "large-v3")
    _stm = _sw2.load_faster_whisper(_model_name, device="cpu", compute_type="int8")

    _all_words = [w for seg in transcript.get("segments", []) for w in seg.get("words", [])]
    _lang = transcript.get("language") or "fr"
    _nr = _re2.compile(r"\W")
    def _n(t: str) -> str:
        return _nr.sub("", t.lower())

    result = list(drops)

    for drop in _rep:
        if not drop.target_intervals:
            continue
        _last_iv = drop.target_intervals[-1]
        _ls, _le = float(_last_iv[0]), float(_last_iv[1])

        # Text of the last cut word (for matching in stable-ts output)
        _ltxt = next(
            (str(w.get("text", "")).strip() for w in _all_words
             if abs(float(w.get("start", 0)) - _ls) < 0.050),
            "",
        )
        if not _ltxt:
            continue

        # Audio window: 1 s before cut → 2× cut-duration after drop end (kept occ)
        _ws2 = max(0.0, drop.start - 1.0)
        _we2 = drop.end + (drop.end - drop.start) + 2.0
        _wav2 = _Path2(_tmp2.mktemp(suffix=".wav"))
        try:
            _sp2.run(
                [_FF2, "-y", "-loglevel", "error",
                 "-ss", str(_ws2), "-to", str(_we2),
                 "-i", str(src),
                 "-vn", "-ac", "1", "-ar", "16000", str(_wav2)],
                check=True, timeout=30,
            )
        except Exception as _ex2:
            print(f"[STABLE-TS] ffmpeg extract error: {_ex2}", flush=True)
            _wav2.unlink(missing_ok=True)
            continue

        try:
            _res2 = _stm.transcribe(
                str(_wav2),
                word_timestamps=True,
                language=_lang,
                temperature=[0.0],
                beam_size=5,
                condition_on_previous_text=False,
            )
            _stw = []
            for _sg2 in _res2.segments:
                for _ww in (getattr(_sg2, "words", None) or []):
                    _tt = (getattr(_ww, "word", "") or "").strip()
                    if _tt:
                        _stw.append({
                            "text":  _tt,
                            "start": round(float(_ww.start) + _ws2, 3),
                            "end":   round(float(_ww.end)   + _ws2, 3),
                        })
        except Exception as _ex2:
            print(f"[STABLE-TS] transcribe error: {_ex2}", flush=True)
            _wav2.unlink(missing_ok=True)
            continue
        finally:
            _wav2.unlink(missing_ok=True)

        # Match last cut word in stable-ts output by text + approximate start
        _m = next(
            (sw for sw in _stw
             if _n(sw["text"]) == _n(_ltxt)
             and abs(sw["start"] - _ls) < 0.200),
            None,
        )
        if _m is None:
            print(f"[STABLE-TS] no match for '{_ltxt}' ~{_ls:.3f}s in cut"
                  f" {drop.start:.2f}-{drop.end:.2f}s", flush=True)
            continue
        if abs(_m["end"] - _le) <= 0.050:
            continue  # no meaningful improvement

        _new_end = _m["end"]
        _new_ivs = drop.target_intervals[:-1] + ((_ls, _new_end),)
        _idx = result.index(drop)
        result[_idx] = _DS2(
            start=drop.start, end=_new_end,
            reason=drop.reason, target_intervals=_new_ivs,
        )
        print(
            f"[STABLE-TS] REFINED '{_ltxt}' cut end"
            f" {_le:.3f}->{_new_end:.3f}s (delta={_new_end-_le:+.3f}s)",
            flush=True,
        )

    del _stm
    _gc2.collect()
    print(f"[STABLE-TS] done {_t2.perf_counter()-_t_start2:.1f}s, model unloaded", flush=True)
    return result


def _llm_editorial_cuts(
    transcript: dict,
    key_lines: list[str],
    *,
    vad_gaps: list[dict] | None = None,
    keep_segs: list[dict] | None = None,
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

    import re as _re_wn
    def _wn(_wd: dict) -> str:
        """Normalize word text: strip non-alpha characters, lowercase."""
        return _re_wn.sub(r"\W", "", str(_wd.get("text", "")).lower())

    # Build numbered transcript (cap at 600 words — Haiku context is large)
    _MAX_WORDS = 600
    numbered = "\n".join(
        f"[{i}] {str(w.get('text', '')).strip()}"
        for i, w in enumerate(words[:_MAX_WORDS])
    )
    _truncated = len(words) > _MAX_WORDS
    _max_idx = min(len(words), _MAX_WORDS) - 1

    key_lines_str = "\n".join(f"- {l}" for l in key_lines[:10]) if key_lines else "(aucun)"

    # VAD acoustic signal block — only visible gaps within the numbered transcript range
    _vad_block = ""
    if vad_gaps:
        _visible = [
            g for g in vad_gaps
            if (g.get("word_before_idx") is None or g["word_before_idx"] < _MAX_WORDS)
            and (g.get("word_after_idx") is None or g["word_after_idx"] < _MAX_WORDS)
        ]
        if _visible:
            _vad_lines = []
            for g in _visible:
                wb = g.get("word_before_idx")
                wa = g.get("word_after_idx")
                ctx_b = g.get("context_before", "")
                ctx_a = g.get("context_after", "")
                dur = g["duration_ms"]
                if wb is not None and wa is not None:
                    ref = f"[{wb}]->[{wa}] \"{ctx_b}\" -> \"{ctx_a}\""
                elif wb is not None:
                    ref = f"apres [{wb}] \"{ctx_b}\""
                else:
                    ref = f"avant [{wa}] \"{ctx_a}\""
                label = "parole entiere manquee" if g["type"] == "full_segment" else "hesitation probable"
                _vad_lines.append(f"  {ref} : {dur}ms ({label})")
            _vad_block = (
                "\nSIGNAL ACOUSTIQUE VAD — paroles detectees par Silero mais non-transcrites par Whisper :\n"
                + "\n".join(_vad_lines)
                + "\n"
            )

    prompt = f"""Tu es un éditeur vidéo expert. Transcription VERBATIM numérotée (un indice = un mot) :

{numbered}{"...(tronqué après [{_max_idx}])" if _truncated else ""}
{_vad_block}
CONSIGNES :
0. SCAN SYSTÉMATIQUE : examine CHAQUE indice de [0] à [{_max_idx}] sans en sauter aucun.
1. Coupe uniquement : fillers isolés (Euh, Bah, Ben, Hein, Hm, ouais isolé), répétitions accidentelles (même mot/groupe répété consécutivement), faux départs (phrase relancée immédiatement).
   SCOPE FILLER — RÈGLE ABSOLUE : un cut de type filler est limité au(x) token(s) filler UNIQUEMENT. Si un mot de CONTENU (verbe, nom, adjectif sémantique) suit immédiatement le filler — même sans pause entre eux — ce mot n'est PAS inclus dans le cut. Ex : "[68] Bah, [69] retiens [70] ça" → coupe [68,68] seulement. JAMAIS [68,69] : "retiens" est un verbe de contenu, il reste. Un filler ne peut jamais "avaler" le mot de contenu qui le suit.
2. Répétitions simples : garde LA DERNIÈRE occurrence, coupe les précédentes. Ex : "[5] il [6] il" → coupe [5,5], garde [6].
3. Répétitions multi-mots : identifie le groupe entier depuis son PREMIER MOT. Ex : "[12] parce [13] qu'ils [14] parce [15] qu'ils" → coupe [12,13] (TOUT le premier groupe), garde [14,15]. ERREUR À ÉVITER : couper seulement [13,13] "qu'ils" en oubliant [12] "parce" → "parce" orphelin audible.
3b. Répétitions SUPERPOSÉES — un doublon peut appartenir à un groupe plus long qui se répète aussi. Ex : "[60] il [61] faut [62] il [63] faut" — '[62] il' ressemble à un doublon de '[60] il', MAIS le groupe 'il faut' se répète entièrement × 2. Coupe le PREMIER groupe complet [60,61], garde [62,63]. ERREUR CRITIQUE : couper seulement [62,62]='il' → laisse '[61] faut [63] faut' audible. Règle : après ta coupe, le texte restant ne doit contenir AUCUNE répétition résiduelle du même mot ou groupe.
IMPORTANT — la règle 3b prime sur la règle 6 (doute) dès que la structure AB-AB est explicitement visible, même si AB a du poids sémantique ('on va', 'je pense', 'c'est', etc.). Ex : "[62] du [63] coup [64] tu [65] dois, [66] tu [67] dois [68] arrêter" → coupe [64,65], garde [62,63,66,67,68,...]. La présence d'un préfixe ('du coup', 'donc', 'alors') AVANT le premier AB ne protège pas la répétition.
4. Répétitions rhétoriques VOLONTAIRES (3+ occurrences identiques, effet stylistique) = NE PAS TOUCHER.
4b. EXCEPTION PHRASE COMPLÈTE AVEC EXTENSION — si un groupe de ≥ 5 mots formant une clause complète (avec verbe conjugué) est répété, ET que la 2ème occurrence ÉTEND la première en ajoutant du contenu supplémentaire après le groupe répété → GARDER LES DEUX occurrences. Ce n'est pas une répétition accidentelle mais une insistance rhétorique délibérée. La règle 3b NE S'APPLIQUE PAS. Signal : group répété ≥ 5 mots ET 2ème occurrence continue au-delà. Ex : "Tu dois te lever chaque matin. Tu dois te lever chaque matin et faire le travail." → kept both (reason="kept — répétition rhétorique phrase complète avec extension").
5. NE JAMAIS toucher ces extraits clés (et UNIQUEMENT ceux-ci — ne crée pas de "segment protégé" de ta propre initiative) :
{key_lines_str}
   → Protection CIBLÉE : seuls les mots exacts de ces extraits sont intouchables. Si un faux départ ou une répétition PRÉCÈDE ces mots dans la même phrase, tu PEUX et DOIS le couper. Ex : "La vérité, la vraie vérité, c'est que tout se joue sur la constance" → coupe "La vérité," (faux départ), garde "la vraie vérité, c'est que tout se joue sur la constance." Ne protège JAMAIS l'ensemble d'un segment sous prétexte qu'il contient une key_line — seuls les mots de la key_line elle-même sont intouchables.
   CONTRE-EXEMPLE INTERDIT — ce que tu NE DOIS PAS faire : kept [32,45] reason="segment clé protégé" alors que [32]="La vérité," est un faux départ visible. CORRECT : cut [32,32] reason="false_start", puis [33,45] reste intact. La règle 6 (doute) ne s'applique pas quand la structure faux-départ est explicitement visible (même groupe relancé). La key_line protège ses indices — elle ne protège PAS les indices qui la précèdent.
6. En cas de doute → NE PAS COUPER.
7. SIGNAL ACOUSTIQUE VAD : si le bloc VAD ci-dessus est présent, examine chaque gap signalé. Un gap indique une parole acoustique NON-TRANSCRITE entre deux mots. Agis UNIQUEMENT si le contexte textuel autour du gap suggère un faux départ ou une répétition — par exemple si le mot APRÈS le gap répète ou reprend un groupe déjà présent juste avant. Dans ce cas, coupe les mots avant le gap qui forment l'ébauche avortée. Si le texte autour du gap est cohérent et non-répété, IGNORE le gap (c'est probablement une pause naturelle ou un son parasite). Rule 6 prime : en cas de doute, ne pas couper.

Réponds UNIQUEMENT en JSON strict (aucun texte avant ni après).
"cuts" = ce qui doit être coupé. "kept" = candidats que tu as examinés mais décidé de GARDER (liste tous pour audit) :
{{"cuts": [{{"indices": [debut, fin_inclus], "reason": "filler|repetition|false_start|premature_conclusive|vad_false_start"}}], "kept": [{{"indices": [i, j], "reason": "kept — explication"}}]}}

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
            temperature=0,
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

    # Log "kept" items for exhaustiveness audit.
    # Also build a set of explicitly kept ranges for the KEPT-WINS guard below.
    _kept_indices: set[tuple[int, int]] = set()
    for kept in data.get("kept", []):
        k_idx = kept.get("indices", [])
        k_reason = kept.get("reason", "")
        if len(k_idx) == 2:
            k0, k1 = int(k_idx[0]), int(k_idx[1])
            if 0 <= k0 <= k1 < len(words):
                _kept_indices.add((k0, k1))
                k_text = " ".join(str(words[k].get("text", "")).strip() for k in range(k0, k1 + 1))
                print(f"[LLM-EDIT] kept [{k0},{k1}] text={k_text!r} reason={k_reason!r}", flush=True)

    # Collect (i0, i1, reason, DropSegment) so the proximity guard below can
    # operate on word indices before committing to the final drops list.
    _pending_drops: list[tuple[int, int, str, object]] = []
    for cut in data.get("cuts", []):
        idx = cut.get("indices", [])
        reason = cut.get("reason", "llm_editorial")
        if len(idx) != 2:
            continue
        i0, i1 = int(idx[0]), int(idx[1])
        if i0 < 0 or i1 >= len(words) or i0 > i1:
            print(f"[LLM-EDIT] skip invalid indices [{i0},{i1}] (words={len(words)})", flush=True)
            continue
        # KEPT-WINS: LLM emitted both kept[i0,i1] and cut[i0,i1] — kept takes priority.
        # Prevents rhetorical/stylistic sequences from being cut despite a contradictory
        # cut entry in the same response (Haiku sometimes emits both simultaneously).
        if (i0, i1) in _kept_indices:
            print(
                f"[LLM-EDIT] KEPT-WINS [{i0},{i1}] — cut suppressed (conflict with kept entry)",
                flush=True,
            )
            continue

        # RHETORICAL-TRIPLE guard: if the span starts a phrase repeated ≥3 times
        # consecutively in context (lookahead 12 words past i1), the repetition is
        # a deliberate stylistic device — block the cut unconditionally.
        # Single-occurrence French function words (stutters) are exempted.
        # Example: "Répète après moi" × 3 must never be removed even though
        # reason="repetition" — the LLM itself stated it as "× 3" but still cut it.
        if reason.startswith("repetition"):
            _FUNC_FR = {
                "il", "elle", "ils", "elles", "je", "tu", "nous", "vous",
                "le", "la", "les", "un", "une", "des", "de", "du", "en", "y", "on",
                "et", "ou", "mais", "donc", "or", "ni", "car", "que", "qui", "quoi",
            }
            _ctx_end = min(len(words) - 1, i1 + 12)
            _rhet_ngram: tuple | None = None
            _rhet_cnt = 0
            for _n in range(1, 5):
                if i0 + _n - 1 > i1:
                    break
                _gram = tuple(_wn(words[i0 + _k]) for _k in range(_n))
                if not all(_gram):
                    continue
                if _n == 1 and _gram[0] in _FUNC_FR:
                    continue  # single function-word repeat → stutter, not rhetoric
                _cnt, _pos = 0, i0
                while _pos + _n - 1 <= _ctx_end:
                    if tuple(_wn(words[_pos + _k]) for _k in range(_n)) == _gram:
                        _cnt += 1
                        _pos += _n
                    else:
                        break
                if _cnt >= 3:
                    _rhet_ngram, _rhet_cnt = _gram, _cnt
                    break
            if _rhet_ngram:
                print(
                    f"[LLM-EDIT] RHETORICAL-TRIPLE [{i0},{i1}]"
                    f" — {_rhet_cnt}× {list(_rhet_ngram)!r} — cut suppressed",
                    flush=True,
                )
                continue

        # PHRASE-EXTENDED-REPEAT guard: a complete phrase (≥5 words) that immediately
        # restarts verbatim then continues with additional content is a deliberate
        # rhetorical emphasis repeat ("Tu dois X. Tu dois X et faire Y.") — not
        # accidental. Block regardless of what the LLM decided. Complements rule 4b
        # in the prompt; this code guard handles LLM non-determinism.
        if reason.startswith("repetition") and (i1 - i0 + 1) >= 5:
            _cut_norms = tuple(
                _wn(words[k]) for k in range(i0, i1 + 1)
                if _wn(words[k])
            )
            if len(_cut_norms) >= 5:
                _rs, _collected = i1 + 1, []
                while _rs < len(words) and len(_collected) < len(_cut_norms):
                    _n = _wn(words[_rs])
                    if _n:
                        _collected.append(_n)
                    _rs += 1
                if tuple(_collected) == _cut_norms and _rs < len(words):
                    print(
                        f"[LLM-EDIT] PHRASE-EXTENDED-REPEAT [{i0},{i1}]"
                        f" — {len(_cut_norms)}-word phrase restarts with extension"
                        f" — cut suppressed",
                        flush=True,
                    )
                    continue

        # LLM sometimes cuts both occurrences of "A A" — shrink to first half so last is kept.
        _dedup_halve_fired = False
        if reason.startswith("repetition"):
            _span = i1 - i0 + 1
            if _span >= 2 and _span % 2 == 0:
                _half = _span // 2
                _lh = [_wn(words[i0 + k]) for k in range(_half)]
                _rh = [_wn(words[i0 + _half + k]) for k in range(_half)]
                if _lh == _rh and all(_lh):
                    _orig_i1 = i1
                    i1 = i0 + _half - 1
                    print(
                        f"[LLM-EDIT] DEDUP-HALVE [{i0},{_orig_i1}]→[{i0},{i1}]"
                        f" ({_half}-word symmetric repeat, keeping last occurrence)",
                        flush=True,
                    )
                    # Verify kept last occurrence falls inside a keep_segment.
                    # If it's in a planner gap it will be absent from the output
                    # regardless — in that case cut the full original span instead.
                    if keep_segs is not None:
                        _kept_t0 = float(words[i0 + _half].get("start", 0))
                        _kept_t1 = float(words[i0 + _span - 1].get("end", 0))
                        _in_ks = any(
                            float(ks.get("start", 0)) <= _kept_t0 + 0.050
                            and float(ks.get("end", 0)) >= _kept_t1 - 0.050
                            for ks in keep_segs
                        )
                        if not _in_ks:
                            i1 = _orig_i1
                            print(
                                f"[LLM-EDIT] DEDUP-HALVE-REVERTED [{i0},{i1}]"
                                f" — kept [{i0+_half},{i0+_span-1}]"
                                f" t={_kept_t0:.3f}-{_kept_t1:.3f}s not in any keep_segment",
                                flush=True,
                            )
                        else:
                            _dedup_halve_fired = True
                    else:
                        _dedup_halve_fired = True

        # Repetition group completeness check.
        # (a) BACKWARD: extend i0 left if the LLM missed the start of the group.
        #     Condition: word just before cut == word just after cut (symmetric).
        #     Example: LLM cuts 'qu'ils' but misses preceding 'parce'.
        # (b) FORWARD: extend i1 right if the LLM missed the end of the group.
        #     Condition: words after i1 match words from the first-occurrence
        #     reference (i0 - grp_size).  Example: LLM cuts 'il' but misses
        #     'faut' — words[i1+1]='faut₂' matches words[i0-1]='faut₁'.
        # Both capped at the current cut size to avoid runaway.
        if reason.startswith("repetition"):
            _ctx_l = _wn(words[i0 - 1]) if i0 > 0 else ""
            _ctx_r = _wn(words[i1 + 1]) if i1 + 1 < len(words) else ""
            print(
                f"[LLM-EDIT] EXTEND-CTX [{i0},{i1}]"
                f" left={_ctx_l!r} right={_ctx_r!r}",
                flush=True,
            )
            _max_ext = i1 - i0 + 1
            _ext = 0
            while (
                _ext < _max_ext
                and i0 - _ext - 1 >= 0
                and i1 + _ext + 1 < len(words)
                and _wn(words[i0 - _ext - 1]) == _wn(words[i1 + _ext + 1])
            ):
                _ext += 1
            if _ext > 0:
                _orig_i0 = i0
                i0 -= _ext
                _ext_text = " ".join(
                    str(words[k].get("text", "")).strip() for k in range(i0, _orig_i0)
                )
                print(
                    f"[LLM-EDIT] EXTEND-GROUP [{_orig_i0},{i1}]→[{i0},{i1}]"
                    f" +{_ext} word(s) {_ext_text!r}",
                    flush=True,
                )
            # (b) Forward extension: words after i1 should continue the second
            # occurrence.  Reference: words[i0 - grp .. i0 - 1] is the tail of
            # the first occurrence immediately before the cut.
            _grp = i1 - i0 + 1
            _fwd = 0
            while (
                _fwd < _grp
                and i1 + _fwd + 1 < len(words)
                and i0 - _grp + _fwd >= 0
                and _wn(words[i1 + _fwd + 1]) == _wn(words[i0 - _grp + _fwd])
            ):
                _fwd += 1
            if _fwd > 0:
                _orig_i1 = i1
                i1 += _fwd
                _fwd_text = " ".join(
                    str(words[k].get("text", "")).strip()
                    for k in range(_orig_i1 + 1, i1 + 1)
                )
                print(
                    f"[LLM-EDIT] EXTEND-GROUP-FWD [{i0},{_orig_i1}]→[{i0},{i1}]"
                    f" +{_fwd} word(s) {_fwd_text!r}",
                    flush=True,
                )

        # FILLER-SCOPE-TRIM: enforce canonical filler boundaries — the LLM cannot
        # extend a filler cut to include content words. Forward-scan [i0..i1] with
        # _FILLER_PHRASES (multi-word, longest-first) then single _is_filler; truncate
        # i1 to the last canonical filler token found. SKIP if span has no canonical
        # filler at all (protects against hallucinated filler reasons).
        if reason == "filler":
            from app.engine.silence_remover import (
                _is_filler as _sr_is_filler,
                _FILLER_PHRASES as _sr_phrases,
            )
            _filler_end = i0 - 1  # sentinel: no canonical filler found yet
            _k = i0
            while _k <= i1:
                _matched_phrase = False
                for _ph in _sr_phrases:   # sorted longest-first already
                    _pn = len(_ph)
                    if _k + _pn - 1 > i1:
                        continue
                    if all(
                        str(words[_k + _pi].get("text", "")).strip(".,!?;:'\"()").lower() == _ph[_pi]
                        for _pi in range(_pn)
                    ):
                        _filler_end = _k + _pn - 1
                        _k += _pn
                        _matched_phrase = True
                        break
                if not _matched_phrase:
                    if _sr_is_filler(str(words[_k].get("text", "")).strip()):
                        _filler_end = _k
                        _k += 1
                    else:
                        break  # first non-canonical word — stop
            if _filler_end < i0:
                _skip_text = " ".join(str(words[k].get("text", "")).strip() for k in range(i0, i1 + 1))
                print(
                    f"[LLM-EDIT] FILLER-SCOPE-SKIP [{i0},{i1}] text={_skip_text!r}"
                    f" — no canonical filler in span",
                    flush=True,
                )
                continue
            if _filler_end < i1:
                _trim_orig = " ".join(str(words[k].get("text", "")).strip() for k in range(i0, i1 + 1))
                print(
                    f"[LLM-EDIT] FILLER-SCOPE-TRIM [{i0},{i1}]→[{i0},{_filler_end}]"
                    f" text={_trim_orig!r}",
                    flush=True,
                )
                i1 = _filler_end

        t_start = float(words[i0].get("start", 0))
        # Use next word's start to absorb the inter-repetition pause into the cut.
        t_end = (
            float(words[i1 + 1].get("start", 0))
            if i1 + 1 < len(words)
            else float(words[i1].get("end", 0))
        )
        # REP-TAIL-PAD: when Whisper reports adjacent words (gap < 50ms), t_end
        # lands at words[i1].end with no room for the acoustic tail. Extend 60ms
        # into the next word so the tail falls inside the drop rather than bleeding
        # into the next sub-part. Clamped to keep ≥60ms of the next word intact.
        # _rep_tail_ext: partial interval of the next word that the pad eats into,
        # added to target_intervals below so word_safe treats it as intentional.
        _rep_tail_ext: "tuple[float, float] | None" = None
        if reason.startswith("repetition") and i1 + 1 < len(words) and not _dedup_halve_fired:
            _word_i1_end = float(words[i1].get("end", 0))
            _gap = t_end - _word_i1_end
            if _gap < 0.050:
                _next_word_start = float(words[i1 + 1].get("start", 0))
                _next_word_end = float(words[i1 + 1].get("end", 0))
                _tail_target = _word_i1_end + 0.060
                _safe_limit = max(_next_word_end - 0.060, t_end)
                _new_t_end = min(max(t_end, _tail_target), _safe_limit)
                # Guard: don't extend into the next word's consonant onset when
                # the existing gap already covers the acoustic tail (gap ≥ 10ms).
                # gap < 10ms = Whisper adjacent words, no room for tail → extend.
                # gap ≥ 10ms + extension clips next word = cancel extension.
                if _new_t_end > _next_word_start and _gap >= 0.010:
                    _new_t_end = t_end
                if _new_t_end > t_end:
                    print(
                        f"[LLM-EDIT] REP-TAIL-PAD [{i0},{i1}]"
                        f" t_end {t_end:.3f}→{_new_t_end:.3f}"
                        f" gap={_gap*1000:.0f}ms next_word={words[i1+1].get('text','?')!r}",
                        flush=True,
                    )
                    t_end = _new_t_end
                    _rep_tail_ext = (float(words[i1 + 1].get("start", 0)), _new_t_end)
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
        # When REP-TAIL-PAD fired, include the partial next-word interval so
        # word_safe doesn't see the tail-pad extension as a collateral cut.
        _target_ivs = tuple(
            (float(words[k].get("start", 0)), float(words[k].get("end", 0)))
            for k in range(i0, i1 + 1)
        )
        if _rep_tail_ext is not None:
            _target_ivs = _target_ivs + (_rep_tail_ext,)
        _pending_drops.append((
            i0, i1, reason,
            _DS(start=t_start, end=t_end, reason=f"llm_{reason}",
                target_intervals=_target_ivs),
        ))

    # ── Proximity guard for repetition cuts ──────────────────────────────────
    # Two repetition cuts that overlap or sit within 2 word indices of each
    # other are almost always the LLM double-firing on the same phonetic event
    # (e.g. rule-2 + rule-3b both triggering adjacent single-word cuts).
    # Keep the wider cut; drop the narrower to avoid over-cutting.
    _rep_pending = [(i0, i1, d) for i0, i1, r, d in _pending_drops if r.startswith("repetition")]
    _other_drops = [d for _, _, r, d in _pending_drops if not r.startswith("repetition")]
    _rep_pending.sort(key=lambda x: x[0])
    _rep_kept: list[tuple[int, int, object]] = []
    for _ri0, _ri1, _rd in _rep_pending:
        _conflict = next(
            (ci for ci, (ki0, ki1, _) in enumerate(_rep_kept)
             if _ri0 <= ki1 + 2 and _ri1 >= ki0 - 2),
            None,
        )
        if _conflict is None:
            _rep_kept.append((_ri0, _ri1, _rd))
        else:
            _ki0, _ki1, _kd = _rep_kept[_conflict]
            if (_ri1 - _ri0) > (_ki1 - _ki0):
                print(
                    f"[LLM-EDIT] PROXIMITY-DEDUP: [{_ki0},{_ki1}] dropped"
                    f" — [{_ri0},{_ri1}] is wider",
                    flush=True,
                )
                _rep_kept[_conflict] = (_ri0, _ri1, _rd)
            else:
                print(
                    f"[LLM-EDIT] PROXIMITY-DEDUP: [{_ri0},{_ri1}] dropped"
                    f" — [{_ki0},{_ki1}] is wider or equal",
                    flush=True,
                )

    drops = _other_drops + [d for _, _, d in _rep_kept]

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
        import os as _os_st
        _st_raw = _os_st.environ.get("SUBJECT_TRACKING", "(not set)")
        # Gate tracking on OUTPUT format, not source dimensions.
        # format_hint="long" means landscape long-form — no tracking needed.
        # format_hint="short" or "auto" means portrait short-form — run tracking.
        _is_portrait_output = format_hint != "long"
        print(
            f"[CONFIG] SUBJECT_TRACKING={settings.subject_tracking} (env={_st_raw!r})"
            f" format_hint={format_hint!r} portrait_output={_is_portrait_output}",
            flush=True,
        )
        _subject_fn = (
            track_subject
            if settings.subject_tracking and _is_portrait_output
            else analyze_subject_position
        )
        _tx_pool = ThreadPoolExecutor(max_workers=2)
        try:
            f_transcript = _tx_pool.submit(lambda: transcribe(src).to_dict())
            f_subject    = _tx_pool.submit(lambda: _subject_fn(src))
            try:
                transcript = f_transcript.result(timeout=_TRANSCRIPTION_TIMEOUT_S)
            except _FutureTimeoutError:
                _mins = _TRANSCRIPTION_TIMEOUT_S // 60
                store.update(
                    job_id,
                    status="error",
                    is_retry=True,
                    error=f"Transcription timeout after {_mins} min",
                    message=(
                        f"Transcription bloquée ({_mins} min) "
                        "— crédit remboursé automatiquement."
                    ),
                    progress=100,
                )
                return  # finally: _tx_pool.shutdown still runs
            subject_pos = f_subject.result(timeout=120)
        finally:
            _tx_pool.shutdown(wait=False)
        print(f"[TIMING] transcription+vision: {time.perf_counter()-_t0:.1f}s", flush=True)

        # ── Step 1b: VAD gap detection ────────────────────────────────────
        _t = time.perf_counter()
        _vad_gaps: list = []
        try:
            from app.engine.vad_gaps import compute_vad_gaps as _compute_vad_gaps
            _vad_gaps = _compute_vad_gaps(src, transcript)
        except Exception as _vad_exc:
            print(f"[VAD-GAPS] skipped: {_vad_exc}", flush=True)
        print(f"[TIMING] vad_gaps: {time.perf_counter()-_t:.1f}s", flush=True)

        # ── Step 2: Silence removal (Feature 2) ───────────────────────────
        _t = time.perf_counter()
        store.update(job_id, status="transcribing", progress=20,
                     message="Removing silences and filler words…")
        from app.core.config import settings as _cfg
        drops: list = []
        filler_drops: list = []
        _stutter_near_misses: list = []
        if _cfg.disable_cuts:
            # Full bypass: no detection, no cuts.
            transcript_clean = transcript
            print("[PIPELINE] DISABLE_CUTS=true — passthrough, detection skipped", flush=True)
        elif not _cfg.cut_fillers and not _cfg.report_only:
            # Legacy no-cut mode (CUT_FILLERS=false without REPORT_ONLY):
            # skip all detection to preserve existing behaviour.
            transcript_clean = transcript
            print("[PIPELINE] CUT_FILLERS=false — skipping ALL speech cuts", flush=True)
        else:
            # Detection runs: either cut mode (cut_fillers=true) or
            # report-only mode (report_only=true, cuts applied per cut_fillers).
            remover = RhythmAwareSilenceRemover()
            word_timestamps = [
                w for seg in transcript.get("segments", [])
                for w in seg.get("words", [])
            ]
            drops, filler_drops = remover.process(word_timestamps, transcript.get("segments", []))
            _stutter_near_misses = remover.near_misses
            if _cfg.cut_fillers:
                transcript_clean = apply_drops_to_transcript(transcript, drops)
            else:
                # report_only=true but cut_fillers=false: detection ran, no cuts applied.
                transcript_clean = transcript
                drops = []
                print("[PIPELINE] REPORT_ONLY — detection active, cuts suppressed (CUT_FILLERS=false)", flush=True)
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
        # Strip word-level timestamps before sending to the planner LLM.
        # The planner only needs segment text+timestamps for keep/drop decisions —
        # word arrays add ~83% token bloat with zero editorial value at this stage.
        # For a 15-min video this reduces planner input from ~32k → ~5k tokens.
        _transcript_for_planning = {
            **transcript_clean,
            "segments": [
                {k: v for k, v in seg.items() if k != "words"}
                for seg in transcript_clean.get("segments", [])
            ],
        }
        _n_segs = len(transcript_clean.get("segments", []))
        _full_words = sum(
            len(seg.get("words", [])) for seg in transcript_clean.get("segments", [])
        )
        print(
            f"[PLANNER-STRIP] {_n_segs} segments, {_full_words} word-tokens stripped"
            f" (~{_full_words * 40 // 4:,} tokens saved from planner input)",
            flush=True,
        )

        # ── Pass 1: global narrative map (videos > 25 min only) ───────────────
        # Identifies hook/payoff/beats/protected segments/recurring themes in one
        # lightweight call BEFORE chunked planning. Pass 2 (not yet built) will
        # use this map to give each chunk cross-video context.
        _video_duration_s = float(_transcript_for_planning.get("duration", 0))
        _narrative_map: dict = {}
        _CHUNKED_PLANNING_MIN_S = 20 * 60  # must match _CHUNK_THRESHOLD_S in planner.py — both
                                   # gate chunked planning, so a mismatch silently disables it
        if _video_duration_s >= _CHUNKED_PLANNING_MIN_S:
            print(
                f"[NARRATIVE-MAP] Pass 1 — video {_video_duration_s/60:.1f}min > 25min threshold,"
                f" running global narrative analysis…",
                flush=True,
            )
            _t_nm = time.perf_counter()
            _narrative_map = analyze_narrative_map(_transcript_for_planning)
            _nm_elapsed = time.perf_counter() - _t_nm
            if _narrative_map:
                print(
                    f"[NARRATIVE-MAP] done in {_nm_elapsed:.1f}s"
                    f" — hook_ts={_narrative_map.get('hook_ts')}s"
                    f" payoff_ts={_narrative_map.get('payoff_ts')}s"
                    f" beats={len(_narrative_map.get('major_beats', []))}"
                    f" protected={len(_narrative_map.get('protected', []))}"
                    f" recurring_themes={len(_narrative_map.get('recurring_themes', []))}",
                    flush=True,
                )
                print(f"[NARRATIVE-MAP] full={_narrative_map}", flush=True)
            else:
                print("[NARRATIVE-MAP] returned empty — skipping (non-fatal)", flush=True)
        else:
            print(
                f"[NARRATIVE-MAP] skipped — video {_video_duration_s/60:.1f}min < 25min threshold",
                flush=True,
            )

        plan = plan_edit(
            _transcript_for_planning,
            enriched_instructions,
            format_hint=format_hint,
            brand_color=brand_color,
            caption_color=caption_color,
            caption_position=caption_position,
            caption_font=caption_font,
            subject_position=subject_pos,
            coach_profile=coach_profile,
            editing_style=editing_style,
            narrative_map=_narrative_map if _narrative_map else None,
        )
        print(f"[TIMING] planning: {time.perf_counter()-_t:.1f}s", flush=True)

        # ── Step 6.5: LLM editorial layer ────────────────────────────────────
        # Claude receives the verbatim transcript numbered word-by-word and returns
        # the indices to cut (fillers, accidental repetitions, false starts).
        # Runs AFTER planning so key_lines can be passed to the prompt.
        # The CUT-GUARD below is a second safety net in case the LLM overshoots.
        import os as _os_cfg
        _cfg_fillers = _cfg.cut_fillers   # master switch — gated above
        _cfg_reps = _os_cfg.getenv("CUT_REPETITIONS", "false").strip().lower() == "true"
        _cfg_fs   = _os_cfg.getenv("CUT_FALSE_STARTS", "false").strip().lower() == "true"
        _cfg_paus = _os_cfg.getenv("CUT_PAUSES",       "false").strip().lower() == "true"
        if _cfg.report_only:
            print(
                f"[CONFIG] REPORT_ONLY — detection: fillers=ON repetitions=ON false_starts=ON"
                f" | cuts: {'fillers+reps+fs' if _cfg_fillers else 'NONE'}",
                flush=True,
            )
        elif not _cfg_fillers:
            print("[CONFIG] cuts: ALL OFF (fillers paused — CUT_FILLERS=false)", flush=True)
        else:
            print(
                f"[CONFIG] cuts: fillers=ON"
                f" repetitions={'ON' if _cfg_reps else 'OFF'}"
                f" false_starts={'ON' if _cfg_fs else 'OFF'}"
                f" pauses={'ON' if _cfg_paus else 'OFF'}",
                flush=True,
            )
        _t = time.perf_counter()
        try:
            if not _cfg_fillers and not _cfg.report_only:
                _llm_drops = []   # all cuts off — skip LLM editorial entirely
            else:
                _llm_drops = _llm_editorial_cuts(
                    transcript, plan.key_lines or [],
                    vad_gaps=_vad_gaps,
                    keep_segs=plan.keep_segments,
                )
                # Step 6.6: stable-ts targeted refinement (STABLE_TS_REPAIR=true only)
                _llm_drops = _stable_ts_refine_cuts(src, _llm_drops, transcript)
            # In report_only mode: keep all detected categories for the PDF.
            # In normal cut mode: filter by active cut category flags.
            _n_llm_before = len(_llm_drops)
            if not _cfg.report_only:
                if not _cfg_reps:
                    _llm_drops = [d for d in _llm_drops if not d.reason.startswith("llm_repetition")]
                if not _cfg_fs:
                    _llm_drops = [d for d in _llm_drops if not d.reason.startswith("llm_false_start")]
            if _n_llm_before != len(_llm_drops):
                print(
                    f"[CONFIG] LLM drops filtered: {_n_llm_before}→{len(_llm_drops)}"
                    f" (repetitions={'ON' if _cfg_reps else 'OFF'}"
                    f" false_starts={'ON' if _cfg_fs else 'OFF'})",
                    flush=True,
                )
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

        # ── Acoustic-stutter detection (logging only, gated on CUT_REPETITIONS) ──
        _STUTTER_MAX_LEN = 4    # token chars — short function words only
        _STUTTER_MIN_DUR = 0.55 # seconds — anomalously long for short tokens
        _STUTTER_GAP_MIN = 0.08 # seconds — must be adjacent to a gap

        for _si, _sw in enumerate(_source_words) if _cfg_reps else []:
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

        # Build LLM-filler zone list: gaps the LLM explicitly marked as filler/
        # tangent/repeat must NOT be rescued — the editorial intent was to cut them.
        # These timestamps are in the same compressed space as keep_segments.
        # Technical drops (filler, repeat): only block GAP-RESCUE when an actual
        # filler_drop overlaps the zone (source-coord comparison via _c2s_diag).
        # A planner "filler"/"repeat" label with no acoustic/LLM-EDIT confirmation
        # is a planning false-positive — GAP-RESCUE must be allowed to recover it.
        # Editorial drops (tangent, weak+context_ok): trust the planner directly,
        # no acoustic confirmation required.
        _llm_filler_zones: list[tuple[float, float]] = []
        for _ds in plan.raw.get("drop_segments", []):
            _ds_r = str(_ds.get("reason", "")).lower()
            _fz_s_c = float(_ds.get("start", 0))
            _fz_e_c = float(_ds.get("end", 0))
            if _ds_r in {"filler", "repeat"}:
                _fz_s_src, _ = _c2s_diag(_fz_s_c)
                _fz_e_src, _ = _c2s_diag(_fz_e_c)
                if not any(
                    d.end > _fz_s_src + 0.050 and d.start < _fz_e_src - 0.050
                    for d in filler_drops
                ):
                    print(
                        f"[GAP-RESCUE] plan drop [{_fz_s_c:.2f},{_fz_e_c:.2f}]"
                        f" reason={_ds_r!r} unbacked by any filler_drop"
                        f" → GAP-RESCUE allowed to recover",
                        flush=True,
                    )
                    continue
            elif _ds_r in {"tangent", "weak"} and bool(_ds.get("context_ok", False)):
                pass  # editorial drop — trust planner without acoustic confirmation
            else:
                continue
            _llm_filler_zones.append((_fz_s_c, _fz_e_c))

        _n_rescued = 0
        for _gi in range(len(_keep_raw) - 1):
            _gs_c = float(_keep_raw[_gi].get("end", 0))
            _ge_c = float(_keep_raw[_gi + 1].get("start", 0))
            if _ge_c <= _gs_c + 0.050:
                continue  # gap too narrow to host real speech

            # Skip gap if the LLM explicitly marked this zone as filler/tangent/repeat.
            # Use overlap fraction (>50% of the gap) rather than boundary proximity so
            # that a drop_segment starting slightly after the gap start still fires
            # (e.g. gap=[9.48,10.90], drop=[9.62,10.90] → 90% overlap → skipped).
            _gap_dur = _ge_c - _gs_c
            if any(
                (min(_fz_e, _ge_c) - max(_fz_s, _gs_c)) / _gap_dur > 0.50
                for _fz_s, _fz_e in _llm_filler_zones
            ):
                print(
                    f"[GAP-RESCUE] gap-{_gi} [{_gs_c:.2f},{_ge_c:.2f}] skipped"
                    f" — LLM-marked filler zone",
                    flush=True,
                )
                continue

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
            # Hard source-space cap: extension must never reach the next segment's
            # source start (_ge_src).  In a gap, src delta == compressed delta, so
            # this translates directly back to a compressed ceiling.
            _src_cap_c = _gs_c + (_ge_src - 0.010 - _gs_src)
            _new_end_c = min(_new_end_c, _ge_c - 0.010, _next_first_c - 0.010, _src_cap_c)
            if _new_end_c <= _gs_c + 0.050:
                continue

            _actual_src_end = _gs_src + (_new_end_c - _gs_c)  # clamped source end
            _keep_raw[_gi]["end"] = round(_new_end_c, 3)
            _n_rescued += 1
            print(
                f"[GAP-RESCUE] gap-{_gi} extended seg[{_gi}].end"
                f" {_gs_c:.2f}->{_new_end_c:.2f}"
                f" (src {_gs_src:.2f}->{_actual_src_end:.2f}): '{_wtxt}'"
                f" [next-seg src={_ge_src:.2f}]",
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

        # ── GAP-RESCUE lead-in: words before the first keep_segment ──────────
        # The main loop only covers inter-segment gaps (range(len-1)).
        # Content at the very start of the video (e.g. "Bon, aujourd'hui…")
        # was silently lost. Here we extend the first segment's start backward.
        if _keep_raw:
            _li_ge_c = float(_keep_raw[0].get("start", 0))
            if _li_ge_c > 0.050:
                _li_dur = _li_ge_c  # gap = [0, _li_ge_c]
                _li_blocked = any(
                    (min(_fz_e, _li_ge_c) - max(_fz_s, 0.0)) / _li_dur > 0.50
                    for _fz_s, _fz_e in _llm_filler_zones
                )
                if not _li_blocked:
                    _li_gs_src, _ = _c2s_diag(0.0)
                    _li_ge_src, _ = _c2s_diag(_li_ge_c)
                    _li_uncov = [
                        w for w in _source_words
                        if float(w.get("start", 0)) >= _li_gs_src - 0.005
                        and float(w.get("start", 0)) < _li_ge_src - 0.005
                        and float(w.get("end", 0)) > _li_gs_src + 0.005
                        and float(w.get("end", 0)) <= _li_ge_src + 0.005
                        and (float(w.get("end", 0)) - float(w.get("start", 0))) >= 0.030
                        and not any(
                            d.start < float(w.get("end", 0)) and d.end > float(w.get("start", 0))
                            for d in filler_drops
                        )
                    ]
                    if _li_uncov:
                        _first_w_src = float(_li_uncov[0].get("start", 0))
                        _new_start_src = max(0.0, _first_w_src - 0.150)
                        _new_start_c = max(0.0, _new_start_src)  # offset=0 before first drop
                        _old_start_c = _li_ge_c
                        _keep_raw[0]["start"] = round(_new_start_c, 3)
                        _wtxt_li = " ".join(str(w.get("text", "")).strip() for w in _li_uncov[:8])
                        print(
                            f"[GAP-RESCUE] lead-in extended seg[0].start"
                            f" {_old_start_c:.2f}->{_new_start_c:.2f}"
                            f" (src {_li_gs_src:.2f}->{_new_start_src:.2f}): '{_wtxt_li}'",
                            flush=True,
                        )
                else:
                    print(
                        f"[GAP-RESCUE] lead-in [{0:.2f},{_li_ge_c:.2f}] skipped"
                        f" — LLM-marked filler zone",
                        flush=True,
                    )

        # ── GAP-RESCUE tail: words after the last keep_segment ───────────────
        if _keep_raw:
            _tl_gs_c = float(_keep_raw[-1].get("end", 0))
            _tl_ge_src, _ = _c2s_diag(_tl_gs_c)
            _tl_uncov = [
                w for w in _source_words
                if float(w.get("start", 0)) >= _tl_ge_src - 0.005
                and (float(w.get("end", 0)) - float(w.get("start", 0))) >= 0.030
                and not any(
                    d.start < float(w.get("end", 0)) and d.end > float(w.get("start", 0))
                    for d in filler_drops
                )
            ]
            if _tl_uncov:
                _last_w_end_src = float(_tl_uncov[-1].get("end", 0)) + 0.150
                _new_end_c = _tl_gs_c + (_last_w_end_src - _tl_ge_src)
                _wtxt_tl = " ".join(str(w.get("text", "")).strip() for w in _tl_uncov[:8])
                _keep_raw[-1]["end"] = round(_new_end_c, 3)
                print(
                    f"[GAP-RESCUE] tail extended seg[{len(_keep_raw)-1}].end"
                    f" {_tl_gs_c:.2f}->{_new_end_c:.2f}"
                    f" (src {_tl_ge_src:.2f}->{_last_w_end_src:.2f}): '{_wtxt_tl}'",
                    flush=True,
                )

        # ── GAP-RESCUE mega-segment split ─────────────────────────────────────
        # Segments extended to > _GSM_MAX_S by GAP-RESCUE cause storyboard cards
        # to land at startSec (segment start) while the content is spoken 15-40s
        # later.  TITLE-ANCHOR corrects at most ~14s (10s search + 4s lead), so
        # segments > 15s guarantee misplaced cards.  Split long segments at the
        # longest inter-word pauses, keeping each sub-segment <= _GSM_MAX_S.
        _GSM_MAX_S =  15.0   # split segments longer than this (seconds, compressed)
        _GSM_MIN_S =   5.0   # minimum sub-segment duration after split
        _GSM_PAUSE =   0.15  # minimum inter-word silence to use as a split boundary

        def _s2c(ts_src: float) -> float:
            """Approximate compressed time for source time ts (inverse of _c2s_diag).

            In gaps (no virtual drops) this is exact; inside dropped regions it
            clamps to the drop's compressed start.
            """
            _running = 0.0
            for _d in _vd_sorted:
                if ts_src <= _d.start:
                    break
                if ts_src < _d.end:
                    return round(_d.start - _running, 4)
                _running += _d.end - _d.start
            return round(ts_src - _running, 4)

        _keep_split: list[dict] = []
        _gsm_split_count = 0
        for _seg in _keep_raw:
            _sc = float(_seg.get("start", 0))
            _ec = float(_seg.get("end", 0))
            if _ec - _sc <= _GSM_MAX_S:
                _keep_split.append(_seg)
                continue

            # Convert segment bounds to source time
            _sc_src, _ = _c2s_diag(_sc)
            _ec_src, _ = _c2s_diag(_ec)

            # Gather words in this segment (source time, sorted)
            _seg_words_src = sorted(
                [
                    w for w in _source_words
                    if float(w.get("start", 0)) >= _sc_src - 0.01
                    and float(w.get("end",   0)) <= _ec_src + 0.01
                ],
                key=lambda w: float(w["start"]),
            )

            # Build pause midpoints (compressed time) for inter-word gaps >= _GSM_PAUSE
            _pauses_c: list[float] = []
            for _wi in range(len(_seg_words_src) - 1):
                _w_end_src   = float(_seg_words_src[_wi].get("end", 0))
                _w_next_src  = float(_seg_words_src[_wi + 1].get("start", 0))
                if _w_next_src - _w_end_src >= _GSM_PAUSE:
                    _mid_c = _s2c((_w_end_src + _w_next_src) / 2)
                    if _sc < _mid_c < _ec:
                        _pauses_c.append(_mid_c)

            _sorted_pauses = sorted(set(round(p, 3) for p in _pauses_c))

            # Greedy split: while remaining chunk > max, cut at the latest pause
            # within [cursor+min, cursor+max], or at the first available pause
            # after cursor+min, or (last resort) at a hard time boundary.
            _boundaries = [_sc]
            while _ec - _boundaries[-1] > _GSM_MAX_S:
                _lo = _boundaries[-1] + _GSM_MIN_S
                _hi = _boundaries[-1] + _GSM_MAX_S
                _in_win = [p for p in _sorted_pauses if _lo <= p <= _hi]
                _after  = [p for p in _sorted_pauses if p > _lo]
                if _in_win:
                    _cut = max(_in_win)          # latest pause in window
                elif _after:
                    _cut = _after[0]             # nearest pause outside window
                else:
                    _cut = _hi                   # no pauses → hard time split
                # Guard: don't create a final sliver < _GSM_MIN_S
                _cut = min(round(_cut, 3), _ec - _GSM_MIN_S)
                # Safety: ensure progress (avoid infinite loop on edge cases)
                if _cut <= _boundaries[-1] + 0.05:
                    _cut = round(_boundaries[-1] + _GSM_MIN_S, 3)
                _boundaries.append(_cut)
            _boundaries.append(_ec)

            if len(_boundaries) <= 2:
                _keep_split.append(_seg)
                continue

            _n_subs = len(_boundaries) - 1
            _gsm_split_count += 1
            _sub_durs = [round(_boundaries[_i + 1] - _boundaries[_i], 1) for _i in range(_n_subs)]
            print(
                f"[GAP-RESCUE] SPLIT [{_sc:.2f},{_ec:.2f}] ({_ec - _sc:.1f}s)"
                f" → {_n_subs} sub-segs {_sub_durs}",
                flush=True,
            )
            for _bi in range(_n_subs):
                _sub = dict(_seg)
                _sub["start"] = _boundaries[_bi]
                _sub["end"]   = _boundaries[_bi + 1]
                _keep_split.append(_sub)

        if _gsm_split_count:
            print(
                f"[GAP-RESCUE] SPLIT {_gsm_split_count} mega-segment(s) → sub-segments"
                f" (threshold {_GSM_MAX_S:.0f}s)",
                flush=True,
            )
        _keep_raw = _keep_split

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

        # ── REPORT_ONLY: generate PDF from detections, then zero filler_drops ──
        # PDF is generated here (Phase 1) while all detection data is in memory.
        # Phase 2 (render) will add the download link to the final job result.
        # filler_drops are cleared so plan_data carries no physical cuts when
        # CUT_FILLERS=false, preserving the existing no-cut video behaviour.
        _has_edit_report: bool = False
        if _cfg.report_only:
            _report_drops = list(filler_drops)
            if not _cfg.cut_fillers:
                # Suppress all physical cuts — detection ran for reporting only.
                filler_drops = []
            try:
                from app.engine.edit_report import build_edit_report as _build_report
                _nm_for_pdf = [nm for nm in _stutter_near_misses if nm.get("gap", 1.0) < 1.0]
                _src_dur = float(transcript.get("duration") or 0.0)
                _build_report(
                    job_id=job_id,
                    source_filename=src.name,
                    plan_segments=plan.keep_segments,
                    key_lines=plan.key_lines or [],
                    detected_drops=_report_drops,
                    stutter_near_misses=_nm_for_pdf,
                    source_words=_source_words,
                    src_duration=_src_dur,
                    output_path=settings.outputs_dir / f"{job_id}_report.pdf",
                )
                _has_edit_report = True
                print(f"[REPORT] PDF ready → {job_id}_report.pdf", flush=True)
            except Exception as _rpt_exc:
                print(f"[REPORT] PDF generation failed: {_rpt_exc}", flush=True)

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
                "has_edit_report": _has_edit_report,
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
            is_retry=True,
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
    # Guard: if a SIGTERM arrived while we were queued or just about to start,
    # bail out immediately.  The job is already in "rendering" / "queued" state
    # on disk so Layer C recovery will requeue it on the next boot.
    if _shutdown_event.is_set():
        print(f"[PIPELINE] shutdown pending — deferring render for {job_id} to recovery", flush=True)
        return

    job = store.get(job_id)
    if not job:
        return
    work_dir = settings.work_dir / job_id  # defined before try so finally can always access it
    # Serialize heavy renders: Chrome worker pools (8+ processes × 700 MB each)
    # exhaust the cgroup limit when two renders overlap.  The semaphore queues
    # the second render until the first is complete — no job is dropped.
    _sem_acquired = _RENDER_SEM.acquire(blocking=False)
    if not _sem_acquired:
        # Check shutdown before committing to a potentially long wait.
        if _shutdown_event.is_set():
            print(f"[PIPELINE] shutdown — not queuing render for {job_id}", flush=True)
            return
        print(
            f"[PIPELINE] job {job_id}: render semaphore busy — queuing"
            " (another render is in progress)",
            flush=True,
        )
        store.update(job_id, message="En file d'attente (rendu précédent en cours)…")
        # Poll with a short timeout so we can abort on shutdown rather than
        # blocking indefinitely behind a render that may outlast a SIGTERM.
        while not _RENDER_SEM.acquire(timeout=5):
            if _shutdown_event.is_set():
                print(
                    f"[PIPELINE] shutdown while queued — aborting render for {job_id}",
                    flush=True,
                )
                return
        print(f"[PIPELINE] job {job_id}: render semaphore acquired — starting", flush=True)
    try:
        _t_phase2 = time.perf_counter()
        plan_data     = job.plan_data or {}
        transcript    = job.transcript or {}
        subject_pos   = job.subject_pos
        _energy_prof  = job.energy_profile or []
        params      = job.params or {}

        # Disk-space guard: estimate temp file footprint before committing to
        # render.  Proxy (CRF=0 H.264) + HDR stripped + per-segment cuts +
        # HyperFrames PNGs + output file — roughly 4× source for 1080p, 6× for 4K.
        # Floor at 300 MB so tiny test files still trigger a useful check.
        _is_4k_job  = params.get("output_quality") == "4k"
        _src_bytes  = src.stat().st_size if src.exists() else 0
        _space_mult = 6 if _is_4k_job else 4
        _min_free   = max(int(_src_bytes * _space_mult), 300 * 1024 * 1024)
        _disk_free  = shutil.disk_usage(str(settings._data_root)).free
        if _disk_free < _min_free:
            print(
                f"[DISK-GUARD] job {job_id}: disk space too low — "
                f"need {_min_free // 1024 // 1024} MB, "
                f"free {_disk_free // 1024 // 1024} MB",
                flush=True,
            )
            store.update(
                job_id,
                status="error",
                error=(
                    f"Disk space insufficient: need {_min_free // 1024 // 1024} MB, "
                    f"free {_disk_free // 1024 // 1024} MB"
                ),
                message="Le serveur est temporairement surchargé, réessayez dans quelques minutes.",
                is_retry=True,
            )
            return

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
            allow_4k=(params.get("output_quality", "1080p") == "4k") and has_4k_access(params.get("coach_profile")),
            filler_drops=filler_drops,
            virtual_drops=virtual_drops,
            source_words=source_words,
            energy_profile=_energy_prof,
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

        _edit_report_url: str | None = None
        if plan_data.get("has_edit_report"):
            _rp = settings.outputs_dir / f"{job_id}_report.pdf"
            if _rp.exists():
                _edit_report_url = f"/api/jobs/{job_id}/edit-report"

        # Copy narrative timeline to persistent outputs_dir before work_dir cleanup.
        _narrative_src = work_dir / "narrative_timeline.txt"
        if _narrative_src.exists():
            import shutil as _shutil_nt
            _shutil_nt.copy2(_narrative_src, settings.outputs_dir / f"{job_id}_narrative.txt")
            print(f"[NARRATIVE] Sauvegardé → {job_id}_narrative.txt", flush=True)

        store.update(
            job_id,
            status="done",
            completed_at=time.time(),
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
                "edit_report_url": _edit_report_url,
                "narrative_url": f"/api/jobs/{job_id}/narrative",
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
            is_retry=True,
        )
    finally:
        _RENDER_SEM.release()
        try:
            shutil.rmtree(work_dir, ignore_errors=False)
        except Exception as _cleanup_exc:
            import logging as _cleanup_lg
            _cleanup_lg.getLogger(__name__).warning(
                "[CLEANUP] WARNING: work_dir cleanup failed for job %s: %s",
                job_id, _cleanup_exc,
            )


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
