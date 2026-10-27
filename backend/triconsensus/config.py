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
    model_id: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    model_fallbacks: str = "openrouter/free"

    supabase_url: str | None = None
    supabase_service_role_key: SecretStr | None = None
    control_api_key: SecretStr | None = None

    paper_starting_balance: float = 10_000.0
    paper_slippage_bps: float = Field(default=2.0, ge=0, le=10)
    emergency_kill_switch: bool = False

    risk_per_trade: float = Field(default=0.0025, gt=0, le=0.01)
    max_daily_loss: float = Field(default=0.01, gt=0, le=0.05)
    max_drawdown: float = Field(default=0.05, gt=0, le=0.20)
    max_concurrent_trades: int = Field(default=1, ge=1, le=3)
    max_account_exposure: float = Field(default=0.20, gt=0, le=1)
    max_correlated_exposure: float = Field(default=0.20, gt=0, le=1)
    max_losing_streak: int = Field(default=3, ge=1, le=20)
    cooldown_after_loss_minutes: int = Field(default=60, ge=0, le=1440)
    min_risk_reward: float = Field(default=1.5, ge=1.0)
    max_spread_bps: float = Field(default=8.0, ge=0)
    max_slippage_bps: float = Field(default=10.0, ge=0)
    max_realized_volatility_20: float = Field(default=0.08, gt=0, le=1)
    max_market_data_age_ms: int = Field(default=5000, ge=250)

    @model_validator(mode="after")
    def prohibit_live_trading(self) -> "Settings":
        if str(self.trading_mode).lower() not in {"paper", "dry_run", "demo"}:
            raise ValueError("Live trading is disabled in TriConsensus AI V1")
        if self.paper_slippage_bps > self.max_slippage_bps:
            raise ValueError("paper slippage cannot exceed the risk-engine slippage ceiling")
        return self


settings = Settings()
