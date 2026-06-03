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

    storage_uploads: str = "storage/uploads"
    storage_outputs: str = "storage/outputs"
    storage_work: str = "storage/work"

    host: str = "0.0.0.0"
    port: int = 8000

    # If set, the API and frontend require this token via cookie or header.
    # Leave empty to allow anonymous access (only do this for local dev).
    access_password: str = ""

    # How long to keep uploaded source videos on disk (hours).
    upload_retention_hours: int = 24

    @property
    def uploads_dir(self) -> Path:
        return (BACKEND_DIR / self.storage_uploads).resolve()

    @property
    def outputs_dir(self) -> Path:
        return (BACKEND_DIR / self.storage_outputs).resolve()

    @property
    def work_dir(self) -> Path:
        return (BACKEND_DIR / self.storage_work).resolve()


settings = Settings()

for d in (settings.uploads_dir, settings.outputs_dir, settings.work_dir):
    d.mkdir(parents=True, exist_ok=True)
