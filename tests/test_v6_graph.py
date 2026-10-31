import pytest

from triconsensus.config import Settings
from triconsensus.domain import (
    Bias,
    DevilsAdvocate,
    MarketIntel,
    RiskVerdict,
    TradeDecision,
    TradeProposal,
)
from triconsensus.graph import TriConsensusGraph
from triconsensus.levels import build_trade_levels
from triconsensus.risk import DeterministicRiskEngine
from triconsensus.v51_forward import V51_MODEL_FAMILY
from triconsensus.v6_agents import (
    DynamicVolatilityRiskAgent,
    MetaRLPortfolioAdapterAgent,
)


class ApprovingAgents:
    async def market_intelligence(self, context):
        return MarketIntel(
            market_regime="trend",
            bias=Bias.BULLISH,
            confidence=0.9,
            evidence=["v6_shadow_test"],
        )

    async def strategist(self, context):
        levels = context["allowed_trade_levels"]["long"]
        return TradeProposal(
            decision=TradeDecision.LONG,
            symbol=context["symbol"],
            entry_zone=[levels["entry"]],
            stop_loss=levels["stop_loss"],
            take_profit_1=levels["take_profit_1"],
            take_profit_2=levels["take_profit_2"],
            risk_reward=1.5,
            confidence=0.85,
            evidence=["server_levels_only"],
        )

    async def risk_advocate(self, context):
        return DevilsAdvocate(
            verdict=RiskVerdict.APPROVE,
            confidence=0.85,
            evidence=["no_veto"],
        )


class AgentsMustNotRun:
    async def market_intelligence(self, context):
        raise AssertionError("LLM agent must not run after V6 portfolio veto")

    async def strategist(self, context):
        raise AssertionError("strategist must not run after V6 portfolio veto")

    async def risk_advocate(self, context):
        raise AssertionError("critic must not run after V6 portfolio veto")


def account_state():
    return {
        "equity": 10_000.0,
        "day_start_equity": 10_000.0,
        "daily_pnl": 0.0,
        "peak_equity": 10_000.0,
        "open_positions": 0,
        "current_exposure": 0.0,
        "correlated_exposure": 0.0,
        "losing_streak": 0,
        "minutes_since_last_loss": 999999,
        "kill_switch_active": False,
        "slippage_bps": 2.0,
    }


def portfolio_state(*, eth_losses: bool = False):
    if eth_losses:
        eth = {
            "symbol": "ETH/USDT",
            "sample_size": 8,
            "hit_rate": 0.0,
            "mean_net_return": -0.004,
            "cumulative_net_return": -0.0315,
            "max_drawdown": 0.0315,
            "losing_streak": 8,
        }
    else:
        eth = {
            "symbol": "ETH/USDT",
            "sample_size": 0,
            "hit_rate": 0.0,
            "mean_net_return": 0.0,
            "cumulative_net_return": 0.0,
            "max_drawdown": 0.0,
            "losing_streak": 0,
        }
    return {
        "as_of": "2026-10-28T14:00:00+00:00",
        "assets": {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "sample_size": 0,
                "hit_rate": 0.0,
                "mean_net_return": 0.0,
                "cumulative_net_return": 0.0,
                "max_drawdown": 0.0,
                "losing_streak": 0,
            },
            "ETH/USDT": eth,
        },
        "portfolio_drawdown": 0.0,
        "recent_trade_count": 8 if eth_losses else 0,
        "warmup_complete": False,
        "source": "test",
    }


def volatility_state(symbol: str, side: str):
    return {
        "symbol": symbol,
        "side": side,
        "entry_price": 100.0,
        "atr_14": 2.0,
        "realized_volatility": 0.4,
        "implied_volatility": None,
        "bybit_orderbook_imbalance": 0.1,
        "binance_orderbook_imbalance": None,
        "spread_bps": 1.0,
        "market_regime": "trend_up",
        "microstructure_age_ms": None,
    }


@pytest.mark.asyncio
async def test_v6_shadow_nodes_preserve_v51_trade_and_audit_actions():
    settings = Settings(_env_file=None)
    levels = build_trade_levels(
        last_price=100.0,
        atr=2.0,
        min_risk_reward=settings.min_risk_reward,
    )
    graph = TriConsensusGraph(
        ApprovingAgents(),
        DeterministicRiskEngine(settings),
        portfolio_adapter=MetaRLPortfolioAdapterAgent(shadow_mode=True),
        dynamic_risk_agent=DynamicVolatilityRiskAgent(shadow_mode=True),
    )

    result = await graph.decide(
        symbol="BTC/USDT",
        market_context={
            "ticker": {"last_price": 100.0},
            "market_data_age_ms": 100,
        },
        quant_features={
            "atr_14": 2.0,
            "spread_bps": 1.0,
            "realized_vol_20": 0.02,
        },
        ml_evidence={
            "model_family": V51_MODEL_FAMILY,
            "signal": "LONG",
        },
        account_state=account_state(),
        allowed_levels=levels,
        adaptive_context={
            "portfolio_state": portfolio_state(),
            "volatility_state": volatility_state("BTC/USDT", "LONG"),
        },
    )

    assert result["outcome"] == "LONG"
    assert result["risk"]["approved"] is True
    assert result["v6_adaptive"]["portfolio"]["shadow_mode"] is True
    assert result["v6_adaptive"]["portfolio"]["asset_weights"]["BTC/USDT"] == pytest.approx(1.0)
    assert result["v6_adaptive"]["dynamic_risk"]["shadow_mode"] is True


@pytest.mark.asyncio
async def test_v6_enforcement_blacklist_stops_before_llm_agents():
    settings = Settings(_env_file=None)
    levels = build_trade_levels(
        last_price=100.0,
        atr=2.0,
        min_risk_reward=settings.min_risk_reward,
    )
    graph = TriConsensusGraph(
        AgentsMustNotRun(),
        DeterministicRiskEngine(settings),
        portfolio_adapter=MetaRLPortfolioAdapterAgent(
            shadow_mode=False,
            minimum_blacklist_samples=8,
            hard_losing_streak=4,
            hard_mean_net_return=-0.003,
        ),
        dynamic_risk_agent=DynamicVolatilityRiskAgent(shadow_mode=False),
    )

    result = await graph.decide(
        symbol="ETH/USDT",
        market_context={
            "ticker": {"last_price": 100.0},
            "market_data_age_ms": 100,
        },
        quant_features={
            "atr_14": 2.0,
            "spread_bps": 1.0,
            "realized_vol_20": 0.02,
        },
        ml_evidence={
            "model_family": V51_MODEL_FAMILY,
            "signal": "SHORT",
        },
        account_state=account_state(),
        allowed_levels=levels,
        adaptive_context={
            "portfolio_state": portfolio_state(eth_losses=True),
            "volatility_state": volatility_state("ETH/USDT", "SHORT"),
        },
    )

    assert result["outcome"] == "NO_TRADE"
    assert result["reason"] == "v6_portfolio_gate_no_trade"
    assert result["full_consensus"] is False
    assert result["v6_adaptive"]["portfolio"]["asset_weights"]["ETH/USDT"] == pytest.approx(0.0)
