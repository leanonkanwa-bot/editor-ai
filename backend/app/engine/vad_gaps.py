from __future__ import annotations

from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000
_VAD_THRESHOLD = 0.4
_MIN_GAP_MS = 100
_MAX_GAP_MS = 2_000
_MAX_GAPS = 15  # prompt size cap


def _load_audio_mono_16k(path: Path) -> np.ndarray:
    import av  # transitive dep of faster-whisper

    container = av.open(str(path))
    resampler = av.AudioResampler(format="fltp", layout="mono", rate=SAMPLE_RATE)
    chunks: list[np.ndarray] = []
    for frame in container.decode(audio=0):
        for out in resampler.resample(frame):
            chunks.append(out.to_ndarray()[0])
    for out in resampler.resample(None):
        chunks.append(out.to_ndarray()[0])
    container.close()
    if not chunks:
        raise RuntimeError(f"No audio stream in {path}")
    return np.concatenate(chunks).astype(np.float32)


def compute_vad_gaps(src: Path, transcript: dict) -> list[dict]:
    """Return VAD gaps (internal + full_segment only, 100–2000ms) with word indices.

    Indices match the flat word-list used in _llm_editorial_cuts so the prompt
    can reference [word_before_idx]->[word_after_idx] directly.

    Returns [] on any error — never raises.
    """
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    words = [w for seg in transcript.get("segments", []) for w in seg.get("words", [])]
    if not words:
        return []

    try:
        audio = _load_audio_mono_16k(src)
    except Exception as exc:
        print(f"[VAD-GAPS] audio load error — skipped: {exc}", flush=True)
        return []

    opts = VadOptions(
        threshold=_VAD_THRESHOLD,
        min_speech_duration_ms=80,
        min_silence_duration_ms=120,
        speech_pad_ms=0,
    )
    try:
        raw = get_speech_timestamps(audio, opts, sampling_rate=SAMPLE_RATE)
    except Exception as exc:
        print(f"[VAD-GAPS] VAD error — skipped: {exc}", flush=True)
        return []

    del audio  # free ASAP — can be large for long videos

    vad_segs = [
        {"start": s["start"] / SAMPLE_RATE, "end": s["end"] / SAMPLE_RATE}
        for s in raw
    ]

    min_s = _MIN_GAP_MS / 1_000.0
    max_s = _MAX_GAP_MS / 1_000.0

    # Sort once; index = position in flat word list (matches Haiku prompt numbering)
    indexed = sorted(enumerate(words), key=lambda iw: iw[1].get("start", 0.0))

    def _before(t: float) -> tuple[int | None, str]:
        """Last word (by start order) whose end <= t."""
        ri, rt = None, ""
        for i, w in indexed:
            if w.get("end", 0.0) <= t:
                ri, rt = i, str(w.get("text", "")).strip()
        return ri, rt

    def _after(t: float) -> tuple[int | None, str]:
        """First word (by start order) whose start >= t."""
        for i, w in indexed:
            if w.get("start", 0.0) >= t:
                return i, str(w.get("text", "")).strip()
        return None, ""

    gaps: list[dict] = []

    def _emit(g_start: float, g_end: float, gap_type: str) -> None:
        dur = g_end - g_start
        if not (min_s <= dur <= max_s):
            return
        wb_i, wb_txt = _before(g_start)
        wa_i, wa_txt = _after(g_end)
        gaps.append({
            "gap_start_s":    round(g_start, 3),
            "gap_end_s":      round(g_end,   3),
            "duration_ms":    round(dur * 1_000),
            "type":           gap_type,
            "word_before_idx": wb_i,
            "word_after_idx":  wa_i,
            "context_before":  wb_txt,
            "context_after":   wa_txt,
        })

    for vad in vad_segs:
        v_start, v_end = vad["start"], vad["end"]
        seg_words = [
            (i, w) for i, w in indexed
            if w.get("end", 0.0) > v_start and w.get("start", 0.0) < v_end
        ]

        if not seg_words:
            _emit(v_start, v_end, "full_segment")
            continue

        seg_words.sort(key=lambda iw: iw[1].get("start", 0.0))
        for k in range(len(seg_words) - 1):
            _, w_cur = seg_words[k]
            _, w_nxt = seg_words[k + 1]
            g_start = max(w_cur.get("end", 0.0), v_start)
            g_end   = min(w_nxt.get("start", 0.0), v_end)
            if g_end > g_start:
                _emit(g_start, g_end, "internal")

    gaps.sort(key=lambda g: g["gap_start_s"])

    if len(gaps) > _MAX_GAPS:
        gaps = sorted(gaps, key=lambda g: g["duration_ms"], reverse=True)[:_MAX_GAPS]
        gaps.sort(key=lambda g: g["gap_start_s"])

    print(
        f"[VAD-GAPS] {len(gaps)} gap(s) (internal+full_segment, "
        f"{_MIN_GAP_MS}-{_MAX_GAP_MS}ms) from {len(vad_segs)} VAD segments",
        flush=True,
    )
    return gaps
