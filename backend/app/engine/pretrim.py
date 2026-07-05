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


def _smart_pause_cuts(
    words: list[WordTiming],
) -> list[tuple[float, float]]:
    """Return (excess_start, excess_end) intervals for intelligent pause compression.

    Each interval is the portion of a pause to physically cut. Pass the result to
    _compress_pauses with max_silence_s=0.0 to remove exactly the excess.
    Mirrors the virtual-drop logic in RhythmAwareSilenceRemover.process().
    """
    cuts: list[tuple[float, float]] = []
    for i in range(len(words) - 1):
        gap_start = words[i].end
        gap_end   = words[i + 1].start
        gap_dur   = gap_end - gap_start

        if gap_dur < _PAUSE_TOUCH_THRESHOLD:
            continue
        if i == len(words) - 2:              # final gap before last word
            continue

        is_sentence_end = words[i].text.rstrip().endswith((".", "?", "!"))

        if is_sentence_end:
            if gap_dur <= _PAUSE_LONG_THRESHOLD:
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


def pretrim(
    src: Path,
    transcript: dict[str, Any],
    plan: EditPlan,
    work_dir: Path,
    *,
    filler_drops: list[DropSegment] | None = None,
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

        # Split this segment around any filler drops that fall within it.
        sub_intervals = _subtract_fillers(s_padded, e_padded, filler_drops or [])

        # Cut each sub-interval with the same fast lossless encoding used for segments.
        sub_parts: list[Path] = []
        for j, (si_start, si_end) in enumerate(sub_intervals):
            if si_end - si_start < 0.05:
                continue
            sub_part = work_dir / f"trim_{i:04d}_{j:02d}.mp4"
            _run([
                FFMPEG_PATH, "-y", "-loglevel", "error",
                "-ss", f"{si_start:.6f}", "-accurate_seek",
                "-i", str(src),
                "-t", f"{si_end - si_start:.6f}",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
                "-c:a", "aac", "-b:a", "192k",
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
        words_in_range = [
            w for w in all_words
            if (s_raw - 0.05) <= float(w["start"]) < (e + 0.05)
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
        cum += sub_cum

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

    # ── Intelligent pause compression ────────────────────────────────
    output_path = concat_path
    _compressed: list[tuple[float, float]] | None = None

    if remapped_words:
        smart_cuts = _smart_pause_cuts(remapped_words)
        if smart_cuts:
            compressed_path = work_dir / "trimmed_compressed.mp4"
            compressed_intervals = _compress_pauses(
                concat_path, compressed_path, work_dir,
                smart_cuts, max_silence_s=0.0,
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
