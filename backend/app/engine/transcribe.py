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
# 48 vCPU available — allow Whisper / CTranslate2 to use more threads.
os.environ["OMP_NUM_THREADS"] = "8"
os.environ["MKL_NUM_THREADS"] = "8"
os.environ["OPENBLAS_NUM_THREADS"] = "8"

import shutil
import subprocess
import sys
import time
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
# Verbatim ASR: preserve disfluences (Euh, Bah, repetitions) for LLM editorial layer.
# Set VERBATIM_ASR=false to revert to clean/beam mode.
_VERBATIM_ASR = os.getenv("VERBATIM_ASR", "true").strip().lower() != "false"

if sys.platform == "win32":
    _WIN_CANDIDATES = [
        r"C:\Users\KANWAGI\Downloads\ffmpeg-master-latest-win64-gpl-shared\ffmpeg-master-latest-win64-gpl-shared\bin",
        r"C:\tmp\ffmpeg_extract\ffmpeg-8.1.1-essentials_build\bin",
    ]
    _WIN_BIN = next((p for p in _WIN_CANDIDATES if os.path.isdir(p)), _WIN_CANDIDATES[0])
    FFMPEG_PATH: str = _WIN_BIN + r"\ffmpeg.exe"
    FFPROBE_PATH: str = _WIN_BIN + r"\ffprobe.exe"
else:
    FFMPEG_PATH: str = shutil.which("ffmpeg") or "ffmpeg"
    FFPROBE_PATH: str = shutil.which("ffprobe") or "ffprobe"


class AudioMissingError(RuntimeError):
    """Raised when the input video has no audio stream."""


_model = None


