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
    editing_style: str = "viral",
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
                    "Segments with net score ≤ 0 must ALWAYS be cut.\n"
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
                    "LONG-FORM MOTION GRAPHICS (is_slide entries in keep_segments):\n"
                    "  When the speaker explains a concept, formula, statistic, comparison,\n"
                    "  or progression, you may insert a full-screen animated motion graphic\n"
                    "  that REPLACES speaker footage (audio continues underneath).\n"
                    "  ALL text/numbers must come from THIS transcript.  Never invent content.\n"
                    "  Zero graphics is valid if nothing fits.\n"
                    "  HARD CAP: maximum 4 motion graphics per video.  Pick the most impactful\n"
                    "  moments — prefer sections where visual explanation adds the most value.\n\n"
                    "  Add an entry inside keep_segments with these fields:\n"
                    '    { "start": N.N, "end": N.N, "is_slide": true,\n'
                    '      "concept_description": "<rich natural-language description of the visual>",\n'
                    '      "slide_content": { <extracted text/numbers/labels from transcript> },\n'
                    '      "accent_color": "#00C3FF",\n'
                    '      "beat": "explanation", "zoom_level": 100, "caption_style": "priestley" }\n\n'
                    "  concept_description: A detailed visual brief (2-4 sentences) describing what\n"
                    "  the motion graphic should look like.  Be SPECIFIC about layout, shapes,\n"
                    "  colors, and animation.  Examples of good descriptions:\n"
                    '    - "A dark background with a large digital counter counting up from 0 to\n'
                    '       1,500,000 in cyan. Label below reads \'Total Revenue\' in white."\n'
                    '    - "A horizontal bar chart with 4 bars growing from left to right,\n'
                    '       labeled Phase 1, Phase 2, Phase 3, Phase 4. Each bar is cyan with\n'
                    '       white labels. Bars animate in sequentially top to bottom."\n'
                    '    - "A before/after split screen. Left side dark-tinted with red accent\n'
                    '       showing \'Old Method\' and \'2 hours/day\'. Right side bright with cyan\n'
                    '       accent showing \'New Method\' and \'20 minutes/day\'."\n\n'
                    "  slide_content: Structured data extracted verbatim from the transcript:\n"
                    '    { "title": "<heading>", "items": [...], "value": "<number>",\n'
                    '      "label": "<label>", "steps": [...] }\n'
                    "  Include whatever fields match the content — there is no fixed schema.\n\n"
                    "  Rules:\n"
                    "  - Slides last 5–20s.  No captions on slide segments.\n"
                    "  - start/end = SOURCE timestamps where audio is extracted.\n"
                    "  - Only during EXPLANATION sections (never hook, story, emotional moments).\n"
                    "  - Match the graphic to what the speaker is ACTUALLY doing in that moment.\n"
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

    resp = _client().messages.create(
        model=settings.anthropic_model,
        max_tokens=16000,
        system=system_prompt(
            format_hint=fmt,
            brand_color=brand_color or "#FF7751",
            caption_color=caption_color or "white",
            caption_position=caption_position or "center",
            caption_font=caption_font or "Poppins Bold",
            editing_style=editing_style,
        ),
        messages=[user_msg],
    )

    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    plan = _extract_json(text)
    _model_format = plan.get("format", "<missing>")
    plan["format"] = fmt
    print(f"[FORMAT] user_hint={fmt!r} model_originally_said={_model_format!r} final_format={fmt!r}")
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
