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

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent.planner import EditPlan
from app.engine.render import (
    _dedup_segments,
    _merge_short_segments,
    _fix_word_boundaries,
    _flat_words,
    _snap_to_word_boundary,
    _extend_for_semantic_completeness,
    _find_long_pauses,
    _compress_pauses,
    _probe_duration,
    _probe_video_info,
    _run,
    _remap_time,
    SHORT_PAD_S,
    LONG_PAD_S,
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


def pretrim(
    src: Path,
    transcript: dict[str, Any],
    plan: EditPlan,
    work_dir: Path,
) -> tuple[Path, TimingMap]:
    """Cut dead content from source, produce continuous trimmed video.

    Returns (trimmed_path, timing_map).
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    short_form = plan.format == "short"
    pad = SHORT_PAD_S if short_form else LONG_PAD_S

    all_words = [
        w for seg in transcript.get("segments", [])
        for w in seg.get("words", [])
    ]
    words = _flat_words(transcript)
    src_duration = float(transcript.get("duration", 0.0)) or _probe_duration(src)

    # ── Segment preparation (same logic as render.py) ────────────────
    keep = plan.keep_segments or [
        {"start": 0.0, "end": src_duration}
    ]
    keep = _dedup_segments(keep)
    keep = _merge_short_segments(keep)
    keep = _fix_word_boundaries(keep, all_words)
    keep = _merge_short_segments(keep)

    # ── Cut each segment with word-boundary snapping ─────────────────
    parts: list[Path] = []
    source_intervals: list[tuple[float, float]] = []
    remapped_words: list[WordTiming] = []
    cum = 0.0

    for i, seg in enumerate(keep):
        s_raw = float(seg["start"])
        e_raw = float(seg["end"])
        if e_raw <= s_raw:
            continue

        s = _snap_to_word_boundary(s_raw, words, edge="start")
        e = _snap_to_word_boundary(e_raw, words, edge="end")
        e = _extend_for_semantic_completeness(e, transcript, src_duration)
        if i + 1 < len(keep):
            next_s = float(keep[i + 1]["start"])
            e = min(e, max(s + 0.15, next_s - 0.05))

        s_padded = max(0.0, s - pad)
        e_padded = min(src_duration, e + pad) if src_duration > 0 else e + pad
        if e_padded - s_padded < 0.15:
            continue

        part = work_dir / f"trim_{i:04d}.mp4"
        _run([
            FFMPEG_PATH, "-y", "-loglevel", "error",
            "-ss", f"{s_padded:.6f}", "-accurate_seek",
            "-i", str(src),
            "-t", f"{e_padded - s_padded:.6f}",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
            "-c:a", "aac", "-b:a", "192k",
            "-avoid_negative_ts", "make_zero",
            str(part),
        ])

        if not part.exists():
            continue

        seg_offset = cum
        seg_dur = e_padded - s_padded

        # Remap words using direct offset (proven 0.0ms drift)
        words_in_range = [
            w for w in all_words
            if (s_raw - 0.05) <= float(w["start"]) < (e + 0.05)
        ]
        for w in words_in_range:
            ws = float(w["start"])
            we = float(w["end"])
            remapped_words.append(WordTiming(
                text=w["text"].strip(),
                start=max(0.0, seg_offset + (ws - s_padded)),
                end=max(0.0, seg_offset + (we - s_padded)),
            ))

        source_intervals.append((s_padded, e_padded))
        parts.append(part)
        cum += seg_dur

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

    # Clean up part files
    for p in parts:
        p.unlink(missing_ok=True)

    # ── Pause compression ────────────────────────────────────────────
    sil_min = 0.50 if short_form else 0.80
    sil_max = 0.30 if short_form else 0.50
    output_path = concat_path
    _compressed: list[tuple[float, float]] | None = None

    if remapped_words:
        long_pauses = _find_long_pauses(remapped_words, min_gap_s=sil_min)
        if long_pauses:
            compressed_path = work_dir / "trimmed_compressed.mp4"
            compressed_intervals = _compress_pauses(
                concat_path, compressed_path, work_dir,
                long_pauses, max_silence_s=sil_max,
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
