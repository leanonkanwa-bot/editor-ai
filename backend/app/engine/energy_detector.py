"""Speaker Energy Detection — Feature 4.

Uses FFmpeg astats + word timestamps to classify 3-second windows of audio
as HIGH / MEDIUM / LOW energy. The resulting energy map is passed to the
renderer to adjust zoom speed, caption emphasis, and graphic frequency.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.engine.transcribe import FFMPEG_PATH, FFPROBE_PATH


_WINDOW_S = 3.0  # seconds per energy window


@dataclass
class EnergyPoint:
    at: float           # window start time (seconds)
    duration: float     # window length (seconds)
    rms_db: float       # RMS level in dB (negative, e.g. -12.0)
    speech_rate: float  # words per second in this window
    level: str          # "HIGH" | "MEDIUM" | "LOW"


class EnergyDetector:
    """Analyses an audio/video file and returns per-window energy data."""

    def analyze(
        self,
        media_path: Path,
        word_timestamps: list[dict[str, Any]] | None = None,
    ) -> list[EnergyPoint]:
        """
        Returns a list of EnergyPoint objects for each 3-second window.
        Gracefully falls back to empty list on any FFmpeg error.
        """
        try:
            rms_map = self._extract_rms(media_path)
            rate_map = self._speech_rate_map(word_timestamps or [])
            return self._classify(rms_map, rate_map)
        except Exception:
            return []

    def _extract_rms(self, media_path: Path) -> dict[float, float]:
        """Run FFmpeg astats and parse per-window RMS values."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            stats_path = f.name

        try:
            subprocess.run(
                [
                    FFMPEG_PATH, "-y", "-loglevel", "error",
                    "-i", str(media_path),
                    "-af", f"astats=metadata=1:reset={int(_WINDOW_S * 44100)},"
                           f"ametadata=print:file={stats_path}",
                    "-f", "null", "-",
                ],
                capture_output=True,
                timeout=120,
            )
        except Exception:
            return {}

        rms_map: dict[float, float] = {}
        try:
            text = Path(stats_path).read_text(errors="ignore")
        except OSError:
            return {}

        current_pts: float | None = None
        for line in text.splitlines():
            pts_m = re.match(r"pts_time:([\d.]+)", line)
            if pts_m:
                current_pts = float(pts_m.group(1))
            rms_m = re.match(r"lavfi\.astats\.Overall\.RMS_level=(-?[\d.]+)", line)
            if rms_m and current_pts is not None:
                win_key = round(current_pts / _WINDOW_S) * _WINDOW_S
                rms_val = float(rms_m.group(1))
                if rms_val > -100:  # skip inf/-inf silence
                    rms_map[win_key] = rms_val
                current_pts = None

        try:
            Path(stats_path).unlink(missing_ok=True)
        except OSError:
            pass

        return rms_map

    def _speech_rate_map(self, words: list[dict[str, Any]]) -> dict[float, float]:
        """Count words per 3-second window."""
        counts: dict[float, int] = {}
        for w in words:
            try:
                t = float(w["start"])
            except (KeyError, TypeError, ValueError):
                continue
            key = round(t / _WINDOW_S) * _WINDOW_S
            counts[key] = counts.get(key, 0) + 1
        return {k: v / _WINDOW_S for k, v in counts.items()}

    def _classify(
        self,
        rms_map: dict[float, float],
        rate_map: dict[float, float],
    ) -> list[EnergyPoint]:
        all_keys = sorted(set(list(rms_map.keys()) + list(rate_map.keys())))
        if not all_keys:
            return []

        rms_values  = [v for v in rms_map.values() if v > -80]
        rate_values = [v for v in rate_map.values() if v > 0]

        rms_median  = _median(rms_values)  if rms_values  else -30.0
        rate_median = _median(rate_values) if rate_values else 2.0

        points: list[EnergyPoint] = []
        for key in all_keys:
            rms  = rms_map.get(key, -60.0)
            rate = rate_map.get(key, 0.0)

            # Score: 0-10
            rms_score  = _clamp((rms  - (rms_median  - 15)) / 20, 0.0, 1.0)
            rate_score = _clamp((rate - (rate_median -  1)) /  3, 0.0, 1.0)
            score = 0.6 * rms_score + 0.4 * rate_score

            if score >= 0.65:
                level = "HIGH"
            elif score >= 0.35:
                level = "MEDIUM"
            else:
                level = "LOW"

            points.append(EnergyPoint(
                at=key,
                duration=_WINDOW_S,
                rms_db=rms,
                speech_rate=rate,
                level=level,
            ))

        return points


def get_energy_at(energy_map: list[EnergyPoint], t: float) -> EnergyPoint | None:
    """Return the EnergyPoint whose window contains time t."""
    for ep in energy_map:
        if ep.at <= t < ep.at + ep.duration:
            return ep
    return None


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
