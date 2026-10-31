from triconsensus.candidate_replication import evaluate_preregistered_candidates


def manifest():
    return {
        "experiment": "V4.1-CANDIDATE-REPLICATION",
        "setup_version": "v4.1-frozen-quant-setups",
        "replication_window": {"days": 90},
        "interpretation": {
            "directional_replication_rule": {
                "minimum_replication_trades": 10,
                "profit_factor_minimum": 1.1,
                "minimum_fold_coverage": 3,
                "minimum_profitable_fold_fraction": 0.67,
            }
        },
        "candidates": [
            {
                "symbol": "BTCUSDT",
                "setup": "breakout",
                "regime": "range|high_vol|momentum_down",
                "side": "SHORT",
            }
        ],
    }


def test_preregistered_candidate_directionally_replicates_without_activation():
    reports = {
        "BTCUSDT": {
            "ranked": [
                {
                    "setup": "breakout",
                    "regime": "range|high_vol|momentum_down",
                    "side": "SHORT",
                    "trades": 20,
                    "mean_net_return": 0.001,
                    "profit_factor": 1.5,
                    "folds": 4,
                    "profitable_folds": 3,
                    "bootstrap_lcb_95": -0.0001,
                    "qualified": False,
                }
            ]
        }
    }
    out = evaluate_preregistered_candidates(
        manifest=manifest(),
        reports_by_symbol=reports,
    )
    assert out["directionally_replicated_count"] == 1
    assert out["full_evidence_pass_count"] == 0
    assert out["default_action"] == "NO_TRADE"


def test_preregistered_candidate_missing_is_failure():
    out = evaluate_preregistered_candidates(
        manifest=manifest(),
        reports_by_symbol={"BTCUSDT": {"ranked": []}},
    )
    assert out["directionally_replicated_count"] == 0
    assert out["results"][0]["reason"] == "candidate_absent_in_replication"


def test_full_evidence_pass_is_recorded_but_policy_stays_fail_closed():
    reports = {
        "BTCUSDT": {
            "ranked": [
                {
                    "setup": "breakout",
                    "regime": "range|high_vol|momentum_down",
                    "side": "SHORT",
                    "trades": 60,
                    "mean_net_return": 0.001,
                    "profit_factor": 1.5,
                    "folds": 4,
                    "profitable_folds": 4,
                    "bootstrap_lcb_95": 0.0002,
                    "qualified": True,
                }
            ]
        }
    }
    out = evaluate_preregistered_candidates(
        manifest=manifest(),
        reports_by_symbol=reports,
    )
    assert out["full_evidence_pass_count"] == 1
    assert out["default_action"] == "NO_TRADE"
