import inspect
from pathlib import Path

import pytest

from triconsensus.config import Settings
from triconsensus.paper_runtime import (
    PaperRuntimeFailure,
    PaperRuntimeService,
    _reward_records,
)
import triconsensus.research_cycle as research_cycle
import triconsensus.runtime_gateway as runtime_gateway


def test_runtime_gateway_never_embeds_supabase_privileged_credentials():
    source = inspect.getsource(runtime_gateway)
    assert "SUPABASE_SERVICE_ROLE_KEY" not in source
    assert "TRICONSENSUS_SUPABASE_PUBLISHABLE_KEY" not in source
    assert "TRICONSENSUS_SUPABASE_URL" not in source
    assert "Authorization" in source
    assert "Bearer" in source


def test_paper_runtime_refuses_non_paper_mode():
    settings = Settings(
        _env_file=None,
        trading_mode="dry_run",
        triconsensus_runtime_gateway_url="https://example.invalid/gateway",
    )
    with pytest.raises(PaperRuntimeFailure):
        PaperRuntimeService(settings, vercel_oidc_token="oidc")


def test_paper_runtime_refuses_v6_enforcement():
    settings = Settings(
        _env_file=None,
        trading_mode="paper",
        v6_marl_enabled=True,
        v6_marl_shadow_mode=False,
        triconsensus_runtime_gateway_url="https://example.invalid/gateway",
    )
    with pytest.raises(PaperRuntimeFailure):
        PaperRuntimeService(settings, vercel_oidc_token="oidc")


def test_reward_memory_rows_are_rehydrated_for_portfolio_state():
    records = _reward_records(
        [
            {
                "symbol": "BTC/USDT",
                "trade_id": "t1",
                "policy_version": "v6-shadow",
                "state_json": {"x": 1},
                "action_json": {"shadow_mode": True},
                "net_return": 0.01,
                "max_drawdown": 0.002,
                "reward": 0.006,
                "terminal": True,
                "created_at": "2026-10-28T20:00:00+00:00",
            }
        ]
    )
    assert len(records) == 1
    assert records[0].symbol == "BTC/USDT"
    assert records[0].state == {"x": 1}
    assert records[0].action["shadow_mode"] is True


def test_research_candidate_requires_full_consensus():
    source = inspect.getsource(research_cycle.run_live_research_cycle)
    assert 'result.get("full_consensus", False)' in source
    assert "protocol_direction_match" in source
    assert "risk_approved" in source


def test_edge_gateway_is_locked_to_exact_vercel_preview_identity():
    source = Path(
        "supabase/functions/triconsensus-paper-runtime-gateway/index.ts"
    ).read_text()
    assert "https://oidc.vercel.com/smart-media1" in source
    assert "https://vercel.com/smart-media1" in source
    assert (
        "owner:smart-media1:project:triconsensus-runtime-preview:"
        "environment:preview"
    ) in source
    assert (
        "owner:smart-media1:project:triconsensus-runtime-preview:"
        "environment:production"
    ) in source
    assert 'req.method !== "POST"' in source
    assert "SUPABASE_SERVICE_ROLE_KEY" in source
    assert "paper_only: true" in source
    assert "live_trading: false" in source


def test_cron_surface_is_single_and_secured():
    source = Path("backend/triconsensus/api.py").read_text()
    assert '@app.get("/v1/runtime/cron")' in source
    assert '@app.get("/v1/runtime/status")' in source
    assert '@app.post("/v1/runtime-cycle/' not in source
    assert '@app.post("/v1/runtime-monitor")' not in source
    assert "CRON_SECRET" in source


def test_secondary_runtime_rpcs_are_disabled():
    source = Path(
        "supabase/migrations/20261028223549_disable_secondary_paper_runtime_rpcs.sql"
    ).read_text()
    assert "triconsensus_open_paper_trade" in source
    assert "triconsensus_monitor_paper_trade" in source
    assert "triconsensus_close_paper_trade" in source
    assert "from service_role" in source
