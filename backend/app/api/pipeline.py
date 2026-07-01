"""End-to-end pipeline runner — two-phase:
  Phase 1 (run_job):  transcribe → silence removal → energy → speakers →
                      plan → hook rewrite → broll specs → ready_for_review
  Phase 2 (run_render_phase): render → done
"""

from __future__ import annotations

import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.agent.planner import FormatHint, analyze_subject_position, plan_edit, rewrite_hook
from app.api.jobs import store
from app.core.config import settings
from app.core.plans import has_4k_access
from app.engine.analytics_engine import build_insights_instructions, load_insights
from app.engine.brand_engine import BrandEngine, load_brand
from app.engine.broll_generator import BrollGenerator
from app.engine.energy_detector import EnergyDetector
from app.engine.graphics_engine import GraphicSelector, build_video_context, detect_content_type
from app.engine.render import render
from app.engine.silence_remover import RhythmAwareSilenceRemover, apply_drops_to_transcript
from app.engine.speaker_detector import SpeakerDetector
from app.engine.template_engine import apply_template, get_template
from app.engine.transcribe import transcribe, unload_model


def verify_caption_sync(remapped_words: list, edited_duration: float) -> list:
    """Filter caption words to only those that fall within the edited video.

    This is a utility wrapper around render._verify_caption_sync() exposed
    here for testing and external use. The render pipeline calls the private
    version directly before build_ass().
    """
    import logging
    log = logging.getLogger(__name__)
    issues = []
    valid = []
    for w in remapped_words:
        start = getattr(w, "start", w.get("start", 0) if isinstance(w, dict) else 0)
        if start < 0:
            issues.append(f"'{getattr(w, 'text', '')}' negative start={start:.3f}s")
            continue
        if start > edited_duration:
            issues.append(f"'{getattr(w, 'text', '')}' start={start:.3f}s > duration={edited_duration:.3f}s")
            continue
        valid.append(w)
    if issues:
        log.warning("caption sync issues (%d): %s", len(issues), issues[:10])
    return valid


