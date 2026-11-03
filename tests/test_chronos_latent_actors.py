from __future__ import annotations

import numpy as np

from triconsensus.chronos_latent_actors import (
    LatentActorArchetypeMiner,
    ResidualEvent,
)


def test_latent_actor_miner_recovers_recurrent_hidden_behavior_without_identity_claim():
    rng = np.random.default_rng(2700)
    events = []
    base = 1_800_000_000_000
    for i in range(240):
        actor = i % 2
        residual = (
            0.45 + rng.normal(0, 0.05)
            if actor == 0
            else -0.40 + rng.normal(0, 0.05)
        )
        next_flow = (
            0.55 * residual + rng.normal(0, 0.02)
        )
        events.append(
            ResidualEvent(
                timestamp_ms=base + i * 30_000,
                residual_order_flow=float(residual),
                return_1m=float((0.002 if actor == 0 else -0.002) + rng.normal(0, 0.0005)),
                basis_bps=float((3.0 if actor == 0 else -2.5) + rng.normal(0, 0.4)),
                spread_bps=float(1.5 + rng.normal(0, 0.15)),
                depth_imbalance=float((0.25 if actor == 0 else -0.2) + rng.normal(0, 0.05)),
                funding_rate=0.00005 if actor == 0 else -0.00004,
                open_interest_change=0.02 if actor == 0 else -0.015,
                next_order_flow=float(next_flow),
            )
        )
    result = LatentActorArchetypeMiner(
        clusters=2,
        minimum_events=80,
        minimum_cluster_size=20,
        seed=12,
    ).discover(events)
    assert len(result.archetypes) == 2
    assert result.paper_trading_authority is False
    assert result.live_trading_authority is False
    assert all(actor.identity_claim is False for actor in result.archetypes)
    assert all(actor.actor_id.startswith("LATENT-ACTOR-") for actor in result.archetypes)
    assert max(actor.persistence for actor in result.archetypes) > 0.80
    assert max(abs(actor.directional_lead_score) for actor in result.archetypes) > 0.5
