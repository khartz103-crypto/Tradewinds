"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """App settings with defaults suitable for docker-compose development."""

    # Database
    database_url: str = "postgresql://tradewind:tradewind@db:5432/tradewind"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # App
    app_name: str = "TradeWind AI"
    debug: bool = False
    cors_origins: str = "http://localhost:3000"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