def run_job(
    job_id: str,
    src: Path,
    instructions: str,
    format_hint: FormatHint,
    *,
    caption_font: str = "Poppins Bold",
    caption_color: str = "white",
    caption_position: str = "center",
    caption_style: str = "impact",
    brand_color: str | None = None,
    aesthetic: str = "dark-pro",
    editing_style: str = "viral",
    # Content brief fields (Feature 6)
    target_audience: str = "",
    main_message: str = "",
    desired_emotion: str = "",
    platform: str = "",
    content_type_hint: str = "",
    # Template Memory (Feature 1)
    template_id: str = "",
    # Style pack
    style_pack: str = "lean_glass",
    # Coach Profile IA (Feature 3)
    coach_profile: dict | None = None,
    # Absorbed from stored params (re-detected internally — ignored here)
    content_type: str = "",
    **kwargs,
) -> None:
    """Phase 1: transcription, analysis, planning → status: ready_for_review."""
    try:
        # ── Step 1: Transcribe + Vision (concurrent) ──────────────────────
        store.update(job_id, status="transcribing", progress=10,
                     message="Transcribing audio + analysing subject position…")
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_transcript = pool.submit(lambda: transcribe(src).to_dict())
            f_subject    = pool.submit(lambda: analyze_subject_position(src))
            transcript  = f_transcript.result()
            subject_pos = f_subject.result()

        # ── Step 2: Silence removal (Feature 2) ───────────────────────────
        store.update(job_id, status="transcribing", progress=20,
                     message="Removing silences and filler words…")
        from app.core.config import settings as _cfg
        if _cfg.disable_cuts:
            # DISABLE_CUTS: skip timestamp adjustment — the video plays
            # uncut, so drop-shifted timestamps would cause progressive
            # drift (captions ahead of speech, gap growing toward the end).
            transcript_clean = transcript
            drops = []
            print("[PIPELINE] DISABLE_CUTS=true — skipping silence removal timestamp shift")
        else:
            try:
                remover = RhythmAwareSilenceRemover()
                word_timestamps = [
                    w for seg in transcript.get("segments", [])
                    for w in seg.get("words", [])
                ]
                drops = remover.process(word_timestamps, transcript.get("segments", []))
                transcript_clean = apply_drops_to_transcript(transcript, drops)
            except Exception:
                transcript_clean = transcript
                drops = []

        # ── Step 3: Energy detection (Feature 4) ──────────────────────────
        store.update(job_id, status="transcribing", progress=25,
                     message="Detecting speaker energy…")
        try:
            detector = EnergyDetector()
            word_ts  = [
                w for seg in transcript_clean.get("segments", [])
                for w in seg.get("words", [])
            ]
            energy_profile = detector.analyze(src, word_ts)
            energy_dicts   = [
                {"at": ep.at, "duration": ep.duration,
                 "rms_db": ep.rms_db, "speech_rate": ep.speech_rate,
                 "level": ep.level}
                for ep in energy_profile
            ]
        except Exception:
            energy_dicts = []

        # ── Step 4: Multi-speaker detection (Feature 8) ───────────────────
        store.update(job_id, status="transcribing", progress=30,
                     message="Detecting speakers…")
        try:
            spk_detector   = SpeakerDetector()
            speaker_segs   = spk_detector.detect(src, transcript_clean.get("segments", []))
            speaker_dicts  = [
                {"start": ss.start, "end": ss.end, "speaker_id": ss.speaker_id,
                 "camera_pos": ss.camera_pos, "lower_third": ss.lower_third}
                for ss in speaker_segs
            ]
        except Exception:
            speaker_dicts = []

        # ── Step 5a: Load template if specified (Feature 1) ───────────────
        template: dict = {}
        if template_id:
            try:
                t = get_template(template_id)
                if t:
                    template = t
                    # Apply template caption overrides now so they propagate.
                    style = t.get("style", {})
                    cap_style_map = {"one_word": "impact", "phrase": "impact", "full_sentence": "kinetic"}
                    caption_style = cap_style_map.get(style.get("caption_style", ""), caption_style)
                    caption_position = style.get("caption_position", caption_position)
            except Exception:
                pass

        # ── Step 5b: Load brand kit (Feature 2) ───────────────────────────
        brand_kit: dict = {}
        try:
            brand_kit = load_brand()
            brand_engine = BrandEngine()
            brand_primary = brand_kit.get("colors", {}).get("primary", "")
            if brand_primary and not brand_color:
                brand_color = brand_primary
            cap_color_override = brand_kit.get("font", {}).get("caption_color", "")
            if cap_color_override:
                from app.engine.brand_engine import _hex_to_name
                caption_color = _hex_to_name(cap_color_override)
        except Exception:
            brand_kit = {}

        # ── Step 5b+: Override from coach_profile (highest priority) ──────
        _FONT_EXPAND = {
            "Poppins":     "Poppins Bold",
            "Inter":       "Inter Bold",
            "Montserrat":  "Montserrat Bold",
            "Bebas":       "Bebas Neue",
            "Anton":       "Anton",
            "DM Sans":     "DM Sans Bold",
            "Quicksand":   "Quicksand Bold",
            "Roboto":      "Roboto Bold",
        }
        if coach_profile:
            _profile_color = coach_profile.get("primaryColor", "")
            if _profile_color and not brand_color:
                brand_color = _profile_color
            _profile_font = coach_profile.get("font", "")
            if _profile_font and caption_font == "Poppins Bold":
                caption_font = _FONT_EXPAND.get(_profile_font, _profile_font)
            print(
                f"[BRAND] Loaded profile: font={coach_profile.get('font', '')!r} "
                f"color={coach_profile.get('primaryColor', '')!r} "
                f"secondary={coach_profile.get('secondaryColor', '')!r}"
            )
        print(f"[BRAND] Passing to render: caption_font={caption_font!r} brand_color={brand_color!r}")

        # ── Step 5c: Load analytics insights (Feature 4) ──────────────────
        insights_instructions = ""
        try:
            insights = load_insights()
            insights_instructions = build_insights_instructions(insights)
            # Bias content_type_hint from analytics if not user-set.
            if not content_type_hint:
                content_type_hint = insights.get("recommended_settings", {}).get(
                    "preferred_content_type", ""
                )
        except Exception:
            pass

        # ── Step 5d: Build enriched instructions from content brief ────────
        enriched_instructions = _build_instructions(
            instructions, target_audience, main_message,
            desired_emotion, platform, content_type_hint,
            insights_instructions=insights_instructions,
            template=template,
        )

        # ── Step 6: Planning ───────────────────────────────────────────────
        store.update(job_id, status="planning", progress=40,
                     message="Asking the agent for an edit plan…")
        plan = plan_edit(
            transcript_clean,
            enriched_instructions,
            format_hint=format_hint,
            brand_color=brand_color,
            caption_color=caption_color,
            caption_position=caption_position,
            caption_font=caption_font,
            subject_position=subject_pos,
            coach_profile=coach_profile,
            editing_style=editing_style,
        )

        # ── Step 7: Hook rewrite (Feature 3) ──────────────────────────────
        store.update(job_id, status="planning", progress=50,
                     message="Rewriting hook for maximum retention…")
        hook_overlay: dict = {}
        try:
            keep_segs = plan.keep_segments
            if keep_segs:
                first_seg_text = keep_segs[0].get("summary", "") or ""
                if not first_seg_text:
                    # Pull text from transcript segments that overlap the first keep segment.
                    fs_start = float(keep_segs[0].get("start", 0))
                    fs_end   = float(keep_segs[0].get("end", fs_start + 10))
                    first_seg_text = " ".join(
                        w.get("text", "")
                        for seg in transcript_clean.get("segments", [])
                        for w in seg.get("words", [])
                        if fs_start <= float(w.get("start", 0)) <= fs_end
                    )[:200]
                full_text = transcript_clean.get("text", "")[:500]
                hook_result = rewrite_hook(full_text, first_seg_text, brand_color or "#FF7751")
                if hook_result.get("confidence", 0.0) >= 0.7:
                    hook_overlay = hook_result
        except Exception:
            hook_overlay = {}

        # ── Step 8: B-roll generation (Feature 1) ─────────────────────────
        store.update(job_id, status="planning", progress=55,
                     message="Generating b-roll overlays…")
        broll_specs_dicts: list[dict] = []
        try:
            broll_gen = BrollGenerator()
            keep_segs = plan.keep_segments or []

            # Build edit_timeline_map: source_start → cumulative_edit_start.
            edit_map: dict[float, float] = {}
            cum = 0.0
            for seg in keep_segs:
                ss = float(seg.get("start", 0))
                ee = float(seg.get("end", ss))
                edit_map[ss] = cum
                cum += max(0.0, ee - ss)
            total_edit_dur = cum

            transcript_segs = transcript_clean.get("segments", [])
            broll_specs = broll_gen.generate(
                transcript_segs, edit_map, total_edit_dur, subject_pos
            )
            broll_specs_dicts = [
                {"kind": bs.kind, "at": bs.at, "duration": bs.duration, "params": bs.params}
                for bs in broll_specs
            ]
        except Exception:
            broll_specs_dicts = []

        # ── Step 9: Detect content type for color grade ────────────────────
        detected_content_type = content_type_hint.lower() if content_type_hint else ""
        if not detected_content_type:
            detected_content_type = detect_content_type(transcript_clean.get("text", ""))

        # ── Step 10: Build adaptive graphic specs ─────────────────────────
        selector  = GraphicSelector()
        video_ctx = build_video_context(transcript_clean, plan)
        selector.configure(video_ctx["content_type"])
        graphic_specs_objs = []
        for seg in (plan.script_structure or []):
            seg_text  = " ".join(seg.get("lines", []))
            seg_role  = seg.get("beat", "")
            try:
                seg_start = float(seg.get("start", 0.0))
                seg_end   = float(seg.get("end", seg_start + 3.0))
            except (TypeError, ValueError):
                continue
            seg_dur = max(1.0, seg_end - seg_start)
            spec = selector.select(seg_text, seg_role, seg_start, seg_dur, video_ctx)
            if spec is not None:
                graphic_specs_objs.append(spec)

        # ── Step 11: Build preview for frontend ───────────────────────────
        preview = _build_preview(
            plan, transcript_clean, detected_content_type,
            hook_overlay, broll_specs_dicts, speaker_dicts,
        )

        # ── Persist everything; set status → ready_for_review ─────────────
        store.update(
            job_id,
            status="ready_for_review",
            progress=65,
            message="Edit plan ready — review before rendering.",
            plan_data={
                "raw": plan.raw,
                "content_type": detected_content_type,
                "graphic_specs": [
                    {"kind": gs.kind, "at": gs.at, "duration": gs.duration}
                    for gs in graphic_specs_objs
                ],
                "broll_specs": broll_specs_dicts,
                "speaker_segments": speaker_dicts,
                "hook_overlay": hook_overlay,
                "brand_kit": brand_kit,
                "template": template,
            },
            transcript=transcript_clean,
            subject_pos=subject_pos,
            energy_profile=energy_dicts,
            hook_overlay=hook_overlay,
            preview=preview,
            params=store.get(job_id).params | dict(
                caption_font=caption_font,
                caption_color=caption_color,
                caption_position=caption_position,
                caption_style=caption_style,
                brand_color=brand_color,
                aesthetic=aesthetic,
                editing_style=editing_style,
                style_pack=style_pack,
                content_type=detected_content_type,
            ),
        )

    except Exception as e:
        store.update(
            job_id,
            status="error",
            error=f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
            message="Phase 1 (analysis) failed.",
        )


