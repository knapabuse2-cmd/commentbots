"""
Application configuration via environment variables.

Uses pydantic-settings to load from .env file with full validation.
All timing values are in seconds unless noted otherwise.
"""

from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Main application settings loaded from environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database ---
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str = "commentbot"
    postgres_password: str
    postgres_db: str = "commentbot"

    # --- Telegram API ---
    telegram_api_id: int
    telegram_api_hash: str

    # --- Bot ---
    bot_token: str

    # --- Security ---
    session_encryption_key: str

    # --- Logging ---
    log_level: str = "DEBUG"
    log_pretty: bool = True

    # --- Worker Timings ---
    worker_min_comment_delay: int = Field(default=60, ge=10, description="Min seconds between comments")
    worker_max_comment_delay: int = Field(default=120, ge=20, description="Max seconds between comments")
    worker_action_delay: int = Field(default=5, ge=1, description="Seconds between small actions")
    worker_health_check_interval: int = Field(default=300, ge=60, description="Seconds between health checks")
    worker_repost_interval: int = Field(default=1800, ge=300, description="Seconds between comment reposts")
    worker_max_connections: int = Field(default=20, ge=1, le=100, description="Max concurrent Telethon clients")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """Async PostgreSQL connection string for SQLAlchemy."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url_sync(self) -> str:
        """Sync PostgreSQL connection string for Alembic migrations."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached settings singleton."""
    return Settings()  # type: ignore[call-arg]
