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

_TIMING_RE = re.compile(r"^\[TIMING\]\s+([^:]+):\s*([\d.]+)s")


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
                    m = _TIMING_RE.match(line.strip())
                    if m:
                        d["stages"].append({
                            "stage": m.group(1).strip(),
                            "seconds": float(m.group(2)),
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


def start(job_id: str) -> None:
    """Begin collecting for this thread's render."""
    try:
        _install_tap()
        _local.d = {
            "job_id": job_id,
            "started_at": time.time(),
            "cards": [],
            "anchor_moves": [],
            "anchor_summary": {},
            "stages": [],
            "_buf": "",
        }
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


def record_cards(cards: list[dict], words: Any) -> None:
    """The final card list, after every pass. Called where [CARD-TIMING] prints."""
    try:
        d = getattr(_local, "d", None)
        if d is None:
            return
        out = []
        for c in cards:
            hints = c.get("contentHints", {}) or {}
            text = ""
            for fld in ("title", "confession_text", "qa_question", "text", "kicker"):
                v = hints.get(fld, "")
                if isinstance(v, list):
                    v = " ".join(str(x) for x in v)
                if v:
                    text = str(v).strip()
                    break
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
            out.append({
                "id": c.get("id"),
                "style": hints.get("style"),
                "zone": c.get("zone"),
                "startSec": round(s, 3),
                "endSec": round(e, 3),
                "duration": round(e - s, 3),
                "text": text,                     # the point of the whole file
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
        payload = {
            "job_id": job_id,
            "written_at": time.time(),
            "render_seconds": round(time.time() - d.get("started_at", time.time()), 1),
            "card_count": len(d.get("cards", [])),
            "anchor_summary": d.get("anchor_summary", {}),
            "stages": d.get("stages", []),
            "anchor_moves": d.get("anchor_moves", []),
            "cards": d.get("cards", []),
        }
        path = Path(outputs_dir) / f"{job_id}_cards.json"
        tmp = path.with_suffix(".json.part")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)                         # atomic: never a half file
        print(f"[CARD-DIAG] wrote {path.name} "
              f"({payload['card_count']} cards, {len(payload['stages'])} stages)", flush=True)
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
            _local.d = None
        except Exception:
            pass


def _active_for_tests() -> dict | None:
    return getattr(_local, "d", None)
