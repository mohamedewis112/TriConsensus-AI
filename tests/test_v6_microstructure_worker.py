from __future__ import annotations

import time

import pytest

from triconsensus.config import Settings
from triconsensus.v6_microstructure import MicrostructureSnapshot
from triconsensus.v6_microstructure_worker import V6MicrostructureWorker


class FakeRepo:
    def __init__(self):
        self.latest_rows = []
        self.history_rows = []

    def upsert_v6_microstructure_latest(self, payload):
        self.latest_rows.append(payload)
        return payload

    def insert(self, table, payload):
        assert table == "v6_microstructure_snapshots"
        self.history_rows.append(payload)
        return payload


@pytest.mark.asyncio
async def test_microstructure_worker_keeps_fresh_latest_and_sampled_history():
    settings = Settings(
        _env_file=None,
        v6_microstructure_latest_interval_ms=1000,
        v6_microstructure_persist_interval_ms=60000,
    )
    worker = object.__new__(V6MicrostructureWorker)
    worker.settings = settings
    worker.repo = FakeRepo()
    worker.last_persisted_ms = {}
    worker.last_latest_ms = {}
    worker.latest = {}

    now_ms = int(time.time() * 1000)
    snapshot = MicrostructureSnapshot(
        venue="bybit",
        symbol="BTCUSDT",
        exchange_timestamp_ms=now_ms,
        received_timestamp_ms=now_ms,
        bid_depth=10.0,
        ask_depth=8.0,
        imbalance=(10.0 - 8.0) / 18.0,
        spread_bps=1.0,
        sequence=1,
    )

    await worker._persist(snapshot)
    await worker._persist(snapshot)

    assert len(worker.repo.latest_rows) == 1
    assert len(worker.repo.history_rows) == 1
    assert worker.repo.latest_rows[0]["symbol"] == "BTC/USDT"
    assert worker.repo.latest_rows[0]["payload"]["state_kind"] == "latest"
    assert worker.repo.history_rows[0]["payload"]["state_kind"] == "history"
