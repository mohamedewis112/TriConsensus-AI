from __future__ import annotations

import numpy as np

from triconsensus.chronos import MarketState
from triconsensus.chronos_fast_forward import FastHeadLedger
from triconsensus.chronos_learning import TransitionForecast


def _state(i: int, ofi: float, depth: float, mid: float) -> MarketState:
    return MarketState(
        symbol="ETHUSDT",
        timestamp_ms=1_800_000_000_000 + i * 1_000,
        mid_price=mid,
        bid_depth=depth * (1 + ofi) / 2,
        ask_depth=depth * (1 - ofi) / 2,
        order_flow_imbalance=ofi,
        spread_bps=1.2,
    )


def test_fast_head_ledger_detects_skill_against_naive_baselines():
    rng = np.random.default_rng(20)
    ledger = FastHeadLedger()
    mid = 3200.0
    state = _state(0, 0.20, 2_000_000.0, mid)
    for i in range(1, 90):
        next_ofi = float(np.clip(0.70 * state.order_flow_imbalance + rng.normal(0, 0.02), -1, 1))
        next_depth = state.total_depth * (1.0 + 0.10 * state.depth_imbalance)
        next_mid = state.mid_price * (1.0 + rng.normal(0, 0.0001))
        forecast = TransitionForecast(
            next_return_mean=0.0,
            next_return_std=0.0001,
            next_return_low=-0.0003,
            next_return_high=0.0003,
            next_order_flow_mean=next_ofi + float(rng.normal(0, 0.005)),
            next_order_flow_std=0.02,
            next_order_flow_low=next_ofi - 0.08,
            next_order_flow_high=next_ofi + 0.08,
            next_liquidity_change_mean=0.10 * state.depth_imbalance,
            next_liquidity_change_std=0.02,
            next_liquidity_change_low=0.10 * state.depth_imbalance - 0.08,
            next_liquidity_change_high=0.10 * state.depth_imbalance + 0.08,
            model_version="test-fast-head",
            sample_count=200,
        )
        receipt = ledger.register(symbol="ETHUSDT", state=state, forecast=forecast)
        next_state = _state(i, next_ofi, next_depth, next_mid)
        ledger.resolve(receipt, next_state, state.mid_price)
        state = next_state

    audit = ledger.audit(symbol="ETHUSDT", training_transitions=200)
    assert audit.receipt_chain_valid is True
    assert audit.orderflow.relative_skill > 0.0
    assert audit.liquidity.relative_skill > 0.0
    assert audit.orderflow.qualified is True
    assert audit.liquidity.qualified is True
    assert audit.fast_execution_evidence_qualified is True
    assert audit.paper_trading_authority is False
    assert audit.live_trading_authority is False
