from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
import json
import time

from .config import Settings
from .db import PersistenceFailure, SupabaseRepository
from .v6_microstructure import (
    BinanceOrderbookStream,
    BybitOrderbookStream,
    MicrostructureSnapshot,
)


def _display_symbol(symbol: str) -> str:
    normalized = symbol.upper().replace("/", "").replace("-", "")
    mapping = {"BTCUSDT": "BTC/USDT", "ETHUSDT": "ETH/USDT"}
    if normalized not in mapping:
        raise ValueError("unsupported V6 microstructure symbol")
    return mapping[normalized]


class V6MicrostructureWorker:
    """Cross-venue stream collector kept outside the 15m decision path."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repo = SupabaseRepository(settings)
        self.bybit = BybitOrderbookStream(settings.bybit_ws_url)
        self.binance = BinanceOrderbookStream(settings.binance_ws_url)
        self.last_persisted_ms: dict[tuple[str, str], int] = {}
        self.last_latest_ms: dict[tuple[str, str], int] = {}
        self.latest: dict[tuple[str, str], MicrostructureSnapshot] = {}

    async def _persist(self, snapshot: MicrostructureSnapshot) -> None:
        display = _display_symbol(snapshot.symbol)
        key = (snapshot.venue, display)
        self.latest[key] = snapshot

        now_ms = int(time.time() * 1000)
        payload = {
            "venue": snapshot.venue,
            "symbol": display,
            "exchange_timestamp_ms": snapshot.exchange_timestamp_ms,
            "received_timestamp_ms": snapshot.received_timestamp_ms,
            "sequence": snapshot.sequence,
            "bid_depth": snapshot.bid_depth,
            "ask_depth": snapshot.ask_depth,
            "imbalance": snapshot.imbalance,
            "spread_bps": snapshot.spread_bps,
        }
        checksum = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        captured_at = datetime.fromtimestamp(
            snapshot.received_timestamp_ms / 1000,
            tz=UTC,
        ).isoformat()

        previous_latest = self.last_latest_ms.get(key, 0)
        if (
            now_ms - previous_latest
            >= self.settings.v6_microstructure_latest_interval_ms
        ):
            latest_row = {
                **payload,
                "captured_at": captured_at,
                "payload": {
                    "collector": "v6_cross_venue_ws",
                    "state_kind": "latest",
                    "latest_interval_ms": (
                        self.settings.v6_microstructure_latest_interval_ms
                    ),
                },
                "checksum": checksum,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            try:
                await asyncio.to_thread(
                    self.repo.upsert_v6_microstructure_latest,
                    latest_row,
                )
                self.last_latest_ms[key] = now_ms
            except PersistenceFailure:
                # Missing latest state is fail-closed for any future enforcement.
                pass

        previous_history = self.last_persisted_ms.get(key, 0)
        if (
            now_ms - previous_history
            < self.settings.v6_microstructure_persist_interval_ms
        ):
            return

        history_row = {
            **payload,
            "captured_at": captured_at,
            "payload": {
                "collector": "v6_cross_venue_ws",
                "state_kind": "history",
                "persist_interval_ms": (
                    self.settings.v6_microstructure_persist_interval_ms
                ),
            },
            "checksum": checksum,
        }
        try:
            await asyncio.to_thread(
                self.repo.insert,
                "v6_microstructure_snapshots",
                history_row,
            )
            self.last_persisted_ms[key] = now_ms
        except PersistenceFailure:
            # Collection failures never enter the decision path. Enforcement sees
            # stale/missing microstructure and fails closed.
            return

    async def run_symbol(self, symbol: str) -> None:
        await asyncio.gather(
            self.bybit.run(symbol, self._persist),
            self.binance.run(symbol, self._persist),
        )

    async def run(self) -> None:
        await asyncio.gather(
            self.run_symbol("BTCUSDT"),
            self.run_symbol("ETHUSDT"),
        )


def main() -> None:
    asyncio.run(V6MicrostructureWorker(Settings()).run())


if __name__ == "__main__":
    main()
