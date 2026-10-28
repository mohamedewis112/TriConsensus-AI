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
    binance_ws_url: str = "wss://fstream.binance.com/public/ws"

    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: SecretStr | None = None
    model_id: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    model_fallbacks: str = "openrouter/free"

    supabase_url: str | None = None
    supabase_secret_key: SecretStr | None = None
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

    # V6 observation is independent from enforcement. Observation may collect
    # shadow states/actions/rewards while V6_MARL_ENABLED remains false.
    # Enforcement is active only when enabled=true AND shadow_mode=false.
    v6_marl_observe: bool = False
    v6_marl_enabled: bool = False
    v6_marl_shadow_mode: bool = True
    v6_reward_window: int = Field(default=40, ge=20, le=40)
    v6_min_warmup_trades_per_asset: int = Field(default=20, ge=20, le=40)
    v6_drawdown_penalty: float = Field(default=2.0, ge=0.0, le=10.0)
    v6_blacklist_probability: float = Field(default=0.80, ge=0.50, le=1.0)
    v6_min_blacklist_samples: int = Field(default=8, ge=4, le=40)
    v6_hard_losing_streak: int = Field(default=4, ge=2, le=20)
    v6_hard_mean_net_return: float = Field(default=-0.003, ge=-0.05, le=0.0)
    v6_max_microstructure_age_ms: int = Field(default=2000, ge=100, le=10000)
    v6_adverse_orderbook_imbalance: float = Field(default=0.35, ge=0.05, le=0.95)
    v6_microstructure_persist_interval_ms: int = Field(default=60000, ge=1000, le=300000)
    v6_microstructure_latest_interval_ms: int = Field(default=1000, ge=200, le=10000)
    v6_trade_horizon_minutes: int = Field(default=180, ge=30, le=720)
    v6_iv_poll_seconds: int = Field(default=300, ge=60, le=3600)

    @model_validator(mode="after")
    def prohibit_live_trading(self) -> "Settings":
        if str(self.trading_mode).lower() not in {"paper", "dry_run", "demo"}:
            raise ValueError("Live trading is disabled in TriConsensus AI V1")
        if self.paper_slippage_bps > self.max_slippage_bps:
            raise ValueError("paper slippage cannot exceed the risk-engine slippage ceiling")
        if self.v6_min_warmup_trades_per_asset > self.v6_reward_window:
            raise ValueError("V6 warmup cannot exceed the rolling reward window")
        if self.v6_marl_enabled and not self.v6_marl_shadow_mode and self.trading_mode != TradingMode.PAPER:
            raise ValueError("V6 enforcement is allowed only in paper mode")
        return self

    @property
    def v6_marl_observation_active(self) -> bool:
        return bool(self.v6_marl_observe or self.v6_marl_enabled)

    @property
    def v6_marl_enforcement_active(self) -> bool:
        return bool(self.v6_marl_enabled and not self.v6_marl_shadow_mode)

    @property
    def v6_marl_effective_shadow_mode(self) -> bool:
        # A disabled enforcement flag can never be bypassed by setting
        # V6_MARL_SHADOW_MODE=false accidentally.
        return not self.v6_marl_enforcement_active


settings = Settings()
