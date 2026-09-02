"""
The Brain. Calls Claude to turn a transcript + user instructions into an
EditPlan that the FFmpeg engine can execute.
"""

from __future__ import annotations

import base64
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from anthropic import Anthropic

from app.agent.rules import system_prompt
from app.core.config import settings

logger = logging.getLogger(__name__)


FormatHint = Literal["short", "long", "auto"]


@dataclass
class EditPlan:
    raw: dict[str, Any]

    @property
    def format(self) -> str:
        return self.raw.get("format", "short")

    @property
    def keep_segments(self) -> list[dict[str, Any]]:
        return [
            {
                **s,
                "beat":          s.get("beat") or "story",
                "zoom_level":    int(s.get("zoom_level") or 130),
                "score":         int(s.get("score") or 0),
                "caption_style": s.get("caption_style") or "normal",
            }
            for s in self.raw.get("keep_segments", [])
        ]

    @property
    def zoom_plan(self) -> list[dict[str, Any]]:
        return self.raw.get("zoom_plan", [])

    @property
    def caption_emphasis_words(self) -> list[str]:
        return [w.lower() for w in self.raw.get("caption_emphasis_words", [])]

    @property
    def broll_suggestions(self) -> list[dict[str, Any]]:
        return self.raw.get("broll_suggestions", [])

    @property
    def hyperframes(self) -> list[dict[str, Any]]:
        return self.raw.get("hyperframes", [])

    @property
    def motion_graphics(self) -> list[dict[str, Any]]:
        return self.raw.get("motion_graphics", [])

    @property
    def key_lines(self) -> list[str]:
        return self.raw.get("key_lines", [])

    @property
    def packaging(self) -> dict[str, Any]:
        return self.raw.get("packaging", {})

    @property
    def script_structure(self) -> list[dict[str, Any]]:
        return self.raw.get("script_structure", [])

    @property
    def silences(self) -> list[dict[str, Any]]:
        return self.raw.get("silences", [])

    @property
    def titres_ctr(self) -> list[str]:
        return self.raw.get("titres_ctr", [])

    @property
    def thumbnail_mot(self) -> str:
        return self.raw.get("thumbnail_mot", "")

    @property
    def visual_style_moments(self) -> list[dict[str, Any]]:
        return []  # disabled — clean professional output

    @property
    def sfx_cues(self) -> list[dict[str, Any]]:
        return self.raw.get("sfx_cues", [])

    @property
    def speed_ramps(self) -> list[dict[str, Any]]:
        return self.raw.get("speed_ramps", [])

    @property
    def music_energy(self) -> list[dict[str, Any]]:
        return self.raw.get("music_energy", [])

    @property
    def word_colors(self) -> dict[str, str]:
        return self.raw.get("word_colors", {})

    @property
    def word_categories(self) -> dict[str, str]:
        return self.raw.get("word_categories", {})

    @property
    def caption_moments(self) -> list[dict[str, Any]]:
        moments = self.raw.get("caption_moments", [])
        if moments:
            return moments
        # Auto-generate from script_structure for long-form when planner omitted caption_moments.
        # Takes the first line of each beat section as a concept caption.
        if self.raw.get("format") == "long":
            script = self.raw.get("script_structure", [])
            print(f"[CAPTIONS] format='long', script_structure has {len(script)} entries, model caption_moments={len(moments)}")
            auto: list[dict[str, Any]] = []
            for beat in script:
                lines = beat.get("lines", [])
                if not lines:
                    continue
                try:
                    start = float(beat.get("start", 0))
                    end = float(beat.get("end", start + 4.0))
                except (TypeError, ValueError):
                    continue
                if start < 15.0:
                    # Hook phase: caption every line for maximum visual engagement
                    beat_dur = max(1.0, end - start)
                    n = len(lines)
                    dur_per = beat_dur / n
                    for li, ln in enumerate(lines):
                        if not str(ln).strip():
                            continue
                        cap_s = start + li * dur_per
                        cap_e = min(cap_s + min(dur_per - 0.05, 4.0), end)
                        if cap_e <= cap_s:
                            cap_e = cap_s + 2.0
                        auto.append({
                            "start": round(cap_s, 3),
                            "end": round(cap_e, 3),
                            "text": str(ln).strip(),
                            "style": "hook" if li == 0 and start < 5.0 else "concept",
                            "emphasis_words": [],
                            "position": "center_bottom",
                        })
                else:
                    text = lines[0]
                    cap_end = min(end, start + 4.0)
                    auto.append({
                        "start": start,
                        "end": cap_end,
                        "text": text,
                        "style": "concept",
                        "emphasis_words": [],
                        "position": "bottom_center",
                    })
            if auto:
                print(f"[CAPTIONS] Auto-generated {len(auto)} caption_moments from script_structure")
                return auto
        return moments


def _decide_format(duration_s: float, hint: FormatHint) -> str:
    if hint in ("short", "long"):
        return hint
    return "short" if duration_s <= 90 else "long"


def _client() -> Anthropic:
    return Anthropic(api_key=settings.anthropic_api_key)


def _extract_video_frame(src: Path, at_s: float = 2.0) -> bytes | None:
    """Pull one frame from the video as raw JPEG bytes via ffmpeg pipe."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", str(at_s), "-i", str(src),
                "-frames:v", "1",
                "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
            ],
            capture_output=True, timeout=20,
        )
        return result.stdout if result.returncode == 0 and result.stdout else None
    except Exception:
        return None


def analyze_subject_position(src: Path) -> dict[str, float]:
    """Send a representative frame to Claude Vision and ask where the subject's
    face is. Returns safe y/x zones for graphic placement.

    Falls back to conservative portrait defaults on any error so the rest of
    the pipeline is never blocked by a Vision failure."""
    frame = _extract_video_frame(src, at_s=2.0)
    if not frame:
        return {"safe_top_y_pct": 10.0, "safe_bottom_y_pct": 72.0,
                "face_top_pct": 15.0, "face_bottom_pct": 65.0,
                "face_left_pct": 25.0, "face_right_pct": 75.0}
    try:
        frame_b64 = base64.standard_b64encode(frame).decode()
        resp = _client().messages.create(
            model=settings.effective_model,
            max_tokens=256,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": frame_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "This is a frame from a talking-head video "
                            "(portrait 9:16 or landscape 16:9).\n"
                            "I will overlay motion graphics on the video and must NOT cover "
                            "the subject's face.\n\n"
                            "Estimate the subject's face position as % of frame dimensions "
                            "(0 = top/left edge, 100 = bottom/right edge):\n"
                            "  face_top_pct    — top of the head\n"
                            "  face_bottom_pct — bottom of the chin\n"
                            "  face_left_pct   — left edge of the face\n"
                            "  face_right_pct  — right edge of the face\n\n"
                            "Then give the SAFE ZONES for overlaying graphics without "
                            "touching the face:\n"
                            "  safe_upper_y_max — highest y% that is ABOVE the head\n"
                            "  safe_lower_y_min — lowest y% that is BELOW the chin\n\n"
                            "Reply ONLY with JSON, no prose:\n"
                            '{"face_top_pct": N, "face_bottom_pct": N, '
                            '"face_left_pct": N, "face_right_pct": N, '
                            '"safe_upper_y_max": N, "safe_lower_y_min": N}'
                        ),
                    },
                ],
            }],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        start, end = text.find("{"), text.rfind("}")
        data: dict = json.loads(text[start: end + 1]) if start != -1 else {}
        return {
            "safe_top_y_pct":   float(data.get("safe_upper_y_max",  10)),
            "safe_bottom_y_pct": float(data.get("safe_lower_y_min", 72)),
            "face_top_pct":     float(data.get("face_top_pct",      15)),
            "face_bottom_pct":  float(data.get("face_bottom_pct",   65)),
            "face_left_pct":    float(data.get("face_left_pct",     25)),
            "face_right_pct":   float(data.get("face_right_pct",    75)),
        }
    except Exception:
        return {"safe_top_y_pct": 10.0, "safe_bottom_y_pct": 72.0,
                "face_top_pct": 15.0, "face_bottom_pct": 65.0,
                "face_left_pct": 25.0, "face_right_pct": 75.0}


def _build_coach_context(coach_profile: dict[str, Any] | None) -> str:
    """Build a coach profile context string to inject into the system prompt."""
    if not coach_profile:
        return ""
    lines = ["\nCOACH PROFILE — use to personalise the edit plan:"]
    if coach_profile.get("name"):
        lines.append(f"  Creator name: {coach_profile['name']}")
    if coach_profile.get("brandName"):
        lines.append(f"  Brand: {coach_profile['brandName']}")
    if coach_profile.get("role"):
        role_labels = {"coach": "Coach", "entrepreneur": "Entrepreneur", "educator": "Educator", "creator": "Content Creator"}
        lines.append(f"  Role: {role_labels.get(coach_profile['role'], coach_profile['role'])}")
    if coach_profile.get("audience"):
        lines.append(f"  Target audience: {coach_profile['audience']}")
    if coach_profile.get("offer"):
        lines.append(f"  Main offer: {coach_profile['offer']}")
    if coach_profile.get("icp"):
        lines.append(f"  Ideal client profile: {coach_profile['icp']}")
    if coach_profile.get("platforms"):
        lines.append(f"  Platforms: {', '.join(coach_profile['platforms'])}")
    if coach_profile.get("editingStyle") or coach_profile.get("editing_style"):
        style = coach_profile.get("editingStyle") or coach_profile.get("editing_style")
        lines.append(f"  Editing style: {style}")
    if coach_profile.get("font"):
        lines.append(f"  Preferred font: {coach_profile['font']}")
    pillars = coach_profile.get("pillars") or []
    pillar_strs = [p for p in pillars if p]
    if pillar_strs:
        lines.append(f"  Content pillars: {'; '.join(pillar_strs)}")
    lines.append(
        "  → Tailor the hook, segment selection, and packaging to this creator's voice, "
        "audience, and offer. Make references feel native to their brand."
    )
    return "\n".join(lines)


def analyze_narrative_map(transcript: dict[str, Any]) -> dict[str, Any]:
    """Pass 1 of chunked planning — global narrative skeleton analysis.

    Activated only for videos > 25 min. Receives the word-stripped transcript dict
    (no word-level timestamps) and returns a compact JSON map identifying:
    - The single best hook and payoff (with timestamps)
    - 3-6 major structural beats
    - Protected segments that must survive any chunk's keep/drop decisions
    - Recurring themes where only one occurrence should be kept

    Does NOT make keep/drop decisions — that is Pass 2's job.
    The returned dict is passed into each chunk's plan_edit call as context.

    Estimated cost: ~500 tokens input + 1500 output per call, once per video.
    """
    segments = transcript.get("segments", [])
    duration = float(transcript.get("duration", 0))
    language = transcript.get("language", "fr")

    # Format as readable timestamped lines — far more compact and legible than raw JSON.
    # Each line: [start-end] text  (~50-70 chars vs ~180 chars for equivalent JSON segment)
    _lines: list[str] = []
    for seg in segments:
        s = float(seg.get("start", 0))
        e = float(seg.get("end", 0))
        t = str(seg.get("text", "")).strip()
        if t:
            _lines.append(f"[{s:.1f}-{e:.1f}] {t}")
    transcript_text = "\n".join(_lines)

    system_prompt_nm = f"""\
