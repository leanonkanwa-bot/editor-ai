"""Template Memory Engine — Feature 1.

Analyses an existing edited video to extract its editing fingerprint,
stores it as a JSON template, and applies it to future render configs.

Analysis uses FFmpeg scene detection, astats, and signalstats.
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import BACKEND_DIR
from app.engine.transcribe import FFMPEG_PATH, FFPROBE_PATH

TEMPLATES_DIR = BACKEND_DIR / "storage" / "templates"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class TemplateStyle:
    avg_cuts_per_minute: float = 12.0
    min_cut_duration: float = 1.5
    max_cut_duration: float = 6.0
    zoom_intensity: str = "medium"       # subtle / medium / aggressive
    zoom_style: str = "mixed"            # slow_in / punch / mixed
    caption_style: str = "one_word"      # one_word / phrase / full_sentence
    caption_position: str = "center"     # top / center / bottom
    pacing: str = "medium"               # fast / medium / slow
    energy_level: str = "medium"         # high / medium / low
    energy_variance: str = "dynamic"     # consistent / dynamic
    graphics_per_minute: float = 2.0
    graphic_density: str = "medium"      # low / medium / high
    color_temperature: str = "neutral"   # warm / neutral / cool
    contrast_level: str = "medium"       # low / medium / high
    pattern_interrupt_frequency: float = 8.0
    broll_frequency: float = 0.5


@dataclass
class TemplateRulesOverride:
    pause_threshold: float = 0.3
    zoom_start: float = 1.0
    zoom_end: float = 1.08
    punch_in_scale: float = 1.15
    caption_size_pct: float = 0.07


class TemplateAnalyzer:
    """Analyses a video and extracts its editing fingerprint as a reusable template."""

    def analyze_video(self, video_path: Path, template_name: str) -> dict[str, Any]:
        """
        Analyses an existing edited video and extracts its editing style.
        Returns the full template dict (also saved to disk).
        """
        duration = self._probe_duration(video_path)
        cuts = self._detect_cuts(video_path, duration)
        energy = self._analyze_audio_energy(video_path)
        color = self._analyze_color(video_path)

        style = self._build_style(cuts, duration, energy, color)
        rules = self._derive_rules(style)

        template: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "name": template_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_video": video_path.name,
            "style": asdict(style),
            "rules_override": asdict(rules),
        }

        path = TEMPLATES_DIR / f"{template['id']}.json"
        path.write_text(json.dumps(template, indent=2), encoding="utf-8")
        return template

    # ── FFmpeg analysis helpers ───────────────────────────────────────────

    def _probe_duration(self, path: Path) -> float:
        try:
            out = subprocess.check_output(
                [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                text=True, timeout=30,
            )
            return float(out.strip())
        except Exception:
            return 60.0

    def _detect_cuts(self, path: Path, duration: float) -> dict[str, Any]:
        """Use scene detection to count cuts and measure durations."""
        result = subprocess.run(
            [FFMPEG_PATH, "-y", "-loglevel", "error",
             "-i", str(path),
             "-vf", "select='gt(scene,0.25)',showinfo",
             "-vsync", "vfr", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        output = result.stderr + result.stdout
        timestamps: list[float] = [0.0]
        for line in output.splitlines():
            m = re.search(r"pts_time:([\d.]+)", line)
            if m:
                timestamps.append(float(m.group(1)))
        if duration > 0:
            timestamps.append(duration)
        timestamps = sorted(set(timestamps))

        if len(timestamps) < 2:
            return {"count": 0, "avg_cuts_per_minute": 0,
                    "min_duration": 2.0, "max_duration": 10.0, "durations": []}

        durations = [timestamps[i+1] - timestamps[i]
                     for i in range(len(timestamps)-1) if timestamps[i+1] - timestamps[i] > 0.1]
        cut_count = len(durations)
        minutes    = max(0.01, duration / 60)

        return {
            "count":               cut_count,
            "avg_cuts_per_minute": round(cut_count / minutes, 2),
            "min_duration":        round(min(durations), 2) if durations else 1.5,
            "max_duration":        round(max(durations), 2) if durations else 6.0,
            "durations":           durations,
        }

    def _analyze_audio_energy(self, path: Path) -> dict[str, Any]:
        """Measure RMS energy level and variance."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            stats_path = f.name

        try:
            subprocess.run(
                [FFMPEG_PATH, "-y", "-loglevel", "error",
                 "-i", str(path),
                 "-af", f"astats=metadata=1:reset=44100,ametadata=print:file={stats_path}",
                 "-f", "null", "-"],
                capture_output=True, timeout=120,
            )
            text = Path(stats_path).read_text(errors="ignore")
            Path(stats_path).unlink(missing_ok=True)
        except Exception:
            return {"rms_mean": -25.0, "rms_variance": 5.0}

        rms_values: list[float] = []
        for line in text.splitlines():
            m = re.match(r"lavfi\.astats\.Overall\.RMS_level=(-?[\d.]+)", line)
            if m:
                v = float(m.group(1))
                if v > -80:
                    rms_values.append(v)

        if not rms_values:
            return {"rms_mean": -25.0, "rms_variance": 5.0}

        mean = sum(rms_values) / len(rms_values)
        variance = sum((x - mean) ** 2 for x in rms_values) / len(rms_values)
        return {"rms_mean": round(mean, 2), "rms_variance": round(variance, 2)}

    def _analyze_color(self, path: Path) -> dict[str, Any]:
        """Extract color temperature and contrast using signalstats."""
        result = subprocess.run(
            [FFMPEG_PATH, "-y", "-loglevel", "error",
             "-i", str(path),
             "-vf", "signalstats=stat=brng",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
        output = result.stderr + result.stdout

        # Extract YAVG (luma average) — proxy for brightness/contrast.
        yavg_values: list[float] = []
        for line in output.splitlines():
            m = re.search(r"YAVG:([\d.]+)", line)
            if m:
                yavg_values.append(float(m.group(1)))

        luma_mean = sum(yavg_values) / len(yavg_values) if yavg_values else 128.0
        return {"luma_mean": round(luma_mean, 2)}

    # ── Style derivation ──────────────────────────────────────────────────

    def _build_style(
        self,
        cuts: dict[str, Any],
        duration: float,
        energy: dict[str, Any],
        color: dict[str, Any],
    ) -> TemplateStyle:
        cpm   = cuts.get("avg_cuts_per_minute", 0)
        rms   = energy.get("rms_mean", -25.0)
        var   = energy.get("rms_variance", 5.0)
        luma  = color.get("luma_mean", 128.0)
        mins  = max(0.01, duration / 60)

        pacing = "fast" if cpm > 15 else ("slow" if cpm < 6 else "medium")
        energy_level = "high" if rms > -18 else ("low" if rms < -30 else "medium")
        energy_var   = "dynamic" if var > 40 else "consistent"
        zoom_intensity = "aggressive" if cpm > 18 else ("subtle" if cpm < 8 else "medium")
        zoom_style     = "punch" if cpm > 18 else ("slow_in" if cpm < 8 else "mixed")
        contrast_level = "high" if luma < 80 or luma > 180 else ("low" if 90 < luma < 160 else "medium")
        color_temp     = "warm" if luma < 100 else ("cool" if luma > 160 else "neutral")
        graphic_density = "high" if cpm > 15 else ("low" if cpm < 6 else "medium")

        return TemplateStyle(
            avg_cuts_per_minute=round(cpm, 2),
            min_cut_duration=round(cuts.get("min_duration", 1.5), 2),
            max_cut_duration=round(cuts.get("max_duration", 6.0), 2),
            zoom_intensity=zoom_intensity,
            zoom_style=zoom_style,
            caption_style="one_word" if pacing == "fast" else ("phrase" if pacing == "medium" else "full_sentence"),
            caption_position="bottom" if energy_level == "high" else "center",
            pacing=pacing,
            energy_level=energy_level,
            energy_variance=energy_var,
            graphics_per_minute=round(max(0.5, cpm / 6), 2),
            graphic_density=graphic_density,
            color_temperature=color_temp,
            contrast_level=contrast_level,
            pattern_interrupt_frequency=round(max(4.0, 60.0 / max(1, cpm)), 1),
            broll_frequency=round(0.3 if cpm > 15 else 0.7, 1),
        )

    def _derive_rules(self, style: TemplateStyle) -> TemplateRulesOverride:
        zoom_base = 1.0
        zoom_end = {"subtle": 1.05, "medium": 1.08, "aggressive": 1.12}.get(style.zoom_intensity, 1.08)
        punch_scale = {"subtle": 1.10, "medium": 1.15, "aggressive": 1.18}.get(style.zoom_intensity, 1.15)
        pause_thresh = {"fast": 0.2, "medium": 0.3, "slow": 0.4}.get(style.pacing, 0.3)
        caption_size = {"one_word": 0.09, "phrase": 0.07, "full_sentence": 0.055}.get(style.caption_style, 0.07)

        return TemplateRulesOverride(
            pause_threshold=pause_thresh,
            zoom_start=zoom_base,
            zoom_end=zoom_end,
            punch_in_scale=punch_scale,
            caption_size_pct=caption_size,
        )


def apply_template(template: dict[str, Any], render_config: dict[str, Any]) -> dict[str, Any]:
    """Merge template style overrides into a render config dict. Returns updated config."""
    import copy
    cfg = copy.deepcopy(render_config)
    style = template.get("style", {})
    rules = template.get("rules_override", {})

    # Caption style.
    cap_style = style.get("caption_style", "")
    if cap_style == "one_word":
        cfg.setdefault("caption_style", "impact")
    elif cap_style == "full_sentence":
        cfg.setdefault("caption_style", "kinetic")

    cap_pos = style.get("caption_position", "")
    if cap_pos:
        cfg["caption_position"] = cap_pos

    # Pacing target (stored as hint for planner).
    cfg["_template_cuts_per_minute"] = style.get("avg_cuts_per_minute", 12)
    cfg["_template_pacing"]          = style.get("pacing", "medium")
    cfg["_template_zoom_intensity"]  = style.get("zoom_intensity", "medium")
    cfg["_template_energy_level"]    = style.get("energy_level", "medium")
    cfg["_template_color_temp"]      = style.get("color_temperature", "neutral")
    cfg["_template_pattern_freq"]    = style.get("pattern_interrupt_frequency", 8.0)
    cfg["_template_rules"]           = rules

    return cfg


# ── Template CRUD helpers used by the API router ─────────────────────────────

def list_templates() -> list[dict[str, Any]]:
    templates = []
    for p in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            t = json.loads(p.read_text(encoding="utf-8"))
            style = t.get("style", {})
            templates.append({
                "id":    t["id"],
                "name":  t.get("name", "Unnamed"),
                "created_at": t.get("created_at", ""),
                "style_summary": {
                    "pacing":          style.get("pacing", "medium"),
                    "zoom_intensity":  style.get("zoom_intensity", "medium"),
                    "caption_style":   style.get("caption_style", "one_word"),
                    "energy_level":    style.get("energy_level", "medium"),
                    "cuts_per_minute": style.get("avg_cuts_per_minute", 0),
                },
            })
        except Exception:
            continue
    return templates


def get_template(template_id: str) -> dict[str, Any] | None:
    p = TEMPLATES_DIR / f"{template_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def delete_template(template_id: str) -> bool:
    p = TEMPLATES_DIR / f"{template_id}.json"
    if p.exists():
        p.unlink()
        return True
    return False
