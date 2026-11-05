import pytest

from triconsensus.config import Settings
from triconsensus.v6_shadow_collector import V6ShadowCollector


class FakeRepo:
    def __init__(self):
        self.rows = []

    def insert(self, table, payload):
        self.rows.append((table, payload))
        return payload


class FakeMarket:
    async def implied_volatility(self, symbol):
        return {
            "base_coin": "BTC" if symbol == "BTCUSDT" else "ETH",
            "mark_iv": 0.55,
            "expiry": "2026-11-01T00:00:00+00:00",
            "sample_count": 4,
            "open_interest_weight": 100.0,
            "captured_at": "2026-10-28T20:00:00+00:00",
            "source": "bybit_option_tickers_mark_iv",
        }


def _settings(**overrides):
    values = {
        "_env_file": None,
        "trading_mode": "paper",
        "v6_marl_observe": True,
        "v6_marl_enabled": False,
        "v6_marl_shadow_mode": True,
    }
    values.update(overrides)
    return Settings(**values)


def test_collector_refuses_enforcement_enabled_config():
    with pytest.raises(RuntimeError, match="enforcement-enabled"):
        V6ShadowCollector(
            _settings(v6_marl_enabled=True, v6_marl_shadow_mode=False)
        )


def test_collector_refuses_non_paper_mode():
    with pytest.raises(RuntimeError, match="paper mode"):
        V6ShadowCollector(_settings(trading_mode="dry_run"))


def test_collector_refuses_disabled_observation():
    with pytest.raises(RuntimeError, match="observation is disabled"):
        V6ShadowCollector(_settings(v6_marl_observe=False))


@pytest.mark.asyncio
async def test_capture_iv_persists_shadow_evidence_without_trade_action():
    collector = object.__new__(V6ShadowCollector)
    collector.settings = _settings()
    collector.repo = FakeRepo()
    collector.market = FakeMarket()

    row = await collector._capture_iv("BTCUSDT")

    assert row is not None
    assert row["symbol"] == "BTC/USDT"
    assert row["mark_iv"] == pytest.approx(0.55)
    assert collector.repo.rows[0][0] == "v6_volatility_snapshots"
    assert collector.repo.rows[0][1]["payload"]["collector"] == "v6_shadow_collector"
