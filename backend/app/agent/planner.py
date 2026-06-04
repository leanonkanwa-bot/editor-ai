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
                    "  score: net score from sentence_scoring above\n"
                    "         (+10 counterintuitive, +8 stat, +7 vulnerable,\n"
                    "          +6 pain/story, +5 contrast, +4 time urgency,\n"
                    "          -5 filler, -8 repetition, -10 greeting)\n"
                    "  cut_before_silence: true if breath pause ≥0.25s precedes\n"
                    "    this segment's first word (always cut at breath boundaries)\n"
                    "  retention_note: one sentence on why this earns watch time\n"
                    "Drop segments with net score ≤ 3 unless hook or payoff.\n"
                    "Segments with net score ≤ 0 must ALWAYS be cut.\n"
                    "Hook must be the highest-scoring segment in keep_segments.\n\n"
                    "LOOP TIMER: Every 15–20s of output, a new curiosity loop must open.\n"
                    "Track the output timeline — no 20s window without a new tension.\n\n"
                    "COHERENCE TEST — run after selecting all segments:\n"
                    "Read the selected segments in order. Ask: 'If someone heard ONLY these\n"
                    "segments, in this order, would they understand what happened and why it matters?'\n\n"
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
                    f"USER INSTRUCTIONS:\n{user_instructions or '(none — apply default high-retention edit)'}\n\n"
                    "TRANSCRIPT WITH WORD TIMESTAMPS (JSON):\n"
                    f"{json.dumps(transcript, ensure_ascii=False)}\n\n"
                    "Before outputting the edit plan, complete all 5 phases:\n\n"
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
                    "If a segment doesn't move the arc forward — cut it.\n\n"
                    "PHASE 3 — UNIFIED INTENTION\n"
                    "State ONE sentence:\n"
                    "  'The unified intention of this edit is: [SENTENCE]'\n"
                    "Then verify: does every kept segment serve this intention?\n"
                    "Does every caption emphasis word serve this intention?\n"
                    "Do the zoom moments serve this intention?\n"
                    "If not — revise until everything is unified.\n\n"
                    "PHASE 4 — IMPLEMENTATION\n"
                    "Output the edit plan with:\n"
                    "  - keep_segments (scored, ordered by narrative function)\n"
                    "  - hook (highest score, serves the unified intention)\n"
                    "  - caption_emphasis_words (only words that serve the intention)\n"
                    "  - broll (only when it ADDS to the intention, not decorates)\n"
                    "  - hyperframes (only at moments of maximum emotional impact)\n\n"
                    "PHASE 5 — SELF-EVALUATION (before finalizing output)\n"
                    "Run this checklist and output PASS/FAIL for each:\n"
                    "  □ IMAGINATION CHECK: Does this edit achieve the emotion stated in Phase 0?\n"
                    "  □ HOOK CHECK: Does second 0 make leaving feel impossible?\n"
                    "  □ ARC CHECK: Does the emotional arc flow without flat sections?\n"
                    "  □ UNITY CHECK: Does every element serve the unified intention?\n"
                    "  □ SPECIFICITY CHECK: Are all segments specific enough to be believable?\n"
                    "  □ PAYOFF CHECK: Does the last line close every open loop?\n"
                    "  □ HUMANITY CHECK: Would a real human feel something watching this?\n"
                    "If any check FAILS: explain why and revise the plan before outputting.\n\n"
                    "Complete Phases 0–3 and Phase 5 as plain text thinking.\n"
                    "Output the final JSON edit plan (Phase 4) last, after all phases are complete.\n"
                    "Return the JSON edit plan after completing all 5 phases. No other prose after the JSON."
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
