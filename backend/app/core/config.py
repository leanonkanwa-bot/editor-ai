from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-7"
    # tiny ≈ 390 MB RAM, base ≈ 1 GB. Default to tiny so the app fits on
    # small Railway / Render dynos. Set WHISPER_MODEL=base|small|medium|large
    # in your env to trade RAM for accuracy.
    whisper_model: str = "tiny"

    # Base directory for all persistent data. Set DATA_DIR=/data on Railway
    # (mounted volume). Defaults to storage/ for local development.
    data_dir: str = "storage"

    storage_uploads: str = "uploads"
    storage_outputs: str = "outputs"
    storage_work: str = "work"

    host: str = "0.0.0.0"
    port: int = 8000

    # If set, the API and frontend require this token via cookie or header.
    # Leave empty to allow anonymous access (only do this for local dev).
    access_password: str = ""

    # How long to keep uploaded source videos on disk (hours).
    upload_retention_hours: int = 24

    # Absolute path to a background music MP3/WAV to mix at -28dB under the voice.
    # Leave empty to disable. Set BACKGROUND_MUSIC_PATH in .env to enable.
    background_music_path: str = ""

    # Pexels API key for b-roll stock video fetching (free tier is sufficient).
    # Set PEXELS_API_KEY in .env / Railway variables to enable automatic b-roll.
    # Leave empty to disable — b-roll suggestions will be noted in the plan but
    # no stock video is downloaded or overlaid.
    pexels_api_key: str = ""

    # Motion graphics generation mode for long-form videos.
    # "generated" = AI-generated custom visuals via Claude API (default).
    # "templates_only" = use only the 9 hand-built templates, no API call.
    # Toggle via Railway env var to instantly roll back without a deploy.
    motion_graphics_mode: str = "generated"

    # Render engine: "ffmpeg" (current pipeline) or "hyperframes" (new
    # pre-trim + HyperFrames composition pipeline). Toggle via RENDER_ENGINE
    # env var on Railway for instant rollback.
    render_engine: str = "ffmpeg"

    # Testing: bypass segment cutting entirely. The full source video
    # is treated as one kept segment. Planner still runs (zoom, captions,
    # beat structure) but keep_segments/drop_segments are ignored.
    disable_cuts: bool = False

    # Master speech-cut switch. When false (default), ALL automatic cuts
    # (filler words, pauses, repetitions, false-starts) are disabled and the
    # full source audio is kept unchanged. Set CUT_FILLERS=true to re-enable.
    # Individual cut types are further gated by CUT_REPETITIONS, CUT_PAUSES, etc.
    cut_fillers: bool = False

    # MediaPipe BlazeFace 2fps subject tracking.
    # When true, replaces single-frame Claude Vision with multi-frame face detection.
    # Toggle via SUBJECT_TRACKING=true in Railway env vars (default: false).
    subject_tracking: bool = False

    @field_validator("subject_tracking", "disable_cuts", "cut_fillers", mode="before")
    @classmethod
    def _coerce_bool_env(cls, v: object) -> bool:
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "on")
        return bool(v)

    # Style pack for graphic cards and captions.
    # "lean_glass" = dark glass panels with cyan glow (LeanGlass).
    style_pack: str = "lean_glass"

    # Stripe billing. Set on Railway; leave empty for local dev (checkout
    # endpoint returns 503 instead of crashing if unset).
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_starter: str = ""
    stripe_price_pro: str = ""
    stripe_price_agency: str = ""

    # Public base URL of the app (no trailing slash). Required on Railway so
    # the OAuth redirect_uri uses the public HTTPS domain instead of the
    # internal container address that request.base_url returns behind a proxy.
    # Example: https://leanlead-production.up.railway.app
    app_base_url: str = ""

    # Google OAuth login. Set on Railway.
    google_client_id: str = ""
    google_client_secret: str = ""

    # Server-only: the Google account that auto-links to the founder profile
    # on login. Never settable through any client-facing API.
    founder_google_email: str = ""
    founder_profile_id: str = "jPxx5_4OKXPIm7xR"

    # HMAC key for signing the lle_session cookie. Leave empty to derive a
    # stable key from access_password (fine since it's already a server-only
    # secret) -- set a dedicated SESSION_SECRET on Railway for stronger
    # isolation if desired, but it isn't required to get started.
    session_secret: str = ""

    @property
    def _data_root(self) -> Path:
        p = Path(self.data_dir)
        return p if p.is_absolute() else (BACKEND_DIR / p).resolve()

    @property
    def uploads_dir(self) -> Path:
        return self._data_root / self.storage_uploads

    @property
    def outputs_dir(self) -> Path:
        return self._data_root / self.storage_outputs

    @property
    def work_dir(self) -> Path:
        return self._data_root / self.storage_work


settings = Settings()

for d in (settings.uploads_dir, settings.outputs_dir, settings.work_dir):
    d.mkdir(parents=True, exist_ok=True)
