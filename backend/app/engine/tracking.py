"""
Subject tracking — MediaPipe BlazeFace at 2fps.
Controlled by SUBJECT_TRACKING=true in Railway env vars / settings.subject_tracking.

Output schema (superset of the existing subject_position dict):
  face_cx_pct, face_cy_pct         — median face centre (0-100 %)
  face_left/right/top/bottom_pct   — median face bounding box (0-100 %)
  safe_top/bottom_y_pct            — safe overlay zones above/below the head
  subject_side                     — "left" | "center" | "right"
  face_track                       — list[{"t": s, "cx": %, "cy": %}]
  tracking_method                  — "mediapipe_2fps" | "default_center"

Cascade fallback on every failure level:
  frame missing       → skip, continue
  < 30 % detected     → return default_center
  mediapipe not found → return default_center
  any exception       → return default_center (never raises, never blocks render)
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

# Re-use the binary paths already resolved at startup.
from app.engine.transcribe import FFMPEG_PATH, FFPROBE_PATH

_DEFAULT: dict = {
    "face_cx_pct":       50.0,
    "face_cy_pct":       40.0,
    "face_left_pct":     25.0,
    "face_right_pct":    75.0,
    "face_top_pct":      10.0,
    "face_bottom_pct":   65.0,
    "safe_top_y_pct":     5.0,
    "safe_bottom_y_pct": 72.0,
    "subject_side":      "center",
    "face_track":        [],
    "tracking_method":   "default_center",
}


def track_subject(src: Path) -> dict:
    """Detect face positions across the video at ~2fps. Always returns a safe dict."""
    try:
        return _track(src)
    except ImportError as exc:
        print(f"[TRACKING] mediapipe unavailable ({exc}), fallback center", flush=True)
    except Exception as exc:
        print(f"[TRACKING] detection failed: {exc!r}, fallback center", flush=True)
    return dict(_DEFAULT)


# ---------------------------------------------------------------------------

def _probe_duration(src: Path) -> float:
    try:
        r = subprocess.run(
            [FFPROBE_PATH, "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             str(src)],
            capture_output=True, text=True, timeout=15,
        )
        return max(0.0, float(r.stdout.strip()))
    except Exception:
        return 0.0


def _track(src: Path) -> dict:
    # Explicit submodule import required: `import mediapipe as mp` does NOT
    # auto-expose mp.solutions in mediapipe 0.10.x (lazy namespace, not pulled
    # into __init__). Importing the submodule directly always works.
    import mediapipe.solutions.face_detection as _mp_fd
    import numpy as np
    from PIL import Image

    duration = _probe_duration(src)
    MAX_FRAMES = 60
    sample_fps = min(2.0, MAX_FRAMES / duration) if duration > 5.0 else 2.0

    with tempfile.TemporaryDirectory(prefix="tracking_") as tmp:
        frame_dir = Path(tmp)
        subprocess.run(
            [FFMPEG_PATH, "-i", str(src),
             "-vf", f"fps={sample_fps:.4f},scale=480:-2",
             "-q:v", "4", "-frames:v", str(MAX_FRAMES + 5),
             str(frame_dir / "f%06d.jpg")],
            capture_output=True, timeout=120, check=False,
        )
        frame_paths = sorted(frame_dir.glob("f*.jpg"))

        if not frame_paths:
            raise RuntimeError("no frames extracted from video")

        n_frames = len(frame_paths)
        dt = (duration / n_frames) if (duration > 0 and n_frames > 1) else (1.0 / sample_fps)

        detections: dict[int, tuple[float, float, float, float]] = {}

        with _mp_fd.FaceDetection(
            model_selection=0,
            min_detection_confidence=0.5,
        ) as detector:
            for fi, fp in enumerate(frame_paths):
                try:
                    arr = np.array(Image.open(fp).convert("RGB"))
                    res = detector.process(arr)
                    if res.detections:
                        b = res.detections[0].location_data.relative_bounding_box
                        cx = (b.xmin + b.width / 2) * 100
                        cy = (b.ymin + b.height / 2) * 100
                        detections[fi] = (
                            round(cx, 1),
                            round(cy, 1),
                            round(b.width  * 100, 1),
                            round(b.height * 100, 1),
                        )
                except Exception:
                    pass  # single-frame failure is recoverable

    n_det      = len(detections)
    det_rate   = n_det / n_frames if n_frames else 0.0
    print(f"[TRACKING] {n_det}/{n_frames} frames detected ({det_rate:.0%})", flush=True)

    if det_rate < 0.3:
        print("[TRACKING] detection rate < 30%, using default center", flush=True)
        return dict(_DEFAULT)

    # Build interpolated face_track
    face_track: list[dict] = []
    last_cx, last_cy = 50.0, 40.0
    for fi in range(n_frames):
        t = round(fi * dt, 2)
        if fi in detections:
            last_cx, last_cy = detections[fi][0], detections[fi][1]
            face_track.append({"t": t, "cx": last_cx, "cy": last_cy})
        else:
            prev_fi = max((k for k in detections if k < fi), default=None)
            next_fi = min((k for k in detections if k > fi), default=None)
            if prev_fi is not None and next_fi is not None:
                a   = (fi - prev_fi) / (next_fi - prev_fi)
                cx  = detections[prev_fi][0] + a * (detections[next_fi][0] - detections[prev_fi][0])
                cy  = detections[prev_fi][1] + a * (detections[next_fi][1] - detections[prev_fi][1])
            elif prev_fi is not None:
                cx, cy = detections[prev_fi][0], detections[prev_fi][1]
            elif next_fi is not None:
                cx, cy = detections[next_fi][0], detections[next_fi][1]
            else:
                cx, cy = last_cx, last_cy
            last_cx, last_cy = round(cx, 1), round(cy, 1)
            face_track.append({"t": t, "cx": last_cx, "cy": last_cy})

    # Median position for static summary fields
    sorted_cx = sorted(d["cx"] for d in face_track)
    sorted_cy = sorted(d["cy"] for d in face_track)
    n_t = len(sorted_cx)
    med_cx = sorted_cx[n_t // 2]
    med_cy = sorted_cy[n_t // 2]

    subject_side = "left" if med_cx < 38.0 else ("right" if med_cx > 62.0 else "center")

    avg_w = sum(detections[fi][2] for fi in detections) / n_det
    avg_h = sum(detections[fi][3] for fi in detections) / n_det

    face_left  = round(max(0.0,   med_cx - avg_w / 2), 1)
    face_right = round(min(100.0, med_cx + avg_w / 2), 1)
    face_top   = round(max(0.0,   med_cy - avg_h / 2), 1)
    face_bot   = round(min(100.0, med_cy + avg_h / 2), 1)

    print(
        f"[TRACKING] median cx={med_cx:.1f}% cy={med_cy:.1f}% → side={subject_side}",
        flush=True,
    )

    return {
        "face_cx_pct":       med_cx,
        "face_cy_pct":       med_cy,
        "face_left_pct":     face_left,
        "face_right_pct":    face_right,
        "face_top_pct":      face_top,
        "face_bottom_pct":   face_bot,
        "safe_top_y_pct":    round(max(0.0,   face_top - 5.0), 1),
        "safe_bottom_y_pct": round(min(100.0, face_bot + 5.0), 1),
        "subject_side":      subject_side,
        "face_track":        face_track,
        "tracking_method":   "mediapipe_2fps",
    }