def quality_check(plan, result: dict) -> list[str]:
    """Post-render quality gate — returns a list of warning/error strings.

    Called automatically after every render. Issues are logged at WARNING
    level and surfaced in Railway logs so they are easy to spot.
    """
    issues: list[str] = []
    edited_duration = float(result.get("duration", 0.0))

    # Hook strength
    segs = plan.keep_segments or []
    if segs and segs[0].get("score", 5) < 10:
        issues.append(
            f"WARN: Hook segment score={segs[0].get('score','?')} — retention risk"
        )

    # Duration guard for short-form
    if edited_duration > 90 and plan.format == "short":
        issues.append(f"WARN: Short-form is {edited_duration:.1f}s — exceeds 90s target")

    # Minimum segment count
    if len(segs) < 3:
        issues.append(f"WARN: Only {len(segs)} segment(s) — may feel incomplete")

    # No empty output
    if edited_duration < 5:
        issues.append(f"ERROR: Output duration {edited_duration:.1f}s is suspiciously short")

    return issues


def run_render_phase(job_id: str, src: Path) -> None:
    """Phase 2: render the approved edit plan → status: done."""
    job = store.get(job_id)
    if not job:
        return
    try:
        plan_data   = job.plan_data or {}
        transcript  = job.transcript or {}
        subject_pos = job.subject_pos
        params      = job.params or {}

        from app.agent.planner import EditPlan
        plan = EditPlan(raw=plan_data.get("raw", {}))

        content_type  = plan_data.get("content_type", "coaching")
        broll_specs_d = plan_data.get("broll_specs", [])
        speaker_segs  = plan_data.get("speaker_segments", [])
        hook_overlay  = plan_data.get("hook_overlay", {})

        # Rebuild GraphicSpec objects from stored dicts.
        from app.engine.graphics_engine import GraphicSelector, build_video_context
        selector  = GraphicSelector()
        video_ctx = build_video_context(transcript, plan)
        selector.configure(video_ctx["content_type"])
        graphic_specs = []
        for seg in (plan.script_structure or []):
            seg_text  = " ".join(seg.get("lines", []))
            seg_role  = seg.get("beat", "")
            try:
                seg_start = float(seg.get("start", 0.0))
                seg_end   = float(seg.get("end", seg_start + 3.0))
            except (TypeError, ValueError):
                continue
            seg_dur = max(1.0, seg_end - seg_start)
            spec = selector.select(seg_text, seg_role, seg_start, seg_dur, video_ctx)
            if spec is not None:
                graphic_specs.append(spec)

        # Rebuild BrollSpec objects.
        from app.engine.broll_generator import BrollSpec
        broll_specs = [
            BrollSpec(
                kind=bd["kind"], at=bd["at"],
                duration=bd["duration"], params=bd.get("params", {}),
            )
            for bd in broll_specs_d
        ]

        # Free Whisper RAM before FFmpeg.
        unload_model()

        store.update(job_id, status="rendering", progress=70,
                     message="Rendering with FFmpeg…")

        out_path = settings.outputs_dir / f"{job_id}.mp4"
        work_dir = settings.work_dir / job_id

        result = render(
            src,
            transcript,
            plan,
            work_dir,
            out_path,
            caption_font=params.get("caption_font", "Poppins Bold"),
            caption_color=params.get("caption_color", "white"),
            caption_position=params.get("caption_position", "center"),
            caption_style=params.get("caption_style", "impact"),
            brand_color=params.get("brand_color"),
            aesthetic=params.get("aesthetic", "dark-pro"),
            editing_style=params.get("editing_style", "viral"),
            style_pack=params.get("style_pack", "lean_glass"),
            subject_position=subject_pos,
            graphic_specs=graphic_specs,
            content_type=content_type,
            allow_4k=has_4k_access(params.get("coach_profile")),
        )

        # Post-render quality check — logs warnings so they surface in Railway logs.
        import logging as _logging
        _qlog = _logging.getLogger(__name__)
        _issues = quality_check(plan, result)
        for _issue in _issues:
            _qlog.warning("quality_check: %s", _issue)

        # ── Brand: apply intro/outro bumpers (Feature 2) ──────────────────
        brand_kit  = plan_data.get("brand_kit", {})
        final_path = out_path
        try:
            if brand_kit:
                be = BrandEngine()
                final_path = be.prepend_intro(out_path, brand_kit, work_dir)
                final_path = be.append_outro(final_path, brand_kit, work_dir)
                if final_path != out_path:
                    import shutil as _shutil
                    _shutil.move(str(final_path), str(out_path))
                    final_path = out_path
        except Exception:
            final_path = out_path

        store.update(
            job_id,
            status="done",
            progress=100,
            message="Done.",
            result={
                "video_url": f"/api/download/{job_id}",
                "packaging": result["packaging"],
                "format": result["format"],
                "duration": result["duration"],
                "plan": result["plan"],
                "titres_ctr": plan.titres_ctr,
                "thumbnail_mot": plan.thumbnail_mot,
                "script_structure": plan.script_structure,
                "content_type": content_type,
                "hook_overlay": hook_overlay,
                "brand_applied": bool(brand_kit.get("name")),
            },
        )
        # Video-ready email — fire-and-forget, never blocks render delivery
        try:
            import json as _json
            from app import emails as _emails
            if job.profile_id:
                _prof_path = settings._data_root / "profiles" / f"{job.profile_id}.json"
                if _prof_path.exists():
                    _emails.send_video_ready(_json.loads(_prof_path.read_text(encoding="utf-8")))
        except Exception as _email_exc:
            print(f"[email] video_ready hook failed for job {job_id}: {_email_exc}")
    except Exception as e:
        store.update(
            job_id,
            status="error",
            error=f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
            message="Phase 2 (render) failed.",
        )


