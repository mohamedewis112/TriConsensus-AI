from __future__ import annotations

import math
from typing import Iterable

import numpy as np

from .levels import TradeLevels, build_trade_levels
from .v6_marl import MAPPOPolicyBundle, MARLDependencyError, MARLPolicyNotReady
from .v6_models import (
    DynamicRiskAction,
    PortfolioAction,
    PortfolioState,
    SupportedSymbol,
    VolatilityRiskState,
)


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, float(value)))


class MetaRLPortfolioAdapterAgent:
    """Constrained MAPPO portfolio adapter.

    The agent can only preserve or reduce the base deterministic risk budget.
    It can never increase risk above the V5.1 deterministic engine ceiling.
    """

    def __init__(
        self,
        policy: MAPPOPolicyBundle | None = None,
        *,
        shadow_mode: bool = True,
        blacklist_probability: float = 0.80,
        minimum_blacklist_samples: int = 8,
        hard_losing_streak: int = 4,
        hard_mean_net_return: float = -0.003,
    ) -> None:
        self.policy = policy
        self.shadow_mode = shadow_mode
        self.blacklist_probability = blacklist_probability
        self.minimum_blacklist_samples = minimum_blacklist_samples
        self.hard_losing_streak = hard_losing_streak
        self.hard_mean_net_return = hard_mean_net_return

    @staticmethod
    def state_vector(state: PortfolioState) -> list[float]:
        values: list[float] = []
        for symbol in ("BTC/USDT", "ETH/USDT"):
            stats = state.assets[symbol]
            values.extend(
                [
                    stats.sample_size / 40.0,
                    stats.hit_rate,
                    stats.mean_net_return,
                    stats.cumulative_net_return,
                    stats.max_drawdown,
                    stats.losing_streak / 10.0,
                ]
            )
        values.extend(
            [
                state.portfolio_drawdown,
                state.recent_trade_count / 80.0,
                1.0 if state.warmup_complete else 0.0,
            ]
        )
        return values

    def _hard_blacklist(self, state: PortfolioState) -> list[SupportedSymbol]:
        blocked: list[SupportedSymbol] = []
        for symbol, stats in state.assets.items():
            if stats.sample_size < self.minimum_blacklist_samples:
                continue
            if (
                stats.losing_streak >= self.hard_losing_streak
                or stats.mean_net_return <= self.hard_mean_net_return
            ):
                blocked.append(symbol)
        return blocked

    def decide(self, state: PortfolioState) -> PortfolioAction:
        hard_blacklist = self._hard_blacklist(state)
        neutral = {"BTC/USDT": 1.0, "ETH/USDT": 1.0}
        for symbol in hard_blacklist:
            neutral[symbol] = 0.0

        if not state.warmup_complete:
            return PortfolioAction(
                asset_weights=neutral,
                blacklisted_assets=hard_blacklist,
                policy_version="v6-warmup",
                reason="warmup_insufficient_reward_memory",
                shadow_mode=self.shadow_mode,
            )

        if self.policy is None or not getattr(self.policy, "ready", False):
            if self.shadow_mode:
                return PortfolioAction(
                    asset_weights=neutral,
                    blacklisted_assets=hard_blacklist,
                    policy_version="v6-shadow-untrained",
                    reason="mappo_policy_not_ready_shadow_only",
                    shadow_mode=True,
                )
            return PortfolioAction(
                asset_weights={"BTC/USDT": 0.0, "ETH/USDT": 0.0},
                blacklisted_assets=["BTC/USDT", "ETH/USDT"],
                policy_version="v6-fail-closed",
                reason="mappo_policy_not_ready",
                shadow_mode=False,
            )

        try:
            raw_values, policy_log_prob = self.policy.portfolio_action_with_log_prob(
                self.state_vector(state),
                stochastic=self.shadow_mode,
            )
            raw = np.asarray(raw_values, dtype=float)
        except (MARLDependencyError, MARLPolicyNotReady, ValueError):
            if self.shadow_mode:
                return PortfolioAction(
                    asset_weights=neutral,
                    blacklisted_assets=hard_blacklist,
                    policy_version="v6-shadow-policy-failure",
                    reason="mappo_inference_failure_shadow_only",
                    shadow_mode=True,
                )
            return PortfolioAction(
                asset_weights={"BTC/USDT": 0.0, "ETH/USDT": 0.0},
                blacklisted_assets=["BTC/USDT", "ETH/USDT"],
                policy_version="v6-fail-closed",
                reason="mappo_inference_failure",
                shadow_mode=False,
            )

        if raw.shape != (4,) or not np.isfinite(raw).all():
            raise ValueError("MAPPO portfolio action must contain four finite values")

        weights = {
            "BTC/USDT": _clamp(raw[0], 0.0, 1.0),
            "ETH/USDT": _clamp(raw[1], 0.0, 1.0),
        }
        blacklisted: list[SupportedSymbol] = list(hard_blacklist)
        for symbol, probability in (
            ("BTC/USDT", raw[2]),
            ("ETH/USDT", raw[3]),
        ):
            stats = state.assets[symbol]
            if (
                stats.sample_size >= self.minimum_blacklist_samples
                and float(probability) >= self.blacklist_probability
                and symbol not in blacklisted
            ):
                blacklisted.append(symbol)

        for symbol in blacklisted:
            weights[symbol] = 0.0

        return PortfolioAction(
            asset_weights=weights,
            blacklisted_assets=blacklisted,
            policy_version=getattr(self.policy, "policy_version", "v6-mappo"),
            reason="mappo_projected_inside_deterministic_risk_envelope",
            shadow_mode=self.shadow_mode,
            policy_log_prob=policy_log_prob,
        )


