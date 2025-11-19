"""Application configuration using pydantic-settings."""

from typing import Literal

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application settings
    environment: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    base_url: str = Field(
        default="http://localhost:8000",
        description="Base URL for webhooks and callbacks (e.g., https://your-domain.com)"
    )

    # Temporal settings
    temporal_host: str = "localhost"
    temporal_port: int = 7233
    temporal_namespace: str = "default"
    worker_task_queue: str = "voice-ai-task-queue"
    max_concurrent_activities: int = 100
    max_concurrent_workflows: int = 1000

    # Database settings
    database_url: PostgresDsn = Field(
        default="postgresql://temporal:temporal@localhost:5432/voice_ai"
    )
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_timeout: int = 30

    # Twilio settings
    twilio_account_sid: str = Field(default="")
    twilio_auth_token: str = Field(default="")
    twilio_phone_number: str = Field(default="")

    # Google Gemini settings
    gemini_api_key: str = Field(default="")

    # Redis settings
    redis_host: str = Field(default="localhost")
    redis_port: int = Field(default=6379)
    redis_db: int = Field(default=0)
    redis_password: str | None = Field(default=None)
    redis_session_ttl: int = Field(
        default=7200,
        description="Session TTL in seconds (2 hours default)"
    )

    @property
    def temporal_address(self) -> str:
        """Get Temporal server address."""
        return f"{self.temporal_host}:{self.temporal_port}"

    @property
    def redis_url(self) -> str:
        """Get Redis connection URL."""
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.environment == "development"

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.environment == "production"


# Global settings instance
settings = Settings()
