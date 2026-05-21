"""Word-level transcription powered by faster-whisper.

We use faster-whisper (CTranslate2 backend) instead of openai-whisper
(PyTorch backend) because:

  - No torch dependency → ~700 MB less RAM, ~700 MB less image size.
  - int8 quantization keeps the same word-level quality at ~1/4 the
    memory of float32. This is what lets the app survive on a 1 GB dyno.
  - Faster on CPU (typically 3–4×).

Public surface stays identical: `transcribe(path) -> Transcript` with
`segments[i].words[j].{text,start,end}`.
"""
from __future__ import annotations

import os
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"

import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings

# ---------------------------------------------------------------------------
# Resolve ffmpeg / ffprobe binaries once at import time.
#
# On Windows the full absolute path is hardcoded so Python's subprocess
# inherits the correct executable regardless of whether the user's
# PowerShell $env:Path was made permanent or not.  The bin folder is also
# prepended to os.environ["PATH"] so the shared-build DLLs
# (avcodec-61.dll, avutil-59.dll, …) are found by the Windows DLL loader.
#
# On Linux / macOS we fall back to shutil.which() → bare name.
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    _WIN_BIN = r"C:\Users\KANWAGI\Downloads\ffmpeg-master-latest-win64-gpl-shared\ffmpeg-master-latest-win64-gpl-shared\bin"
    FFMPEG_PATH: str = _WIN_BIN + r"\ffmpeg.exe"
    FFPROBE_PATH: str = _WIN_BIN + r"\ffprobe.exe"
else:
    FFMPEG_PATH: str = shutil.which("ffmpeg") or "ffmpeg"
    FFPROBE_PATH: str = shutil.which("ffprobe") or "ffprobe"


_model = None


def _load_model():
    """Lazy import + load. Keeps the heavy imports off the server's
    cold-start path so /healthz responds within the cloud platform's window."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel  # noqa: PLC0415 — intentional lazy import

        _model = WhisperModel(
            settings.whisper_model,
            device="cpu",
            compute_type="int8",
            cpu_threads=1,   # default uses all cores → multiplies CTranslate2 RAM buffers
            num_workers=1,
        )
    return _model


def unload_model() -> None:
    """Release the Whisper model and reclaim ~250 MB of RAM. Call this
    between transcription and rendering so ffmpeg has room to encode on
    a 1 GB dyno."""
    global _model
    _model = None
    import gc  # noqa: PLC0415
    gc.collect()


def _run_ffmpeg(args: list[str], timeout: int = 300) -> None:
    subprocess.run(args, check=True, timeout=timeout,
                   stderr=subprocess.PIPE)


def _extract_audio_wav(video_path: Path, wav_path: Path) -> None:
    """Pre-extract audio to 16kHz mono WAV for faster-whisper.

    Tries three progressively simpler command variants so that the Windows
    "shared" FFmpeg build (which is missing some DLLs and returns exit code
    4294967283 / -13) still works via the fallback.

    Variant 1 — full quality, explicit codec (works on all static/essentials builds)
    Variant 2 — remove codec flag (handles shared builds that lack libswresample DLLs)
    Variant 3 — minimal flags only (last resort; accepts whatever sample format ffmpeg picks)
    """
    base = [FFMPEG_PATH, "-y", "-loglevel", "error", "-i", str(video_path),
            "-vn", "-ac", "1", "-ar", "16000"]

    variants = [
        base + ["-acodec", "pcm_s16le", str(wav_path)],        # variant 1: explicit codec
        base + ["-sample_fmt", "s16", str(wav_path)],           # variant 2: sample-fmt flag
        base + [str(wav_path)],                                  # variant 3: minimal flags
    ]

    last_err: Exception | None = None
    for cmd in variants:
        try:
            _run_ffmpeg(cmd)
            return
        except subprocess.CalledProcessError as exc:
            last_err = exc
            continue
        except FileNotFoundError:
            raise RuntimeError(
                f"ffmpeg not found at {FFMPEG_PATH!r}. "
                "Check that the path in transcribe.py matches your actual install location."
            ) from None

    raise RuntimeError(
        f"ffmpeg failed to extract audio after {len(variants)} attempts "
        f"(last exit code: {getattr(last_err, 'returncode', '?')}). "
        f"ffmpeg path used: {FFMPEG_PATH!r}"
    ) from last_err


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word]


@dataclass
class Transcript:
    language: str
    duration: float
    text: str
    segments: list[Segment]

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "duration": self.duration,
            "text": self.text,
            "segments": [
                {
                    "start": s.start,
                    "end": s.end,
                    "text": s.text,
                    "words": [asdict(w) for w in s.words],
                }
                for s in self.segments
            ],
        }


def transcribe(video_path: Path) -> Transcript:
    # Use a deterministic path in the project work_dir rather than the system
    # temp directory.  On Windows, NamedTemporaryFile keeps the file handle
    # open while the context manager is active, so ffmpeg gets "Permission
    # denied" when it tries to write to the same path.  Writing to work_dir
    # and cleaning up with try/finally avoids the lock entirely.
    wav_path = settings.work_dir / f"{video_path.stem}_audio.wav"
    try:
        _extract_audio_wav(video_path, wav_path)

        model = _load_model()
        seg_iter, info = model.transcribe(  # type: ignore[union-attr]
            str(wav_path),
            word_timestamps=True,
            beam_size=1,        # save RAM + CPU vs the default beam_size=5
            vad_filter=False,   # silero-VAD adds ~60MB onnxruntime overhead on tight dynos
            language=None,      # auto-detect: French, English, Spanish, Arabic, etc.
        )

        segments: list[Segment] = []
        last_end = 0.0
        for seg in seg_iter:
            words = [
                Word(
                    text=(w.word or "").strip(),
                    start=float(w.start),
                    end=float(w.end),
                )
                for w in (seg.words or [])
                if w.start is not None and w.end is not None
            ]
            segments.append(
                Segment(
                    start=float(seg.start),
                    end=float(seg.end),
                    text=(seg.text or "").strip(),
                    words=words,
                )
            )
            last_end = max(last_end, float(seg.end))

        full_text = " ".join(s.text for s in segments).strip()
        detected_lang = getattr(info, "language", None) or "en"
        return Transcript(
            language=detected_lang,
            duration=last_end,
            text=full_text,
            segments=segments,
        )
    finally:
        wav_path.unlink(missing_ok=True)
