from __future__ import annotations

import asyncio

import triconsensus.chronos_runtime as runtime


class _Settings:
    triconsensus_runtime_gateway_url = "https://example.invalid"


def _matched_report() -> dict:
    rows = []
    for horizon in (5, 15, 30):
        rows.append(
            {
                "horizon_seconds": horizon,
                "improvement_per_opportunity": 0.001,
                "baseline_mean_net": -0.001,
                "filtered_mean_net_per_opportunity": 0.0,
                "baseline_max_drawdown": 0.02,
                "filtered_max_drawdown": 0.01,
                "vetoed_losses_fraction": 0.8,
                "vetoed_winners_fraction": 0.2,
            }
        )
    return {
        "generated_at": "2026-11-04T21:00:00+00:00",
        "research_only": True,
        "paper_trading_authority": False,
        "live_trading_authority": False,
        "gate_before": {
            "matched": True,
            "latest_closed_candle_start_ms": 123,
        },
        "gate_after": {"matched": True},
        "verdict": "REGIME_MATCH_TIMING_HELPED_THIS_EPISODE",
        "ab": {"horizons": rows},
    }


def test_no_match_never_attempts_persistence(monkeypatch):
    async def fake_run():
        return {
            "research_only": True,
            "paper_trading_authority": False,
            "live_trading_authority": False,
            "gate_before": {"matched": False},
            "verdict": "NO_MATCH_NO_AB_TEST",
            "ab": None,
        }

    class FakeGateway:
        def __init__(self, *_args, **_kwargs):
            pass

        async def chronos_record(self, _payload):
            raise AssertionError("NO_MATCH must not be persisted as an episode")

        async def chronos_list(self):
            return {"episodes": []}

    monkeypatch.setattr(runtime, "run_regime_gated_ab", fake_run)
    monkeypatch.setattr(runtime, "PaperRuntimeGateway", FakeGateway)
    result = asyncio.run(
        runtime.run_chronos_runtime_cycle(
            _Settings(),
            vercel_oidc_token="oidc",
        )
    )
    assert result["report"]["verdict"] == "NO_MATCH_NO_AB_TEST"
    assert result["persistence"]["attempted"] is False
    assert result["ledger"]["episode_count"] == 0


def test_match_is_recorded_and_ledgered(monkeypatch):
    report = _matched_report()

    async def fake_run():
        return report

    class FakeGateway:
        def __init__(self, *_args, **_kwargs):
            pass

        async def chronos_record(self, payload):
            assert payload is report
            return {"persisted": True, "duplicate": False}

        async def chronos_list(self):
            return {"episodes": [report]}

    monkeypatch.setattr(runtime, "run_regime_gated_ab", fake_run)
    monkeypatch.setattr(runtime, "PaperRuntimeGateway", FakeGateway)
    result = asyncio.run(
        runtime.run_chronos_runtime_cycle(
            _Settings(),
            vercel_oidc_token="oidc",
        )
    )
    assert result["persistence"]["persisted"] is True
    assert result["ledger"]["episode_count"] == 1
    assert result["ledger"]["evidence_qualified"] is False