class DynamicVolatilityRiskAgent:
    """Neuro-symbolic risk adapter.

    A neural action is projected through symbolic microstructure/volatility rules.
    Missing or stale evidence can only preserve baseline levels in shadow mode or
    force NO_TRADE in enforcement mode.
    """

    def __init__(
        self,
        policy: MAPPOPolicyBundle | None = None,
        *,
        shadow_mode: bool = True,
        max_microstructure_age_ms: int = 2_000,
        adverse_imbalance: float = 0.35,
    ) -> None:
        self.policy = policy
        self.shadow_mode = shadow_mode
        self.max_microstructure_age_ms = max_microstructure_age_ms
        self.adverse_imbalance = adverse_imbalance

    @staticmethod
    def state_vector(state: VolatilityRiskState) -> list[float]:
        iv = state.implied_volatility if state.implied_volatility is not None else 0.0
        bybit = (
            state.bybit_orderbook_imbalance
            if state.bybit_orderbook_imbalance is not None
            else 0.0
        )
        binance = (
            state.binance_orderbook_imbalance
            if state.binance_orderbook_imbalance is not None
            else 0.0
        )
        return [
            math.log(max(state.entry_price, 1e-9)),
            state.atr_14 / state.entry_price,
            state.realized_volatility,
            iv,
            bybit,
            binance,
            state.spread_bps / 100.0,
            1.0 if state.side == "LONG" else -1.0,
        ]

    def _symbolic_projection(
        self,
        state: VolatilityRiskState,
        action: DynamicRiskAction,
    ) -> DynamicRiskAction:
        stop = action.stop_atr_multiplier
        rr = action.reward_to_risk_multiplier
        trailing = action.trailing_lock_fraction
        force_no_trade = action.force_no_trade
        reasons = [action.reason]

        if (
            state.microstructure_age_ms is None
            or state.microstructure_age_ms > self.max_microstructure_age_ms
        ):
            if not self.shadow_mode:
                force_no_trade = True
                reasons.append("microstructure_stale")
            else:
                reasons.append("microstructure_stale_shadow")

        if state.implied_volatility is None:
            if not self.shadow_mode:
                force_no_trade = True
                reasons.append("implied_volatility_missing")
            else:
                reasons.append("implied_volatility_missing_shadow")
        elif state.realized_volatility > 0:
            vol_ratio = state.implied_volatility / state.realized_volatility
            if vol_ratio >= 1.25:
                stop = min(2.25, stop * min(1.25, 1.0 + (vol_ratio - 1.0) * 0.15))
                rr = max(0.75, rr * 0.90)
                trailing = min(0.90, max(trailing, 0.35))
                reasons.append("volatility_shock_widen_stop_tighten_profit")

        bybit = state.bybit_orderbook_imbalance
        binance = state.binance_orderbook_imbalance
        if bybit is not None and binance is not None:
            if (
                state.side == "LONG"
                and bybit <= -self.adverse_imbalance
                and binance <= -self.adverse_imbalance
            ):
                force_no_trade = True
                reasons.append("cross_venue_orderbook_veto_long")
            if (
                state.side == "SHORT"
                and bybit >= self.adverse_imbalance
                and binance >= self.adverse_imbalance
            ):
                force_no_trade = True
                reasons.append("cross_venue_orderbook_veto_short")

        return DynamicRiskAction(
            stop_atr_multiplier=_clamp(stop, 0.75, 2.25),
            reward_to_risk_multiplier=_clamp(rr, 0.75, 1.50),
            trailing_lock_fraction=_clamp(trailing, 0.0, 0.90),
            force_no_trade=force_no_trade,
            policy_version=action.policy_version,
            reason=";".join(reasons),
            shadow_mode=self.shadow_mode,
        )

    def decide(self, state: VolatilityRiskState) -> DynamicRiskAction:
        base = DynamicRiskAction(
            stop_atr_multiplier=1.5,
            reward_to_risk_multiplier=1.0,
            trailing_lock_fraction=0.0,
            force_no_trade=False,
            policy_version="v6-symbolic-baseline",
            reason="baseline_before_neural_policy",
            shadow_mode=self.shadow_mode,
        )

        if self.policy is None or not getattr(self.policy, "ready", False):
            return self._symbolic_projection(state, base)

        try:
            raw = np.asarray(self.policy.risk_action(self.state_vector(state)), dtype=float)
        except (MARLDependencyError, MARLPolicyNotReady, ValueError):
            failed = base.model_copy(
                update={
                    "reason": "mappo_risk_inference_failure",
                    "force_no_trade": not self.shadow_mode,
                }
            )
            return self._symbolic_projection(state, failed)

        if raw.shape != (4,) or not np.isfinite(raw).all():
            raise ValueError("MAPPO risk action must contain four finite values")

        neural = DynamicRiskAction(
            stop_atr_multiplier=0.75 + (_clamp(raw[0], 0, 1) * 1.50),
            reward_to_risk_multiplier=0.75 + (_clamp(raw[1], 0, 1) * 0.75),
            trailing_lock_fraction=_clamp(raw[2], 0, 1) * 0.90,
            force_no_trade=bool(raw[3] >= 0.80),
            policy_version=getattr(self.policy, "policy_version", "v6-mappo"),
            reason="mappo_raw_action",
            shadow_mode=self.shadow_mode,
        )
        return self._symbolic_projection(state, neural)

    @staticmethod
    def adapt_levels(
        *,
        last_price: float,
        atr: float,
        minimum_risk_reward: float,
        action: DynamicRiskAction,
    ) -> TradeLevels:
        projected_rr = max(
            minimum_risk_reward,
            minimum_risk_reward * action.reward_to_risk_multiplier,
        )
        return build_trade_levels(
            last_price=last_price,
            atr=atr,
            min_risk_reward=projected_rr,
            stop_atr=action.stop_atr_multiplier,
        )