def _load_model():
    """Lazy import + load. Keeps the heavy imports off the server's
    cold-start path so /healthz responds within the cloud platform's window."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel  # noqa: PLC0415 — intentional lazy import

        _model = WhisperModel(
            settings.whisper_model,   # reads WHISPER_MODEL env var (e.g. large-v3)
            device="cpu",
            compute_type="int8",
            cpu_threads=12,  # 48 vCPU available — use 12 threads for faster transcription
            num_workers=2,
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


# ---------------------------------------------------------------------------
# Groq Whisper API — fast cloud transcription with local fallback.
#
# Activated when GROQ_API_KEY is set in Railway Variables.
# Falls back to local faster-whisper transparently on any error.
# ---------------------------------------------------------------------------

# Groq returns language as full English names; map to ISO 639-1.
_GROQ_LANG_MAP: dict[str, str] = {
    "afrikaans": "af", "arabic": "ar", "armenian": "hy", "azerbaijani": "az",
    "belarusian": "be", "bosnian": "bs", "bulgarian": "bg", "catalan": "ca",
    "chinese": "zh", "croatian": "hr", "czech": "cs", "danish": "da",
    "dutch": "nl", "english": "en", "estonian": "et", "finnish": "fi",
    "french": "fr", "galician": "gl", "german": "de", "greek": "el",
    "hebrew": "he", "hindi": "hi", "hungarian": "hu", "icelandic": "is",
    "indonesian": "id", "italian": "it", "japanese": "ja", "kannada": "kn",
    "kazakh": "kk", "korean": "ko", "latvian": "lv", "lithuanian": "lt",
    "macedonian": "mk", "malay": "ms", "marathi": "mr", "maori": "mi",
    "nepali": "ne", "norwegian": "no", "persian": "fa", "polish": "pl",
    "portuguese": "pt", "romanian": "ro", "russian": "ru", "serbian": "sr",
    "slovak": "sk", "slovenian": "sl", "spanish": "es", "swahili": "sw",
    "swedish": "sv", "tagalog": "tl", "tamil": "ta", "thai": "th",
    "turkish": "tr", "ukrainian": "uk", "urdu": "ur", "vietnamese": "vi",
    "welsh": "cy",
}

# WAV at 16kHz/16-bit = ~32 KB/s → 25 MB limit hit at ~13 min.
# Above this threshold we compress to OGG/opus (32 kbps, holds up to ~109 min).
_GROQ_WAV_MAX_BYTES = 24 * 1024 * 1024  # 24 MB — 1 MB safety margin


def _parse_groq_response(response: Any) -> "Transcript":
    """Map a Groq verbose_json transcription response to our Transcript dataclass."""
    lang_raw = (getattr(response, "language", "") or "").lower().strip()
    language = _GROQ_LANG_MAP.get(lang_raw, lang_raw[:2] if len(lang_raw) >= 2 else "fr")

    duration = float(getattr(response, "duration", 0) or 0)
    full_text = (getattr(response, "text", "") or "").strip()

    raw_words: list[Any] = list(getattr(response, "words", None) or [])
    raw_segs: list[Any] = list(getattr(response, "segments", None) or [])

    _MIN_DUR = 0.010
    segments: list[Segment] = []
    word_idx = 0

    for i, seg in enumerate(raw_segs):
        seg_start = float(getattr(seg, "start", 0))
        seg_end = float(getattr(seg, "end", seg_start))
        seg_text = (getattr(seg, "text", "") or "").strip()
        seg_words: list[Word] = []

        while word_idx < len(raw_words):
            w = raw_words[word_idx]
            w_start = float(getattr(w, "start", 0))
            w_end = float(getattr(w, "end", w_start))
            w_text = (getattr(w, "word", "") or "").strip()
            # Stop collecting for this segment when we reach the next segment's start,
            # unless this is the last segment (then collect all remaining words).
            if w_start >= seg_end and i < len(raw_segs) - 1:
                break
            word_idx += 1
            if not w_text or (w_end - w_start) < _MIN_DUR:
                continue
            seg_words.append(Word(text=w_text, start=w_start, end=w_end))

        segments.append(Segment(start=seg_start, end=seg_end, text=seg_text, words=seg_words))

    # Assign any trailing words (float precision edge cases) to the last segment.
    if word_idx < len(raw_words) and segments:
        for w in raw_words[word_idx:]:
            w_start = float(getattr(w, "start", 0))
            w_end = float(getattr(w, "end", w_start))
            w_text = (getattr(w, "word", "") or "").strip()
            if w_text and (w_end - w_start) >= _MIN_DUR:
                segments[-1].words.append(Word(text=w_text, start=w_start, end=w_end))

    if not duration and segments:
        duration = max(s.end for s in segments)

    _total_words = sum(len(s.words) for s in segments)
    print(f"[GROQ] Parsed: {len(segments)} segments, {_total_words} words", flush=True)
    _all_flat = [(w.text, round(w.start, 2), round(w.end, 2))
                 for s in segments for w in s.words]
    print(f"[GROQ] First 10 words: {_all_flat[:10]}", flush=True)

    return Transcript(language=language, duration=duration, text=full_text, segments=segments)


def _transcribe_groq(wav_path: Path) -> "Transcript | None":
    """Attempt Groq Whisper transcription. Returns None to trigger local fallback.

    Never raises — all errors are caught and logged.  The caller falls back to
    local faster-whisper transparently so the pipeline never breaks on API issues.
    """
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return None  # not configured — silent skip

    _t0 = time.perf_counter()
    _compressed: Path | None = None

    try:
        audio_path = wav_path
        size_bytes = wav_path.stat().st_size

        # WAV > 24 MB (audio > ~13 min): compress to OGG/opus to stay under 25 MB limit.
        if size_bytes > _GROQ_WAV_MAX_BYTES:
            _compressed = wav_path.with_suffix(".groq.ogg")
            _t_comp = time.perf_counter()
            subprocess.run(
                [FFMPEG_PATH, "-y", "-loglevel", "error",
                 "-i", str(wav_path),
                 "-c:a", "libopus", "-b:a", "32k", "-ar", "16000", "-ac", "1",
                 str(_compressed)],
                check=True, capture_output=True, timeout=300,
            )
            size_mb_after = _compressed.stat().st_size / (1024 ** 2)
            print(
                f"[GROQ] Compressed {size_bytes / (1024**2):.1f}MB WAV"
                f" → {size_mb_after:.1f}MB OGG/opus in {time.perf_counter()-_t_comp:.1f}s",
                flush=True,
            )
            audio_path = _compressed

        model_name = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3").strip()
        prompt: str | None = None
        if _VERBATIM_ASR:
            prompt = (
                "Euh, bah, ben, hein, ouais, hm, enfin voilà. "
                "Je je pense, il il faut, parce que parce que, c'est c'est."
            )

        from groq import Groq  # noqa: PLC0415 — lazy import (not installed locally)
        client = Groq(api_key=api_key)

        with open(audio_path, "rb") as _f:
            response = client.audio.transcriptions.create(
                file=(audio_path.name, _f),
                model=model_name,
                response_format="verbose_json",
                timestamp_granularities=["word", "segment"],
                prompt=prompt,
            )

        _elapsed = time.perf_counter() - _t0
        print(
            f"[GROQ] Transcription done in {_elapsed:.1f}s"
            f" (model={model_name} lang={getattr(response, 'language', '?')})",
            flush=True,
        )
        return _parse_groq_response(response)

    except Exception as exc:
        print(
            f"[GROQ] {type(exc).__name__}: {exc} — falling back to local Whisper",
            flush=True,
        )
        return None
    finally:
        if _compressed is not None:
            try:
                _compressed.unlink(missing_ok=True)
            except Exception:
                pass


def _has_audio_stream(video_path: Path) -> bool:
    """Return True iff the video contains at least one audio stream."""
    try:
        result = subprocess.run(
            [
                FFPROBE_PATH, "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_type",
                "-of", "csv=p=0",
                str(video_path),
            ],
            capture_output=True, text=True, timeout=15,
        )
        return bool(result.stdout.strip())
    except Exception:
        return True  # fail-open: let _extract_audio_wav surface the real error


def transcribe(video_path: Path) -> Transcript:
    # Use a deterministic path in the project work_dir rather than the system
    # temp directory.  On Windows, NamedTemporaryFile keeps the file handle
    # open while the context manager is active, so ffmpeg gets "Permission
    # denied" when it tries to write to the same path.  Writing to work_dir
    # and cleaning up with try/finally avoids the lock entirely.
    if not _has_audio_stream(video_path):
        raise AudioMissingError(
            "Cette vidéo ne contient pas de piste audio. "
            "Veuillez fournir une vidéo avec une piste audio pour continuer."
        )

    wav_path = settings.work_dir / f"{video_path.stem}_audio.wav"
    try:
        _extract_audio_wav(video_path, wav_path)

        # ── Groq Whisper (fast path) ───────────────────────────────────────
        # Activate by setting GROQ_API_KEY in Railway Variables.
        # Falls back to local faster-whisper on any error (key missing, network
        # failure, model error, file size exceeded even after compression).
        _groq_result = _transcribe_groq(wav_path)
        if _groq_result is not None:
            return _groq_result

        # ── Local faster-whisper (fallback) ────────────────────────────────
        model = _load_model()

        # Select transcription params based on mode.
        # VERBATIM: suppresses Whisper's built-in cleaning so fillers/repetitions are preserved.
        # CLEAN:    deterministic beam search, high-conf output (legacy behaviour).
        if _VERBATIM_ASR:
            _transcribe_kwargs: dict = dict(
                beam_size=7,
                best_of=7,
                # Fallback chain: deterministic first, stochastic if compression_ratio too high.
                temperature=[0.0, 0.2, 0.4],
                condition_on_previous_text=False,   # no context carry-over → each segment fresh
                suppress_tokens=[],                 # keep ALL tokens incl. hesitation sounds
                initial_prompt=(
                    "Euh, bah, ben, hein, ouais, hm, enfin voilà. "
                    "Je je pense, il il faut, parce que parce que, c'est c'est."
                ),
                no_speech_threshold=0.6,
                compression_ratio_threshold=2.4,    # tolerate repetition (stutters are repetition)
            )
            _MIN_WORD_DUR = 0.010
            _MIN_WORD_PROB = 0.15   # fillers score low (suppress_tokens=[]) but are real
            print("[WHISPER] mode=VERBATIM (VERBATIM_ASR=true)", flush=True)
        else:
            _transcribe_kwargs = dict(
                beam_size=10,
                best_of=10,
                temperature=[0.0],
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
                compression_ratio_threshold=2.0,
            )
            _MIN_WORD_DUR = 0.010
            _MIN_WORD_PROB = 0.30
            print("[WHISPER] mode=CLEAN (VERBATIM_ASR=false)", flush=True)

        seg_iter, info = model.transcribe(  # type: ignore[union-attr]
            str(wav_path),
            word_timestamps=True,
            vad_filter=False,               # silero-VAD adds ~60MB onnxruntime overhead
            language=None,                  # auto-detect: French, English, Spanish, Arabic
            **_transcribe_kwargs,
        )

        segments: list[Segment] = []
        last_end = 0.0
        _dropped_words: list[str] = []
        for seg in seg_iter:
            words: list[Word] = []
            for w in (seg.words or []):
                if w.start is None or w.end is None:
                    continue
                text = (w.word or "").strip()
                if not text:
                    continue
                dur = float(w.end) - float(w.start)
                prob = getattr(w, "probability", 1.0)
                if dur < _MIN_WORD_DUR:
                    _dropped_words.append(
                        f"\"{text}\" dur={dur:.3f}s prob={prob:.2f} at {w.start:.2f}s (too short)")
                    continue
                if prob < _MIN_WORD_PROB:
                    _dropped_words.append(
                        f"\"{text}\" dur={dur:.3f}s prob={prob:.2f} at {w.start:.2f}s (low conf)")
                    continue
                words.append(Word(text=text, start=float(w.start), end=float(w.end)))
            segments.append(
                Segment(
                    start=float(seg.start),
                    end=float(seg.end),
                    text=(seg.text or "").strip(),
                    words=words,
                )
            )
            last_end = max(last_end, float(seg.end))

        _total_words = sum(len(s.words) for s in segments)
        if _dropped_words:
            print(f"[WHISPER] Dropped {len(_dropped_words)} words (kept {_total_words}):")
            for dw in _dropped_words[:20]:
                print(f"  {dw}")
        else:
            print(f"[WHISPER] Kept all {_total_words} words (0 dropped)")

        # Fix 3: first-10-words diagnostic — show timestamps + text so we can audit
        # whether disfluences (Euh, Bah…) were captured or silently dropped.
        _all_flat = [(w.text, round(w.start, 2), round(w.end, 2))
                     for s in segments for w in s.words]
        print(f"[WHISPER] First 10 words: {_all_flat[:10]}", flush=True)

        # Diagnostic: audio duration vs transcript coverage
        try:
            _wav_probe = subprocess.run(
                [FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(wav_path)],
                capture_output=True, text=True, timeout=10,
            )
            _wav_dur = float(_wav_probe.stdout.strip()) if _wav_probe.returncode == 0 else 0
        except Exception:
            _wav_dur = 0
        _last_word_end = max((w.end for s in segments for w in s.words), default=0)
        _last_seg_end = max((s.end for s in segments), default=0)
        print(f"[WHISPER] Audio duration: {_wav_dur:.2f}s")
        print(f"[WHISPER] Last segment end: {_last_seg_end:.2f}s")
        print(f"[WHISPER] Last word end: {_last_word_end:.2f}s")
        if _wav_dur > 0 and _last_word_end < _wav_dur - 2.0:
            print(f"[WHISPER] WARNING: transcript stops {_wav_dur - _last_word_end:.1f}s "
                  f"before audio ends — possible missed speech at tail")

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
