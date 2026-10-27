from __future__ import annotations

from enum import StrEnum
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(StrEnum):
    PAPER = "paper"
    DRY_RUN = "dry_run"
    DEMO = "demo"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: str = "development"
    trading_mode: TradingMode = TradingMode.PAPER

    bybit_base_url: str = "https://api.bybit.com"
    bybit_ws_url: str = "wss://stream.bybit.com/v5/public/linear"

    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: SecretStr | None = None
    model_id: str = "nvidia/nemotron-3-ultra-253b-a12b:free"
    model_fallbacks: str = ""

    supabase_url: str | None = None
    supabase_service_role_key: SecretStr | None = None

    paper_starting_balance: float = 10_000.0
    risk_per_trade: float = Field(default=0.0025, gt=0, le=0.01)
    max_daily_loss: float = Field(default=0.01, gt=0, le=0.05)
    max_drawdown: float = Field(default=0.05, gt=0, le=0.20)
    max_concurrent_trades: int = Field(default=1, ge=1, le=3)
    min_risk_reward: float = Field(default=1.5, ge=1.0)
    max_spread_bps: float = Field(default=8.0, ge=0)
    max_market_data_age_ms: int = Field(default=5000, ge=250)

    @model_validator(mode="after")
    def prohibit_live_trading(self) -> "Settings":
        if str(self.trading_mode).lower() not in {"paper", "dry_run", "demo"}:
            raise ValueError("Live trading is disabled in TriConsensus AI V1")
        return self


settings = Settings()