def _build_instructions(
    instructions: str,
    target_audience: str,
    main_message: str,
    desired_emotion: str,
    platform: str,
    content_type_hint: str,
    insights_instructions: str = "",
    template: dict | None = None,
) -> str:
    """Append content brief, template, and analytics hints to the user's instructions."""
    parts = [instructions] if instructions.strip() else []
    if target_audience:
        parts.append(f"TARGET AUDIENCE: {target_audience}")
    if main_message:
        parts.append(f"CORE MESSAGE: {main_message}")
    if desired_emotion:
        parts.append(f"DESIRED EMOTION: Make the viewer feel {desired_emotion}.")
    if platform:
        parts.append(f"PLATFORM: Optimise for {platform}.")
    if content_type_hint:
        parts.append(f"CONTENT TYPE: {content_type_hint}")
    if insights_instructions:
        parts.append(insights_instructions)
    if template:
        style = template.get("style", {})
        name  = template.get("name", "reference video")
        parts.append(
            f"TEMPLATE STYLE (from '{name}'): "
            f"pacing={style.get('pacing','medium')}, "
            f"zoom={style.get('zoom_intensity','medium')}, "
            f"captions={style.get('caption_style','one_word')}, "
            f"energy={style.get('energy_level','medium')}, "
            f"cuts/min={style.get('avg_cuts_per_minute',12):.0f}. "
            f"Match this editing fingerprint as closely as possible."
        )
    return "\n".join(parts) or "(none — apply default high-retention edit)"


