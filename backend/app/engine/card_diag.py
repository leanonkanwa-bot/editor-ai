"""Per-job card diagnostic, written next to the rendered video.

Why this exists
---------------
The cards a viewer actually sees are decided in phase 2 — anchoring onto spoken
words, anti-collision, the safety net, script-fill — and the result lived in
exactly two places, both of which disappear:

  * work_dir, deleted in the `finally` of every render;
  * the log, where [CARD-TIMING] and [CARD-WORDS] print once and scroll away.
    The retained window measured 4 minutes.

So any question about a card that shipped — was it on its words, how far was it
moved, did anti-collision undo the move — was unanswerable an hour later. This
writes that answer to `<job>_cards.json`, beside the output, where it lives as
long as the job does.

Design notes
------------
`generate_storyboard()` does not know the job id (zero references), and
threading one through would touch every caller. Instead the collector is keyed
by THREAD: each render already runs in its own thread, so a collector started
by the pipeline is the one the storyboard writes into, with no parameter
passing and no chance of two concurrent renders mixing.

Stage timings are captured by tapping stdout rather than editing the dozen
`print(f"[TIMING] ...")` sites. The tap writes everything through untouched and
only *also* records lines for the thread that asked; a thread with no collector
is unaffected. That also catches the stages in render.py that an edit-by-hand
pass would have missed.

Nothing here may break a render. Every public function swallows its own errors:
a diagnostic that takes down a video the user waited forty minutes for would be
worse than no diagnostic at all.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

_local = threading.local()
_tap_lock = threading.Lock()
_tap_installed = False

# Phase 1 (transcribe, detect, plan) and phase 2 (render) are separate calls,
# on separate threads, with the user's approval in between. Thread-local
# storage alone therefore loses everything phase 1 measured — which is why the
# shipped file accounts for 2661 s of a job whose phase 1 took another 337 s.
# The collector is keyed by job id so the second phase resumes the first one's
# entry; the thread-local binding is kept, because it is what lets the
# storyboard find its collector without the job id being threaded through.
_by_job: dict[str, dict] = {}
_by_job_lock = threading.Lock()
_MAX_JOBS = 32                    # a phase 1 with no phase 2 must not leak

_TIMING_RE = re.compile(r"^\[TIMING\]\s+([^:]+):\s*([\d.]+)s")
# The 4K upscale is 456 s on a nine-minute video — 17 % of the render — and it
# announces itself in its own format, so no [TIMING] line ever existed for it.
_UPSCALE_RE = re.compile(r"^\[UPSCALE\]\s+Done in\s*([\d.]+)s")

# Stages that CONTAIN other stages. Without the distinction, summing the list
# double-counts and the arithmetic looks broken.
_AGGREGATES = frozenset({
    "phase1_total", "phase2_total", "render", "render_hf_total",
})


class _Tap:
    """Pass-through stdout wrapper that also records [TIMING] for its thread."""

    def __init__(self, wrapped):
        self._w = wrapped

    def write(self, text):
        n = self._w.write(text)
        try:
            d = getattr(_local, "d", None)
            if d is not None:
                buf = d["_buf"] + text
                while "\n" in buf:
                    line, _, buf = buf.partition("\n")
                    line = line.strip()
                    m = _TIMING_RE.match(line)
                    nom = seconds = None
                    if m:
                        nom, seconds = m.group(1).strip(), float(m.group(2))
                    else:
                        u = _UPSCALE_RE.match(line)
                        if u:
                            nom, seconds = "upscale_4k", float(u.group(1))
                    if nom is not None:
                        d["stages"].append({
                            "stage": nom,
                            "seconds": seconds,
                            # elapsed since the collector started: the only way
                            # to see where the gaps between stages are
                            "at": round(time.time() - d["started_at"], 1),
                            "phase": d["phase"],
                            "aggregate": nom in _AGGREGATES,
                        })
                d["_buf"] = buf[-4096:]        # never let a partial line grow
        except Exception:
            pass
        return n

    def __getattr__(self, name):
        return getattr(self._w, name)


def _install_tap() -> None:
    global _tap_installed
    with _tap_lock:
        if _tap_installed:
            return
        try:
            sys.stdout = _Tap(sys.stdout)
            _tap_installed = True
        except Exception:
            pass


def start(job_id: str, phase: int = 2) -> None:
    """Begin — or RESUME — collecting for this job on this thread.

    Phase 2 calling start() for a job phase 1 already opened picks the same
    entry up, so the stages, and the ~340 s phase 1 costs, survive the gap
    between the two calls.
    """
    try:
        _install_tap()
        with _by_job_lock:
            d = _by_job.get(job_id)
            if d is None:
                if len(_by_job) >= _MAX_JOBS:    # a phase 1 with no phase 2
                    oldest = min(_by_job, key=lambda k: _by_job[k]["started_at"])
                    _by_job.pop(oldest, None)
                d = {
                    "job_id": job_id,
                    "started_at": time.time(),
                    "cards": [],
                    "cards_before_anchor": [],
                    "storyboard": None,
                    "anchor_moves": [],
                    "anchor_summary": {},
                    "zoom": [],
                    "stages": [],
                    # record_usage() would create this lazily; declared here so
                    # a reader of start() sees everything a job accumulates.
                    # Keyed by job_id like the rest, so one job's spend can
                    # never be attributed to another on a reused thread.
                    "llm_usage": [],
                    "_buf": "",
                }
                _by_job[job_id] = d
            d["phase"] = phase
            d["_buf"] = ""           # a partial line cannot span two phases
        _local.d = d
    except Exception:
        pass


def record_anchor_moves(moves: list[dict], summary: dict | None = None) -> None:
    """Every card the anchoring pass shifted, trimmed, or declined to shift."""
    try:
        d = getattr(_local, "d", None)
        if d is None:
            return
        d["anchor_moves"] = moves
        if summary:
            d["anchor_summary"] = summary
    except Exception:
        pass


def record_cards_before_anchor(cards: list[dict]) -> None:
    """The planner's own times, kept so the two can be compared.

    The shipped file recorded ONLY these and labelled them the card's times.
    They are not: anchoring overwrites startSec afterwards, and the render used
    the overwritten value. On job 37f76379 card-24 was filed at 350.00 s and
    actually appeared at 341.87 s.
    """
    try:
        d = getattr(_local, "d", None)
        if d is None:
            return
        d["cards_before_anchor"] = [
            {"id": c.get("id"),
             "startSec": round(float(c.get("startSec", 0)), 3),
             "endSec": round(float(c.get("endSec", 0)), 3)}
            for c in cards
        ]
    except Exception:
        pass


def record_zoom(entries: Any) -> None:
    """The zoom plan the renderer actually used, with measured speed.

    Kind names are not a movement ranking: measured on a real render, the
    `pull_out` the engine calls significant moves at 0.0021 scale/s while the
    `breath` it dismisses as invisible moves at 0.0706 — thirty times faster.
    Recording amplitude and duration lets that be checked instead of believed.
    """
    try:
        d = getattr(_local, "d", None)
        if d is None:
            return
        out = []
        for z in entries or []:
            try:
                s = float(z.get("start", 0))
                e = float(z.get("end", s))
                f = float(z.get("from", 1.0))
                t = float(z.get("to", f))
                dur = max(e - s, 1e-3)
                out.append({
                    "kind": z.get("kind"),
                    "start": round(s, 3),
                    "end": round(e, 3),
                    "amplitude": round(abs(t - f), 4),
                    "scale_per_second": round(abs(t - f) / dur, 5),
                })
            except Exception:
                continue
        d["zoom"] = out
    except Exception:
        pass


def record_storyboard(storyboard: dict) -> None:
    """The storyboard exactly as handed to the renderer, every field kept.

    The curated `cards` list below keeps the handful of fields an audit reads
    quickly, and that curation is a guess about which fields will matter. The
    planner is a model call: its output is not reproducible, so a parameter
    that is not written down when the job runs cannot be recovered afterwards —
    the video can never be explained again. This keeps the whole thing.
    """
    try:
        d = getattr(_local, "d", None)
        if d is None:
            return
        # A deep copy via json round-trip: the renderer mutates the storyboard
        # after this point, and a reference would record the mutations instead.
        d["storyboard"] = json.loads(json.dumps(storyboard, ensure_ascii=False,
                                                default=str))
    except Exception:
        pass


def record_usage(etape: str, modele: str, usage: Any) -> None:
    """One model call's real token usage, accumulated for the job.

    The spend of a job could not be measured at all: only LLM-EDIT read
    response.usage, and it priced it from its own stale table. The planner and
    GAP-FILL read nothing, so "what did this video cost" had no answer — which
    made every estimate of a change's cost a guess.

    Appends rather than replaces: the planner runs once per chunk, GAP-FILL
    once per gap, and the per-call rows are what make a cap or a threshold
    arguable afterwards. Never raises — a diagnostic must not cost a render.
    """
    try:
        d = getattr(_local, "d", None)
        if d is None:
            return
        tin = int(getattr(usage, "input_tokens", 0) or 0)
        tout = int(getattr(usage, "output_tokens", 0) or 0)
        # Cache fields are absent on older SDK responses; 0 is not a claim.
        cin = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        cread = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        from app.core.pricing import cout_usd
        d.setdefault("llm_usage", []).append({
            "stage": etape,
            "model": modele,
            "input_tokens": tin,
            "output_tokens": tout,
            "cache_creation_input_tokens": cin,
            "cache_read_input_tokens": cread,
            # null, not 0, when the rate is unlisted — a missing price must not
            # look like a free call.
            "cost_usd": (lambda c: round(c, 6) if c is not None else None)(
                cout_usd(modele, tin, tout)),
        })
    except Exception:
        pass


def record_storyboard_final(storyboard: dict) -> None:
    """The storyboard as RENDERED, after the safety net has had its say.

    record_storyboard() above freezes the planner's version on purpose, and
    that copy is worth keeping. But it is taken in storyboard.py, while the
    safety net runs later in render.py — so neither it nor `cards` held a
    single safety- card. Measured on job b17a54cc (post-497ede5): cards[] 41
    entries, storyboard.cards 43, zero safety-. A reader could not tell "the
    net injected nothing" from "nothing recorded the net", which is the one
    question the file exists to answer.
    """
    try:
        d = getattr(_local, "d", None)
        if d is None:
            return
        d["storyboard_rendered"] = json.loads(
            json.dumps(storyboard, ensure_ascii=False, default=str))
    except Exception:
        pass


def record_cards(cards: list[dict], words: Any) -> None:
    """The final card list, after every pass. Called where [CARD-TIMING] prints."""
    try:
        d = getattr(_local, "d", None)
        if d is None:
            return
        avant = {c["id"]: c for c in d.get("cards_before_anchor", []) if c.get("id")}
        moves = {m.get("id"): m for m in d.get("anchor_moves", []) if m.get("id")}
        out = []
        for c in cards:
            hints = c.get("contentHints", {}) or {}
            # Five hardcoded fields saw nothing for card-26 (myth_text) and
            # card-34 (warning_text) on job b17a54cc: the file showed them as
            # textless cards when they say two sentences and one. The map in
            # champs_texte.py knows what each style draws; joined, because a
            # myth_vs_fact says two things and reporting only the first
            # under-counts its text.
            try:
                from app.engine.champs_texte import texte_affiche
                text = texte_affiche(hints, joindre=True)
            except Exception:
                text = str(hints.get("title") or "").strip()
            s = float(c.get("startSec", 0))
            e = float(c.get("endSec", s))
            near = [w for w in words if s - 0.3 <= w.start <= s + 0.5]
            if near:
                sync, sw, gap = "OK", near[0].text, round(near[0].start - s, 3)
            else:
                cl = min(words, key=lambda w: abs(w.start - s), default=None)
                sync = "NOSPEECH"
                sw = cl.text if cl else None
                gap = round(cl.start - s, 3) if cl else None
            spoken = " ".join(str(w.text) for w in words if w.end > s - 0.3 and w.start < e + 0.3)
            # Three states, because "OK" could not tell them apart: it is
            # computed from the first word of the MATCH WINDOW, which begins at
            # the card's own start, so it reads ~0 by construction and labelled
            # four cards OK that the engine itself logged as never checked.
            _m = moves.get(c.get("id"))
            if not text:
                ancrage = "SANS TEXTE"
            elif _m is not None and abs(float(_m.get("shift", 0) or 0)) > 1e-6:
                ancrage = "ANCREE"
            else:
                ancrage = "NON ANCREE"
            _av = avant.get(c.get("id"))
            out.append({
                "id": c.get("id"),
                "style": hints.get("style"),
                "zone": c.get("zone"),
                "startSec": round(s, 3),          # what the render used
                "endSec": round(e, 3),
                "duration": round(e - s, 3),
                "startSec_planner": _av["startSec"] if _av else None,
                "shift_by_anchoring": (round(s - _av["startSec"], 3) if _av else None),
                "text": text,                     # the point of the whole file
                "anchoring": ancrage,
                "coverage_after": (_m or {}).get("coverage_after"),
                "sync": sync,
                "start_word": sw,
                "start_word_gap": gap,            # + = word after the card appears
                "spoken_while_on_screen": spoken,
            })
        d["cards"] = out
    except Exception:
        pass


def dump(outputs_dir: Path) -> Path | None:
    """Write <job>_cards.json. Never raises; returns the path on success."""
    d = None
    try:
        d = getattr(_local, "d", None)
        if d is None:
            return None
        job_id = d.get("job_id") or "unknown"
        stages = d.get("stages", [])
        cards = d.get("cards", [])
        # Aggregates contain the others; summing everything double-counts. The
        # leftover is the number that matters — on job 37f76379 it was 620 s,
        # and 456 s of it turned out to be the upscale, which emitted no
        # [TIMING] line at all.
        _par_phase = {}
        for ph in (1, 2):
            tot = next((s["seconds"] for s in stages
                        if s.get("phase") == ph and s["stage"] == f"phase{ph}_total"), None)
            feuilles = sum(s["seconds"] for s in stages
                           if s.get("phase") == ph and not s.get("aggregate"))
            _par_phase[f"phase{ph}"] = {
                "total": tot,
                "sum_of_leaves": round(feuilles, 1),
                "unaccounted": (round(tot - feuilles, 1) if tot is not None else None),
            }
        # The job's real model spend, summed from the per-call rows. A call
        # whose model is unpriced is counted in the tokens and named in
        # `unpriced_calls`, never folded into the dollar total as zero.
        _usage = d.get("llm_usage", [])
        _tot_in = sum(u.get("input_tokens", 0) for u in _usage)
        _tot_out = sum(u.get("output_tokens", 0) for u in _usage)
        _chiffres = [u["cost_usd"] for u in _usage if u.get("cost_usd") is not None]
        _sans_prix = sorted({u.get("model") for u in _usage
                             if u.get("cost_usd") is None})
        _usage_total = {
            "calls": len(_usage),
            "input_tokens": _tot_in,
            "output_tokens": _tot_out,
            "cost_usd": round(sum(_chiffres), 6) if _chiffres else None,
            "priced_calls": len(_chiffres),
            "unpriced_calls": _sans_prix,
            "by_stage": {},
        }
        for u in _usage:
            st = _usage_total["by_stage"].setdefault(
                u.get("stage", "?"),
                {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
            st["calls"] += 1
            st["input_tokens"] += u.get("input_tokens", 0)
            st["output_tokens"] += u.get("output_tokens", 0)
            if u.get("cost_usd") is not None:
                st["cost_usd"] = round(st["cost_usd"] + u["cost_usd"], 6)

        _etiquettes: dict[str, int] = {}
        for c in cards:
            k = c.get("anchoring") or "?"
            _etiquettes[k] = _etiquettes.get(k, 0) + 1
        payload = {
            "job_id": job_id,
            "written_at": time.time(),
            "render_seconds": round(time.time() - d.get("started_at", time.time()), 1),
            "card_count": len(cards),
            "anchoring_counts": _etiquettes,
            "anchor_summary": d.get("anchor_summary", {}),
            "time_accounting": _par_phase,
            "llm_usage": _usage,
            "llm_usage_total": _usage_total,
            "stages": stages,
            "zoom": d.get("zoom", []),
            "storyboard": d.get("storyboard"),
            "storyboard_rendered": d.get("storyboard_rendered"),
            "anchor_moves": d.get("anchor_moves", []),
            "cards_before_anchor": d.get("cards_before_anchor", []),
            "cards": cards,
        }
        path = Path(outputs_dir) / f"{job_id}_cards.json"
        tmp = path.with_suffix(".json.part")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)                         # atomic: never a half file
        _c = _usage_total["cost_usd"]
        print(f"[CARD-DIAG] wrote {path.name} "
              f"({payload['card_count']} cards, {len(payload['stages'])} stages, "
              f"{len(payload['zoom'])} zoom entries) "
              f"anchoring={payload['anchoring_counts']}", flush=True)
        # "tarif inconnu" on a job with no calls at all said the wrong thing:
        # the cost is not unknown, there was none. The three cases are named
        # apart, because a cost line that misreads its own silence is how the
        # stale table survived for months.
        if not _usage_total["calls"]:
            _montant = "aucun appel"
        elif _c is None:
            _montant = "tarif inconnu"
        else:
            _montant = "$%.4f" % _c
        print(f"[LLM-COST] job total: {_usage_total['calls']} call(s), "
              f"{_tot_in}in+{_tot_out}out tokens, {_montant}"
              + (f" — modeles sans tarif: {_sans_prix}" if _sans_prix else ""),
              flush=True)
        return path
    except Exception as exc:
        # A diagnostic must never cost a render.
        try:
            print(f"[CARD-DIAG] WARNING: could not write diagnostic: "
                  f"{type(exc).__name__}: {exc}", flush=True)
        except Exception:
            pass
        return None
    finally:
        try:
            if d is not None:
                with _by_job_lock:
                    _by_job.pop(d.get("job_id"), None)
            _local.d = None
        except Exception:
            pass


def _active_for_tests() -> dict | None:
    return getattr(_local, "d", None)
