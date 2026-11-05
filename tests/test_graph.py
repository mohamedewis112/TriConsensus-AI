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


class ApprovingAgents:
    async def market_intelligence(self, context):
        return MarketIntel(
            market_regime="trend",
            bias=Bias.BULLISH,
            confidence=0.9,
            evidence=["trend"],
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
            confidence=0.8,
            evidence=["quant_levels"],
        )

    async def risk_advocate(self, context):
        return DevilsAdvocate(
            verdict=RiskVerdict.APPROVE,
            confidence=0.8,
            evidence=["no_material_veto"],
        )


class VetoAgents(ApprovingAgents):
    async def risk_advocate(self, context):
        return DevilsAdvocate(
            verdict=RiskVerdict.VETO,
            confidence=0.9,
            reason="test veto",
            evidence=["risk_flag"],
        )


class ShouldNotRunAgents:
    async def market_intelligence(self, context):
        raise AssertionError("agents must not run while ML is untrained")


def state():
    settings = Settings()
    levels = build_trade_levels(
        last_price=100,
        atr=2,
        min_risk_reward=settings.min_risk_reward,
    )
    return settings, levels, {
        "symbol": "BTC/USDT",
        "market_context": {"market_data_age_ms": 100},
        "quant_features": {
            "spread_bps": 1,
            "realized_vol_20": 0.02,
        },
        "ml_evidence": {
            "probability_positive": 0.6,
            "model_family": "lightgbm",
        },
        "account_state": {
            "equity": 10000,
            "day_start_equity": 10000,
            "peak_equity": 10000,
            "daily_pnl": 0,
            "open_positions": 0,
            "current_exposure": 0,
            "correlated_exposure": 0,
            "losing_streak": 0,
            "minutes_since_last_loss": 999999,
            "kill_switch_active": False,
            "slippage_bps": 2,
        },
        "allowed_levels": levels,
    }


@pytest.mark.asyncio
async def test_langgraph_full_consensus_reaches_risk_engine():
    settings, _, inputs = state()
    graph = TriConsensusGraph(ApprovingAgents(), DeterministicRiskEngine(settings))
    result = await graph.decide(**inputs)
    assert result["outcome"] == "LONG"
    assert result["consensus_outcome"] == "LONG"
    assert result["full_consensus"] is True
    assert result["risk"]["approved"] is True


@pytest.mark.asyncio
async def test_langgraph_veto_routes_to_no_trade():
    settings, _, inputs = state()
    graph = TriConsensusGraph(VetoAgents(), DeterministicRiskEngine(settings))
    result = await graph.decide(**inputs)
    assert result["outcome"] == "NO_TRADE"
    assert result["consensus_outcome"] == "NO_TRADE"
    assert result["risk"] is None


@pytest.mark.asyncio
async def test_untrained_ml_fails_before_agents():
    settings, _, inputs = state()
    inputs["ml_evidence"] = {
        "probability_positive": 0.5,
        "model_family": "untrained_neutral_baseline",
    }
    graph = TriConsensusGraph(ShouldNotRunAgents(), DeterministicRiskEngine(settings))
    result = await graph.decide(**inputs)
    assert result["outcome"] == "NO_TRADE"
    assert result["reason"] == "ml_untrained"


@pytest.mark.asyncio
async def test_risk_rejection_keeps_full_agent_consensus_in_audit():
    settings, _, inputs = state()
    inputs["account_state"]["kill_switch_active"] = True
    graph = TriConsensusGraph(ApprovingAgents(), DeterministicRiskEngine(settings))
    result = await graph.decide(**inputs)
    assert result["outcome"] == "NO_TRADE"
    assert result["consensus_outcome"] == "LONG"
    assert result["full_consensus"] is True
    assert result["consensus_reason"] == "Full bullish consensus"
    assert result["risk"]["approved"] is False
    assert result["reason"] == "kill_switch_active"


@pytest.mark.asyncio
async def test_v51_no_trade_signal_fails_before_agents():
    settings, _, inputs = state()
    inputs["ml_evidence"] = {
        "model_family": V51_MODEL_FAMILY,
        "signal": "NO_TRADE",
    }
    graph = TriConsensusGraph(ShouldNotRunAgents(), DeterministicRiskEngine(settings))
    result = await graph.decide(**inputs)
    assert result["outcome"] == "NO_TRADE"
    assert result["reason"] == "strategy_signal_no_trade"


@pytest.mark.asyncio
async def test_v51_signal_must_match_agent_consensus():
    settings, _, inputs = state()
    inputs["ml_evidence"] = {
        "model_family": V51_MODEL_FAMILY,
        "signal": "SHORT",
    }
    graph = TriConsensusGraph(ApprovingAgents(), DeterministicRiskEngine(settings))
    result = await graph.decide(**inputs)
    assert result["outcome"] == "NO_TRADE"
    assert result["consensus_outcome"] == "NO_TRADE"
    assert result["reason"] == "strategy_signal_disagreement"
