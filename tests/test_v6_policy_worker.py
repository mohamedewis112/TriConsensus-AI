import pytest

from triconsensus.v6_policy_worker import _extract_transition


def test_policy_worker_extracts_exact_behavior_transition():
    row = {
        "state_json": {
            "portfolio_state": {
                "as_of": "2026-10-28T14:00:00+00:00",
                "assets": {
                    "BTC/USDT": {
                        "symbol": "BTC/USDT",
                        "sample_size": 20,
                        "hit_rate": 0.55,
                        "mean_net_return": 0.001,
                        "cumulative_net_return": 0.02,
                        "max_drawdown": 0.01,
                        "losing_streak": 1,
                    },
                    "ETH/USDT": {
                        "symbol": "ETH/USDT",
                        "sample_size": 20,
                        "hit_rate": 0.45,
                        "mean_net_return": -0.001,
                        "cumulative_net_return": -0.02,
                        "max_drawdown": 0.03,
                        "losing_streak": 3,
                    },
                },
                "portfolio_drawdown": 0.02,
                "recent_trade_count": 40,
                "warmup_complete": True,
            },
            "volatility_state": {
                "symbol": "BTC/USDT",
                "side": "LONG",
                "entry_price": 80_000.0,
                "atr_14": 800.0,
                "realized_volatility": 0.50,
                "implied_volatility": 0.60,
                "bybit_orderbook_imbalance": 0.10,
                "binance_orderbook_imbalance": 0.05,
                "spread_bps": 1.2,
                "market_regime": "trend_up",
                "microstructure_age_ms": 120,
            },
        },
        "action_json": {
            "portfolio": {
                "policy_action": [0.80, 0.20, 0.05, 0.90],
                "policy_log_prob": -1.25,
            },
            "dynamic_risk": {
                "policy_action": [0.50, 0.40, 0.30, 0.10],
                "policy_log_prob": -0.75,
            },
        },
        "reward": 0.004,
    }

    transition = _extract_transition(row)
    assert transition is not None
    assert len(transition["portfolio_state"]) == 15
    assert len(transition["risk_state"]) == 8
    assert len(transition["global_state"]) == 23
    assert transition["portfolio_action"] == pytest.approx([0.80, 0.20, 0.05, 0.90])
    assert transition["risk_action"] == pytest.approx([0.50, 0.40, 0.30, 0.10])
    assert transition["portfolio_log_prob"] == pytest.approx(-1.25)
    assert transition["risk_log_prob"] == pytest.approx(-0.75)
    assert transition["reward"] == pytest.approx(0.004)
