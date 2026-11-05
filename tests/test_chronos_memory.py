from __future__ import annotations

import numpy as np

from triconsensus.chronos import MarketState
from triconsensus.chronos_learning import ChronosTransition
from triconsensus.chronos_memory import (
    AlphaMemoryOutcome,
    MarketMemoryIndex,
    episodes_from_transitions,
)


def _transitions(n: int = 240):
    rng = np.random.default_rng(44)
    rows = []
    base = 1_800_000_000_000
    for i in range(n):
        cluster = 1.0 if i % 3 else -1.0
        ofi = float(np.clip(cluster * 0.45 + rng.normal(0, 0.08), -1, 1))
        ret5 = float(cluster * 0.003 + rng.normal(0, 0.0008))
        state = MarketState(
            symbol="ETHUSDT",
            timestamp_ms=base + i * 60_000,
            mid_price=3200.0,
            return_1m=0.35 * ret5,
            return_5m=ret5,
            realized_volatility=0.015,
            bid_depth=1200.0 + 180 * ofi,
            ask_depth=1100.0 - 180 * ofi,
            order_flow_imbalance=ofi,
            spread_bps=1.5,
            funding_rate=0.00005 * cluster,
            open_interest_change=0.01 * cluster,
            liquidation_imbalance=0.1 * cluster,
            cross_venue_basis_bps=1.5 * cluster,
            cross_venue_latency_ms=70.0,
            stablecoin_flow_z=0.4 * cluster,
            dealer_gamma_z=-0.2,
            mempool_pressure_z=0.1,
            crowding_score=0.2 * cluster,
        )
        rows.append(
            ChronosTransition(
                state=state,
                next_return=0.004 * ofi + 0.4 * ret5,
                next_order_flow=float(np.clip(0.7 * ofi, -1, 1)),
                next_liquidity_change=-0.08 * abs(ofi),
            )
        )
    return rows


def test_memory_never_uses_future_episode():
    rows = _transitions()
    index = MarketMemoryIndex(minimum_neighbors=5, similarity_floor=0.50, max_neighbors=20)
    index.fit_transitions(rows)
    query = rows[100].state
    recall = index.recall(query)
    assert recall.neighbor_count > 0
    assert all(neighbor.timestamp_ms < query.timestamp_ms for neighbor in recall.neighbors)
    assert recall.paper_trading_authority is False
    assert recall.live_trading_authority is False


def test_memory_recalls_repeating_market_state_and_weighted_outcome():
    rows = _transitions()
    index = MarketMemoryIndex(minimum_neighbors=8, similarity_floor=0.60, max_neighbors=30)
    index.fit_transitions(rows)
    query = rows[-1].state
    recall = index.recall(query)
    assert recall.neighbor_count >= 8
    assert recall.recurrence_score >= 0.60
    assert recall.usable is True
    assert np.sign(recall.weighted_next_order_flow) == np.sign(query.order_flow_imbalance)


def test_memory_can_rank_alpha_and_recurring_latent_actor_without_leakage():
    rows = _transitions()
    alpha = {}
    latent = {}
    for row in rows:
        if row.state.timestamp_ms % (3 * 60_000) != 0:
            alpha[row.state.timestamp_ms] = (
                AlphaMemoryOutcome(
                    alpha_id="trend_breakout_eth",
                    net_return=0.004,
                    profit_factor_proxy=1.6,
                    survived=True,
                ),
                AlphaMemoryOutcome(
                    alpha_id="mean_reversion_eth",
                    net_return=-0.002,
                    profit_factor_proxy=0.7,
                    survived=False,
                ),
            )
            latent[row.state.timestamp_ms] = ("LATENT-WHALE-01",)
    episodes = episodes_from_transitions(
        rows,
        alpha_outcomes_by_timestamp=alpha,
        latent_actor_ids_by_timestamp=latent,
    )
    index = MarketMemoryIndex(minimum_neighbors=5, similarity_floor=0.50, max_neighbors=40)
    index.fit(episodes)
    recall = index.recall(rows[-1].state)
    assert recall.top_alpha in {"trend_breakout_eth", None}
    if recall.alpha_scores:
        assert recall.alpha_scores[0].weighted_mean_net_return > 0
    if recall.recurring_latent_actors:
        assert "LATENT-WHALE-01" in recall.recurring_latent_actors
