"""Multi-Speaker Detection — Feature 8.

Uses FFmpeg silencedetect + astats to identify speaker changes without
external diarization libraries. Produces SpeakerSegment objects with:
  - Speaker labels (Speaker A, Speaker B, etc.)
  - Lower-third overlay specs (drawbox + drawtext for names)
  - Camera position hints (left/right/center zoom target)

Approach:
  1. silencedetect -30dB 0.3s → boundary candidates
  2. astats per candidate → RMS + zero_crossing estimate pitch proxy
  3. Cluster boundaries by pitch proximity → speaker IDs
  4. Emit 0.1s jump-cut markers at speaker changes
  5. Lower-third: dark rect + #FF7751 bar, visible for 2.5s
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.engine.transcribe import FFMPEG_PATH, FFPROBE_PATH


_MIN_SILENCE_DB  = -30
_MIN_SILENCE_DUR = 0.3   # seconds — minimum silence for a potential boundary
_PITCH_CLUSTER_THRESH = 0.3  # normalized distance for same-speaker clustering


@dataclass
class SpeakerSegment:
    start: float
    end: float
    speaker_id: str          # "A", "B", "C" …
    camera_pos: str          # "left" | "right" | "center"
    lower_third: bool = True # show name overlay for first appearance
    pitch_proxy: float = 0.0

    def lower_third_filter(self, target_w: int, target_h: int, t0: float, t1: float) -> str:
        """Returns FFmpeg filters for speaker name lower-third overlay."""
        name = f"Speaker {self.speaker_id}"
        bar_h = max(36, target_h // 20)
        bar_y = target_h - bar_h - max(20, target_h // 25)
        bar_w = max(200, target_w // 3)
        bar_x = 20
        fs    = max(14, min(22, target_h // 30))
        enable = f"between(t,{t0:.3f},{min(t0 + 2.5, t1):.3f})"

        return ",".join([
            # Dark background bar.
            f"drawbox=x={bar_x}:y={bar_y}:w={bar_w}:h={bar_h}:"
            f"color=0x0A0A0A@0.90:t=fill:enable={enable}",
            # Salmon accent strip.
            f"drawbox=x={bar_x}:y={bar_y}:w=4:h={bar_h}:"
            f"color=0xFF7751@1.0:t=fill:enable={enable}",
            # Speaker name.
            f"drawtext=text={_esc_spk(name)}:"
            f"fontsize={fs}:fontcolor=white:"
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            f"x={bar_x + 10}:y={bar_y + (bar_h - fs) // 2}:enable={enable}",
        ])



def _esc_spk(text: str) -> str:
    """Escape text for FFmpeg drawtext (no shell, no single-quote wrapping)."""
    return (text.replace("\\", "\\\\")
                .replace("'", "\\'")
                .replace(":", "\\:")
                .replace("%", "\\%"))

class SpeakerDetector:
    """Detects speaker boundaries in a media file."""

    def detect(
        self,
        media_path: Path,
        transcript_segments: list[dict[str, Any]] | None = None,
    ) -> list[SpeakerSegment]:
        """
        Returns list of SpeakerSegment objects, sorted by start time.
        Falls back to a single-speaker result on any error.
        """
        try:
            boundaries = self._find_boundaries(media_path)
            if not boundaries:
                return self._single_speaker(media_path)

            profiles = self._pitch_profiles(media_path, boundaries)
            segments = self._cluster_to_speakers(boundaries, profiles)
            return segments
        except Exception:
            return self._single_speaker(media_path)

    def _find_boundaries(self, path: Path) -> list[float]:
        """Return timestamps of silence boundaries."""
        result = subprocess.run(
            [
                FFMPEG_PATH, "-y", "-loglevel", "error",
                "-i", str(path),
                "-af", f"silencedetect=noise={_MIN_SILENCE_DB}dB:d={_MIN_SILENCE_DUR}",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=120,
        )
        boundaries: list[float] = []
        for line in (result.stdout + result.stderr).splitlines():
            m = re.search(r"silence_end:\s*([\d.]+)", line)
            if m:
                boundaries.append(float(m.group(1)))
        return sorted(set(boundaries))

    def _pitch_profiles(self, path: Path, boundaries: list[float]) -> list[float]:
        """Estimate pitch proxy (zero-crossing rate) for each segment between boundaries."""
        profiles: list[float] = []
        times = [0.0] + boundaries

        for i in range(len(times) - 1):
            t_start = times[i]
            t_end   = times[i + 1]
            dur = t_end - t_start
            if dur < 0.2:
                profiles.append(0.5)
                continue
            try:
                with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
                    stats_path = f.name
                subprocess.run(
                    [
                        FFMPEG_PATH, "-y", "-loglevel", "error",
                        "-ss", str(t_start), "-t", str(min(dur, 5.0)),
                        "-i", str(path),
                        "-af", f"astats=metadata=1:reset=44100,"
                               f"ametadata=print:file={stats_path}",
                        "-f", "null", "-",
                    ],
                    capture_output=True, timeout=30,
                )
                text = Path(stats_path).read_text(errors="ignore")
                Path(stats_path).unlink(missing_ok=True)
                # Extract Zero_crossings_rate as pitch proxy.
                zcr_match = re.search(r"Zero_crossings_rate=([\d.]+)", text)
                if zcr_match:
                    profiles.append(float(zcr_match.group(1)))
                else:
                    profiles.append(0.5)
            except Exception:
                profiles.append(0.5)

        return profiles

    def _cluster_to_speakers(
        self,
        boundaries: list[float],
        profiles: list[float],
    ) -> list[SpeakerSegment]:
        """Assign speaker IDs by clustering pitch profiles."""
        times = [0.0] + boundaries
        segments: list[SpeakerSegment] = []

        # Simple nearest-centroid clustering (online, no sklearn dependency).
        centroids: list[float] = []
        speaker_ids: list[str] = []
        label_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

        seen_speakers: set[str] = set()

        for i, profile in enumerate(profiles):
            if not centroids:
                sid = label_chars[0]
                centroids.append(profile)
                speaker_ids.append(sid)
            else:
                dists = [abs(profile - c) for c in centroids]
                best_i = min(range(len(dists)), key=lambda k: dists[k])
                if dists[best_i] < _PITCH_CLUSTER_THRESH:
                    sid = label_chars[best_i % len(label_chars)]
                    # Update centroid.
                    centroids[best_i] = (centroids[best_i] + profile) / 2
                else:
                    if len(centroids) < 4:  # max 4 speakers
                        centroids.append(profile)
                        sid = label_chars[len(centroids) - 1]
                    else:
                        sid = label_chars[best_i % len(label_chars)]
                speaker_ids.append(sid)

            seg_start = times[i]
            seg_end   = times[i + 1] if i + 1 < len(times) else seg_start + 999

            # Camera position: alternate left/right for different speakers, center for same.
            if i == 0:
                cam = "center"
            else:
                cam = "right" if i % 2 == 0 else "left"

            show_lower = sid not in seen_speakers
            seen_speakers.add(sid)

            segments.append(SpeakerSegment(
                start=seg_start,
                end=seg_end,
                speaker_id=sid,
                camera_pos=cam,
                lower_third=show_lower,
                pitch_proxy=profile,
            ))

        return segments

    def _single_speaker(self, path: Path) -> list[SpeakerSegment]:
        """Fallback: entire video is a single speaker."""
        try:
            result = subprocess.check_output(
                [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                text=True,
            )
            dur = float(result.strip())
        except Exception:
            dur = 0.0
        return [SpeakerSegment(
            start=0.0, end=dur,
            speaker_id="A", camera_pos="center",
            lower_third=False,
        )]
