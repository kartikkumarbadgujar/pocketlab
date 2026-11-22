from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    app_name: str = "PocketLab"
    environment: str = "development"
    database_url: str = "sqlite:///./pocketlab.db"
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    runner_token: str
    cors_origins: list[str] = ["http://localhost:3000"]
    # Optional regex for hosted frontends whose exact hostname is assigned by the platform (e.g. Vercel deployment URLs).
    cors_origin_regex: str | None = None
    trusted_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    upload_dir: str = "./private_uploads"
    max_apk_size_bytes: int = 104857600
    rate_limit: str = "120/minute"
    log_level: str = "INFO"

    @field_validator("database_url")
    @classmethod
    def use_psycopg3_driver(cls, value: str) -> str:
        # Hosting platforms hand out postgres:// or postgresql:// URLs; SQLAlchemy would then pick psycopg2, which is not installed.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix):]
        return value

    @field_validator("jwt_secret")
    @classmethod
    def secret_must_be_long_enough(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("JWT_SECRET must contain at least 32 characters")
        return value

    @field_validator("runner_token")
    @classmethod
    def runner_secret_must_be_long_enough(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("RUNNER_TOKEN must contain at least 32 characters")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
