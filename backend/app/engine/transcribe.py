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

import subprocess
import sys
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings

if sys.platform == "win32":
    FFMPEG_PATH = r"C:\Users\KANWAGI\Downloads\ffmpeg-master-latest-win64-gpl-shared\ffmpeg-master-latest-win64-gpl-shared\bin\ffmpeg.exe"
    FFPROBE_PATH = r"C:\Users\KANWAGI\Downloads\ffmpeg-master-latest-win64-gpl-shared\ffmpeg-master-latest-win64-gpl-shared\bin\ffprobe.exe"
else:
    FFMPEG_PATH = shutil.which("ffmpeg") or "ffmpeg"
    FFPROBE_PATH = shutil.which("ffprobe") or "ffprobe"


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


def _extract_audio_wav(video_path: Path, wav_path: Path) -> None:
    subprocess.run(
        [
            FFMPEG_PATH,
            "-y", "-loglevel", "error",
            "-i", str(video_path),
            "-vn", "-ac", "1", "-ar", "16000",
            "-acodec", "pcm_s16le",
            str(wav_path),
        ],
        check=True,
        timeout=300,
    )


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
    wav_path = settings.work_dir / f"{video_path.stem}_audio.wav"
    try:
        _extract_audio_wav(video_path, wav_path)

        model = _load_model()
        seg_iter, info = model.transcribe(
            str(wav_path),
            word_timestamps=True,
            beam_size=1,
            vad_filter=False,
            language=None,
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
