from pathlib import Path
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
