"""
Pre-trim pipeline: cut dead content from source video, produce a
continuous trimmed MP4 + timing map.

This is Stage 1 of the HyperFrames pipeline. It reuses the proven
word-boundary snapping, sentence extension, and pause compression
logic from render.py, but produces a single continuous video instead
of per-segment clips with zoom/captions.

The timing map records which source ranges survived and at what
offsets in the trimmed output, enabling downstream stages to place
overlays/captions at the correct times.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent.planner import EditPlan
from app.engine.silence_remover import DropSegment
from app.engine.render import (
    _dedup_segments,
    _merge_short_segments,
    _fix_word_boundaries,
    _flat_words,
    _snap_to_word_boundary,
    _extend_for_semantic_completeness,
    _compress_pauses,
    _probe_duration,
    _probe_video_info,
    _run,
    _remap_time,
    SHORT_PAD_S,
    LONG_PAD_S,
)
from app.engine.silence_remover import (
    _PAUSE_TOUCH_THRESHOLD,
    _PAUSE_LONG_THRESHOLD,
    _PAUSE_MID_SHORT_KEEP,
    _PAUSE_EOL_LONG_KEEP,
    _PAUSE_MID_LONG_KEEP,
)
from app.engine.transcribe import FFMPEG_PATH
from app.engine.captions import WordTiming


@dataclass
class TimingMap:
    """Maps source timeline to trimmed-output timeline."""
    source_intervals: list[tuple[float, float]]
    compressed_intervals: list[tuple[float, float]] | None
    output_duration: float
    remapped_words: list[WordTiming]

    def source_to_output(self, t: float) -> float:
        """Convert a source timestamp to its position in the trimmed output.

        Two-stage remap:
          1. source → concat timeline (via source_intervals)
          2. concat → final timeline (via compressed_intervals, if pauses compressed)
        """
        concat_t = _remap_time(t, self.source_intervals)
        if self.compressed_intervals:
            return _remap_time(concat_t, self.compressed_intervals)
        return concat_t


def _pretrim_passthrough(
    src: Path,
    transcript: dict[str, Any],
    all_words: list[dict],
    src_duration: float,
    work_dir: Path,
) -> tuple[Path, TimingMap]:
    """No-cut passthrough: re-encode source with dense keyframes, 1:1 timing."""
    fps = 30
    final_path = work_dir / "trimmed.mp4"
    _run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-i", str(src),
        "-r", str(fps), "-vsync", "cfr",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-g", str(fps), "-keyint_min", str(fps),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "192k",
        str(final_path),
    ])

    output_duration = _probe_duration(final_path)

    # 1:1 timing map — every source word maps to the same output time
    remapped_words = [
        WordTiming(
            text=w.get("text", "").strip(),
            start=float(w.get("start", 0)),
            end=float(w.get("end", 0)),
        )
        for w in all_words
        if w.get("text", "").strip()
    ]

    timing_map = TimingMap(
        source_intervals=[(0.0, src_duration)],
        compressed_intervals=None,
        output_duration=output_duration,
        remapped_words=remapped_words,
    )

    print(f"[PRETRIM] Passthrough: {output_duration:.1f}s, {len(remapped_words)} words (no cuts)")
    return final_path, timing_map


_LEAD_IN_THRESHOLD = 1.0   # seconds: trigger lead-in cut if first word starts later than this
_LEAD_IN_KEEP_S    = 0.5   # seconds: amount of lead-in to preserve before the first word


def _acoustic_stutter_cuts(
    video_path: Path,
    words: list[WordTiming],
    key_lines: list[str],
    work_dir: Path,
    max_cuts: int = 3,
    min_word_dur: float = 0.55,
    max_token_chars: int = 4,
    noise_db: float = -30.0,
    detect_min_s: float = 0.10,
    act_min_s: float = 0.20,
) -> list[tuple[float, float]]:
    """Return physical-cut intervals (output-space) for intra-word hesitations.

    For each short token (≤4 chars) that is suspiciously long (>0.55s) and
    not in key_lines, run silencedetect on its interval. If an internal silence
    ≥ act_min_s is found, schedule [word.start, word.start+sil_end_rel] for
    removal, keeping only the final articulation (the cleaner, more assured one).
    """
    _key_tokens = {re.sub(r"[^\w]", "", kl.lower()) for kl in key_lines}
    cuts: list[tuple[float, float]] = []

    # ── Diagnostic header ─────────────────────────────────────────────────────
    _n_dur_pass  = sum(1 for w in words if (w.end - w.start) >= min_word_dur)
    _n_tok_pass  = sum(
        1 for w in words
        if (w.end - w.start) >= min_word_dur
        and 1 <= len(re.sub(r"[^\w]", "", w.text.lower())) <= max_token_chars
    )
    print(
        f"[ACOUSTIC-STUTTER] scanning {len(words)} words"
        f" | dur≥{min_word_dur}s: {_n_dur_pass}"
        f" | +tok≤{max_token_chars}: {_n_tok_pass}",
        flush=True,
    )

    for w in words:
        if len(cuts) >= max_cuts:
            break
        dur = w.end - w.start
        if dur < min_word_dur:
            continue
        tok = re.sub(r"[^\w]", "", w.text.lower())
        if not tok or len(tok) > max_token_chars:
            continue
        if tok in _key_tokens:
            print(
                f"[ACOUSTIC-STUTTER] skip '{w.text}' dur={dur:.3f}s"
                f" tok='{tok}' — key_line protected",
                flush=True,
            )
            continue

        # Candidate: log before probing so we see what reached silencedetect.
        print(
            f"[ACOUSTIC-STUTTER] candidate '{w.text}'"
            f" {w.start:.3f}-{w.end:.3f} dur={dur:.3f}s tok='{tok}'",
            flush=True,
        )

        # ── Volume probe: measure peak level to compute adaptive silence threshold ──
        # -30dB (the fixed default) is often too strict — breath/hesitation gaps
        # may only dip to -22dB. We measure max_volume and set threshold 20dB below.
        _adaptive_db = noise_db  # fallback
        try:
            _vd = subprocess.run(
                [
                    FFMPEG_PATH, "-y", "-loglevel", "info",
                    "-ss", f"{w.start:.6f}", "-accurate_seek",
                    "-i", str(video_path),
                    "-t", f"{dur:.6f}",
                    "-vn", "-af", "volumedetect",
                    "-f", "null", "-",
                ],
                capture_output=True, text=True,
            )
            _vd_out = _vd.stderr
            _max_m  = re.search(r"max_volume:\s*([\-\d.]+)", _vd_out)
            _mean_m = re.search(r"mean_volume:\s*([\-\d.]+)", _vd_out)
            _max_vol  = float(_max_m.group(1))  if _max_m  else None
            _mean_vol = float(_mean_m.group(1)) if _mean_m else None
            if _max_vol is not None:
                # 20 dB below speech peak → catches hesitation dips
                _adaptive_db = max(-42.0, _max_vol - 20.0)
            print(
                f"[ACOUSTIC-STUTTER] volume '{w.text}':"
                f" mean={_mean_vol}dB max={_max_vol}dB"
                f" → noise_threshold={_adaptive_db:.1f}dB",
                flush=True,
            )
        except Exception as _vexc:
            print(
                f"[ACOUSTIC-STUTTER] volume probe failed for '{w.text}': {_vexc}"
                f" → using {_adaptive_db:.1f}dB",
                flush=True,
            )

        # ── Silence probe ──────────────────────────────────────────────────────
        _sd_cmd = [
            FFMPEG_PATH, "-y", "-loglevel", "error",
            "-ss", f"{w.start:.6f}", "-accurate_seek",
            "-i", str(video_path),
            "-t", f"{dur:.6f}",
            "-vn",
            "-af", f"silencedetect=noise={_adaptive_db:.1f}dB:d={detect_min_s}",
            "-f", "null", "-",
        ]
        print(f"[ACOUSTIC-STUTTER] cmd: {' '.join(_sd_cmd)}", flush=True)
        try:
            _sd  = subprocess.run(_sd_cmd, capture_output=True, text=True)
            _out = _sd.stderr
        except Exception as _exc:
            print(f"[ACOUSTIC-STUTTER] probe failed for '{w.text}': {_exc}", flush=True)
            continue

        # Log only silence-related lines so output stays readable.
        _sil_lines = [ln for ln in _out.splitlines() if "silence" in ln.lower()]
        print(
            f"[ACOUSTIC-STUTTER] raw '{w.text}': {_sil_lines}",
            flush=True,
        )

        # Parse all silence_start / silence_end pairs (relative to start of word clip)
        _sil_starts = [float(m.group(1)) for m in re.finditer(r"silence_start: ([\d.]+)", _out)]
        _sil_ends   = [float(m.group(1)) for m in re.finditer(r"silence_end: ([\d.]+)", _out)]
        print(
            f"[ACOUSTIC-STUTTER] probe '{w.text}':"
            f" sil_starts={[round(x, 3) for x in _sil_starts]}"
            f" sil_ends={[round(x, 3) for x in _sil_ends]}",
            flush=True,
        )
        if not _sil_ends:
            continue

        # Use the LAST silence that is INTERNAL (has speech before AND after it).
        # Tail-guard: reject only if the silence runs to the very end of the word
        # (sil_e ≥ dur - 0.02). Previously used dur-0.05, which discarded the
        # "il…il" pattern where the 2nd articulation is only ~30-50ms.
        # Additional guard: sil_s must be ≥ 0.10 — there must be audible initial
        # speech before the internal silence (the first hesitant articulation).
        for sil_s, sil_e in reversed(list(zip(_sil_starts or [0.0]*len(_sil_ends), _sil_ends))):
            sil_dur = sil_e - sil_s
            if sil_dur < act_min_s:
                print(
                    f"[ACOUSTIC-STUTTER] skip: sil_dur={sil_dur:.3f}s < {act_min_s}s",
                    flush=True,
                )
                continue
            if sil_s < 0.10:
                print(
                    f"[ACOUSTIC-STUTTER] skip: sil_s={sil_s:.3f}s < 0.10"
                    f" (no initial articulation)",
                    flush=True,
                )
                continue
            if sil_e >= dur - 0.02:
                print(
                    f"[ACOUSTIC-STUTTER] skip: sil_e={sil_e:.3f}s"
                    f" ≥ dur-0.02={dur - 0.02:.3f}s (tail silence)",
                    flush=True,
                )
                continue
            # Cut [word.start, word.start + sil_e] — removes the stutter + silence
            cut_s = w.start
            cut_e = w.start + sil_e
            if cut_e - cut_s < 0.05:
                continue
            print(
                f"[ACOUSTIC-STUTTER] '{w.text}' {w.start:.3f}-{w.end:.3f} ({dur:.2f}s)"
                f" internal silence {sil_s:.3f}-{sil_e:.3f}s ({sil_dur:.2f}s)"
                f" → cut {cut_s:.3f}-{cut_e:.3f}",
                flush=True,
            )
            cuts.append((cut_s, cut_e))
            break

    if not cuts:
        print("[ACOUSTIC-STUTTER] no stutter candidates found", flush=True)
    return cuts


def _smart_pause_cuts(
    words: list[WordTiming],
) -> list[tuple[float, float]]:
    """Return (excess_start, excess_end) intervals for intelligent pause compression.

    Each interval is the portion of a pause to physically cut. Pass the result to
    _compress_pauses with max_silence_s=0.0 to remove exactly the excess.
    Mirrors the virtual-drop logic in RhythmAwareSilenceRemover.process().
    """
    cuts: list[tuple[float, float]] = []
    _n_below = 0
    _n_eos_prot = 0
    _n_final_prot = 0

    # Lead-in: if the first word starts after _LEAD_IN_THRESHOLD seconds in the
    # trimmed output, shorten the opening silence to _LEAD_IN_KEEP_S seconds.
    if words and words[0].start > _LEAD_IN_THRESHOLD:
        cut_start = _LEAD_IN_KEEP_S
        cut_end   = words[0].start
        if cut_end > cut_start + 0.05:
            cuts.append((cut_start, cut_end))
            print(
                f"[PRETRIM] lead-in {words[0].start:.2f}s -> kept {_LEAD_IN_KEEP_S:.2f}s "
                f"(cut {cut_end - cut_start:.2f}s)",
                flush=True,
            )

    for i in range(len(words) - 1):
        gap_start = words[i].end
        gap_end   = words[i + 1].start
        gap_dur   = gap_end - gap_start

        if gap_dur < _PAUSE_TOUCH_THRESHOLD:
            _n_below += 1
            continue
        if i == len(words) - 2:              # final gap before last word
            _n_final_prot += 1
            continue

        is_sentence_end = words[i].text.rstrip().endswith((".", "?", "!"))

        if is_sentence_end:
            if gap_dur <= _PAUSE_LONG_THRESHOLD:
                _n_eos_prot += 1
                continue                          # 0.70–2.00s EOS: untouched
            keep_s = _PAUSE_EOL_LONG_KEEP         # >2.00s EOS: shorten to 0.80s
        else:
            keep_s = (
                _PAUSE_MID_SHORT_KEEP             # 0.70–2.00s mid: shorten to 0.45s
                if gap_dur <= _PAUSE_LONG_THRESHOLD
                else _PAUSE_MID_LONG_KEEP          # >2.00s mid: shorten to 0.40s
            )

        excess_start = gap_start + keep_s
        if excess_start < gap_end - 0.05:
            cuts.append((excess_start, gap_end))
            print(
                f"[PRETRIM] pause cut: {gap_dur:.2f}s at {gap_start:.2f}s"
                f" ({'EOS' if is_sentence_end else 'mid'})"
                f" -> keep {keep_s:.2f}s, cut {gap_end - excess_start:.2f}s",
                flush=True,
            )

    total_cut = sum(e - s for s, e in cuts) if cuts else 0.0
    print(
        f"[PRETRIM] smart-pause-cuts: {len(cuts)} cut(s) "
        f"({total_cut if cuts else 0:.2f}s) | "
        f"below-thresh={_n_below} eos-protected={_n_eos_prot} "
        f"final-protected={_n_final_prot}",
        flush=True,
    )

    return cuts


def _subtract_fillers(
    seg_start: float,
    seg_end: float,
    filler_drops: list[DropSegment],
) -> list[tuple[float, float]]:
    """Split source interval [seg_start, seg_end] into sub-intervals, excising filler drop ranges."""
    within = sorted(
        (d for d in filler_drops if d.start < seg_end and d.end > seg_start),
        key=lambda d: d.start,
    )
    if not within:
        return [(seg_start, seg_end)]
    parts: list[tuple[float, float]] = []
    cursor = seg_start
    for d in within:
        clip_s = max(seg_start, d.start)
        clip_e = min(seg_end, d.end)
        if cursor < clip_s - 0.001:
            parts.append((cursor, clip_s))
        cursor = max(cursor, clip_e)
    if cursor < seg_end - 0.001:
        parts.append((cursor, seg_end))
    return parts


def _snap_boundary_out_of_word(
    t: float,
    word_timings: list[tuple[float, float]],
    gap_s: float = 0.010,
    min_dur_s: float = 0.030,
    adjacent_gap_tol: float = 0.020,
    debug_label: str = "",
) -> tuple[float, tuple[float, float] | None]:
    """Push t to a clean word boundary if it falls inside a word.

    Adjacent-word pre-check: if t sits in the gap between two consecutive words
    whose inter-word gap is < adjacent_gap_tol, the boundary is already valid at
    the contact point — return (t, None) immediately, no snap.

    Normal snap: if t is strictly inside a word, move to the side determined by
    majority. When the adjacent word on that side is within adjacent_gap_tol,
    snap to the exact contact point (no extra gap_s) to prevent oscillation.

    debug_label: when non-empty, log every decision (pre-check, clean-gap, snap).
    Used by the universal-snap sweep to make its reasoning visible.
    """
    valid = [(ws, we) for ws, we in word_timings if we - ws >= min_dur_s]

    # Explicit exact-boundary: t == ws or t == we is always a valid cut point.
    # The clip ending at t excludes (or completes) the word; the clip starting
    # at t includes (or skips) it — both sides are word-clean.
    for ws, we in valid:
        if t == ws:
            if debug_label:
                print(
                    f"[SNAP] {debug_label}={t:.3f} exact word-start"
                    f" [{ws:.3f},{we:.3f}] → valid (no snap)",
                    flush=True,
                )
            return t, None
        if t == we:
            if debug_label:
                print(
                    f"[SNAP] {debug_label}={t:.3f} exact word-end"
                    f" [{ws:.3f},{we:.3f}] → valid (no snap)",
                    flush=True,
                )
            return t, None

    # Pre-check: t at or between two adjacent words → already a valid boundary
    for idx in range(len(valid) - 1):
        we_a = valid[idx][1]
        ws_b = valid[idx + 1][0]
        if ws_b - we_a < adjacent_gap_tol and we_a <= t <= ws_b:
            if debug_label:
                print(
                    f"[SNAP] {debug_label}={t:.3f} tight inter-word gap"
                    f" [{we_a:.3f},{ws_b:.3f}] gap={ws_b - we_a:.3f}s → valid (no snap)",
                    flush=True,
                )
            return t, None

    # Normal snap: only fires if t is strictly inside a word
    for idx, (ws, we) in enumerate(valid):
        if not (ws < t < we):
            continue
        majority_before = (t - ws >= we - t)
        if majority_before:
            # Word goes to left clip; next word adjacent → snap to contact point
            if idx + 1 < len(valid) and valid[idx + 1][0] - we < adjacent_gap_tol:
                return we, (ws, we)
            return we + gap_s, (ws, we)
        else:
            # Word goes to right clip; prev word adjacent → snap to contact point
            if idx > 0 and ws - valid[idx - 1][1] < adjacent_gap_tol:
                return valid[idx - 1][1], (ws, we)
            return ws - gap_s, (ws, we)

    # t is in a wide inter-word gap (≥ adjacent_gap_tol), not inside any word.
    if debug_label:
        _near = [(ws, we) for ws, we in valid if abs(ws - t) < 0.35 or abs(we - t) < 0.35]
        print(
            f"[SNAP] {debug_label}={t:.3f} clean wide gap,"
            f" nearest words={[(round(ws,3), round(we,3)) for ws, we in _near[:3]]}",
            flush=True,
        )
    return t, None


def _c2s(tc: float, virtual_drops_sorted: list) -> float:
    """Convert compressed-space timestamp to source-space timestamp."""
    offset = 0.0
    for d in virtual_drops_sorted:
        c_drop_start = d.start - offset
        if tc <= c_drop_start:
            break
        offset += d.end - d.start
    return tc + offset


def _output_verify(
    video_path: Path,
    expected_words: list,  # list[WordTiming]
    work_dir: Path,
) -> None:
    """Re-transcribe video_path and compare to expected_words. Controlled by VERIFY_OUTPUT env var."""
    import os, re, difflib
    if os.environ.get("VERIFY_OUTPUT", "true").lower() in ("false", "0", "no"):
        return

    # Hallucination patterns Whisper generates on silence (FR + EN).
    # Token must match exactly after _norm() — add more as discovered.
    _HALLUC_TOKS: frozenset[str] = frozenset({
        "amara", "soustitres", "sous", "titres", "realises", "realisees",
        "para", "communaute", "merci", "davoir", "regarde", "regarder",
        "soustitresrealisesparalacommunautedamaraorg",
        "transcriptbyamara",
    })

    def _norm(t: str) -> str:
        return re.sub(r"[^\w]", "", t.lower(), flags=re.UNICODE)

    def _wtext(w) -> str:
        return w.text if isinstance(w.text, str) else getattr(w, "text", "")

    def _wprob(w) -> float:
        return float(getattr(w, "probability", getattr(w, "score", 1.0)))

    _LOW_CONF = 0.50  # re-transcribed words below this probability are "uncertain"

    try:
        from app.engine.transcribe import transcribe, unload_model
        result = transcribe(video_path)
        unload_model()

        act_words_all = [w for seg in result.segments for w in (seg.words or [])]

        # Bound comparison: ignore hallucinated words past last expected word + 1.0s.
        # Whisper invents credits/Amara text on trailing silence — never in expected.
        _last_exp_end = max((float(getattr(w, "end", 0)) for w in expected_words), default=0.0)
        _act_cutoff   = _last_exp_end + 1.0
        act_words_bounded = [
            w for w in act_words_all
            if float(getattr(w, "start", 0)) <= _act_cutoff
        ]
        _halluc_tail = len(act_words_all) - len(act_words_bounded)
        if _halluc_tail:
            _halluc_txts = [_wtext(w) for w in act_words_all[len(act_words_bounded):len(act_words_bounded)+5]]
            print(f"[OUTPUT-VERIFY] trimmed {_halluc_tail} tail word(s) past {_act_cutoff:.1f}s: {_halluc_txts}", flush=True)

        # Filter expected words in brackets (e.g. "[music]") — ASR never sees those.
        exp_toks = [
            _norm(w.text) for w in expected_words
            if _norm(w.text) and not str(w.text).strip().startswith("[")
        ]
        # Filter known hallucination tokens from actual (they're not real speech).
        act_toks = [
            _norm(_wtext(w)) for w in act_words_bounded
            if _norm(_wtext(w)) and _norm(_wtext(w)) not in _HALLUC_TOKS
        ]
        # Low-conf tokens: may explain "missing" that are actually present but uncertain
        act_uncertain: set[str] = {
            _norm(_wtext(w)) for w in act_words_bounded
            if _wprob(w) < _LOW_CONF and _norm(_wtext(w))
        }

        matcher = difflib.SequenceMatcher(None, exp_toks, act_toks, autojunk=False)
        missing, extra = [], []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "delete":
                missing.extend(exp_toks[i1:i2])
            elif tag == "insert":
                extra.extend(act_toks[j1:j2])
            elif tag == "replace":
                missing.extend(exp_toks[i1:i2])
                extra.extend(act_toks[j1:j2])

        # Separate confirmed missing from uncertain (re-transcribed at low confidence)
        missing_cert   = [t for t in missing if t not in act_uncertain]
        missing_uncert = [t for t in missing if t in act_uncertain]

        ratio = matcher.ratio()

        # ── Long-pause diagnostic: 5 longest inter-word gaps in trimmed output ──
        if len(act_words_all) > 1:
            _iw_gaps = []
            for _wi in range(len(act_words_all) - 1):
                _wa, _wb = act_words_all[_wi], act_words_all[_wi + 1]
                _g = float(getattr(_wb, "start", 0)) - float(getattr(_wa, "end", 0))
                if _g > 0:
                    _iw_gaps.append((_g, _norm(_wtext(_wa)), _norm(_wtext(_wb))))
            _iw_gaps.sort(reverse=True)
            if _iw_gaps:
                print(
                    "[OUTPUT-VERIFY] top-5 gaps: "
                    + " | ".join(f"{d:.2f}s({a}→{b})" for d, a, b in _iw_gaps[:5]),
                    flush=True,
                )

        _unc = f" | uncertain({len(missing_uncert)}): {missing_uncert[:5]}" if missing_uncert else ""
        if not missing_cert and not extra:
            print(f"[OUTPUT-VERIFY] PASS — {len(exp_toks)} words | ratio={ratio:.3f}{_unc}", flush=True)
        else:
            sev = "CRITICAL" if (len(missing_cert) > 3 or len(extra) > 3) else "WARNING"
            print(
                f"[OUTPUT-VERIFY] {sev} — ratio={ratio:.3f}"
                f" | missing({len(missing_cert)}): {missing_cert[:10]}"
                f" | extra({len(extra)}): {extra[:10]}{_unc}",
                flush=True,
            )
    except Exception as _e:
        print(f"[OUTPUT-VERIFY] ERROR — {_e}", flush=True)


def pretrim(
    src: Path,
    transcript: dict[str, Any],
    plan: EditPlan,
    work_dir: Path,
    *,
    filler_drops: list[DropSegment] | None = None,
    virtual_drops: list[DropSegment] | None = None,
    source_words: list | None = None,
) -> tuple[Path, TimingMap]:
    """Cut dead content from source, produce continuous trimmed video.

    Returns (trimmed_path, timing_map).
    """
    from app.core.config import settings as _cfg

    work_dir.mkdir(parents=True, exist_ok=True)
    short_form = plan.format == "short"
    pad = SHORT_PAD_S if short_form else LONG_PAD_S

    all_words = [
        w for seg in transcript.get("segments", [])
        for w in seg.get("words", [])
    ]
    words = _flat_words(transcript)
    src_duration = float(transcript.get("duration", 0.0)) or _probe_duration(src)

    # Source-coordinate mode: use virtual_drops to convert compressed→source
    use_source_coords = bool(virtual_drops and source_words)
    if use_source_coords:
        _vd_sorted = sorted(virtual_drops, key=lambda d: d.start)
        source_duration = _probe_duration(src)
        # _snap_to_word_boundary expects list[tuple[float, float]] — same as _flat_words()
        src_word_timings: list[tuple[float, float]] = sorted(
            [
                (float(w.get("start", 0)), float(w.get("end", 0)))
                for w in source_words
                if w.get("text", "").strip()
            ],
            key=lambda t: t[0],
        )
    else:
        _vd_sorted = []
        source_duration = src_duration
        src_word_timings = words  # already list[tuple[float, float]] from _flat_words()

    # ── DISABLE_CUTS bypass: use full source as one segment ──────────
    if _cfg.disable_cuts:
        print("[PRETRIM] DISABLE_CUTS=true — skipping all segment cutting")
        return _pretrim_passthrough(src, transcript, all_words, src_duration, work_dir)

    # ── Segment preparation (same logic as render.py) ────────────────
    keep = plan.keep_segments or [
        {"start": 0.0, "end": src_duration}
    ]
    keep = _dedup_segments(keep)
    keep = _merge_short_segments(keep)

    # ── Merge keep_segments that are contiguous (end[i] == start[i+1]).
    # A cut into the middle of continuous speech produces an audible glitch.
    _merged_keep: list[dict] = []
    for _seg in keep:
        if _merged_keep and abs(float(_merged_keep[-1]["end"]) - float(_seg["start"])) < 0.005:
            _p = _merged_keep[-1]
            print(
                f"[PRETRIM] merged contiguous segs:"
                f" {float(_p['start']):.3f}-{float(_p['end']):.3f}"
                f" + {float(_seg['start']):.3f}-{float(_seg['end']):.3f}"
                f" → {float(_p['start']):.3f}-{float(_seg['end']):.3f}",
                flush=True,
            )
            _merged_keep[-1] = {**_p, "end": _seg["end"]}
        else:
            _merged_keep.append(_seg)
    keep = _merged_keep

    # ── Pass 1: compute source intervals (snap + pad) for every segment ─
    # Separating computation from FFmpeg lets us apply a pairwise clamp
    # to eliminate padding overlaps before any frame is read.
    # Tuple: (keep_index, s_src, e_snapped, s_padded, e_padded)
    _planned: list[tuple[int, float, float, float, float]] = []

    for i, seg in enumerate(keep):
        s_raw = float(seg["start"])
        e_raw = float(seg["end"])
        if e_raw <= s_raw:
            continue

        if use_source_coords:
            # Convert compressed keep_segment boundaries → source space before seeking
            s_src = _c2s(s_raw, _vd_sorted)
            e_src = _c2s(e_raw, _vd_sorted)
            s = _snap_to_word_boundary(s_src, src_word_timings, edge="start")
            e = _snap_to_word_boundary(e_src, src_word_timings, edge="end")
            if i + 1 < len(keep):
                next_s = _c2s(float(keep[i + 1]["start"]), _vd_sorted)
                e = min(e, max(s + 0.15, next_s - 0.05))
            s_padded = max(0.0, s - pad)
            e_padded = min(source_duration, e + pad) if source_duration > 0 else e + pad
        else:
            s_src = s_raw
            s = _snap_to_word_boundary(s_raw, words, edge="start")
            e = _snap_to_word_boundary(e_raw, words, edge="end")
            e = _extend_for_semantic_completeness(e, transcript, src_duration)
            if i + 1 < len(keep):
                next_s = float(keep[i + 1]["start"])
                e = min(e, max(s + 0.15, next_s - 0.05))
            s_padded = max(0.0, s - pad)
            e_padded = min(src_duration, e + pad) if src_duration > 0 else e + pad
        # "Liar-gap" fix: when a filler drop ends just before this segment's padded
        # start, the gap between drop_end and the first word may contain audio that
        # Whisper mistakenly timestamps as silence (e.g. 're-' of 'retiens' at 27.52
        # when Whisper places the word at 27.84).  Retract s_padded to drop_end+20ms
        # so that speech in the gap is included rather than silently abandoned.
        for _fd in (filler_drops or []):
            if _fd.end < s and _fd.end + 0.500 >= s_padded:
                _sp_adj = min(s_padded, _fd.end + 0.020)
                if _sp_adj < s_padded:
                    print(
                        f"[PRETRIM] seg[{i}] s_padded {s_padded:.3f}→{_sp_adj:.3f}"
                        f" liar-gap: snapped behind drop '{_fd.reason}' end {_fd.end:.3f}",
                        flush=True,
                    )
                    s_padded = _sp_adj

        if e_padded - s_padded < 0.15:
            continue

        _planned.append((i, s_src, e, s_padded, e_padded))

    # ── Stabilization: snap→clamp iteratively until both invariants hold ────
    # Order: word-snap first (boundary out of word) then pairwise clamp
    # (10ms gap). Repeat up to _MAX_STAB times. If no fixed point, force-assign
    # the word to the clip with the majority, setting boundary at the exact
    # inter-word gap (word always wins over midpoint; 0-gap adjacency allowed).
    _wt = src_word_timings if use_source_coords else words
    _MAX_STAB = 5
    _resolved: set[int] = set()  # pairs resolved by fallback (0-gap allowed)

    # Snapshot original padded boundaries before the loop touches them.
    # Used by the gap-preserving fallback to restore s_padded for seg[j] when
    # the clamp has incorrectly pulled it backward across a planned source gap.
    _planned_padded_orig: list[tuple[float, float]] = [
        (sp, ep) for _, _, _, sp, ep in _planned
    ]

    # Diagnostic: dump initial boundary state for every adjacent pair
    for _pi in range(len(_planned) - 1):
        _i, _, _, _s_i, _e_i_pad = _planned[_pi]
        _j, _, _, _s_j, _ = _planned[_pi + 1]
        print(
            f"[PRETRIM] boundary-init seg[{_i}]/seg[{_j}]:"
            f" e[{_i}]={_e_i_pad:.3f} s[{_j}]={_s_j:.3f}"
            f" gap={_s_j - _e_i_pad:+.3f}s",
            flush=True,
        )

    for _pass in range(_MAX_STAB):
        _snap_changed = False
        _clamp_changed = False

        # ① Word-aware snap (run first this pass)
        for _pi in range(len(_planned)):
            _i, _s_src_i, _e_i, _s_i, _e_i_pad = _planned[_pi]
            _updated = False

            _new_s, _word_s = _snap_boundary_out_of_word(_s_i, _wt)
            if _word_s:
                _new_s = max(0.0, _new_s)
                print(
                    f"[PRETRIM] stab-{_pass+1} snap s[{_i}]: {_s_i:.3f}→{_new_s:.3f}"
                    f" (word {_word_s[0]:.3f}-{_word_s[1]:.3f})",
                    flush=True,
                )
                _s_i = _new_s
                _snap_changed = _updated = True

            _new_e, _word_e = _snap_boundary_out_of_word(_e_i_pad, _wt)
            if _word_e:
                print(
                    f"[PRETRIM] stab-{_pass+1} snap e[{_i}]: {_e_i_pad:.3f}→{_new_e:.3f}"
                    f" (word {_word_e[0]:.3f}-{_word_e[1]:.3f})",
                    flush=True,
                )
                _e_i_pad = _new_e
                _snap_changed = _updated = True

            if _updated:
                _planned[_pi] = (_i, _s_src_i, _e_i, _s_i, _e_i_pad)

        # ② Pairwise clamp (run second this pass)
        for _pi in range(len(_planned) - 1):
            _i, _s_src_i, _e_i, _s_i, _e_i_pad = _planned[_pi]
            _j, _s_src_j, _e_j, _s_j, _e_j_pad = _planned[_pi + 1]
            if round(_e_i_pad, 3) > round(_s_j - 0.010, 3):
                _mid = (_e_i_pad + _s_j) / 2.0
                _planned[_pi]     = (_i, _s_src_i, _e_i, _s_i, _mid - 0.005)
                _planned[_pi + 1] = (_j, _s_src_j, _e_j, _mid + 0.005, _e_j_pad)
                _clamp_changed = True
                print(
                    f"[PRETRIM] stab-{_pass+1} clamp seg[{_i}]/seg[{_j}]:"
                    f" e→{_mid - 0.005:.3f} s→{_mid + 0.005:.3f}",
                    flush=True,
                )

        if not _snap_changed and not _clamp_changed:
            if _pass > 0:
                print(f"[PRETRIM] stabilized after {_pass + 1} pass(es)", flush=True)
            break
    else:
        # No fixed point: force-assign — word always wins over midpoint.
        print("[PRETRIM] stabilize: no fixed point — force-assigning words", flush=True)
        for _pi in range(len(_planned) - 1):
            _i, _s_src_i, _e_i, _s_i, _e_i_pad = _planned[_pi]
            _j, _s_src_j, _e_j, _s_j, _e_j_pad = _planned[_pi + 1]
            # Skip only if both invariants are already satisfied
            _clamp_ok = _e_i_pad <= _s_j - 0.010 + 1e-9
            _e_clean = not any(ws < _e_i_pad < we for ws, we in _wt if we - ws >= 0.030)
            _s_clean = not any(ws < _s_j < we for ws, we in _wt if we - ws >= 0.030)
            if _clamp_ok and _e_clean and _s_clean:
                continue
            # If there is a real source-space gap between the two segments (> 300ms),
            # the clamp has illegitimately pulled s[j] backward across the gap.
            # Restore s[j] to its original padded start and trim e[i] to just past
            # its last word. The gap stays excluded; do NOT add to _resolved.
            _src_gap = _s_src_j - _e_i
            if _src_gap > 0.300:
                _e_new = _e_i + 0.010
                _s_new = _planned_padded_orig[_pi + 1][0]
                # Snap restored s_new: original s_padded may land inside a word
                _s_new_snapped, _snap_w = _snap_boundary_out_of_word(_s_new, _wt)
                if _snap_w:
                    print(
                        f"[PRETRIM] stabilize FALLBACK snap s[{_j}]:"
                        f" {_s_new:.3f}→{_s_new_snapped:.3f}"
                        f" (word {_snap_w[0]:.3f}-{_snap_w[1]:.3f})",
                        flush=True,
                    )
                    _s_new = _s_new_snapped
                if _e_new <= _s_new - 0.010:
                    _planned[_pi]     = (_i, _s_src_i, _e_i, _s_i, _e_new)
                    _planned[_pi + 1] = (_j, _s_src_j, _e_j, _s_new, _e_j_pad)
                    print(
                        f"[PRETRIM] stabilize FALLBACK seg[{_i}]/seg[{_j}]: GAP-PRESERVED"
                        f" e={_e_new:.3f} s={_s_new:.3f} (src gap={_src_gap:.3f}s)",
                        flush=True,
                    )
                    continue  # 10ms gap required in final assert; don't add to _resolved
            # Contiguous segments: place boundary at exact inter-word contact point.
            _bd_pt: float | None = None
            for ws, we in _wt:
                if we - ws < 0.030:
                    continue
                if ws < _e_i_pad < we:
                    # e is inside a word; majority decides which clip gets it
                    _bd_pt = we if (_e_i_pad - ws >= we - _e_i_pad) else ws
                    break
                if ws < _s_j < we:
                    # s is inside a word
                    _bd_pt = ws if (_s_j - ws < we - _s_j) else we
                    break
            _gap_pt = _bd_pt if _bd_pt is not None else (_e_i_pad + _s_j) / 2.0
            _planned[_pi]     = (_i, _s_src_i, _e_i, _s_i, _gap_pt)
            _planned[_pi + 1] = (_j, _s_src_j, _e_j, _gap_pt, _e_j_pad)
            _resolved.add(_pi)
            print(
                f"[PRETRIM] stabilize FALLBACK seg[{_i}]/seg[{_j}]:"
                f" e=s={_gap_pt:.3f} (contiguous — word wins)",
                flush=True,
            )

    # ── ③ Orphan repair: words entirely in gap [e_i_pad, s_j] belong to no clip ─
    # Assign each orphaned word to the nearer segment; snap boundaries afterward.
    # Skip pairs with a large source gap: words between e_i and s_src_j are
    # intentionally excluded source content, not accidental orphans.
    for _pi in range(len(_planned) - 1):
        _i, _s_src_i, _e_i, _s_i, _e_i_pad = _planned[_pi]
        _j, _s_src_j, _e_j, _s_j, _e_j_pad = _planned[_pi + 1]
        if _s_src_j - _e_i > 0.300:
            continue  # source gap: excluded words are intentional, not orphans
        _orphan_fixed = False
        for _ws, _we in _wt:
            if _ws > _s_j: break
            if _we - _ws < 0.030: continue
            if not (_e_i_pad <= _ws and _we <= _s_j): continue
            # Orphan word: assign to whichever segment it's closest to
            _d_i = _ws - _e_i_pad  # gap from e[i] to word start
            _d_j = _s_j - _we      # gap from word end to s[j]
            if _d_i < _d_j:        # closer to seg[i] → give to seg[i]
                _e_i_pad = _we + 0.010
                _s_j = max(_s_j, _e_i_pad)
            else:                   # closer to seg[j] (or tie) → give to seg[j]
                _s_j = _ws - 0.010
                _e_i_pad = min(_e_i_pad, _s_j)
            print(
                f"[PRETRIM] orphan-repair seg[{_i}]/seg[{_j}]:"
                f" word [{_ws:.3f},{_we:.3f}]"
                f" → seg[{_i if _d_i < _d_j else _j}]"
                f" e={_e_i_pad:.3f} s={_s_j:.3f}",
                flush=True,
            )
            _orphan_fixed = True
        if _orphan_fixed:
            # Snap repaired boundaries to ensure word-safety
            _e_i_pad_r, _ = _snap_boundary_out_of_word(_e_i_pad, _wt)
            _s_j_r, _     = _snap_boundary_out_of_word(_s_j, _wt)
            _e_i_pad, _s_j = _e_i_pad_r, _s_j_r
            if _e_i_pad >= _s_j - 1e-9:
                _resolved.add(_pi)
            _planned[_pi]     = (_i, _s_src_i, _e_i, _s_i, _e_i_pad)
            _planned[_pi + 1] = (_j, _s_src_j, _e_j, _s_j, _e_j_pad)

    # ── ④ Universal boundary snap ─────────────────────────────────────────────
    # The stabilization loop only snaps pairs in clamp-conflict. Boundaries with
    # large gaps (e.g. s[5] with +1.38s gap) never trigger clamp and may still
    # land inside a word after padding or fallback restoration. One final sweep
    # over ALL boundaries guarantees word-cleanliness before the assert.
    _univ_changed = False
    _univ_n_snapped = 0
    for _pi in range(len(_planned)):
        _i, _s_src_i, _e_i, _s_i, _e_i_pad = _planned[_pi]
        _updated = False
        _new_s, _word_s = _snap_boundary_out_of_word(
            _s_i, _wt, debug_label=f"s[{_i}]"
        )
        if _word_s:
            _new_s = max(0.0, _new_s)
            print(
                f"[PRETRIM] universal-snap s[{_i}]: {_s_i:.3f}→{_new_s:.3f}"
                f" (word {_word_s[0]:.3f}-{_word_s[1]:.3f})",
                flush=True,
            )
            _s_i = _new_s
            _updated = True
            _univ_n_snapped += 1
        _new_e, _word_e = _snap_boundary_out_of_word(
            _e_i_pad, _wt, debug_label=f"e[{_i}]"
        )
        if _word_e:
            print(
                f"[PRETRIM] universal-snap e[{_i}]: {_e_i_pad:.3f}→{_new_e:.3f}"
                f" (word {_word_e[0]:.3f}-{_word_e[1]:.3f})",
                flush=True,
            )
            _e_i_pad = _new_e
            _updated = True
            _univ_n_snapped += 1
        if _updated:
            _planned[_pi] = (_i, _s_src_i, _e_i, _s_i, _e_i_pad)
            _univ_changed = True
    print(
        f"[PRETRIM] universal-snap sweep: {len(_planned)} segs,"
        f" {len(_planned) * 2} boundaries checked, {_univ_n_snapped} snapped",
        flush=True,
    )

    # Re-verify pairwise 10ms after universal snap (snap rarely creates conflict).
    if _univ_changed:
        for _pi in range(len(_planned) - 1):
            _i, _s_src_i, _e_i, _s_i, _e_i_pad = _planned[_pi]
            _j, _s_src_j, _e_j, _s_j, _e_j_pad = _planned[_pi + 1]
            if round(_e_i_pad, 3) > round(_s_j - 0.010, 3) and _pi not in _resolved:
                _mid = (_e_i_pad + _s_j) / 2.0
                _planned[_pi]     = (_i, _s_src_i, _e_i, _s_i, _mid - 0.005)
                _planned[_pi + 1] = (_j, _s_src_j, _e_j, _mid + 0.005, _e_j_pad)
                print(
                    f"[PRETRIM] universal-snap reclamp seg[{_i}]/seg[{_j}]:"
                    f" e→{_mid - 0.005:.3f} s→{_mid + 0.005:.3f}",
                    flush=True,
                )

    # ── Final assert: overlap, word-clean, and orphan invariants ─────────────
    # All comparisons use round(x, 3) — we work in milliseconds; comparing raw
    # float64 triggers false positives (e.g. 20.685 - 0.010 = 20.674999...997
    # when the mathematically correct result is 20.675, causing a spurious assert).
    _R = 3  # shared rounding precision (ms)
    for _pi in range(len(_planned) - 1):
        _i, _, _e_i, _s_i, _e_i_pad = _planned[_pi]
        _j, _s_src_j, _, _s_j, _ = _planned[_pi + 1]
        # Force-resolved pairs allow e == s (0-gap); normal pairs need 10ms buffer.
        _min_gap = 0.0 if _pi in _resolved else 0.010
        _e_r = round(_e_i_pad, _R)
        _s_r = round(_s_j, _R)
        _thresh_r = round(_s_j - _min_gap, _R)
        if _e_r > _thresh_r:
            raise RuntimeError(
                f"[PRETRIM] INVARIANT: seg[{_i}].e={_e_i_pad:.3f}"
                f" > seg[{_j}].s - {_min_gap:.3f} = {_s_j - _min_gap:.3f}"
                f" — overlap after stabilization"
            )
        for ws, we in _wt:
            if ws > _s_j + 0.5:
                break  # _wt is sorted; no further word can straddle this boundary
            if we - ws < 0.030:
                continue
            _ws_r = round(ws, _R)
            _we_r = round(we, _R)
            if _ws_r < _e_r < _we_r:
                raise RuntimeError(
                    f"[PRETRIM] INVARIANT: seg[{_i}].e={_e_i_pad:.3f}"
                    f" inside word [{ws:.3f},{we:.3f}]"
                )
            if _ws_r < _s_r < _we_r:
                raise RuntimeError(
                    f"[PRETRIM] INVARIANT: seg[{_j}].s={_s_j:.3f}"
                    f" inside word [{ws:.3f},{we:.3f}]"
                )
            # Orphan: word entirely in the gap [e_i_pad, s_j]
            # Skip for pairs with a large source gap — excluded words are intentional.
            if _s_src_j - _e_i <= 0.300 and _e_r <= _ws_r and _we_r <= _s_r:
                raise RuntimeError(
                    f"[PRETRIM] INVARIANT: word [{ws:.3f},{we:.3f}] orphaned"
                    f" between seg[{_i}].e={_e_i_pad:.3f} and seg[{_j}].s={_s_j:.3f}"
                )

    # ── WORD-LOST REPAIR: recover words expelled by snap, BEFORE FFmpeg render ──
    # A snap may push e[i] backward past a word that belongs in segment i,
    # orphaning it in a gap >300ms (below the orphan-invariant radar).
    # Extend the nearest segment's e_padded to cover the word before cutting.
    # Distinction: a word in a gap is ONLY acceptable if it's in a filler_drop.
    # "Planned gap" is not an excuse — GAP-RESCUE handles those upstream;
    # anything reaching here without drop coverage is always a snap artifact.
    _wl_pool = (source_words if use_source_coords else all_words) or []
    _wl_sig  = [
        w for w in _wl_pool
        if str(w.get("text", "")).strip()
        and float(w.get("end", 0)) - float(w.get("start", 0)) >= 0.030
    ]

    if _wl_sig:
        # ONE definition of "valid junction" shared by repair + assert.
        # Normal pairs need e ≤ s-10ms; force-resolved pairs (orphan-repair / fallback)
        # allow e == s (0-gap).  Consulting _resolved is mandatory — without it,
        # a 0-gap junction installed by orphan-repair looks like an overlap.
        def _jv(_pi2: int) -> bool:
            _R3 = 3
            _ep = _planned[_pi2][4]
            _sp = _planned[_pi2 + 1][3]
            if round(_ep, _R3) <= round(_sp - 0.010, _R3):
                return True   # standard 10ms gap — always valid
            if _pi2 in _resolved and round(_ep, _R3) <= round(_sp, _R3):
                return True   # force-resolved 0-gap — valid by prior decision
            return False

        _wl_total_repaired = 0
        _wl_pass_n = 0
        _wl_modified_pis: set[int] = set()  # pairs TOUCHED by this repair pass
        for _wl_pass_n in range(5):  # up to 5 cascade iterations
            _wl_repaired_this = 0
            for _wl_w in _wl_sig:
                _wl_ws  = float(_wl_w.get("start", 0))
                _wl_we  = float(_wl_w.get("end", 0))
                _wl_txt = str(_wl_w.get("text", "")).strip()

                if any(
                    _planned[_p2][3] - 0.010 <= _wl_ws and _wl_we <= _planned[_p2][4] + 0.010
                    for _p2 in range(len(_planned))
                ):
                    continue  # covered by a planned interval
                if any(
                    d.start - 0.010 <= _wl_ws and _wl_we <= d.end + 0.010
                    for d in (filler_drops or [])
                ):
                    continue  # explicitly dropped

                # ── Bidirectional repair ─────────────────────────────────────────────
                # (a) extend e[pi_a] rightward to cover word.end
                # (b) retract s[pi_b] leftward to cover word.start
                # Edge-adjacent (|word.end - s[pi_b]| ≤ 5ms): prefer (b) with 0-gap,
                #     retract s[pi_b] to word.start and mark the pair as resolved.
                # If both sides fail: merge adjacent pi_a and pi_b.
                # ─────────────────────────────────────────────────────────────────

                # Option (a) candidate: last segment whose end is near word.start
                _wl_pi_a     = None
                _wl_new_ep_a = None
                _wl_a_dist   = float("inf")
                for _wl_pi in range(len(_planned)):
                    _ep = _planned[_wl_pi][4]
                    if _wl_ws < _ep - 0.100:       # word starts well before seg end
                        continue
                    _gap = 0.0 if _wl_pi in _resolved else 0.010
                    _ne  = _wl_we + 0.010
                    if _wl_pi + 1 < len(_planned):
                        _ne = min(_ne, _planned[_wl_pi + 1][3] - _gap)
                    if _ne < _wl_we - 0.001:       # clamped extension can't cover word.end
                        continue
                    _dist = max(0.0, _wl_ws - _ep)
                    if _dist < _wl_a_dist:
                        _wl_a_dist   = _dist
                        _wl_pi_a     = _wl_pi
                        _wl_new_ep_a = _ne

                # Option (b) candidate: first segment whose start is near word.end
                _wl_pi_b     = None
                _wl_new_sp_b = None
                _wl_b_dist   = float("inf")
                for _wl_pi in range(len(_planned)):
                    _sp = _planned[_wl_pi][3]
                    if _wl_we > _sp + 0.100:       # word ends well after seg start
                        continue
                    _prev_pi = _wl_pi - 1
                    _gap = 0.0 if _prev_pi in _resolved else 0.010
                    _ns  = _wl_ws - 0.010
                    if _prev_pi >= 0:
                        _ns = max(_ns, _planned[_prev_pi][4] + _gap)
                    if _ns > _wl_ws + 0.001:       # clamped retraction can't cover word.start
                        continue
                    _dist = max(0.0, _sp - _wl_we)
                    if _dist < _wl_b_dist:
                        _wl_b_dist   = _dist
                        _wl_pi_b     = _wl_pi
                        _wl_new_sp_b = _ns

                # Edge-adjacent: word.end ≈ s[pi_b] within 5ms
                # → retract s[pi_b] to word.start (0-gap), mark pair as resolved
                _wl_edge_adj = (
                    _wl_pi_b is not None
                    and abs(_wl_we - _planned[_wl_pi_b][3]) <= 0.005
                )
                if _wl_edge_adj:
                    _wl_new_sp_b = _wl_ws  # word opens the next segment with 0-gap

                # Choose strategy
                if _wl_edge_adj:
                    _wl_strat = "b0"
                elif _wl_pi_a is not None and _wl_pi_b is not None:
                    _wl_strat = "b" if _wl_b_dist <= _wl_a_dist else "a"
                elif _wl_pi_a is not None:
                    _wl_strat = "a"
                elif _wl_pi_b is not None:
                    _wl_strat = "b"
                else:
                    _wl_strat = "merge"

                # Apply
                if _wl_strat == "a":
                    _wl_mi, _wl_mss, _wl_me, _wl_msp, _wl_mep = _planned[_wl_pi_a]
                    _planned[_wl_pi_a] = (
                        _wl_mi, _wl_mss, max(_wl_me, _wl_we), _wl_msp, _wl_new_ep_a
                    )
                    if _wl_pi_a > 0:
                        _wl_modified_pis.add(_wl_pi_a - 1)
                    if _wl_pi_a + 1 < len(_planned):
                        _wl_modified_pis.add(_wl_pi_a)
                    print(
                        f"[WORD-LOST] REPAIRED(a) '{_wl_txt}' {_wl_ws:.2f}-{_wl_we:.2f}s"
                        f" → seg[{_wl_pi_a}].e_padded {_wl_mep:.3f}→{_wl_new_ep_a:.3f}",
                        flush=True,
                    )
                    _wl_repaired_this += 1

                elif _wl_strat in ("b", "b0"):
                    _wl_mi, _wl_mss, _wl_me, _wl_msp_old, _wl_mep = _planned[_wl_pi_b]
                    _planned[_wl_pi_b] = (
                        _wl_mi, _wl_mss, _wl_me, _wl_new_sp_b, _wl_mep
                    )
                    _wl_pair_b = _wl_pi_b - 1  # junction (pi_b-1, pi_b)
                    if _wl_strat == "b0" and _wl_pair_b >= 0:
                        _resolved.add(_wl_pair_b)
                        print(
                            f"[WORD-LOST] REPAIRED(b0-edge) '{_wl_txt}'"
                            f" {_wl_ws:.2f}-{_wl_we:.2f}s"
                            f" → seg[{_wl_pi_b}].s_padded"
                            f" {_wl_msp_old:.3f}→{_wl_new_sp_b:.3f} (0-gap, resolved)",
                            flush=True,
                        )
                    else:
                        print(
                            f"[WORD-LOST] REPAIRED(b) '{_wl_txt}'"
                            f" {_wl_ws:.2f}-{_wl_we:.2f}s"
                            f" → seg[{_wl_pi_b}].s_padded"
                            f" {_wl_msp_old:.3f}→{_wl_new_sp_b:.3f}",
                            flush=True,
                        )
                    if _wl_pair_b >= 0:
                        _wl_modified_pis.add(_wl_pair_b)
                    if _wl_pi_b + 1 < len(_planned):
                        _wl_modified_pis.add(_wl_pi_b)
                    _wl_repaired_this += 1

                elif _wl_strat == "merge":
                    # GUARANTEED BRACKETING MERGE — both extend (a) and retract (b)
                    # failed because both neighbours are saturated.  Find the segments
                    # that DEFINITIONALLY bracket the word (last segment ending at or
                    # before word.start, first segment starting at or after word.end)
                    # and fuse them into one interval.  The word lies in the gap between
                    # them by construction, so [piL.s_padded, piR.e_padded] always
                    # covers it.  This operation cannot fail mathematically.
                    _wl_piL = None  # last segment whose e_padded ≤ word.start
                    _wl_piR = None  # first segment whose s_padded ≥ word.end
                    for _pi_g in range(len(_planned)):
                        _ep_g = _planned[_pi_g][4]
                        _sp_g = _planned[_pi_g][3]
                        if _ep_g <= _wl_ws + 0.010:
                            if _wl_piL is None or _ep_g > _planned[_wl_piL][4]:
                                _wl_piL = _pi_g
                        if _sp_g >= _wl_we - 0.010:
                            if _wl_piR is None or _sp_g < _planned[_wl_piR][3]:
                                _wl_piR = _pi_g

                    if _wl_piL is not None and _wl_piR is not None and _wl_piL != _wl_piR:
                        _wl_mi_L, _wl_ss_L, _wl_e_L, _wl_sp_L, _wl_ep_L = _planned[_wl_piL]
                        _wl_mi_R, _wl_ss_R, _wl_e_R, _wl_sp_R, _wl_ep_R = _planned[_wl_piR]
                        # Fuse: new segment spans piL.s_padded → piR.e_padded
                        _planned[_wl_piL] = (
                            _wl_mi_L, _wl_ss_L, _wl_e_R, _wl_sp_L, _wl_ep_R
                        )
                        # Delete all segments piL+1 through piR (inclusive)
                        _n_del = _wl_piR - _wl_piL
                        del _planned[_wl_piL + 1 : _wl_piR + 1]
                        # Adjust pair indices: pairs piL..piR-1 are gone;
                        # pairs ≥ piR shift down by n_del.
                        _wl_modified_pis = {
                            p - _n_del if p >= _wl_piR else p
                            for p in _wl_modified_pis
                            if p < _wl_piL or p >= _wl_piR
                        }
                        _resolved = {
                            r - _n_del if r >= _wl_piR else r
                            for r in _resolved
                            if r < _wl_piL or r >= _wl_piR
                        }
                        if _wl_piL > 0:
                            _wl_modified_pis.add(_wl_piL - 1)
                        if _wl_piL + 1 < len(_planned):
                            _wl_modified_pis.add(_wl_piL)
                        print(
                            f"[WORD-LOST] MERGED seg[{_wl_piL}]+seg[{_wl_piR}]"
                            f" to absorb '{_wl_txt}' {_wl_ws:.2f}-{_wl_we:.2f}s",
                            flush=True,
                        )
                        _wl_repaired_this += 1
                    else:
                        # No bracketing segments — word is before the first segment
                        # or after the last (pre/post-plan exclusion). GAP-RESCUE
                        # should have handled these; final assertion skips them too.
                        print(
                            f"[WORD-LOST] UNREPAIRABLE '{_wl_txt}'"
                            f" at {_wl_ws:.2f}-{_wl_we:.2f}s"
                            f" — no bracketing segments (pre/post-plan orphan)",
                            flush=True,
                        )

            _wl_total_repaired += _wl_repaired_this
            if _wl_repaired_this == 0:
                break

        if _wl_total_repaired:
            print(
                f"[WORD-LOST] {_wl_total_repaired} word(s) repaired in"
                f" {_wl_pass_n + 1} pass(es)",
                flush=True,
            )

        # Post-repair check — only verify pairs WE modified; untouched pairs were
        # already validated by stabilization/universal-snap and must not be re-tested
        # with different criteria (false positives guaranteed otherwise).
        # Uses _jv so force-resolved 0-gap junctions are correctly accepted.
        for _wl_pi2 in sorted(_wl_modified_pis):
            if _wl_pi2 + 1 >= len(_planned):
                continue
            if not _jv(_wl_pi2):
                _wl_ep2 = _planned[_wl_pi2][4]
                _wl_sp2 = _planned[_wl_pi2 + 1][3]
                print(
                    f"[WORD-LOST-OVERLAP] seg[{_wl_pi2}].e_padded={_wl_ep2:.3f}"
                    f" > seg[{_wl_pi2 + 1}].s_padded={_wl_sp2:.3f}"
                    f" after repair — marking 0-gap resolved (graceful fallback)",
                    flush=True,
                )
                _resolved.add(_wl_pi2)

        # Final assertion — any word in an INTER-SEGMENT gap (has planned segments
        # both before AND after it) is a snap artifact and must have been repaired.
        # Words before the first segment or after the last segment are intentional
        # plan exclusions (intro/outro/preamble) — skip those.
        for _wl_w in _wl_sig:
            _wl_ws  = float(_wl_w.get("start", 0))
            _wl_we  = float(_wl_w.get("end", 0))
            _wl_txt = str(_wl_w.get("text", "")).strip()
            if any(
                _planned[_p2][3] - 0.010 <= _wl_ws and _wl_we <= _planned[_p2][4] + 0.010
                for _p2 in range(len(_planned))
            ):
                continue
            if any(
                d.start - 0.010 <= _wl_ws and _wl_we <= d.end + 0.010
                for d in (filler_drops or [])
            ):
                continue
            # Is this word in a true inter-segment gap?
            # (at least one segment ends before the word AND one starts after)
            _wl_has_before = any(_planned[_p2][4] <= _wl_ws + 0.010 for _p2 in range(len(_planned)))
            _wl_has_after  = any(_planned[_p2][3] >= _wl_we - 0.010 for _p2 in range(len(_planned)))
            if not (_wl_has_before and _wl_has_after):
                continue  # pre-plan or post-plan word — intentional exclusion, skip
            # GRACEFUL FALLBACK: force-absorb into nearest segment boundary — never crash to user.
            print(
                f"[WORD-LOST-FALLBACK] '{_wl_txt}' {_wl_ws:.3f}-{_wl_we:.3f}s"
                f" still orphaned after {_wl_pass_n + 1} repair pass(es)"
                f" — emergency nearest-segment absorb",
                flush=True,
            )
            _wl_near_d, _wl_near_pi, _wl_near_side = float("inf"), 0, "a"
            for _fb_pi in range(len(_planned)):
                _d_a = abs(_wl_ws - _planned[_fb_pi][4])   # dist word.start → seg.e_padded
                _d_b = abs(_wl_we - _planned[_fb_pi][3])   # dist word.end   → seg.s_padded
                if _d_a < _wl_near_d:
                    _wl_near_d, _wl_near_pi, _wl_near_side = _d_a, _fb_pi, "a"
                if _d_b < _wl_near_d:
                    _wl_near_d, _wl_near_pi, _wl_near_side = _d_b, _fb_pi, "b"
            _fb_mi, _fb_ss, _fb_e, _fb_sp2, _fb_ep2 = _planned[_wl_near_pi]
            if _wl_near_side == "a":
                _fb_new_ep = max(_fb_ep2, _wl_we + 0.010)
                if _wl_near_pi + 1 < len(_planned):
                    _fb_next_sp = _planned[_wl_near_pi + 1][3]
                    if _fb_new_ep > _fb_next_sp:
                        _fb_new_ep = _fb_next_sp
                        _resolved.add(_wl_near_pi)
                _planned[_wl_near_pi] = (_fb_mi, _fb_ss, max(_fb_e, _wl_we), _fb_sp2, _fb_new_ep)
            else:
                _fb_new_sp = min(_fb_sp2, _wl_ws - 0.010)
                if _wl_near_pi > 0:
                    _fb_prev_ep = _planned[_wl_near_pi - 1][4]
                    if _fb_new_sp < _fb_prev_ep:
                        _fb_new_sp = _fb_prev_ep
                        _resolved.add(_wl_near_pi - 1)
                _planned[_wl_near_pi] = (_fb_mi, _fb_ss, _fb_e, _fb_new_sp, _fb_ep2)
            print(
                f"[WORD-LOST-FALLBACK] absorbed into seg[{_wl_near_pi}]"
                f" side={_wl_near_side} dist={_wl_near_d * 1000:.0f}ms",
                flush=True,
            )

        if not _wl_total_repaired:
            print(f"[WORD-LOST] PASS — all {len(_wl_sig)} words covered pre-render", flush=True)

    _planned_dur = sum(ep - sp for _, _, _, sp, ep in _planned)

    # ── Pass 2: FFmpeg cuts and word remapping ───────────────────────
    parts: list[Path] = []
    source_intervals: list[tuple[float, float]] = []
    remapped_words: list[WordTiming] = []
    cum = 0.0
    _total_sub_parts_count = 0

    for _pi, (i, s_src, e, s_padded, e_padded) in enumerate(_planned):
        # Split this segment around any filler drops that fall within it.
        sub_intervals = _subtract_fillers(s_padded, e_padded, filler_drops or [])

        # ── Snap internal sub-interval edges word-clean ───────────────────────
        # _subtract_fillers cuts at filler drop boundaries (filler.start / filler.end).
        # These edges can land inside words — the outer segment boundaries were
        # protected by the universal snap, but the INTERNAL edges were not.
        # Apply the same word-aware snap to each internal cut point.
        if len(sub_intervals) > 1:
            _sub_mut: list[list[float]] = [[si_s, si_e] for si_s, si_e in sub_intervals]
            for _sk in range(len(_sub_mut)):
                # Snap end of sub-interval k (internal edge — skip the last sub's end
                # which is the outer segment boundary, already covered by universal snap).
                if _sk < len(_sub_mut) - 1:
                    _old_e = _sub_mut[_sk][1]
                    _new_e, _sw = _snap_boundary_out_of_word(
                        _old_e, _wt, debug_label=f"sub[{i}][{_sk}].e"
                    )
                    if _sw:
                        # Clamp: can't extend past the start of the next sub-interval.
                        _new_e = min(_new_e, _sub_mut[_sk + 1][0] - 0.001)
                        _new_e = max(_new_e, _sub_mut[_sk][0] + 0.050)
                        print(
                            f"[PRETRIM] sub-part snap e[{i}][{_sk}]:"
                            f" {_old_e:.3f}→{_new_e:.3f}"
                            f" (word {_sw[0]:.3f}-{_sw[1]:.3f})",
                            flush=True,
                        )
                        _sub_mut[_sk][1] = _new_e
                # Snap start of sub-interval k (internal edge — skip the first sub's
                # start which is the outer segment boundary).
                if _sk > 0:
                    _old_s = _sub_mut[_sk][0]
                    _new_s, _sw = _snap_boundary_out_of_word(
                        _old_s, _wt, debug_label=f"sub[{i}][{_sk}].s"
                    )
                    if _sw:
                        # Clamp: can't retreat past the end of the previous sub-interval.
                        _new_s = max(_new_s, _sub_mut[_sk - 1][1] + 0.001)
                        _new_s = min(_new_s, _sub_mut[_sk][1] - 0.050)
                        print(
                            f"[PRETRIM] sub-part snap s[{i}][{_sk}]:"
                            f" {_old_s:.3f}→{_new_s:.3f}"
                            f" (word {_sw[0]:.3f}-{_sw[1]:.3f})",
                            flush=True,
                        )
                        _sub_mut[_sk][0] = _new_s
            sub_intervals = [(si_s, si_e) for si_s, si_e in _sub_mut]

        # Cut each sub-interval with the same fast lossless encoding used for segments.
        sub_parts: list[Path] = []
        for j, (si_start, si_end) in enumerate(sub_intervals):
            if si_end - si_start < 0.05:
                continue
            sub_part = work_dir / f"trim_{i:04d}_{j:02d}.mp4"
            # 4ms audio fade-in on every sub-part after the first to prevent
            # the click that results from a hard cut into a new sub-interval.
            audio_args = (
                ["-af", "afade=t=in:st=0:d=0.004"] if j > 0 else []
            )
            _run([
                FFMPEG_PATH, "-y", "-loglevel", "error",
                "-ss", f"{si_start:.6f}", "-accurate_seek",
                "-i", str(src),
                "-t", f"{si_end - si_start:.6f}",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
                "-c:a", "aac", "-b:a", "192k",
                *audio_args,
                "-avoid_negative_ts", "make_zero",
                str(sub_part),
            ])
            if sub_part.exists():
                sub_parts.append((sub_part, si_start, si_end))

        if not sub_parts:
            continue

        # Concat sub-parts into a single segment file when fillers split the segment.
        part = work_dir / f"trim_{i:04d}.mp4"
        if len(sub_parts) == 1:
            sub_parts[0][0].rename(part)
        else:
            sub_concat = work_dir / f"sub_concat_{i:04d}.txt"
            sub_concat.write_text(
                "\n".join(f"file '{p.name}'" for p, _, _ in sub_parts),
                encoding="utf-8",
            )
            _run([
                FFMPEG_PATH, "-y", "-loglevel", "error",
                "-f", "concat", "-safe", "0",
                "-i", str(sub_concat),
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                str(part),
            ])
            sub_concat.unlink(missing_ok=True)
            for sp, _, _ in sub_parts:
                sp.unlink(missing_ok=True)

        if not part.exists():
            continue

        seg_offset = cum

        # Build (si_start, si_end, cumulative_offset) tuples for word assignment.
        sub_cum = 0.0
        sub_offsets: list[tuple[float, float, float]] = []
        for _, si_start, si_end in sub_parts:
            sub_offsets.append((si_start, si_end, sub_cum))
            sub_cum += si_end - si_start

        # Single pass over words: each word is assigned to exactly one
        # sub-interval (by its start time), preventing double-counting at
        # sub-interval boundaries. Words whose start falls in a filler gap
        # are naturally skipped (no matching interval).
        _word_pool = source_words if use_source_coords else all_words
        words_in_range = [
            w for w in _word_pool
            if (s_padded - 0.05) <= float(w["start"]) < (e + 0.05)
        ]
        for w in words_in_range:
            ws = float(w["start"])
            we = float(w["end"])
            for si_start, si_end, si_off in sub_offsets:
                if si_start <= ws < si_end:
                    remapped_words.append(WordTiming(
                        text=w["text"].strip(),
                        start=max(0.0, seg_offset + si_off + (ws - si_start)),
                        end=max(0.0, seg_offset + si_off + (min(we, si_end) - si_start)),
                    ))
                    break  # each word belongs to at most one sub-interval

        # Record each sub-interval individually so source_to_output() correctly
        # accounts for filler gaps when converting source timestamps (zoom_plan,
        # keep_segments, script_structure) to output positions.
        for _, si_start, si_end in sub_parts:
            source_intervals.append((si_start, si_end))
        parts.append(part)
        _total_sub_parts_count += len(sub_parts)
        cum += sub_cum
        print(
            f"[PRETRIM] seg[{i}] source {s_padded:.3f}-{e_padded:.3f}"
            f" ({e_padded - s_padded:.3f}s, {len(sub_parts)} sub-part(s))",
            flush=True,
        )

    # Invariant: pairwise clamp guarantees no overlap — if this fires it's a bug.
    for _oi in range(len(source_intervals) - 1):
        _oi_end = source_intervals[_oi][1]
        _oi1_start = source_intervals[_oi + 1][0]
        if _oi_end > _oi1_start + 0.005:
            raise RuntimeError(
                f"[PRETRIM-OVERLAP] BUG: interval[{_oi}].end={_oi_end:.3f}"
                f" > interval[{_oi + 1}].start={_oi1_start:.3f}"
                f" (overlap={_oi_end - _oi1_start:.3f}s)"
                f" — invariant violated after pairwise clamp"
            )

    if not parts:
        raise RuntimeError("No segments produced any clip.")

    # ── Concat all parts into one continuous video ────────────────────
    concat_path = work_dir / "trimmed_concat.mp4"
    if len(parts) == 1:
        parts[0].rename(concat_path)
    else:
        concat_list = work_dir / "concat_list.txt"
        concat_list.write_text(
            "\n".join(f"file '{p.name}'" for p in parts),
            encoding="utf-8",
        )
        _run([
            FFMPEG_PATH, "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            str(concat_path),
        ])
        concat_list.unlink(missing_ok=True)

    _concat_actual = _probe_duration(concat_path)
    _source_dur_ref = source_duration if use_source_coords else src_duration
    _filler_cut = _planned_dur - cum
    _gap_excl    = _source_dur_ref - _planned_dur
    _ecart       = _concat_actual - cum
    print(
        f"[PRETRIM-AUDIT] source={_source_dur_ref:.3f}s"
        f" | kept(pad)={_planned_dur:.3f}s | gap(excl)={_gap_excl:.3f}s",
        flush=True,
    )
    print(
        f"[PRETRIM-AUDIT] filler-cut={_filler_cut:.3f}s"
        f" | net-content={cum:.3f}s | concat-actual={_concat_actual:.3f}s"
        f" | écart={_ecart:+.3f}s",
        flush=True,
    )
    # AAC encodes in 1024-sample frames (~23ms at 44.1 kHz) and each libx264
    # sub-part rounds up to the next video frame (~33ms at 30 fps). Each clip
    # contributes up to ~33ms of codec overhead — expected, not a budget error.
    _aac_budget = _total_sub_parts_count * 0.025
    _ecart_residual = _ecart - _aac_budget
    # stable-ts boundary refinements shift drop edges by small amounts, causing
    # the segment planner to emit one extra sub-part and accumulate ~50ms of
    # additional codec overhead per refined repetition cut.
    import os as _os_audit
    _stable_ts_on = _os_audit.getenv("STABLE_TS_REPAIR", "false").lower() == "true"
    _n_llm_rep = (
        sum(1 for d in (filler_drops or []) if d.reason.startswith("llm_repetition"))
        if _stable_ts_on else 0
    )
    _stable_ts_budget = _n_llm_rep * 0.050
    _residual_budget  = 0.100 + _stable_ts_budget
    print(
        f"[PRETRIM-AUDIT] sub-parts={_total_sub_parts_count}"
        f" | codec-budget={_aac_budget:.3f}s"
        f" | residual={_ecart_residual:+.3f}s"
        f" | stable-ts={_stable_ts_on} llm-rep={_n_llm_rep}"
        f" stable-budget={_stable_ts_budget:.3f}s"
        f" | residual-budget={_residual_budget:.3f}s",
        flush=True,
    )
    if _ecart_residual > _residual_budget or _ecart < -0.100:
        raise RuntimeError(
            f"[PRETRIM-AUDIT] BUDGET ERROR: écart={_ecart:+.3f}s"
            f" residual={_ecart_residual:+.3f}s > budget={_residual_budget:.3f}s"
            f" (codec={_aac_budget:.3f}s/{_total_sub_parts_count}sp"
            f" stable-ts={_stable_ts_budget:.3f}s/{_n_llm_rep}cuts,"
            f" net={cum:.3f}s actual={_concat_actual:.3f}s)"
        )

    _output_verify(concat_path, remapped_words, work_dir)

    # Post-render word accounting — belt-and-suspenders after pre-render repair.
    # Only checks inter-segment gap words (pre/post-plan exclusions are OK).
    _pr_pool = (source_words if use_source_coords else all_words) or []
    _pr_lost = 0
    for _sw in _pr_pool:
        _ws  = float(_sw.get("start", 0))
        _we  = float(_sw.get("end", 0))
        _txt = str(_sw.get("text", "")).strip()
        if not _txt or (_we - _ws) < 0.030:
            continue
        if any(si[0] - 0.010 <= _ws and _we <= si[1] + 0.010 for si in source_intervals):
            continue
        if any(d.start - 0.010 <= _ws and _we <= d.end + 0.010 for d in (filler_drops or [])):
            continue
        # Only flag inter-segment gaps (not pre/post-plan exclusions)
        _pr_before = any(si[1] <= _ws + 0.010 for si in source_intervals)
        _pr_after  = any(si[0] >= _we - 0.010 for si in source_intervals)
        if not (_pr_before and _pr_after):
            continue
        _pr_lost += 1
        print(f"[WORD-LOST] POST-RENDER '{_txt}' at {_ws:.2f}-{_we:.2f}s — unaccounted", flush=True)
    if _pr_lost == 0:
        print(f"[WORD-LOST] POST-RENDER PASS — {len(_pr_pool)} words all accounted for", flush=True)
    else:
        raise RuntimeError(
            f"[WORD-LOST] POST-RENDER: {_pr_lost} inter-segment word(s) unaccounted"
            f" — pre-render repair missed them"
        )

    # Clean up part files
    for p in parts:
        p.unlink(missing_ok=True)

    # ── Intelligent pause compression + acoustic-stutter removal ────────────
    output_path = concat_path
    _compressed: list[tuple[float, float]] | None = None

    if remapped_words:
        _smart_cuts   = _smart_pause_cuts(remapped_words)
        _stutter_cuts = _acoustic_stutter_cuts(
            concat_path, remapped_words, plan.key_lines or [], work_dir
        )
        # Merge and sort all micro-cuts; de-overlap adjacent/overlapping intervals.
        _all_cuts: list[tuple[float, float]] = sorted(
            _smart_cuts + _stutter_cuts, key=lambda c: c[0]
        )
        _merged_cuts: list[tuple[float, float]] = []
        for _cs, _ce in _all_cuts:
            if _merged_cuts and _cs < _merged_cuts[-1][1]:
                _merged_cuts[-1] = (_merged_cuts[-1][0], max(_merged_cuts[-1][1], _ce))
            else:
                _merged_cuts.append((_cs, _ce))

        if _merged_cuts:
            compressed_path = work_dir / "trimmed_compressed.mp4"
            compressed_intervals = _compress_pauses(
                concat_path, compressed_path, work_dir,
                _merged_cuts, max_silence_s=0.0,
            )
            if compressed_path.exists():
                output_path = compressed_path
                remapped_words = [
                    WordTiming(
                        text=w.text,
                        start=_remap_time(w.start, compressed_intervals),
                        end=_remap_time(w.end, compressed_intervals),
                    )
                    for w in remapped_words
                ]
                _compressed = compressed_intervals

    output_duration = _probe_duration(output_path)
    print(f"[PRETRIM] Output: {output_path.name} ({output_duration:.1f}s, {len(remapped_words)} words)")

    # ── Re-encode with dense keyframes for HyperFrames seekability ───
    final_path = work_dir / "trimmed.mp4"
    video_info = _probe_video_info(output_path)
    fps = 30
    _run([
        FFMPEG_PATH, "-y", "-loglevel", "error",
        "-i", str(output_path),
        "-r", str(fps), "-vsync", "cfr",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-g", str(fps), "-keyint_min", str(fps),
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "192k",
        str(final_path),
    ])

    if output_path != concat_path:
        output_path.unlink(missing_ok=True)
    concat_path.unlink(missing_ok=True)

    timing_map = TimingMap(
        source_intervals=source_intervals,
        compressed_intervals=_compressed,
        output_duration=_probe_duration(final_path),
        remapped_words=remapped_words,
    )

    return final_path, timing_map
