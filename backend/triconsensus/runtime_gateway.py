from __future__ import annotations

from typing import Any

import httpx

from .config import Settings


class RuntimeGatewayFailure(RuntimeError):
    pass


class PaperRuntimeGateway:
    """Narrow OIDC-authenticated bridge to the Supabase paper-runtime gateway."""

    def __init__(self, settings: Settings, *, vercel_oidc_token: str | None) -> None:
        self.settings = settings
        self.oidc_token = vercel_oidc_token
        self.url = (settings.triconsensus_runtime_gateway_url or "").rstrip("/")

    async def _call(self, action: str, payload: dict[str, Any] | None = None) -> dict:
        if not self.url:
            raise RuntimeGatewayFailure("TRICONSENSUS_RUNTIME_GATEWAY_URL missing")
        if not self.oidc_token:
            raise RuntimeGatewayFailure("Vercel OIDC token missing")

        body: dict[str, Any] = {"action": action}
        if payload is not None:
            body["payload"] = payload

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                response = await client.post(
                    self.url,
                    headers={
                        "Authorization": f"Bearer {self.oidc_token}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise RuntimeGatewayFailure(
                f"runtime gateway request failed: {type(exc).__name__}"
            ) from exc

        if not isinstance(data, dict) or not data.get("ok"):
            raise RuntimeGatewayFailure("runtime gateway returned invalid response")
        result = data.get("result")
        if not isinstance(result, dict):
            raise RuntimeGatewayFailure("runtime gateway result missing")
        return result

    async def snapshot(self) -> dict:
        return await self._call("snapshot")

    async def commit(self, payload: dict[str, Any]) -> dict:
        return await self._call("commit", payload)

    async def monitor(self, payload: dict[str, Any]) -> dict:
        return await self._call("monitor", payload)
