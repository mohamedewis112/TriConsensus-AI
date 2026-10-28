from datetime import UTC, datetime

import pytest

from triconsensus.config import Settings
from triconsensus.cycle import PaperDecisionCycle
from triconsensus.domain import (
    Bias,
    DevilsAdvocate,
    MarketIntel,
    RiskVerdict,
    TradeDecision,
    TradeProposal,
)
from triconsensus.execution import PaperExecutionEngine
from triconsensus.orchestrator import DecisionOrchestrator
from triconsensus.risk import DeterministicRiskEngine
from triconsensus.v51_forward import V51_MODEL_FAMILY
from triconsensus.binance_live import V51LiveSignal


class FakeRepo:
    def __init__(self):
        self.rows = []

    def ensure_experiment(self, *, version, config):
        assert version == "V5.1-PAPER"
        assert config["mode"] == "paper"
        return {
            "id": "00000000-0000-0000-0000-000000000100",
            "version": version,
            "config_hash": "test",
        }

    def list_trades(self, experiment_id, *, status=None, limit=500):
        assert experiment_id == "00000000-0000-0000-0000-000000000100"
        return []

    def get_active_strategy_calibration(
        self,
        *,
        strategy_version,
        symbol,
        as_of,
        calibration_end_on_or_before,
    ):
        assert strategy_version == "V5.1"
        assert symbol == "BTC/USDT"
        return {
            "id": "00000000-0000-0000-0000-000000000200",
            "strategy_version": "V5.1",
            "feature_name": "global_long_short_change_1h",
            "relationship": "negative",
            "low_threshold": -0.01,
            "high_threshold": 0.01,
            "calibration_end": "2026-10-26",
            "horizon_minutes": 180,
            "context_lag_minutes": 15,
            "round_trip_cost": 0.0015,
            "config_hash": "frozen-test-hash",
        }

    def recent_binance_global_ratio(
        self,
        *,
        symbol,
        observed_at_or_before,
        limit=96,
    ):
        assert symbol == "BTCUSDT"
        return []

    def insert(self, table, payload):
        row = {"id": f"{table}-{len(self.rows) + 1}", **payload}
        self.rows.append((table, row))
        return row


class FakeStrategyContext:
    def signal_from_points(
        self,
        symbol,
        points,
        *,
        low_threshold,
        high_threshold,
        now_ms=None,
    ):
        assert symbol == "BTCUSDT"
        assert low_threshold == -0.01
        assert high_threshold == 0.01
        assert now_ms is not None
        return V51LiveSignal(
            symbol=symbol,
            signal="LONG",
            feature_name="global_long_short_change_1h",
            feature_value=-0.02,
            context_timestamp_ms=now_ms - 900_000,
            context_age_ms=900_000,
            low_threshold=low_threshold,
            high_threshold=high_threshold,
            reason="bottom_decile_inverse_signal",
        )


class FakeMarket:
    @staticmethod
    def _candles():
        rows = []
        for i in range(80):
            close = 100.0 + (i * 0.08) + ((i % 5) * 0.015)
            rows.append(
                {
                    "start_ms": 1_700_000_000_000 + i * 900_000,
                    "open": close - 0.04,
                    "high": close + 2.00,
                    "low": close - 2.00,
                    "close": close,
                    "volume": 1000.0 + (i % 7) * 17.0,
                    "turnover": close * (1000.0 + (i % 7) * 17.0),
                }
            )
        return rows

    async def multi_timeframe_snapshot(self, symbol):
        assert symbol == "BTCUSDT"
        candles = {
            timeframe: self._candles()
            for timeframe in ("1m", "5m", "15m", "1h", "4h")
        }
        return {
            "ticker": {
                "symbol": symbol,
                "last_price": candles["15m"][-1]["close"],
                "bid": candles["15m"][-1]["close"] - 0.01,
                "ask": candles["15m"][-1]["close"] + 0.01,
                "funding_rate": 0.0001,
                "open_interest": 1_000_000.0,
                "server_time_ms": 1_700_000_000_000,
                "request_started_ms": 1_700_000_000_000,
                "received_ms": 1_700_000_000_100,
                "captured_at": datetime.now(UTC).isoformat(),
            },
            "orderbook": {
                "spread_bps": 1.0,
                "imbalance": 0.1,
                "bid_depth": 500.0,
                "ask_depth": 450.0,
            },
            "candles": candles,
            "market_data_age_ms": 100,
        }


class ApprovingAgents:
    async def market_intelligence(self, context):
        assert context["ml_evidence"]["model_family"] == V51_MODEL_FAMILY
        assert context["ml_evidence"]["signal"] == "LONG"
        return MarketIntel(
            market_regime="trend",
            bias=Bias.BULLISH,
            confidence=0.90,
            evidence=["synthetic_v51_long"],
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
            evidence=["approved_server_levels"],
        )

    async def risk_advocate(self, context):
        return DevilsAdvocate(
            verdict=RiskVerdict.APPROVE,
            confidence=0.85,
            evidence=["no_material_veto"],
        )


@pytest.mark.asyncio
async def test_synthetic_v51_full_consensus_opens_paper_trade_only():
    settings = Settings(_env_file=None)
    repo = FakeRepo()

    cycle = object.__new__(PaperDecisionCycle)
    cycle.settings = settings
    cycle.market = FakeMarket()
    cycle.strategy_context = FakeStrategyContext()
    cycle.repo = repo
    cycle.orchestrator = DecisionOrchestrator(
        ApprovingAgents(),
        DeterministicRiskEngine(settings),
    )
    cycle.execution = PaperExecutionEngine()

    result = await cycle.run("BTCUSDT")

    assert result["outcome"] == "LONG"
    assert result["full_consensus"] is True
    assert result["risk"]["approved"] is True
    assert result["trade_id"].startswith("paper_trades-")
    assert result["paper_fill"]["side"] == "LONG"

    tables = [table for table, _ in repo.rows]
    assert tables.count("strategy_signal_snapshots") == 1
    assert tables.count("market_snapshots") == 1
    assert tables.count("feature_snapshots") == 1
    assert tables.count("agent_runs") == 3
    assert tables.count("consensus_decisions") == 1
    assert tables.count("risk_evaluations") == 1
    assert tables.count("paper_trades") == 1
    assert tables.count("trade_events") == 1

    trade = next(row for table, row in repo.rows if table == "paper_trades")
    assert trade["status"] == "open"
    assert trade["side"] == "LONG"
    assert trade["position_size"] > 0
    assert trade["estimated_fees"] > 0
    assert trade["estimated_slippage"] > 0

    event = next(row for table, row in repo.rows if table == "trade_events")
    assert event["event_type"] == "PAPER_OPEN"