def _build_preview(
    plan,
    transcript: dict,
    content_type: str,
    hook_overlay: dict,
    broll_specs: list[dict],
    speaker_segments: list[dict],
) -> dict:
    """Build the structured preview object sent to the frontend."""
    keep_segs = plan.keep_segments or []
    total_original = float(transcript.get("duration", 0.0))
    total_edited   = sum(
        max(0.0, float(s.get("end", 0)) - float(s.get("start", 0)))
        for s in keep_segs
    )

    edit_segments = []
    for i, seg in enumerate(keep_segs):
        try:
            s = float(seg.get("start", 0))
            e = float(seg.get("end", s))
        except (TypeError, ValueError):
            continue
        edit_segments.append({
            "order":   i + 1,
            "role":    seg.get("role", ""),
            "score":   seg.get("score", 0),
            "retention_note": seg.get("retention_note", ""),
            "original_time": f"{s:.1f}s–{e:.1f}s",
            "edit_dur": f"{e - s:.1f}s",
            "note":    seg.get("reason", ""),
        })

    return {
        "hook_rewrite":        hook_overlay.get("rewritten_hook", ""),
        "hook_confidence":     hook_overlay.get("confidence", 0.0),
        "total_duration_original": round(total_original, 1),
        "total_duration_edited":   round(total_edited, 1),
        "segments_kept":   len(keep_segs),
        "segments_cut":    max(0, len(transcript.get("segments", [])) - len(keep_segs)),
        "content_type":    content_type,
        "color_grade":     content_type,
        "edit_plan":       edit_segments,
        "graphics_planned": len(broll_specs),
        "speakers_detected": len(set(ss.get("speaker_id", "A") for ss in speaker_segments)),
        "packaging": plan.packaging,
    }
