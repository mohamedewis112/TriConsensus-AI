import math

import pytest

torch = pytest.importorskip("torch")

from triconsensus.v6_marl import MAPPOPolicyBundle, MAPPOTrainer, PPOConfig


def test_safe_prior_is_conservative_and_non_random():
    bundle = MAPPOPolicyBundle(
        portfolio_input_dim=15,
        risk_input_dim=8,
        global_state_dim=23,
    )
    bundle.initialize_safe_prior(policy_version="safe-test")

    portfolio = bundle.portfolio_action([0.0] * 15)
    risk = bundle.risk_action([0.0] * 8)

    assert portfolio == pytest.approx([0.95, 0.95, 0.05, 0.05], abs=1e-4)
    assert risk == pytest.approx([0.50, 1.0 / 3.0, 0.05, 0.05], abs=1e-4)
    assert bundle.policy_version == "safe-test"


def test_export_and_reload_preserve_policy_actions():
    source = MAPPOPolicyBundle(
        portfolio_input_dim=15,
        risk_input_dim=8,
        global_state_dim=23,
    )
    source.initialize_safe_prior(policy_version="safe-source")
    payload = source.export_payload()

    target = MAPPOPolicyBundle(
        portfolio_input_dim=15,
        risk_input_dim=8,
        global_state_dim=23,
    )
    target.load_payload(payload, policy_version="safe-reloaded")

    assert target.portfolio_action([0.0] * 15) == pytest.approx(
        source.portfolio_action([0.0] * 15),
        abs=1e-6,
    )
    assert target.risk_action([0.0] * 8) == pytest.approx(
        source.risk_action([0.0] * 8),
        abs=1e-6,
    )


def test_mappo_clipped_update_produces_finite_metrics():
    bundle = MAPPOPolicyBundle(
        portfolio_input_dim=15,
        risk_input_dim=8,
        global_state_dim=23,
    )
    bundle.initialize_safe_prior(policy_version="train-test")

    portfolio_states = torch.zeros((8, 15), dtype=torch.float32)
    risk_states = torch.zeros((8, 8), dtype=torch.float32)
    global_states = torch.zeros((8, 23), dtype=torch.float32)

    portfolio_actions = []
    risk_actions = []
    portfolio_log_probs = []
    risk_log_probs = []
    for _ in range(8):
        pa, plp = bundle.portfolio_action_with_log_prob([0.0] * 15, stochastic=True)
        ra, rlp = bundle.risk_action_with_log_prob([0.0] * 8, stochastic=True)
        portfolio_actions.append(pa)
        risk_actions.append(ra)
        portfolio_log_probs.append(plp)
        risk_log_probs.append(rlp)

    returns = torch.tensor(
        [0.010, -0.004, 0.006, 0.002, -0.003, 0.008, -0.001, 0.004],
        dtype=torch.float32,
    )
    with torch.no_grad():
        values = bundle.critic(global_states)
        advantages = returns - values
        advantages = (advantages - advantages.mean()) / advantages.std(
            unbiased=False
        ).clamp_min(1e-6)

    metrics = MAPPOTrainer(bundle, PPOConfig()).update(
        portfolio_states=portfolio_states,
        portfolio_actions=torch.tensor(portfolio_actions, dtype=torch.float32),
        portfolio_old_log_probs=torch.tensor(portfolio_log_probs, dtype=torch.float32),
        risk_states=risk_states,
        risk_actions=torch.tensor(risk_actions, dtype=torch.float32),
        risk_old_log_probs=torch.tensor(risk_log_probs, dtype=torch.float32),
        global_states=global_states,
        returns=returns,
        advantages=advantages,
    )

    assert set(metrics) == {
        "portfolio_actor_loss",
        "risk_actor_loss",
        "critic_loss",
        "entropy",
    }
    assert all(math.isfinite(value) for value in metrics.values())


def test_safe_prior_can_start_from_learned_per_asset_caps_without_increasing_risk():
    bundle = MAPPOPolicyBundle(
        portfolio_input_dim=15,
        risk_input_dim=8,
        global_state_dim=23,
    )
    bundle.initialize_safe_prior(
        policy_version="parallel-safe-prior",
        portfolio_weight_caps={
            "BTC/USDT": 0.70,
            "ETH/USDT": 0.0,
        },
    )

    portfolio = bundle.portfolio_action([0.0] * 15)
    assert portfolio[0] == pytest.approx(0.70, abs=1e-4)
    assert portfolio[1] <= 1e-4
    assert portfolio[2] == pytest.approx(0.05, abs=1e-4)
    assert portfolio[3] == pytest.approx(0.95, abs=1e-4)
    assert portfolio[0] <= 0.95
    assert portfolio[1] <= 0.95
