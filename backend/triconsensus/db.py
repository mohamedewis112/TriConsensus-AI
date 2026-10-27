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
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise PersistenceFailure("Supabase backend credentials are missing")
        self.client: Client = create_client(
            settings.supabase_url,
            settings.supabase_service_role_key.get_secret_value(),
        )

    @staticmethod
    def config_hash(config: dict[str, Any]) -> str:
        raw = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()

    def ensure_experiment(self, *, version: str, config: dict[str, Any]) -> dict:
        existing = (
            self.client.table("experiments")
            .select("*")
            .eq("version", version)
            .limit(1)
            .execute()
            .data
        )
        digest = self.config_hash(config)
        if existing:
            row = existing[0]
            if row["config_hash"] != digest:
                raise PersistenceFailure(
                    f"immutable experiment {version} has a different configuration"
                )
            return row

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