You are a narrative structure analyst for video editing. Your job is ONLY to identify
the global narrative skeleton — not to make editing decisions.

OUTPUT: a single JSON object:
{{
  "hook_ts": <float — timestamp in seconds where the single strongest hook begins>,
  "hook_end_ts": <float — where the hook segment ends>,
  "hook_summary": "<why this is the hook, ≤15 words>",
  "payoff_ts": <float — where the main tension resolves>,
  "payoff_end_ts": <float>,
  "payoff_summary": "<≤15 words>",
  "major_beats": [
    {{"ts": <float>, "end_ts": <float>, "role": "tension|revelation|principle|story|contrast", "summary": "<≤10 words>"}}
  ],
  "protected": [
    {{"start": <float>, "end": <float>, "role": "hook|payoff|critical_context", "reason": "<≤10 words>"}}
  ],
  "recurring_themes": [
    {{"theme": "<≤4 words>", "timestamps": [<float>, ...], "keep_best_at": <float>, "reason": "<≤10 words>"}}
  ]
}}

Rules:
- major_beats: 3–6 entries, chronological order, DIFFERENT timestamps than hook/payoff
- protected: always include hook + payoff; add critical_context only if removing it breaks narrative comprehension
- recurring_themes: only themes said ≥2 times where keeping one instance is clearly better
- All timestamps must be within [0, {duration:.0f}]
- Language: {language}

