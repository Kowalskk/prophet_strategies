"""
Application configuration loaded from environment variables / .env file.

All settings are defined as a single :class:`Settings` instance (``settings``)
that is imported everywhere in the codebase::

    from prophet.config import settings

Configuration is backed by pydantic-settings; every field can be overridden
via the corresponding environment variable (upper-cased field name).
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration object for the Prophet engine.

    All values can be set via environment variables or a ``.env`` file located
    in the working directory (or the path provided to ``env_file``).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Allow extra env vars without raising validation errors.
        extra="ignore",
        # Parse comma/JSON lists for list fields.
        env_parse_none_str="null",
    )

    # ------------------------------------------------------------------
    # Polymarket API
    # ------------------------------------------------------------------

    polymarket_api_key: str = Field(default="", description="Polymarket L2 API key.")
    polymarket_secret: str = Field(default="", description="Polymarket L2 API secret.")
    polymarket_passphrase: str = Field(
        default="", description="Polymarket L2 API passphrase."
    )
    private_key: str = Field(
        default="",
        description="Polygon wallet private key (hex, no 0x prefix). "
        "Required for live order signing; unused in paper trading.",
    )
    chain_id: int = Field(
        default=137,
        description="Polygon chain ID. 137 = mainnet, 80001 = Mumbai testnet.",
    )

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------

    database_url: str = Field(
        default="postgresql+asyncpg://prophet:prophet@localhost/prophet",
        description="SQLAlchemy async-compatible PostgreSQL DSN.",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL.",
    )

    # ------------------------------------------------------------------
    # REST API
    # ------------------------------------------------------------------

    api_host: str = Field(default="0.0.0.0", description="Host to bind the API server.")
    api_port: int = Field(default=8000, description="Port to bind the API server.")
    api_secret: str = Field(
        default="",
        description="Bearer token for dashboard authentication. "
        "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\"",
    )
    cors_origins: list[str] = Field(
        default=["https://prophet-dashboard.vercel.app"],
        description="Allowed CORS origins for the dashboard. "
        "Accepts a JSON array or comma-separated list.",
    )

    # ------------------------------------------------------------------
    # Risk limits
    # ------------------------------------------------------------------

    max_position_per_market: float = Field(
        default=100.0, description="Maximum USD exposure per single Polymarket market."
    )
    max_daily_loss: float = Field(
        default=200.0,
        description="Maximum allowed daily loss in USD before trading halts.",
    )
    max_open_positions: int = Field(
        default=20, description="Maximum number of concurrently open positions."
    )
    max_concentration: float = Field(
        default=0.25,
        description="Maximum fraction of total capital allocated to a single crypto (0-1).",
    )
    max_drawdown_total: float = Field(
        default=0.30,
        description="Maximum portfolio drawdown from peak before kill switch triggers (0-1).",
    )
    kill_switch: bool = Field(
        default=False,
        description="When True, all new order placement is immediately blocked.",
    )

    # ------------------------------------------------------------------
    # Trading mode
    # ------------------------------------------------------------------

    paper_trading: bool = Field(
        default=True,
        description=(
            "When True the engine simulates orders without calling the CLOB API. "
            "MUST remain True until >=8 weeks of paper validation is complete."
        ),
    )

    # ------------------------------------------------------------------
    # Scanner
    # ------------------------------------------------------------------

    scan_interval_minutes: int = Field(
        default=15,
        description="How often the quick market scan runs (in minutes).",
    )
    target_cryptos: list[str] = Field(
        default=["BTC", "ETH", "SOL"],
        description="Crypto symbols to track on Polymarket.",
    )

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def is_live_trading(self) -> bool:
        """True only when paper_trading is explicitly disabled."""
        return not self.paper_trading

    @property
    def log_level(self) -> str:
        """Derive log level from environment; defaults to INFO."""
        return "DEBUG" if self.paper_trading else "INFO"


# ---------------------------------------------------------------------------
# Module-level singleton — import this everywhere
# ---------------------------------------------------------------------------

settings = Settings()
