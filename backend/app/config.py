"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """App settings with defaults suitable for docker-compose development."""

    # Database
    database_url: str = "postgresql+asyncpg://tradewind:tradewind@db:5432/tradewind"

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
    alpaca_data_url: str = "https://data.alpaca.markets"

    # Market data cache
    market_data_cache_ttl_minutes: int = 15

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
