from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


SupportedSymbol = Literal["BTC/USDT", "ETH/USDT"]
TradeSide = Literal["LONG", "SHORT"]


class AssetRollingStats(BaseModel):
    symbol: SupportedSymbol
    sample_size: int = Field(ge=0, le=40)
    hit_rate: float = Field(ge=0.0, le=1.0)
    mean_net_return: float
    cumulative_net_return: float
    max_drawdown: float = Field(ge=0.0, le=1.0)
    losing_streak: int = Field(ge=0, le=40)


class PortfolioState(BaseModel):
    as_of: datetime
    assets: dict[SupportedSymbol, AssetRollingStats]
    portfolio_drawdown: float = Field(ge=0.0, le=1.0)
    recent_trade_count: int = Field(ge=0, le=80)
    warmup_complete: bool
    source: str = "immutable_reward_memory"


class PortfolioAction(BaseModel):
    asset_weights: dict[SupportedSymbol, float]
    blacklisted_assets: list[SupportedSymbol] = Field(default_factory=list)
    policy_version: str
    reason: str
    shadow_mode: bool = True
    policy_log_prob: float | None = None

    @model_validator(mode="after")
    def validate_weights(self) -> "PortfolioAction":
        for symbol in ("BTC/USDT", "ETH/USDT"):
            value = float(self.asset_weights.get(symbol, 0.0))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"weight outside [0,1] for {symbol}")
            if symbol in self.blacklisted_assets and value != 0.0:
                raise ValueError(f"blacklisted asset {symbol} must have zero weight")
        return self

    def weight_for(self, symbol: SupportedSymbol) -> float:
        return float(self.asset_weights.get(symbol, 0.0))


class VolatilityRiskState(BaseModel):
    symbol: SupportedSymbol
    side: TradeSide
    entry_price: float = Field(gt=0)
    atr_14: float = Field(gt=0)
    realized_volatility: float = Field(ge=0)
    implied_volatility: float | None = Field(default=None, ge=0)
    bybit_orderbook_imbalance: float | None = Field(default=None, ge=-1, le=1)
    binance_orderbook_imbalance: float | None = Field(default=None, ge=-1, le=1)
    spread_bps: float = Field(ge=0)
    market_regime: str
    microstructure_age_ms: int | None = Field(default=None, ge=0)


class DynamicRiskAction(BaseModel):
    stop_atr_multiplier: float = Field(ge=0.75, le=2.25)
    reward_to_risk_multiplier: float = Field(ge=0.75, le=1.50)
    trailing_lock_fraction: float = Field(ge=0.0, le=0.90)
    force_no_trade: bool = False
    policy_version: str
    reason: str
    shadow_mode: bool = True
    policy_log_prob: float | None = None


class RewardMemoryRecord(BaseModel):
    symbol: SupportedSymbol
    trade_id: str
    policy_version: str
    state: dict
    action: dict
    net_return: float
    max_drawdown: float = Field(ge=0.0, le=1.0)
    reward: float
    terminal: bool = True
    created_at: datetime | None = None


class PolicyUpdateResult(BaseModel):
    policy_version: str
    sample_count: int = Field(ge=0)
    actor_loss: float | None = None
    critic_loss: float | None = None
    entropy: float | None = None
    status: Literal["TRAINED", "SKIPPED", "FAILED"]
    reason: str = ""
