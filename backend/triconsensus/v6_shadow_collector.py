from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from .config import Settings, TradingMode
from .db import PersistenceFailure, SupabaseRepository
from .market_data import BybitPublicClient, MarketDataFailure
from .v6_microstructure_worker import V6MicrostructureWorker


class V6ShadowCollector:
    """Always-on V6 evidence collector with no trading authority."""

    def __init__(self, settings: Settings) -> None:
        if settings.trading_mode != TradingMode.PAPER:
            raise RuntimeError("V6 shadow collector is restricted to paper mode")
        if not settings.v6_marl_observation_active:
            raise RuntimeError("V6 shadow observation is disabled")
        if settings.v6_marl_enforcement_active or settings.v6_marl_enabled:
            raise RuntimeError("V6 shadow collector refuses enforcement-enabled config")

        self.settings = settings
        self.repo = SupabaseRepository(settings)
        self.market = BybitPublicClient(settings)

    async def _capture_iv(self, symbol: str) -> dict | None:
        display = {"BTCUSDT": "BTC/USDT", "ETHUSDT": "ETH/USDT"}[symbol]
        try:
            snapshot = await self.market.implied_volatility(symbol)
            row = {
                "symbol": display,
                "source": str(snapshot["source"]),
                "captured_at": str(snapshot["captured_at"]),
                "mark_iv": float(snapshot["mark_iv"]),
                "expiry": snapshot.get("expiry"),
                "sample_count": int(snapshot.get("sample_count") or 0),
                "payload": {
                    **snapshot,
                    "collector": "v6_shadow_collector",
                    "observed_at": datetime.now(UTC).isoformat(),
                },
            }
            await asyncio.to_thread(
                self.repo.insert,
                "v6_volatility_snapshots",
                row,
            )
            return row
        except (MarketDataFailure, PersistenceFailure, KeyError, TypeError, ValueError):
            return None

    async def _iv_loop(self) -> None:
        while True:
            await asyncio.gather(
                self._capture_iv("BTCUSDT"),
                self._capture_iv("ETHUSDT"),
            )
            await asyncio.sleep(self.settings.v6_iv_poll_seconds)

    async def _microstructure_loop(self) -> None:
        backoff = 1
        while True:
            worker = V6MicrostructureWorker(self.settings)
            try:
                await worker.run()
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def run(self) -> None:
        await asyncio.gather(
            self._microstructure_loop(),
            self._iv_loop(),
        )


def main() -> None:
    asyncio.run(V6ShadowCollector(Settings()).run())


if __name__ == "__main__":
    main()
