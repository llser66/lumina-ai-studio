import os
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://api.openai.com/v1"
    image_model: str = "gpt-image-2.5-sunburst"
    text_model: str = "gpt-5-mini"
    data_dir: Path = ROOT / "data"
    database_url: SecretStr = SecretStr("")
    supabase_url: str = ""
    supabase_service_role_key: SecretStr = SecretStr("")
    supabase_storage_bucket: str = "lumina-assets"
    credential_encryption_key: SecretStr = SecretStr("")
    model_device_id: str = ""
    daily_generation_limit: int = Field(default=20, ge=1, le=1000)
    global_daily_generation_limit: int = Field(default=200, ge=1, le=10000)
    worker_concurrency: int = Field(default=2, ge=1, le=4)
    app_origins: str = (
        "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8090,http://localhost:8090"
    )
    cookie_secure: bool = False
    registration_enabled: bool = True
    registration_limit: int = Field(default=8, ge=1, le=1000)
    session_days: int = Field(default=7, ge=1, le=30)

    @field_validator("data_dir")
    @classmethod
    def absolute_data_dir(cls, value: Path) -> Path:
        return value.resolve() if value.is_absolute() else (ROOT / value).resolve()

    @field_validator("openai_base_url")
    @classmethod
    def secure_provider(cls, value: str) -> str:
        # The provider endpoint is operator configuration, never browser input.
        if not value.startswith("https://"):
            raise ValueError("Provider endpoints must use HTTPS")
        return value.rstrip("/")

    @field_validator("supabase_url")
    @classmethod
    def secure_supabase(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not value.startswith("https://"):
            raise ValueError("Supabase must use HTTPS")
        return value

    @field_validator("database_url")
    @classmethod
    def postgres_database_url(cls, value: SecretStr) -> SecretStr:
        url = value.get_secret_value().strip()
        if url and not url.startswith(("postgres://", "postgresql://")):
            raise ValueError("DATABASE_URL must be a PostgreSQL connection URL")
        return SecretStr(url)

    @property
    def ready(self) -> bool:
        return bool(self.openai_api_key.get_secret_value().strip())

    @property
    def origins(self) -> set[str]:
        allowed = {value.strip().rstrip("/") for value in self.app_origins.split(",")}
        render_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
        if render_url.startswith("https://"):
            allowed.add(render_url)
        return allowed
