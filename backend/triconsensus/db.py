from __future__ import annotations

import hashlib
import json
from typing import Any

from supabase import Client, create_client

from .config import Settings


class PersistenceFailure(RuntimeError):
    pass


class SupabaseRepository:
    def __init__(self, settings: Settings) -> None:
        backend_key = settings.supabase_secret_key or settings.supabase_service_role_key
        if not settings.supabase_url or backend_key is None:
            raise PersistenceFailure("Supabase backend credentials are missing")
        self.client: Client = create_client(
            settings.supabase_url,
            backend_key.get_secret_value(),
        )

    @staticmethod
    def config_hash(config: dict[str, Any]) -> str:
        raw = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()

    def get_experiment(self, version: str) -> dict | None:
        try:
            rows = (
                self.client.table("experiments")
                .select("*")
                .eq("version", version)
                .limit(1)
                .execute()
                .data
            )
        except Exception as exc:
            raise PersistenceFailure(f"experiment read failed: {exc}") from exc
        return rows[0] if rows else None

    def ensure_experiment(self, *, version: str, config: dict[str, Any]) -> dict:
        existing = self.get_experiment(version)
        digest = self.config_hash(config)
        if existing:
            if existing["config_hash"] != digest:
                raise PersistenceFailure(
                    f"immutable experiment {version} has a different configuration"
                )
            return existing

        result = (
            self.client.table("experiments")
            .insert(
                {
                    "version": version,
                    "status": "forward_test",
                    "execution_mode": "paper",
                    "config": config,
                    "config_hash": digest,
                }
            )
            .execute()
            .data
        )
        if not result:
            raise PersistenceFailure("failed to create experiment")
        return result[0]

    def insert(self, table: str, payload: dict[str, Any]) -> dict:
        try:
            rows = self.client.table(table).insert(payload).execute().data
        except Exception as exc:
            raise PersistenceFailure(f"{table} insert failed: {exc}") from exc
        if not rows:
            raise PersistenceFailure(f"{table} insert returned no row")
        return rows[0]

    def update_trade(self, trade_id: str, payload: dict[str, Any]) -> dict:
        try:
            rows = (
                self.client.table("paper_trades")
                .update(payload)
                .eq("id", trade_id)
                .execute()
                .data
            )
        except Exception as exc:
            raise PersistenceFailure(f"paper_trades update failed: {exc}") from exc
        if not rows:
            raise PersistenceFailure("paper_trades update returned no row")
        return rows[0]

    def list_trades(
        self,
        experiment_id: str,
        *,
        status: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        try:
            query = (
                self.client.table("paper_trades")
                .select("*")
                .eq("experiment_id", experiment_id)
                .order("created_at", desc=True)
                .limit(limit)
            )
            if status is not None:
                query = query.eq("status", status)
            return query.execute().data or []
        except Exception as exc:
            raise PersistenceFailure(f"paper_trades read failed: {exc}") from exc

    def get_active_strategy_calibration(
        self,
        *,
        strategy_version: str,
        symbol: str,
        as_of: str,
        calibration_end_on_or_before: str,
    ) -> dict | None:
        try:
            rows = (
                self.client.table("strategy_calibrations")
                .select("*")
                .eq("strategy_version", strategy_version)
                .eq("symbol", symbol)
                .lte("calibration_end", calibration_end_on_or_before)
                .gt("expires_at", as_of)
                .order("calibration_end", desc=True)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
                .data
            )
        except Exception as exc:
            raise PersistenceFailure(
                f"strategy calibration read failed: {exc}"
            ) from exc
        return rows[0] if rows else None

    def recent_binance_global_ratio(
        self,
        *,
        symbol: str,
        observed_at_or_before: str,
        limit: int = 96,
    ) -> list[dict]:
        try:
            rows = (
                self.client.table("market_binance_global_ratio_5m")
                .select(
                    "observed_at,long_short_ratio,long_account,short_account,source"
                )
                .eq("symbol", symbol)
                .lte("observed_at", observed_at_or_before)
                .order("observed_at", desc=True)
                .limit(limit)
                .execute()
                .data
            )
        except Exception as exc:
            raise PersistenceFailure(
                f"Binance global ratio read failed: {exc}"
            ) from exc
        return list(reversed(rows or []))

    def trade_events_for_trade(self, trade_id: str) -> list[dict]:
        try:
            rows = (
                self.client.table("trade_events")
                .select("*")
                .eq("trade_id", trade_id)
                .order("event_time")
                .execute()
                .data
            )
        except Exception as exc:
            raise PersistenceFailure(f"trade event read failed: {exc}") from exc
        return rows or []

    def recent_marl_reward_memory(self, *, limit: int = 80) -> list[dict]:
        try:
            rows = (
                self.client.table("marl_reward_memory")
                .select("*")
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
                .data
            )
        except Exception as exc:
            raise PersistenceFailure(f"marl reward memory read failed: {exc}") from exc
        return list(reversed(rows or []))

    def dequeue_marl_rewards(
        self,
        *,
        visibility_timeout: int = 300,
        quantity: int = 32,
    ) -> list[dict]:
        try:
            rows = (
                self.client.rpc(
                    "v6_dequeue_marl_rewards",
                    {"p_vt": visibility_timeout, "p_qty": quantity},
                )
                .execute()
                .data
            )
        except Exception as exc:
            raise PersistenceFailure(f"MARL reward dequeue failed: {exc}") from exc
        return rows or []

    def archive_marl_reward(self, msg_id: int) -> bool:
        try:
            result = (
                self.client.rpc(
                    "v6_archive_marl_reward",
                    {"p_msg_id": int(msg_id)},
                )
                .execute()
                .data
            )
        except Exception as exc:
            raise PersistenceFailure(f"MARL reward archive failed: {exc}") from exc
        if isinstance(result, list):
            return bool(result[0]) if result else False
        return bool(result)

    def latest_marl_policy(self, agent_role: str) -> dict | None:
        try:
            rows = (
                self.client.table("marl_policy_versions")
                .select("*")
                .eq("agent_role", agent_role)
                .eq("status", "active")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
                .data
            )
        except Exception as exc:
            raise PersistenceFailure(f"marl policy read failed: {exc}") from exc
        return rows[0] if rows else None

    def recent_v6_microstructure(
        self,
        *,
        symbol: str,
        limit: int = 20,
    ) -> list[dict]:
        try:
            rows = (
                self.client.table("v6_microstructure_snapshots")
                .select("*")
                .eq("symbol", symbol)
                .order("captured_at", desc=True)
                .limit(limit)
                .execute()
                .data
            )
        except Exception as exc:
            raise PersistenceFailure(f"microstructure read failed: {exc}") from exc
        return list(reversed(rows or []))

    def decision_replay(self, consensus_id: str) -> dict | None:
        try:
            rows = (
                self.client.table("decision_replay")
                .select("*")
                .eq("consensus_id", consensus_id)
                .limit(1)
                .execute()
                .data
            )
        except Exception as exc:
            raise PersistenceFailure(f"decision replay read failed: {exc}") from exc
        return rows[0] if rows else None


    def latest_ml_model(self, symbol: str) -> dict | None:
        try:
            rows = (
                self.client.table("ml_models")
                .select("*")
                .eq("symbol", symbol)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
                .data
            )
        except Exception as exc:
            raise PersistenceFailure(f"ml_models read failed: {exc}") from exc
        return rows[0] if rows else None
