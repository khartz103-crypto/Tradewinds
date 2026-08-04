"""Application configuration loaded from environment variables."""

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """App settings with defaults suitable for docker-compose development."""

    # Database
    database_url: str = "postgresql+asyncpg://tradewind:tradewind@db:5432/tradewind"

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        """Map plain Postgres URLs to the asyncpg driver SQLAlchemy needs.

        Managed providers (Render, Heroku, etc.) hand out DSNs such as
        ``postgres://user:pass@host/db`` or ``postgresql://user:pass@host/db``.
        ``create_async_engine`` requires an async driver, so rewrite the scheme
        to ``postgresql+asyncpg://`` unless one is already present.
        """
        if value.startswith("postgresql+asyncpg://"):
            return value
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        return value

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # App
    app_name: str = "TradeWind AI"
    debug: bool = False
    cors_origins: str = "http://localhost:3000"

    # Seed
    seed_admin_email: str = "admin@tradewind.ai"
    seed_admin_password: str = "admin"

    # Alpaca
    alpaca_api_key_id: str = ""
    alpaca_api_secret_key: str = ""
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    # Retained for backwards compatibility with the standalone AlpacaProvider.
    alpaca_data_url: str = "https://data.alpaca.markets"

    # Market data cache
    market_data_cache_ttl_minutes: int = 15

    # JWT
    jwt_secret: str = "change-me-in-production"
    jwt_expiry_hours: int = 24

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
