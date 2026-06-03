"""
The Brain. Calls Claude to turn a transcript + user instructions into an
EditPlan that the FFmpeg engine can execute.
"""

from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from anthropic import Anthropic

from app.agent.rules import system_prompt
from app.core.config import settings


FormatHint = Literal["short", "long", "auto"]


@dataclass
class EditPlan:
    raw: dict[str, Any]

    @property
    def format(self) -> str:
        return self.raw.get("format", "short")

    @property
    def keep_segments(self) -> list[dict[str, Any]]:
        return self.raw.get("keep_segments", [])

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
        return []  # disabled — clean professional output

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
            model=settings.anthropic_model,
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
) -> EditPlan:
    """
    Ask Claude to produce an edit plan for the given transcript.
    Returns an EditPlan with the raw JSON the model emitted.
    """
    duration = float(transcript.get("duration", 0.0))
    fmt = _decide_format(duration, format_hint)

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
                    f"LANGUAGE: {transcript.get('language', 'en')}\n"
                    f"{face_context}\n"
                    f"{coach_context}\n"
                    "PRE-ANALYSIS (do before building the plan):\n"
                    "  1. content_type: coaching | education | story | motivation\n"
                    "  2. primary_audience: who this is for (1 sentence)\n"
                    "  3. key_result: the ONE outcome the viewer gets (1 sentence)\n"
                    "  4. sentence_scoring: score EVERY candidate sentence before\n"
                    "     selecting the hook and building keep_segments:\n"
                    "       Counterintuitive claim (contradicts widely held belief) → 10\n"
                    "       Specific stat / number / name / concrete claim          → 8\n"
                    "       Story / narrative / scene / lived moment                → 7\n"
                    "       Contrast / 'but' / 'however' / flip / reframe           → 5\n"
                    "       Answer / payoff / resolution                            → 5\n"
                    "       Connective / transition / setup / context               → 3\n"
                    "       Filler / repetition / warm-up / hedge                   → 1\n"
                    "     hook_score = sentence score of the chosen hook sentence.\n"
                    "     MINIMUM ACCEPTABLE hook_score: 15 (counterintuitive=10 +\n"
                    "     specific=8 → 18 passes; story=7 alone → 7, FAILS).\n"
                    "     If no sentence scores ≥ 15, use the first 8 seconds of video.\n"
                    "     Select the highest-scoring sentence ≤ 8s long as hook_moment.\n"
                    "     The hook MUST NOT resolve the tension it creates.\n\n"
                    "HOOK FIRST — STRICT: The segment at hook_moment must be first in\n"
                    "keep_segments regardless of its position in the original transcript.\n"
                    "No intro. No context. The most surprising thing first, immediately.\n\n"
                    "PAYOFF PLACEMENT RULE — ABSOLUTE:\n"
                    "  Tension resolution (the answer to any open loop) MUST appear in\n"
                    "  the last 20% of the output edit duration.\n"
                    "  Example: 60s video → payoff not before t=48s.\n"
                    "  If the transcript's payoff appears early, DELAY it by reordering\n"
                    "  keep_segments — insert story or principle segments between the\n"
                    "  setup and the payoff to enforce the 20% rule.\n\n"
                    "SEGMENT SCORING: For each keep_segment add these fields:\n"
                    "  role: hook|problem|story|principle|payoff|transition\n"
                    "  score: use sentence_scoring above (10=counterintuitive, 8=stat,\n"
                    "         7=story, 5=contrast/payoff, 3=connective, 1=filler)\n"
                    "  cut_before_silence: true if breath pause ≥0.25s precedes\n"
                    "    this segment's first word (always cut at breath boundaries)\n"
                    "  retention_note: one sentence on why this earns watch time\n"
                    "Drop segments with score ≤ 3 unless they are the hook or payoff.\n"
                    "Compress score-5 payoff segments to one sentence; place in final 20%.\n\n"
                    "LOOP TIMER: Every 15–20s of output, a new curiosity loop must open.\n"
                    "Track the output timeline — no 20s window without a new tension.\n\n"
                    f"USER INSTRUCTIONS:\n{user_instructions or '(none — apply default high-retention edit)'}\n\n"
                    "TRANSCRIPT WITH WORD TIMESTAMPS (JSON):\n"
                    f"{json.dumps(transcript, ensure_ascii=False)}\n\n"
                    "Return the JSON edit plan now. No prose. Just the JSON."
                ),
            }
        ],
    }

    resp = _client().messages.create(
        model=settings.anthropic_model,
        max_tokens=16000,
        system=system_prompt(
            format_hint=fmt,
            brand_color=brand_color or "#FF7751",
            caption_color=caption_color or "white",
            caption_position=caption_position or "center",
            caption_font=caption_font or "Poppins Bold",
        ),
        messages=[user_msg],
    )

    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    plan = _extract_json(text)
    plan.setdefault("format", fmt)
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
            model=settings.anthropic_model,
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
    return json.loads(text[start : end + 1])