Reply with ONLY the JSON object, no preamble, no explanation."""

    user_msg = (
        f"VIDEO DURATION: {duration:.0f}s ({duration/60:.1f} min)\n\n"
        f"TRANSCRIPT:\n{transcript_text}"
    )

    client = Anthropic()
    try:
        response = client.messages.create(
            model=settings.effective_model,
            max_tokens=1500,
            system=system_prompt_nm,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()
        # Reuse existing JSON extractor to handle ```json fences gracefully
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(f"analyze_narrative_map: no JSON in response: {raw[:300]}")
        return json.loads(raw[start: end + 1])
    except Exception as exc:
        print(f"[NARRATIVE-MAP] ERROR: {exc}", flush=True)
        return {}


# ── Chunked planning constants ───────────────────────────────────────────────
_CHUNK_SIZE_S      = 600.0   # 10-minute core window per chunk
_CHUNK_OVERLAP_S   = 90.0    # 45s bleed into each adjacent chunk (90s total overlap zone)
_CHUNK_THRESHOLD_S = 25 * 60  # only chunk videos >= 25 min


def _build_chunk_context(
    chunk_idx: int,
    n_chunks: int,
    chunk_start_abs: float,
    chunk_end_abs: float,
    total_duration: float,
    narrative_map: dict,
    prev_keep_segments_abs: list[dict],
    prev_zoom_end_scale: float | None = None,
    prev_chunk_plans: list[dict] | None = None,
) -> str:
    """Build the narrative context block injected into each chunk's planner prompt.

    All timestamps in this block use ABSOLUTE source time so the LLM can match
    them to the remapped transcript.  A header note clarifies the offset.
    The block is clearly framed as hints — the LLM can deviate if local content
    warrants it, keeping Pass 2 robust to imperfect Pass 1 output.
    """
    local_dur = round(chunk_end_abs - chunk_start_abs, 1)
    lines: list[str] = [
        "╔═══ CHUNKED PLANNING CONTEXT ════════════════════════════════════════╗",
        f"  Chunk {chunk_idx + 1} of {n_chunks}.",
        f"  Source window: {chunk_start_abs:.0f}s–{chunk_end_abs:.0f}s"
        f" of a {total_duration:.0f}s ({total_duration/60:.1f} min) video.",
        f"  NOTE: transcript timestamps are REMAPPED — segment at absolute t={chunk_start_abs:.0f}s",
        f"        appears as t≈0s in the transcript above. Add {chunk_start_abs:.0f}s to recover",
        f"        absolute source time if needed.",
        "╚══════════════════════════════════════════════════════════════════════╝",
        "",
    ]

    # ── Zoom continuity: carry forward the ending scale of the previous chunk ──
    if chunk_idx > 0:
        if prev_zoom_end_scale is not None:
            lines += [
                f"ZOOM CONTINUITY: the previous chunk's zoom arc ended at scale={prev_zoom_end_scale:.3f}.",
                f"  Your zoom_plan's FIRST entry MUST have \"from\": {prev_zoom_end_scale:.3f}",
                "  to prevent a visible scale jump at the chunk boundary.",
                "",
            ]
        else:
            lines += [
                "ZOOM CONTINUITY: previous chunk zoom scale unknown — start your first zoom_plan",
                "  entry with \"from\": 1.000 to be safe.",
                "",
            ]

    # ── Zoom density: ensure a continuous drift baseline covers the full window ─
    lines += [
        f"ZOOM DENSITY: this chunk spans {local_dur:.0f}s of local time (t=0 to t≈{local_dur:.0f}).",
        "  You MUST include at least one continuous drift entry that covers most of this window",
        f"  (e.g. {{\"start\": 0, \"end\": {local_dur:.0f}, \"from\": ..., \"to\": ..., \"kind\": \"drift\"}}).",
        "  Add punch_in events on top of that baseline drift. Without a baseline drift, every",
        "  uncovered second becomes a frozen static hold — the video loses all zoom motion.",
        "",
    ]

    if narrative_map:
        lines.append("GLOBAL NARRATIVE HINTS (guidance from full-video analysis — deviate if")
        lines.append("local content clearly justifies it; these are NOT hard constraints):")
        lines.append("")

        hook_ts    = narrative_map.get("hook_ts")
        hook_end   = narrative_map.get("hook_end_ts")
        hook_sum   = narrative_map.get("hook_summary", "")
        payoff_ts  = narrative_map.get("payoff_ts")
        payoff_end = narrative_map.get("payoff_end_ts")
        payoff_sum = narrative_map.get("payoff_summary", "")

        if hook_ts is not None:
            in_win = chunk_start_abs <= hook_ts < chunk_end_abs
            if in_win:
                local_ts = hook_ts - chunk_start_abs
                lines.append(f"  • GLOBAL HOOK is in THIS chunk (local t≈{local_ts:.0f}s,"
                              f" absolute {hook_ts:.0f}s). Prioritize keeping it. \"{hook_sum}\"")
            else:
                lines.append(f"  • Global hook is in another chunk (absolute t={hook_ts:.0f}s)."
                              f" Do NOT create a competing hook or open a new tension loop.")

        if payoff_ts is not None:
            in_win = chunk_start_abs <= payoff_ts < chunk_end_abs
            if in_win:
                local_ts = payoff_ts - chunk_start_abs
                lines.append(f"  • GLOBAL PAYOFF is in THIS chunk (local t≈{local_ts:.0f}s,"
                              f" absolute {payoff_ts:.0f}s). Place it in the last 20% of kept segments."
                              f" \"{payoff_sum}\"")
            else:
                lines.append(f"  • Global payoff is in another chunk (absolute t={payoff_ts:.0f}s)."
                              f" Do NOT resolve the main tension in this chunk.")

        protected = narrative_map.get("protected", [])
        in_win_protected = [
            p for p in protected
            if chunk_start_abs <= float(p.get("start", 0)) < chunk_end_abs
        ]
        if in_win_protected:
            lines.append("  • Protected segments in this window (MUST survive keep/drop):")
            for p in in_win_protected:
                abs_s, abs_e = float(p.get("start", 0)), float(p.get("end", 0))
                loc_s, loc_e = abs_s - chunk_start_abs, abs_e - chunk_start_abs
                lines.append(f"    – local [{loc_s:.0f}s–{loc_e:.0f}s]"
                              f" role={p.get('role','')} — {p.get('reason','')}")

        recurring = narrative_map.get("recurring_themes", [])
        if recurring:
            lines.append("  • Recurring themes (keep only the best occurrence):")
            for r in recurring:
                theme = r.get("theme", "")
                timestamps = r.get("timestamps", [])
                best_at = r.get("keep_best_at")
                in_win_ts = [t for t in timestamps if chunk_start_abs <= t < chunk_end_abs]
                if in_win_ts:
                    best_here = best_at is not None and chunk_start_abs <= best_at < chunk_end_abs
                    local_best = (best_at - chunk_start_abs) if best_at is not None else None
                    local_in_win = [f"{t - chunk_start_abs:.0f}s" for t in in_win_ts]
                    if best_here:
                        lines.append(f"    – \"{theme}\": best occurrence IS here"
                                     f" (local t≈{local_best:.0f}s). Keep it; cut other occurrences.")
                    else:
                        lines.append(f"    – \"{theme}\": occurs here at {local_in_win},"
                                     f" but best occurrence is elsewhere. Cut if no new info added.")
        lines.append("")

    if prev_keep_segments_abs:
        lines.append("ALREADY SELECTED by previous chunks (do not re-select these):")
        # Show the last 12 to limit context bloat
        for seg in prev_keep_segments_abs[-12:]:
            s, e = float(seg.get("start", 0)), float(seg.get("end", 0))
            beat = seg.get("beat", "")
            lines.append(f"  [{s:.0f}s–{e:.0f}s] beat={beat}")
        lines.append("")

    # ── Cross-chunk context: card anti-redundancy + narrative continuity ──────
    if chunk_idx > 0 and prev_chunk_plans:
        _cross: list[str] = []

        # Key phrases already promoted to cards across ALL previous chunks.
        # Deduplicated and capped at 10 entries to control prompt size (~600 chars).
        _used_keys: list[str] = []
        for _cp in prev_chunk_plans:
            for _kl in (_cp.get("key_lines") or []):
                if _kl and _kl not in _used_keys:
                    _used_keys.append(_kl)
        if _used_keys:
            _cross += [
                "CARD REDUNDANCY GUARD — key phrases already used as cards in previous chunks:",
                "  Do NOT repeat these verbatim or paraphrase closely as a new card:",
            ]
            for _kl in _used_keys[:10]:
                _cross.append(f'    • "{_kl}"')

        # Closing beats of the immediately preceding chunk — narrative continuity
        # at the chunk boundary so the LLM can continue the thread, not restart it.
        _last_ss = (prev_chunk_plans[-1].get("script_structure") or [])
        _tail = _last_ss[-3:] if len(_last_ss) >= 3 else _last_ss
        if _tail:
            _cross.append("NARRATIVE CONTINUITY — the previous chunk's closing beats:")
            for _b in _tail:
                _bt = _b.get("beat", "story")
                _bl = (_b.get("lines") or [""])[0][:80]
                _cross.append(f'  [{_bt}] "{_bl}"')
            _cross.append("  Continue the narrative thread naturally — don't restate or re-introduce these.")

        if _cross:
            lines += _cross + [""]

    return "\n".join(lines)


def _merge_chunk_plans(
    chunk_plans_abs: list[dict],
    chunk_ranges: list[tuple[float, float]],
) -> dict:
    """Merge all per-chunk plan fields into one unified plan.

    Strategy per field type:
    - Time-specific lists (zoom_plan, broll_suggestions, hyperframes,
      motion_graphics, silences, sfx_cues, speed_ramps, caption_moments,
      music_energy): concatenate across all chunks — each chunk covers its own
      time window, so there are no duplicates to worry about.
    - keep_segments: concatenate + 70%-overlap deduplication (overlap zones).
    - script_structure, key_lines: concatenate / dedup-append.
    - Video-wide word lists (caption_emphasis_words, titres_ctr): dedup-append.
    - word_categories (dict word→category): union, later chunks supplement chunk 1.
    - Scalars (format, packaging, thumbnail_mot, …): chunk 1 (base) wins.
    """
    accepted: list[dict] = []

    def _is_duplicate(s: float, e: float) -> bool:
        for a in accepted:
            a_s, a_e = float(a.get("start", 0)), float(a.get("end", 0))
            # Consider duplicate if intervals overlap by more than 70%
            overlap = max(0.0, min(e, a_e) - max(s, a_s))
            span = max(e - s, 0.01)
            if overlap / span > 0.70:
                return True
        return False

    total_raw = 0
    for plan in chunk_plans_abs:
        for seg in plan.get("keep_segments", []):
            if not isinstance(seg, dict):
                continue
            total_raw += 1
            s, e = float(seg.get("start", 0)), float(seg.get("end", 0))
            if not _is_duplicate(s, e):
                accepted.append(seg)

    accepted.sort(key=lambda s: float(s.get("start", 0)))

    # Use first chunk's plan as the base (carries format, packaging, thumbnail_mot, etc.)
    base = chunk_plans_abs[0] if chunk_plans_abs else {}
    merged = {**base, "keep_segments": accepted}

    # ── Time-specific lists: concatenate across all chunks ────────────────────
    # Each chunk covers a distinct time window — no duplication expected.
    _TIME_LIST_FIELDS = (
        "script_structure",   # already was aggregated; keep here for single loop
        "zoom_plan",          # LLM-directed drift/punch_in entries per time window
        "broll_suggestions",  # B-roll anchored to source timestamps
        "hyperframes",        # color flash at specific beats
        "motion_graphics",    # motion graphic overlays at specific moments
        "silences",           # silence overlay cues at specific timestamps
        "sfx_cues",           # sound-effect cues at specific timestamps
        "speed_ramps",        # speed ramp sub-segments
        "caption_moments",    # caption overlay windows
        "music_energy",       # music intensity sections
    )
    for field in _TIME_LIST_FIELDS:
        agg: list = []
        for cp in chunk_plans_abs:
            agg.extend(cp.get(field) or [])
        if agg:
            merged[field] = agg

    # ── Video-wide word lists: dedup-append ───────────────────────────────────
    kl: list[str] = []
    for cp in chunk_plans_abs:
        for line in (cp.get("key_lines") or []):
            if line not in kl:
                kl.append(line)
    if kl:
        merged["key_lines"] = kl

    ew: list[str] = []
    for cp in chunk_plans_abs:
        for w in (cp.get("caption_emphasis_words") or []):
            if w not in ew:
                ew.append(w)
    if ew:
        merged["caption_emphasis_words"] = ew

    tc: list[str] = []
    for cp in chunk_plans_abs:
        for t in (cp.get("titres_ctr") or []):
            if t not in tc:
                tc.append(t)
    if tc:
        merged["titres_ctr"] = tc

    # ── word_categories dict: union (later chunks supplement chunk 1) ─────────
    wc: dict = {}
    for cp in chunk_plans_abs:
        wc.update(cp.get("word_categories") or {})
    if wc:
        merged["word_categories"] = wc

    # ── Option A: zoom continuity repair — bridge any residual scale jumps ──────
    # Even when the LLM honours the ZOOM CONTINUITY hint (Option B), rounding or
    # non-compliance can leave a hard gap between chunk N's last `to` and chunk
    # N+1's first `from`.  Insert a short bridge drift to close any such gap.
    _BRIDGE_DUR_S = 0.8
    _JUMP_THRESH  = 0.005  # ignore sub-0.5% rounding noise
    _n_bridges    = 0
    zp_raw = merged.get("zoom_plan")
    if zp_raw and len(zp_raw) > 1:
        zp_sorted = sorted(zp_raw, key=lambda e: float(e.get("start", 0)))
        zp_repaired: list[dict] = []
        for _i, _entry in enumerate(zp_sorted):
            if _i == 0:
                zp_repaired.append(_entry)
                continue
            _prev      = zp_repaired[-1]
            _prev_to   = float(_prev.get("to",    _prev.get("from", 1.0)))
            _cur_from  = float(_entry.get("from", _entry.get("to",  1.0)))
            _gap_start = float(_prev.get("end",   _prev.get("start", 0)))
            _cur_start = float(_entry.get("start", 0))
            if abs(_cur_from - _prev_to) > _JUMP_THRESH:
                _bridge_end = min(_gap_start + _BRIDGE_DUR_S, _cur_start)
                if _bridge_end > _gap_start + 0.05:
                    zp_repaired.append({
                        "start": round(_gap_start, 4),
                        "end":   round(_bridge_end, 4),
                        "from":  round(_prev_to,   4),
                        "to":    round(_cur_from,   4),
                        "kind":  "drift",
                    })
                    _n_bridges += 1
                    print(
                        f"[ZOOM-BRIDGE] t={_gap_start:.1f}s: scale {_prev_to:.3f}→{_cur_from:.3f}"
                        f" over {_bridge_end - _gap_start:.2f}s (chunk boundary smoothing)",
                        flush=True,
                    )
            zp_repaired.append(_entry)
        merged["zoom_plan"] = zp_repaired

    n_deduped = total_raw - len(accepted)
    _merge_counts = {f: len(merged.get(f) or []) for f in _TIME_LIST_FIELDS}
    print(
        f"[CHUNK-MERGE] {len(chunk_plans_abs)} chunks,"
        f" {total_raw} raw segs → {len(accepted)} kept ({n_deduped} overlap-deduped)"
        f" | zoom={_merge_counts['zoom_plan']} (+{_n_bridges} bridge drifts)"
        f" cap_moments={_merge_counts['caption_moments']}"
        f" broll={_merge_counts['broll_suggestions']}"
        f" sfx={_merge_counts['sfx_cues']}"
        f" hf={_merge_counts['hyperframes']}",
        flush=True,
    )
    return merged


def _unremap_chunk_timestamps(raw: dict, offset: float) -> dict:
    """Shift all timestamp fields in a chunk plan from local (chunk-relative) to absolute time.

    The LLM plans each chunk with t=0 at chunk_start.  Before appending to
    chunk_plans_abs we add `offset` (= chunk_start) to every timestamp field so
    that downstream consumers (render.py zoom_plan, storyboard.py script_structure,
    etc.) all see absolute source-file timestamps.
    """
    if offset == 0.0:
        return raw  # chunk 1: local == absolute, nothing to shift

    result = dict(raw)

    # Items that carry "start" / "end" keys
    for field in ("keep_segments", "zoom_plan", "script_structure",
                  "speed_ramps", "caption_moments", "music_energy"):
        items = raw.get(field)
        if not items:
            continue
        shifted = []
        for item in items:
            if not isinstance(item, dict):
                shifted.append(item)
                continue
            entry = dict(item)
            if "start" in entry:
                entry["start"] = round(float(entry["start"]) + offset, 3)
            if "end" in entry:
                entry["end"] = round(float(entry["end"]) + offset, 3)
            shifted.append(entry)
        result[field] = shifted

    # Items that carry an "at" key
    for field in ("broll_suggestions", "hyperframes", "motion_graphics",
                  "silences", "sfx_cues"):
        items = raw.get(field)
        if not items:
            continue
        shifted = []
        for item in items:
            if not isinstance(item, dict):
                shifted.append(item)
                continue
            entry = dict(item)
            if "at" in entry:
                entry["at"] = round(float(entry["at"]) + offset, 3)
            shifted.append(entry)
        result[field] = shifted

    return result


def _plan_edit_chunked(
    transcript: dict[str, Any],
    narrative_map: dict,
    user_instructions: str,
    format_hint: FormatHint,
    brand_color: str | None,
    caption_color: str | None,
    caption_position: str | None,
    caption_font: str | None,
    subject_position: dict[str, float] | None,
    coach_profile: dict[str, Any] | None,
    editing_style: str,
) -> "EditPlan":
    """Orchestrate chunked planning for videos >= 25 min.

    Each chunk receives a remapped (local t=0) transcript so timestamps stay
    valid for the planner's guards.  After planning, segments are un-remapped
    back to absolute source time before merging.
    """
    segments = transcript.get("segments", [])
    total_duration = float(transcript.get("duration", 0))

    # Build chunk boundary list: [start, start + core + half-overlap)
    chunk_ranges: list[tuple[float, float]] = []
    cur = 0.0
    while cur < total_duration:
        end = min(cur + _CHUNK_SIZE_S + _CHUNK_OVERLAP_S / 2, total_duration)
        chunk_ranges.append((cur, end))
        if end >= total_duration:
            break
        cur += _CHUNK_SIZE_S

    print(
        f"[CHUNK-PLAN] {total_duration/60:.1f}min → {len(chunk_ranges)} chunks:"
        f" {[f'{s:.0f}-{e:.0f}s' for s, e in chunk_ranges]}",
        flush=True,
    )

    chunk_plans_abs: list[dict] = []
    prev_keep_abs: list[dict] = []

    for chunk_idx, (chunk_start, chunk_end) in enumerate(chunk_ranges):
        # Filter segments to this chunk window
        chunk_segs_abs = [
            seg for seg in segments
            if chunk_start <= float(seg.get("start", 0)) < chunk_end
        ]
        if not chunk_segs_abs:
            print(f"[CHUNK-PLAN] chunk {chunk_idx+1}/{len(chunk_ranges)}: no segments — skip", flush=True)
            continue

        # Remap timestamps to local (t=0 at chunk_start) so plan_edit guards work correctly
        chunk_segs_local = [
            {**seg,
             "start": round(float(seg["start"]) - chunk_start, 3),
             "end":   round(float(seg["end"])   - chunk_start, 3)}
            for seg in chunk_segs_abs
        ]
        local_duration = round(max(float(s["end"]) for s in chunk_segs_local), 3)

        chunk_transcript = {
            **transcript,
            "segments": chunk_segs_local,
            "duration": local_duration,
        }

        # Extract the ending scale of the previous chunk's zoom_plan so the next
        # chunk's LLM can start its arc at the correct baseline (Option B: source fix).
        _prev_zoom_end: float | None = None
        if chunk_plans_abs:
            _prev_zp = sorted(
                chunk_plans_abs[-1].get("zoom_plan") or [],
                key=lambda e: float(e.get("end", e.get("start", 0))),
            )
            if _prev_zp:
                _prev_zoom_end = float(_prev_zp[-1].get("to", 1.0))

        ctx = _build_chunk_context(
            chunk_idx=chunk_idx,
            n_chunks=len(chunk_ranges),
            chunk_start_abs=chunk_start,
            chunk_end_abs=chunk_end,
            total_duration=total_duration,
            narrative_map=narrative_map,
            prev_keep_segments_abs=prev_keep_abs,
            prev_zoom_end_scale=_prev_zoom_end,
            prev_chunk_plans=chunk_plans_abs if chunk_idx > 0 else None,
        )

        print(
            f"[CHUNK-PLAN] chunk {chunk_idx+1}/{len(chunk_ranges)}:"
            f" abs {chunk_start:.0f}–{chunk_end:.0f}s,"
            f" local 0–{local_duration:.0f}s,"
            f" {len(chunk_segs_local)} segs …",
            flush=True,
        )

        try:
            chunk_plan_local = plan_edit(
                transcript=chunk_transcript,
                user_instructions=user_instructions,
                format_hint=format_hint,
                brand_color=brand_color,
                caption_color=caption_color,
                caption_position=caption_position,
                caption_font=caption_font,
                subject_position=subject_position,
                coach_profile=coach_profile,
                editing_style=editing_style,
                _chunk_context=ctx,
                # narrative_map intentionally NOT passed — chunks always use single-pass
            )
            # Shift ALL timestamp fields from local (chunk-relative) to absolute time
            chunk_abs = _unremap_chunk_timestamps(chunk_plan_local.raw, chunk_start)
            keep_abs = [s for s in chunk_abs.get("keep_segments", []) if isinstance(s, dict)]
            chunk_plans_abs.append(chunk_abs)
            prev_keep_abs.extend(keep_abs)
            print(
                f"[CHUNK-PLAN] chunk {chunk_idx+1}: {len(keep_abs)} segs kept"
                f" (abs range {min((s['start'] for s in keep_abs), default=0):.0f}s–"
                f"{max((s['end'] for s in keep_abs), default=0):.0f}s)",
                flush=True,
            )
        except BaseException as exc:
            # BaseException (not just Exception) catches SIGTERM-triggered SystemExit
            # so a Railway deployment mid-render doesn't silently drop a chunk.
            print(
                f"[CHUNK-PLAN] chunk {chunk_idx+1} FAILED ({type(exc).__name__}): {exc}"
                f" — fallback: keep all {len(chunk_segs_abs)} segs in this window",
                flush=True,
            )
            fallback_abs = [
                {"start": seg["start"], "end": seg["end"], "beat": "story", "score": 0}
                for seg in chunk_segs_abs
            ]
            # Build a minimal script_structure so the storyboard LLM has BEAT SPINE
            # coverage for this chunk's time range (otherwise script_out in the
            # storyboard prompt is blank for this zone → LLM skips it entirely).
            _fb_ss: list[dict] = []
            _fb_g_start = chunk_segs_abs[0]["start"] if chunk_segs_abs else chunk_start
            _fb_g_lines: list[str] = []
            for _fb_seg in chunk_segs_abs:
                _fb_g_lines.append(str(_fb_seg.get("text", "")).strip())
                if float(_fb_seg["end"]) - _fb_g_start >= 60.0 or _fb_seg is chunk_segs_abs[-1]:
                    _fb_ss.append({
                        "beat": "story",
                        "lines": [" ".join(_fb_g_lines).strip()],
                        "start": _fb_g_start,
                        "end": float(_fb_seg["end"]),
                    })
                    _fb_g_start = float(_fb_seg["end"])
                    _fb_g_lines = []
            chunk_plans_abs.append({"keep_segments": fallback_abs, "script_structure": _fb_ss})
            prev_keep_abs.extend(fallback_abs)
            if isinstance(exc, (SystemExit, KeyboardInterrupt)):
                raise  # re-raise so the process can actually shut down

    if not chunk_plans_abs:
        print("[CHUNK-PLAN] all chunks failed — falling back to single-pass plan_edit", flush=True)
        return plan_edit(
            transcript=transcript,
            user_instructions=user_instructions,
            format_hint=format_hint,
            brand_color=brand_color,
            caption_color=caption_color,
            caption_position=caption_position,
            caption_font=caption_font,
            subject_position=subject_position,
            coach_profile=coach_profile,
            editing_style=editing_style,
        )

    merged = _merge_chunk_plans(chunk_plans_abs, chunk_ranges)
    return EditPlan(raw=merged)


def plan_edit(
    transcript: dict[str, Any],
    user_instructions: str,
    format_hint: FormatHint = "auto",
    brand_color: str | None = None,
    caption_color: str | None = None,
    caption_position: str | None = None,
    caption_font: str | None = None,
    subject_position: dict[str, float] | None = None,
    aesthetic: str = "high-energy",  # kept for API compat, ignored internally
    coach_profile: dict[str, Any] | None = None,
    editing_style: str = "viral",
    narrative_map: dict | None = None,
    _chunk_context: str = "",        # injected by _plan_edit_chunked; empty for top-level calls
) -> EditPlan:
    """
    Ask Claude to produce an edit plan for the given transcript.
    Returns an EditPlan with the raw JSON the model emitted.

    For videos >= 25 min: pass narrative_map (output of analyze_narrative_map) to
    activate chunked planning (_CHUNK_THRESHOLD_S check).  Single-pass is always
    used for chunks themselves (_chunk_context is set; narrative_map stays None).
    """
    if settings.is_test_model:
        logger.warning(
            "⚠️  TEST MODEL ACTIVE — using %s instead of %s. "
            "DO NOT deliver this render to a real client. "
            "Unset ANTHROPIC_MODEL_TEST in Railway to restore production quality.",
            settings.effective_model,
            settings.anthropic_model,
        )
        print(
            f"\n{'='*70}\n"
            f"⚠️  TEST MODEL ACTIVE: {settings.effective_model}\n"
            f"    Production model: {settings.anthropic_model}\n"
            f"    Unset ANTHROPIC_MODEL_TEST to restore production quality.\n"
            f"{'='*70}\n"
        )

    duration = float(transcript.get("duration", 0.0))
    fmt = _decide_format(duration, format_hint)

    # Dispatch to chunked planning when a narrative map is provided and video is long.
    # Chunks always call plan_edit without narrative_map → single-pass, no infinite recursion.
    if narrative_map is not None and duration >= _CHUNK_THRESHOLD_S:
        return _plan_edit_chunked(
            transcript=transcript,
            narrative_map=narrative_map,
            user_instructions=user_instructions,
            format_hint=format_hint,
            brand_color=brand_color,
            caption_color=caption_color,
            caption_position=caption_position,
            caption_font=caption_font,
            subject_position=subject_position,
            coach_profile=coach_profile,
            editing_style=editing_style,
        )

    # Motion graphics disabled — no face-safe-zone context needed.
    face_context = ""

    coach_context = _build_coach_context(coach_profile)

    user_msg = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": (
                    f"FORMAT TARGET: {fmt}\n"
                    f"DURATION: {duration:.2f}s\n"
                    f"LANGUAGE: {transcript.get('language', 'en')} — write ALL text fields "
                    f"(summary, reason, key_lines, titres_ctr, script_structure lines) "
                    f"in {transcript.get('language', 'en')}, matching the speaker's language.\n"
                    "PUNCTUATION: Never use the em-dash character (—) in any generated text. "
                    "Use a comma, colon, or period instead.\n"
                    f"{face_context}\n"
                    f"{coach_context}\n"
                    "PRE-ANALYSIS (do before building the plan):\n"
                    "  1. content_type: coaching | education | story | motivation\n"
                    "  2. primary_audience: who this is for (1 sentence)\n"
                    "  3. key_result: the ONE outcome the viewer gets (1 sentence)\n"
                    "  4. sentence_scoring: score EVERY candidate sentence before\n"
                    "     selecting the hook and building keep_segments (net score):\n"
                    "       POSITIVE: counterintuitive claim → +10\n"
                    "                 specific number / stat / concrete claim → +8\n"
                    "                 personal vulnerable moment → +7\n"
                    "                 physical sensation or pain → +6\n"
                    "                 story / scene / lived moment → +6\n"
                    "                 contrast / 'but' / flip / reframe → +5\n"
                    "                 time urgency ('3AM', '48 hours') → +4\n"
                    "                 story advancement → +3\n"
                    "                 connective / context → +2\n"
                    "       NEGATIVE: filler / hedge → -5\n"
                    "                 repetition of previous info → -8\n"
                    "                 greeting / goodbye / shoutout → -10\n"
                    "     hook_score = net score of the chosen hook sentence.\n"
                    "     MINIMUM ACCEPTABLE hook_score: 15.\n"
                    "     Select the highest-scoring sentence ≤ 8s long as hook_moment.\n"
                    "     The hook MUST NOT resolve the tension it creates.\n\n"
                    "HOOK FIRST — INTELLIGENT, NOT BLIND:\n"
                    "Before placing ANY segment at position 0, run these 3 tests on it:\n\n"
                    "  TEST A — STANDALONE TEST: Can a stranger understand this segment with\n"
                    "    ZERO prior context?\n"
                    "    - References something said before ('that's why', 'like I said', 'but') → FAIL\n"
                    "    - Introduces new information the viewer has no context for → FAIL\n"
                    "    - Creates curiosity entirely on its own → PASS\n\n"
                    "  TEST B — TENSION TEST: Does this segment make the viewer think\n"
                    "    'wait, what? I need to know more'?\n"
                    "    - A specific number ('3am', '10 miles') → PASS\n"
                    "    - A contradiction ('I don't run, but I ran 10 miles') → PASS\n"
                    "    - A result without its cause ('I showed up anyway') → PASS\n"
                    "    - A generic statement ('it was really hard') → FAIL\n\n"
                    "  TEST C — NO REPETITION TEST: When this segment is placed after any\n"
                    "    other segment, does the last word of the previous segment match the\n"
                    "    first word of this segment? If yes → adjust the boundary by ±0.3s.\n\n"
                    "HOOK SELECTION ALGORITHM:\n"
                    "  1. Score every candidate segment 1-10 on TEST A and 1-10 on TEST B.\n"
                    "  2. Only segments scoring 7+ on BOTH tests are hook candidates.\n"
                    "  3. If multiple candidates qualify → pick the highest combined score.\n"
                    "  4. If NO candidate scores 7+ on BOTH tests → DO NOT reorder.\n"
                    "     Output keep_segments in chronological source order — the video's\n"
                    "     natural opening IS the hook.\n\n"
                    "REORDERING REQUIREMENT (only if a hook candidate passed step 4 above):\n"
                    "After selecting segments, REORDER them for maximum psychological impact.\n"
                    "Do NOT keep source chronological order if a qualifying hook exists.\n\n"
                    "Step 1: The qualifying hook segment → place it FIRST (position 0).\n"
                    "        Even if it's at minute 5 of a 6-minute transcript.\n"
                    "Step 2: CONTEXT PRESERVATION — ask 'what does the viewer need to know\n"
                    "        next to make sense of what they just heard?' That answer is the\n"
                    "        context segment → place it SECOND (position 1-2), even if it\n"
                    "        appears before the hook in source.\n"
                    "Step 3: Build tension progressively — each segment adds new info or raises stakes.\n"
                    "        Never two consecutive segments at the same emotional flatline.\n"
                    "Step 4: Close with the payoff — the segment that answers the hook goes LAST.\n"
                    "        Never answer the hook question before the final 20% of the edit.\n\n"
                    "EXAMPLE (hook qualifies):\n"
                    "  Source: [intro@0s, context@10s, HOOK@25s, story@35s, payoff@50s]\n"
                    "  Output: [HOOK@25s, context@10s, story@35s, intro@0s, payoff@50s]\n"
                    "  (hook first, context to explain it, story raises stakes,\n"
                    "   intro repurposed as tension bridge, payoff closes the loop)\n\n"
                    "EXAMPLE (no hook qualifies):\n"
                    "  No segment scores 7+ on both standalone and tension tests.\n"
                    "  Output: [intro@0s, context@10s, HOOK@25s, story@35s, payoff@50s]\n"
                    "  (chronological order preserved — do not force a confusing reorder)\n\n"
                    "JSON format: timestamps (start/end) = SOURCE positions. Array order = EDIT sequence.\n"
                    "  [{start:25,end:30}, {start:10,end:15}] = play source 25-30s FIRST, then 10-15s SECOND.\n\n"
                    "PAYOFF PLACEMENT RULE — ABSOLUTE:\n"
                    "  Tension resolution (the answer to any open loop) MUST appear in\n"
                    "  the last 20% of the output edit duration.\n"
                    "  Example: 60s video → payoff not before t=48s.\n"
                    "  If the transcript's payoff appears early, DELAY it by reordering\n"
                    "  keep_segments — insert story or principle segments between the\n"
                    "  setup and the payoff to enforce the 20% rule.\n\n"
                    "SEGMENT SCORING: For each keep_segment add these fields:\n"
                    "  role: hook|problem|story|principle|payoff|transition\n"
                    "  score: net score from sentence_scoring above\n"
                    "         (+10 counterintuitive, +8 stat, +7 vulnerable,\n"
                    "          +6 pain/story, +5 contrast, +4 time urgency,\n"
                    "          -5 filler, -8 repetition, -10 greeting)\n"
                    "  cut_before_silence: true if breath pause ≥0.25s precedes\n"
                    "    this segment's first word (always cut at breath boundaries)\n"
                    "  retention_note: one sentence on why this earns watch time\n"
                    "Drop segments with net score ≤ 3 unless hook or payoff.\n"
                    "EXCEPTION: before dropping any segment for low score, ask: 'Does the\n"
                    "viewer lose the thread of what follows if this is removed?' If YES,\n"
                    "keep the segment regardless of score — context loss overrides score.\n"
                    "Segments with net score ≤ 0 AND no context dependency must always be cut.\n"
                    "Hook must be the highest-scoring segment in keep_segments.\n\n"
                    "LOOP TIMER: Every 15–20s of output, a new curiosity loop must open.\n"
                    "Track the output timeline — no 20s window without a new tension.\n\n"
                    "COHERENCE TEST — run after selecting all segments:\n"
                    "Read the selected segments in order. Ask: 'If someone heard ONLY these\n"
                    "segments, in this order, would they understand what happened and why it matters?'\n\n"
                    "SEGMENT DEPENDENCY CHECK — scan EVERY kept segment for these signals:\n"
                    "  PUNCHLINE / REACTION markers (segment DEPENDS on its setup):\n"
                    "    'I'm joking' / 'just kidding' / 'plot twist' / 'you felt that'\n"
                    "    'see how you felt' / 'notice what happened' / 'that's the point'\n"
                    "    'but here's the thing' / 'turns out' / 'here's what happened'\n"
                    "    Any emotional reaction to something that hasn't been shown yet\n"
                    "    → Find and keep the segment that SET UP this reaction.\n\n"
                    "  NAMED PERSON markers (segment DEPENDS on an introduction):\n"
                    "    Segment addresses someone by first name (Owen, Arda, Sarah…)\n"
                    "    Segment says 'you' to a visible specific person on screen\n"
                    "    → Find and keep the segment that INTRODUCES that person.\n\n"
                    "  ANSWER-WITHOUT-QUESTION markers (segment DEPENDS on its question):\n"
                    "    Segment is clearly an answer: 'A basketball player.' / 'Three years.'\n"
                    "    Short affirm/deny: 'No.' / 'Yes.' / 'Because of you.'\n"
                    "    → Find and keep the QUESTION segment that prompted this answer.\n\n"
                    "  REFERENCE markers (segment DEPENDS on what it references):\n"
                    "    'just now' / 'that moment' / 'what I said' / 'what happened'\n"
                    "    → Find and keep the segment being referenced.\n\n"
                    "The segments must collectively deliver:\n"
                    "  1. SITUATION — WHO is speaking and WHAT is the context (1 segment minimum)\n"
                    "  2. TENSION   — WHAT problem, conflict, or challenge arose\n"
                    "  3. STRUGGLE  — HOW it felt (emotional reality, not just facts)\n"
                    "  4. RESOLUTION — WHY it matters, what changed, what the viewer should take away\n\n"
                    "If any of these 4 elements is missing → add the best available segment\n"
                    "covering it, even if its individual score is lower than other segments.\n"
                    "A high-scoring clip that makes no narrative sense is worthless.\n\n"
                    "AUDIO-ONLY TEST: Read the kept transcript text aloud (no visuals).\n"
                    "If a first-time listener would not understand the core story → revise\n"
                    "keep_segments until the audio version makes complete narrative sense.\n\n"
                    + (
                    "PRIESTLEY STYLE ACTIVE — apply Daniel Priestley hook structure:\n"
                    "  [0:00–0:02] Pattern interrupt — most shocking claim first\n"
                    "              Start with the conclusion, not the setup\n"
                    "  [0:02–0:10] Problem identification — address viewer's pain directly\n"
                    "              ('If you're still doing X, here's what happens...')\n"
                    "  [0:10–0:25] Proof by data — one specific credible statistic\n"
                    "              Must be real or highly plausible. Cite a source if possible.\n"
                    "  [0:25–0:45] The alternative — transition from warning to opportunity\n"
                    "              ('But the good news is...')\n\n"
                    "Caption moments for Priestley style:\n"
                    "  Generate title cards for the hook statement, key statistics, chapter titles.\n"
                    "  Use style='hook' for the opening statement, style='stat' for numbers.\n"
                    "  Title card text: SHORT (2–5 words max), UPPERCASE.\n"
                    "  Example: 'THE TIME IS OVER' / '$200 PER YEAR' / 'REPACKAGE YOUR VALUE'\n\n"
                    "B-roll suggestions for Priestley style (MANDATORY):\n"
                    "  search_query MUST include: professional, business, entrepreneur, office, executive\n"
                    "  NEVER suggest: fitness, sports, nature, outdoor leisure\n"
                    "  Good: 'entrepreneur laptop office', 'business meeting executive'\n"
                    "  Bad:  'man running trail', 'nature sunrise'\n\n"
                    if editing_style == "priestley" else ""
                    ) +
                    "RETENTION MECHANICS — apply all 5 before building the plan:\n\n"
                    "MECHANIC 1 — OPEN LOOPS:\n"
                    "  Never answer a question before 70% of the video.\n"
                    "  The first 30% CREATES tension. It never resolves it.\n"
                    "  Use cuts to delay payoff:\n"
                    "    Speaker says 'The reason I ran 10 miles is...'\n"
                    "    WRONG: Keep the full sentence → viewer has no reason to stay\n"
                    "    RIGHT: Cut on 'The reason I ran 10 miles is...' → context → answer later\n"
                    "  After building your segment list: write down every question the viewer\n"
                    "  will have. Verify none are answered before the 70% mark.\n\n"
                    "MECHANIC 2 — PATTERN INTERRUPTS every 7s:\n"
                    "  NEVER let 7 seconds pass without something unexpected.\n"
                    "  Types: Reframe | Time jump | Contradiction | Rhetorical question | Silence-drop\n"
                    "  Find natural pattern interrupts in the transcript and cut TO them.\n\n"
                    "MECHANIC 3 — TENSION PROGRESSION:\n"
                    "  Score each segment 1–10 for emotional intensity.\n"
                    "  Required arc: 3 → 5 → 6 → 7 → 8 → 9 → 10 → 8\n"
                    "  WRONG: 7 → 5 → 8 → 4 → 9 (random jumps — viewer disengages)\n"
                    "  WRONG: 5 → 5 → 5 → 5 (flat — viewer leaves)\n"
                    "  Hook must score 7+. Payoff must score 9–10.\n\n"
                    "MECHANIC 4 — AGGRESSIVE SILENCE REMOVAL:\n"
                    "  Remove ALL pauses > 0.15 seconds between words.\n"
                    "  Remove ALL filler: um, uh, like, you know, basically, literally,\n"
                    "    sort of, kind of, I mean, right?, okay so.\n"
                    "  Keep ONLY intentional pauses (max 0.3s before a revelation).\n\n"
                    "MECHANIC 5 — HOOK FIRST (conditional, see TEST A/B/C above):\n"
                    "  Find the single most surprising/counterintuitive moment (score ≥ 15/30)\n"
                    "  that ALSO scores 7+/10 on TEST A (standalone) and TEST B (tension).\n"
                    "  If found: place it at position 0. Minimum context at position 1–2. Payoff last.\n"
                    "  If NOT found: keep chronological order — do not force a confusing reorder.\n"
                    "  Example from '3am / 10 miles' transcript:\n"
                    "    WRONG: [intro → context → running → haters → payoff]\n"
                    "    RIGHT: [10 miles hook → WHY (context) → 3am detail → haters → payoff]\n\n"
                    "BEAT ASSIGNMENT — MANDATORY VARIETY:\n"
                    "Every keep_segment MUST have a 'beat' field. Use these exact values:\n"
                    "  hook · amplify · context · tension · story ·\n"
                    "  realization · principle · payoff · emotional_end\n"
                    "Rules:\n"
                    "  - First segment: ALWAYS beat='hook'\n"
                    "  - Last segment: ALWAYS beat='payoff' or beat='emotional_end'\n"
                    "  - Never more than 2 consecutive segments with the same beat\n"
                    "  - Minimum 4 DIFFERENT beats for any video over 20 seconds\n"
                    "  - Never use beat='story' for more than 30% of all segments\n"
                    "BEAT EXAMPLE for 9 segments:\n"
                    "  [hook, amplify, context, tension, story, story, realization, principle, payoff]\n"
                    "  NEVER: [story, story, story, story, story, story, story, story, story]\n\n"
                    "MOTION BOARD REQUIREMENT — output as `motion_graphics`:\n"
                    "Build a MOTION BOARD: a list of animation beats with exact timestamps,\n"
                    "rendered as real HyperFrames HTML→MP4 compositions and alpha-composited\n"
                    "onto the video. One graphic every 5-7 seconds.\n"
                    "Each entry MUST have these fields:\n"
                    "  at           — output-timeline timestamp (seconds)\n"
                    "  duration     — seconds on screen; must NOT exceed time until next sentence\n"
                    "  type         — one of: kinetic_title | stat_card | lower_third | step_diagram\n"
                    "  text         — exact text to display\n"
                    "  subtext      — smaller text below (use \"\" if none)\n"
                    "  style        — \"" + ("priestley" if editing_style == "priestley" else "momentum") + "\" (matches the active editing_style)\n"
                    "  trigger_word — the exact word the speaker is saying when this appears\n"
                    "  hf_prompt    — a RICH animation description (see below)\n\n"
                    "Type selection:\n"
                    "  - kinetic_title: for the hook statement (first 5s)\n"
                    "  - stat_card: when speaker mentions a number ($X, X%, X years)\n"
                    "  - lower_third: for key phrases and concepts\n"
                    "  - step_diagram: when speaker says 'step 1', 'first', 'second', etc.\n"
                    "Content must match EXACTLY what is being said at the trigger_word's timestamp.\n\n"
                    "HF_PROMPT REQUIREMENT:\n"
                    "For each motion_graphic, generate a rich hf_prompt field.\n"
                    "The hf_prompt must be 3-5 sentences describing:\n"
                    "entry animation, visual style, typography details,\n"
                    "position, timing, and exit animation.\n"
                    "Match the hf_prompt to the editing_style:\n"
                    "  - priestley style: corporate, clean, Inter font, subtle animations\n"
                    "  - momentum style: bold, kinetic, Anton font, aggressive pop-in\n"
                    "  - viral style: maximum energy, brand colors, fast animations\n\n"
                    f"USER INSTRUCTIONS:\n{user_instructions or '(none — apply default high-retention edit)'}\n\n"
                    "TRANSCRIPT (JSON):\n"
                    f"{json.dumps(transcript, ensure_ascii=False)}\n\n"
                    + (f"{_chunk_context}\n\n" if _chunk_context else "")
                    + "Before outputting the edit plan, complete all 5 phases:\n\n"
                    "PHASE 0 — IMAGINATION (before reading the transcript)\n"
                    "State:\n"
                    "  'The final video must make the viewer feel: [ONE EMOTION]'\n"
                    "  'The viewer must think at the end: [ONE THOUGHT]'\n"
                    "  'If this video works perfectly, [DESCRIBE THE IDEAL VIEWER REACTION]'\n"
                    "This vision guides every decision that follows.\n\n"
                    "PHASE 1 — DEEP COMPREHENSION (read full transcript)\n"
                    "Identify:\n"
                    "  - What the speaker SAYS (literal)\n"
                    "  - What the speaker MEANS (subtext)\n"
                    "  - What the speaker FEELS (emotion beneath the words)\n"
                    "  - What the viewer NEEDS to hear (not what was said, but what matters)\n"
                    "These 4 things are often different. The edit serves what the viewer NEEDS,\n"
                    "not what was literally said.\n"
                    "Example:\n"
                    "  Said: 'I ran 10 miles'\n"
                    "  Means: 'I did something I thought was impossible'\n"
                    "  Feels: pride + disbelief + exhaustion\n"
                    "  Viewer needs: proof that limits are mental, not physical\n"
                    "The entire edit communicates this subtext, not the literal words.\n\n"
                    "PHASE 2 — EMOTIONAL ARC MAPPING\n"
                    "Before selecting segments, map the emotional journey. Output a simple arc:\n"
                    "  '0:00 → EMOTION (what happens)'\n"
                    "  '0:08 → EMOTION (what happens)'\n"
                    "  ... (continue through the full edit duration)\n"
                    "Every kept segment must fit somewhere on this arc.\n"
                    "If a segment doesn't move the arc forward — cut it.\n"
                    "This arc is the blueprint for script_structure: the number of arc\n"
                    "entries is approximately the number of script_structure entries needed.\n"
                    "If your arc has 12 steps for a 15-min video, script_structure must\n"
                    "have ~12 entries — not a 3-entry summary. Same granularity;\n"
                    "source timestamps instead of output timestamps.\n\n"
                    "PHASE 3 — UNIFIED INTENTION\n"
                    "State ONE sentence:\n"
                    "  'The unified intention of this edit is: [SENTENCE]'\n"
                    "Then verify: does every kept segment serve this intention?\n"
                    "Does every caption emphasis word serve this intention?\n"
                    "Do the zoom moments serve this intention?\n"
                    "If not — revise until everything is unified.\n\n"
                    "PHASE 4 — IMPLEMENTATION\n"
                    "Fill the JSON fields in this order:\n"
                    "  FIRST — script_structure: derive from your Phase 2 arc, translated\n"
                    "    to source timestamps. Count entries before writing:\n"
                    "    <3min→5-9, 3-10min→8-12, 10-20min→10-16, >20min→1/90-120s.\n"
                    "    Complete script_structure entirely before writing keep_segments.\n"
                    "  THEN — keep_segments (scored, ordered by narrative function)\n"
                    "  - hook (highest score, serves the unified intention, and passes\n"
                    "    TEST A + TEST B at 7+/10 — otherwise use chronological order)\n"
                    "  - CONTEXT PRESERVATION (Rule 3): if a segment was moved to the hook\n"
                    "    position, immediately ask 'what does the viewer need to know next\n"
                    "    to make sense of what they just heard?' Place that context segment\n"
                    "    at position 1 (or 2). Never place an unrelated 'strong' segment\n"
                    "    between the hook and its context.\n"
                    "  - caption_emphasis_words (only words that serve the intention)\n"
                    "  - broll (CONCRETE visuals only — physical actions, locations, objects, numbers):\n"
                    "      Short-form: max 1 b-roll every 8s. 60s video = max 6 b-rolls.\n"
                    "      Long-form: max 1 b-roll every 15s. Max 1 per keep_segment.\n"
                    "      NEVER during beats: realization, payoff, emotional_end, hook.\n"
                    "      NEVER during first 3s. Min 8s speaker face between b-rolls.\n"
                    "  - hyperframes (only at moments of maximum emotional impact)\n"
                    + (
                    "  - caption_moments (LONG-FORM ONLY — LESS IS MORE):\n"
                    "      Target: 1 caption per 8–12 seconds. NEVER caption every sentence.\n"
                    "      Caption ONLY these 7 semantic triggers:\n"
                    "        1. HOOK (first 90s): bold claim, value promise, scroll-stopper → style='hook'\n"
                    "        2. NEW CONCEPT: first time a term is introduced → style='concept'\n"
                    "        3. LIST ITEM: each item in an enumeration, 0.4s apart → style='list_item'\n"
                    "        4. NUMBER/STAT: any specific figure ('$500k', '3 steps') → style='stat'\n"
                    "        5. MANTRA: short punchy memorable phrase → style='mantra'\n"
                    "        6. STRUCTURAL MARKER: 'Step One', 'Phase 2', 'Finally…' → style='marker'\n"
                    "        7. QUESTION: rhetorical question that creates tension → style='concept'\n"
                    "      NEVER caption: transitions, fillers, storytelling, normal narrative.\n"
                    "      Each moment: {\n"
                    '        "start": N.N,           // timestamp in output timeline\n'
                    '        "end":   N.N,           // 2–5s window\n'
                    '        "text":  "...",         // exact verbatim spoken words — never invented\n'
                    '        "style": "hook|concept|stat|list_item|mantra|quote|marker",\n'
                    '        "emphasis_words": ["word1", "word2"]  // 1–2 most impactful words\n'
                    "      }\n"
                    "      VISUAL TREATMENT:\n"
                    '        hook      — Playfair Display 88px white center-screen, slow fade\n'
                    '        concept   — Montserrat 68px white+brand emphasis, lower-third, slide up\n'
                    '        stat      — Montserrat 96px brand color center-screen, scale pop\n'
                    '        list_item — Montserrat 62px white left-side, slide from left\n'
                    '        mantra    — Playfair Display 78px brand color center, cinematic fade\n'
                    '        quote     — same as mantra\n'
                    '        marker    — Montserrat lower-third, fast fade\n'
                    "      emphasis_words: 1–2 words verbatim in text — brand color + 110% size.\n"
                    "      text: exact verbatim spoken words — never invented, never paraphrased.\n"
                    "      start/end: must fall within the corresponding keep_segment window.\n\n"
                    ""
                    if fmt == "long" else ""
                    ) +
                    "PHASE 5 — SELF-EVALUATION (before finalizing output)\n"
                    "Run this checklist and output PASS/FAIL for each:\n"
                    "  □ IMAGINATION CHECK: Does this edit achieve the emotion stated in Phase 0?\n"
                    "  □ HOOK CHECK: Does second 0 make leaving feel impossible?\n"
                    "  □ ARC CHECK: Does the emotional arc flow without flat sections?\n"
                    "  □ UNITY CHECK: Does every element serve the unified intention?\n"
                    "  □ SPECIFICITY CHECK: Are all segments specific enough to be believable?\n"
                    "  □ PAYOFF CHECK: Does the last line close every open loop?\n"
                    "  □ HUMANITY CHECK: Would a real human feel something watching this?\n"
                    "  RETENTION CHECK (5 mechanics):\n"
                    "  □ WAIT-WHAT CHECK: Does the hook make the viewer think 'wait, what?'\n"
                    "      in the first 3 seconds? YES/NO\n"
                    "      If NO → find a different hook segment.\n"
                    "  □ STANDALONE TEST (TEST A): Score the segment at position 0, 1-10 —\n"
                    "      can a stranger understand it with ZERO prior context?\n"
                    "      If < 7 → this segment cannot be the hook. Either find a segment\n"
                    "      that scores 7+, or revert keep_segments to chronological order.\n"
                    "  □ TENSION TEST (TEST B): Score the segment at position 0, 1-10 —\n"
                    "      does it make the viewer think 'wait, what? I need to know more'?\n"
                    "      If < 7 → this segment cannot be the hook. Either find a segment\n"
                    "      that scores 7+, or revert keep_segments to chronological order.\n"
                    "  □ NO REPETITION TEST (TEST C): For the hook and its new neighbour,\n"
                    "      does the last word of the previous segment match the first word\n"
                    "      of this segment (case-insensitive)? If YES → adjust the boundary\n"
                    "      by ±0.3s to remove the duplicate.\n"
                    "  □ LOOP CHECK: Is every open loop closed ONLY at or after the 70% mark?\n"
                    "      YES/NO — list each loop and when it resolves.\n"
                    "      If NO → reorder the answer segment to after the 70% mark.\n"
                    "  □ TENSION CHECK: Does emotional intensity increase with each segment?\n"
                    "      State the intensity score of each segment in order (e.g. 5,6,7,8,9,10).\n"
                    "      YES/NO — if any segment is lower than the previous → cut or reorder it.\n"
                    "  □ STALE SEGMENT CHECK: Is there any segment where NOTHING NEW is revealed?\n"
                    "      (No new info, no new emotion, no new tension, no new question)\n"
                    "      If YES → cut that segment. A flat segment is a skip trigger.\n"
                    "  □ SCROLL-STOP CHECK: Would YOU personally stop scrolling for this hook?\n"
                    "      Answer honestly YES/NO. If NO → go back to Phase 1 and find a better hook.\n"
                    "If any RETENTION CHECK fails: state which one and revise before outputting.\n"
                    "  □ COHERENCE CHECK — read kept segments as a complete stranger:\n"
                    "      For EACH kept segment, verify:\n"
                    "        - Is the hook understandable with ZERO prior context?\n"
                    "        - Are ALL people addressed by name introduced BEFORE they're mentioned?\n"
                    "        - Does every 'I'm joking' have the ORIGINAL JOKE visible before it?\n"
                    "        - Does every reaction ('you felt that', 'see how you felt') have\n"
                    "          the moment that caused that feeling visible earlier in the edit?\n"
                    "        - Does every short answer ('A basketball player.', 'Three years.')\n"
                    "          have its QUESTION kept?\n"
                    "        - Does every reference to 'that' / 'just now' / 'what I said'\n"
                    "          have the referenced segment kept?\n"
                    "      If ANY is NO → add back the minimum context segment that fixes it.\n"
                    "      State which segments you added back and why.\n"
                    "      A 5-second context segment is better than a confusing video.\n\n"
                    "  □ BOUNDARY CHECK:\n"
                    "      For each segment junction, verify:\n"
                    "        - Segment N ends with a complete sentence (period/pause)\n"
                    "        - Segment N+1 starts with a complete sentence\n"
                    "        - Last word of N ≠ first word of N+1\n"
                    "      If any fail → adjust the segment boundaries.\n\n"
                    "  □ CONTEXT CHECK:\n"
                    "      For each segment, verify the first word is not a pronoun or\n"
                    "      reference word that requires prior context ('people', 'they',\n"
                    "      'them', 'it', 'that', 'this', 'he', 'she', 'we', 'those', 'these').\n"
                    "      If it is → move the segment start back to include the\n"
                    "      establishing sentence.\n\n"
                    "  □ ENDING CHECK:\n"
                    "      Does the last segment end with a complete sentence?\n"
                    "      If it ends with 'and', 'but', 'so', 'because', 'that', 'seems',\n"
                    "      'then', 'it', 'the', 'a', 'an' → INVALID. Extend the segment end.\n\n"
                    "If any check FAILS: explain why and revise the plan before outputting.\n\n"
                    "Complete Phases 0–3 and Phase 5 as plain text thinking.\n"
                    "Output the final JSON edit plan (Phase 4) last, after all phases are complete.\n"
                    "Return the JSON edit plan after completing all 5 phases. No other prose after the JSON."
                ),
            }
        ],
    }

    sys_prompt = system_prompt(
        format_hint=fmt,
        brand_color=brand_color or "#FF7751",
        caption_color=caption_color or "white",
        caption_position=caption_position or "center",
        caption_font=caption_font or "Poppins Bold",
        editing_style=editing_style,
        source_duration=duration,
    )

    def _call_api(extra_instruction: str = "") -> dict:
        base_text = user_msg["content"][0]["text"]
        if extra_instruction:
            msg_text = base_text + f"\n\nCRITICAL CORRECTION: {extra_instruction}"
        else:
            msg_text = base_text
        resp = _client().messages.create(
            model=settings.effective_model,
            max_tokens=16000,
            system=sys_prompt,
            messages=[{"role": "user", "content": [{"type": "text", "text": msg_text}]}],
        )
        raw_text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        print(f"[RAW MODEL RESPONSE LENGTH] {len(raw_text)} chars")
        print(f"[RAW MODEL RESPONSE] {raw_text}")
        p = _extract_json(raw_text)
        _model_format = p.get("format", "<missing>")
        p["format"] = fmt
        print(f"[FORMAT] user_hint={fmt!r} model_originally_said={_model_format!r} final_format={fmt!r}")
        return p

    def _kept_duration(p: dict) -> float:
        return sum(
            max(0.0, float(s.get("end", 0)) - float(s.get("start", 0)))
            for s in p.get("keep_segments", [])
            if isinstance(s, dict)
        )

    # Initial call
    plan = _call_api()
    _guard_plan_inplace(plan, transcript, duration)

    # Guard (d): log ratio
    kept_s = _kept_duration(plan)
    drop_pct = 100.0 * (1.0 - kept_s / max(duration, 0.01))
    print(
        f"[PLAN-GUARD] ratio: kept={kept_s:.1f}s / {duration:.1f}s "
        f"({100-drop_pct:.0f}% kept, {drop_pct:.0f}% dropped)",
        flush=True,
    )

    # Guard (c): retry if > 40% dropped
    if drop_pct > 40.0 and duration > 0:
        print(f"[PLAN-GUARD] {drop_pct:.0f}% dropped > 40% threshold — retrying", flush=True)
        correction = (
            f"Your previous plan kept only {kept_s:.1f}s of {duration:.1f}s "
            f"({100-drop_pct:.0f}% kept, {drop_pct:.0f}% dropped). "
            "This is too aggressive — it destroys narrative content. "
            "REQUIREMENT: keep_segments must cover at least 60% of source duration. "
            "Only drop segments with score ≤ 3. "
            "Any segment with score ≥ 7 MUST be in keep_segments. "
            "Do NOT emit a drop_segments key — keep_segments is the only output list. "
            "Return a complete new JSON plan."
        )
        plan = _call_api(correction)
        _guard_plan_inplace(plan, transcript, duration)
        kept_s2 = _kept_duration(plan)
        drop_pct2 = 100.0 * (1.0 - kept_s2 / max(duration, 0.01))
        print(
            f"[PLAN-GUARD] retry: kept={kept_s2:.1f}s dropped={drop_pct2:.0f}%",
            flush=True,
        )
        if drop_pct2 > 40.0:
            print("[PLAN-GUARD] retry still > 40% — applying fallback (keep all)", flush=True)
            plan = _fallback_keep_all(plan, transcript)

    return EditPlan(raw=plan)


def rewrite_hook(
    transcript_text: str,
    original_hook_segment: str,
    brand_color: str = "#FF7751",
) -> dict[str, Any]:
    """
    Ask Claude to rewrite the hook opening line for maximum retention.
    Returns: {rewritten_hook, hook_type, display_style, confidence}
    If confidence < 0.7 the caller should skip the overlay.
    Falls back to a safe default dict on any error.
    """
    try:
        resp = _client().messages.create(
            model=settings.effective_model,
            max_tokens=256,
            messages=[{
                "role": "user",
                "content": (
                    "You are a viral video hook specialist. Rewrite the following opening "
                    "line to maximise scroll-stop retention.\n\n"
                    "RULES:\n"
                    "  - Max 12 words.\n"
                    "  - Start with the most counterintuitive or specific claim.\n"
                    "  - No filler ('In this video...', 'Today I want to...').\n"
                    "  - No em-dashes (—): use a comma or period instead.\n"
                    "  - Match the speaker's voice.\n"
                    "  - hook_type: one of: question | statement | number | contrast | story\n"
                    "  - display_style: bold_overlay | subtitle | none\n"
                    "  - confidence: 0.0–1.0 (how sure you are this improves the original)\n\n"
                    f"ORIGINAL HOOK: {original_hook_segment}\n\n"
                    f"FULL TRANSCRIPT EXCERPT (first 300 chars): {transcript_text[:300]}\n\n"
                    "Reply ONLY with JSON:\n"
                    '{"rewritten_hook":"...","hook_type":"...","display_style":"...",'
                    '"confidence":0.0}'
                ),
            }],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        data = _extract_json(text)
        return {
            "rewritten_hook": str(data.get("rewritten_hook", original_hook_segment)),
            "hook_type":      str(data.get("hook_type",      "statement")),
            "display_style":  str(data.get("display_style",  "bold_overlay")),
            "confidence":     float(data.get("confidence",   0.0)),
        }
    except Exception:
        return {
            "rewritten_hook": original_hook_segment,
            "hook_type":      "statement",
            "display_style":  "none",
            "confidence":     0.0,
        }


def _repair_json(raw: str) -> str:
    """Fix common LLM JSON mistakes before parsing.

    Handles: unescaped quotes inside string values, trailing commas,
    unescaped newlines/tabs.
    """
    import re as _re
    s = raw
    s = s.replace("\t", "\\t")
    s = _re.sub(r",\s*([}\]])", r"\1", s)
    # Fix unescaped double quotes inside string values:
    # Walk character by character, tracking whether we're inside a string.
    out = []
    in_string = False
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and in_string:
            out.append(ch)
            if i + 1 < len(s):
                out.append(s[i + 1])
                i += 2
            else:
                i += 1
            continue
        if ch == '"':
            if not in_string:
                in_string = True
                out.append(ch)
            else:
                rest = s[i + 1:].lstrip()
                if not rest or rest[0] in (",", "}", "]", ":"):
                    in_string = False
                    out.append(ch)
                else:
                    out.append('\\"')
            i += 1
            continue
        if ch == "\n" and in_string:
            out.append("\\n")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _detect_duplicate_keys(pairs: list) -> dict:
    """object_pairs_hook for json.loads — logs and last-wins on duplicate keys."""
    seen: set[str] = set()
    result: dict = {}
    for k, v in pairs:
        if k in seen:
            print(f"[PLAN-JSON] STRUCTURAL duplicate key '{k}' — last value wins", flush=True)
        seen.add(k)
        result[k] = v
    return result


def _log_plan_shape(plan: dict) -> None:
    """Log top-level keys + array lengths for post-parse diagnosis."""
    shape = {
        k: (len(v) if isinstance(v, list) else type(v).__name__)
        for k, v in plan.items()
    }
    print(f"[PLAN-JSON] shape: {shape}", flush=True)
    if "drop_segments" in plan:
        drop = plan["drop_segments"]
        if isinstance(drop, list):
            scores = [s.get("score", "?") for s in drop if isinstance(s, dict)]
            reasons = [
                s.get("reason") or s.get("retention_note", "")
                for s in drop if isinstance(s, dict)
            ]
            _editorial = [
                r for r in reasons
                if str(r).lower() in {"tangent", "weak"}
            ]
            _no_ctx_ok = [
                s for s in drop
                if isinstance(s, dict)
                and str(s.get("reason", "")).lower() in {"tangent", "weak"}
                and not s.get("context_ok", False)
            ]
            print(
                f"[PLAN-JSON] WARNING: Claude emitted drop_segments "
                f"({len(drop)} items, scores={scores}, reasons={reasons})",
                flush=True,
            )
            if _editorial:
                print(
                    f"[PLAN-JSON] EDITORIAL-DROP: {len(_editorial)} non-technical drop(s)"
                    f" reason={_editorial} — verify CONTEXT INTEGRITY TEST was applied",
                    flush=True,
                )
            if _no_ctx_ok:
                spans = [(s.get("start"), s.get("end")) for s in _no_ctx_ok]
                print(
                    f"[PLAN-JSON] CONTEXT-OK-MISSING: {len(_no_ctx_ok)} editorial drop(s)"
                    f" missing context_ok=true: {spans}",
                    flush=True,
                )


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"Agent did not return JSON. Got:\n{text[:500]}")
    raw = text[start: end + 1]
    print(
        f"[PLAN-JSON] raw JSON: {len(raw)} chars, {raw.count(chr(10))} lines",
        flush=True,
    )
    try:
        parsed = json.loads(raw, object_pairs_hook=_detect_duplicate_keys)
        _log_plan_shape(parsed)
        return parsed
    except json.JSONDecodeError as first_err:
        print(
            f"[PLAN-JSON] parse error ({first_err}) — invoking _repair_json",
            flush=True,
        )
        _raw_counts = (raw.count("{"), raw.count("["), raw.count("]"), raw.count("}"))
        repaired = _repair_json(raw)
        _rep_counts = (repaired.count("{"), repaired.count("["), repaired.count("]"), repaired.count("}"))
        if _raw_counts != _rep_counts:
            print(
                f"[PLAN-JSON] STRUCTURAL repair: {{: {_raw_counts[0]}→{_rep_counts[0]}, "
                f"[: {_raw_counts[1]}→{_rep_counts[1]}, "
                f"]: {_raw_counts[2]}→{_rep_counts[2]}, "
                f"}}: {_raw_counts[3]}→{_rep_counts[3]}",
                flush=True,
            )
        else:
            print("[PLAN-JSON] repair: syntax-only (brace counts unchanged)", flush=True)
        parsed = json.loads(repaired, object_pairs_hook=_detect_duplicate_keys)
        _log_plan_shape(parsed)
        return parsed


def _guard_plan_inplace(plan: dict, transcript: dict, total_duration: float) -> None:
    """Apply consistency guards to the plan dict in-place.

    (a) Segments in drop_segments with score >= 7 are rescued to keep_segments.
    (b) The last spoken transcript segment must be in keep_segments.
    """
    print(
        f"[PLAN-GUARD] active va9773a8 — "
        f"keep={len(plan.get('keep_segments') or [])} segs, "
        f"drop_segments={'yes' if plan.get('drop_segments') else 'no'}",
        flush=True,
    )
    keep = plan.setdefault("keep_segments", [])
    drop = plan.get("drop_segments")
    if not isinstance(drop, list):
        drop = []

    # Guard (a): rescue high-score segments from drop_segments
    still_drop = []
    for seg in drop:
        if not isinstance(seg, dict):
            continue
        try:
            score = int(seg.get("score", 0))
        except (ValueError, TypeError):
            score = 0
        if score >= 7:
            reason = seg.get("reason") or seg.get("retention_note", "")
            print(
                f"[PLAN-GUARD] rescued segment (score {score}): "
                f"{seg.get('start')}-{seg.get('end')}s "
                f"reason={reason!r}",
                flush=True,
            )
            keep.append(seg)
        else:
            still_drop.append(seg)
    if drop != still_drop:
        plan["drop_segments"] = still_drop

    # Guard (b): last spoken segment must not be dropped
    src_segs = transcript.get("segments", [])
    if src_segs and keep:
        last_src = src_segs[-1]
        last_s = float(last_src.get("start", 0))
        last_e = float(last_src.get("end", total_duration))
        covered = any(
            float(k.get("start", 0)) <= last_s + 0.5
            and float(k.get("end", 0)) >= last_e - 0.5
            for k in keep
            if isinstance(k, dict)
        )
        if not covered:
            print(
                f"[PLAN-GUARD] last spoken segment ({last_s:.2f}-{last_e:.2f}s) "
                f"not in keep_segments — adding",
                flush=True,
            )
            keep.append({
                "start": last_s,
                "end": last_e,
                "beat": "payoff",
                "score": 0,
                "retention_note": "[PLAN-GUARD] rescue: last segment",
            })

    keep.sort(key=lambda s: float(s.get("start", 0)) if isinstance(s, dict) else 0)


def _fallback_keep_all(plan: dict, transcript: dict) -> dict:
    """Fallback plan: keep every transcript segment, preserving other plan fields."""
    src_segs = transcript.get("segments", [])
    keep_all = [
        {
            "start": float(s.get("start", 0)),
            "end": float(s.get("end", 0)),
            "beat": "story",
            "score": 0,
            "retention_note": "[PLAN-GUARD] fallback: kept all",
        }
        for s in src_segs
        if isinstance(s, dict) and float(s.get("end", 0)) > float(s.get("start", 0))
    ]
    result = dict(plan)
    result["keep_segments"] = keep_all
    print(f"[PLAN-GUARD] fallback applied: {len(keep_all)} segments from transcript", flush=True)
    return result
