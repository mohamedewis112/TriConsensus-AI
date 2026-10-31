from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .config import Settings

T = TypeVar("T", bound=BaseModel)
TOOL_NAME = "submit_structured_response"


class LLMFailure(RuntimeError):
    pass


class LLMProvider(ABC):
    @abstractmethod
    async def structured(self, *, system: str, user: str, schema: type[T]) -> T:
        raise NotImplementedError


def _decode_json(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise ValueError("structured payload is neither string nor object")

    text = value.strip()
    fence = chr(96) * 3
    if text.startswith(fence):
        lines = text.splitlines()
        if lines and lines[0].startswith(fence):
            lines = lines[1:]
        if lines and lines[-1].strip() == fence:
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


def _extract_structured_payload(body: dict) -> dict:
    message = body["choices"][0]["message"]
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        function = tool_calls[0].get("function") or {}
        if function.get("name") != TOOL_NAME:
            raise ValueError("unexpected tool call")
        return _decode_json(function.get("arguments"))

    content = message.get("content")
    if not content:
        raise ValueError("model returned neither tool call nor JSON content")
    return _decode_json(content)


class VercelAIGatewayProvider(LLMProvider):
    def __init__(self, settings: Settings, *, oidc_token: str | None = None) -> None:
        self.s = settings
        self.oidc_token = oidc_token

    async def structured(self, *, system: str, user: str, schema: type[T]) -> T:
        token_value = self.oidc_token
        if not token_value and self.s.vercel_oidc_token:
            token_value = self.s.vercel_oidc_token.get_secret_value()
        if not token_value:
            raise LLMFailure("VERCEL_OIDC_TOKEN missing")

        tool = {
            "type": "function",
            "function": {
                "name": TOOL_NAME,
                "description": "Return the requested structured result exactly matching this schema.",
                "parameters": schema.model_json_schema(),
            },
        }
        payload = {
            "model": self.s.vercel_ai_gateway_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "tools": [tool],
            "tool_choice": {
                "type": "function",
                "function": {"name": TOOL_NAME},
            },
            "temperature": 0,
            "max_tokens": 1200,
        }
        headers = {
            "Authorization": f"Bearer {token_value}",
            "Content-Type": "application/json",
        }
        errors: list[str] = []

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            for attempt in range(2):
                try:
                    response = await client.post(
                        f"{self.s.vercel_ai_gateway_base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                    response.raise_for_status()
                    parsed = _extract_structured_payload(response.json())
                    return schema.model_validate(parsed)
                except (
                    httpx.HTTPError,
                    KeyError,
                    TypeError,
                    ValueError,
                    json.JSONDecodeError,
                    ValidationError,
                ) as exc:
                    errors.append(f"{self.s.vercel_ai_gateway_model}#{attempt + 1}:{type(exc).__name__}")
                    if attempt == 0:
                        await asyncio.sleep(0.5)

        raise LLMFailure("Vercel AI Gateway failed: " + ";".join(errors))


def build_llm_provider(
    settings: Settings,
    *,
    vercel_oidc_token: str | None = None,
) -> LLMProvider:
    if settings.llm_provider == "vercel_ai_gateway":
        return VercelAIGatewayProvider(settings, oidc_token=vercel_oidc_token)
    return OpenRouterProvider(settings)


class OpenRouterProvider(LLMProvider):
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def models(self) -> list[str]:
        fallbacks = [x.strip() for x in self.s.model_fallbacks.split(",") if x.strip()]
        ordered = [self.s.model_id, *fallbacks]
        return list(dict.fromkeys(ordered))

    async def structured(self, *, system: str, user: str, schema: type[T]) -> T:
        if not self.s.openrouter_api_key:
            raise LLMFailure("OPENROUTER_API_KEY missing")

        tool = {
            "type": "function",
            "function": {
                "name": TOOL_NAME,
                "description": "Return the requested trading-agent result exactly matching this schema.",
                "parameters": schema.model_json_schema(),
            },
        }
        headers = {
            "Authorization": f"Bearer {self.s.openrouter_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        errors: list[str] = []

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            for model in self.models():
                for attempt in range(2):
                    payload = {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "tools": [tool],
                        "tool_choice": {
                            "type": "function",
                            "function": {"name": TOOL_NAME},
                        },
                        "temperature": 0,
                        "max_completion_tokens": 1200,
                    }

                    try:
                        response = await client.post(
                            f"{self.s.openrouter_base_url}/chat/completions",
                            json=payload,
                            headers=headers,
                        )
                        response.raise_for_status()
                        parsed = _extract_structured_payload(response.json())
                        return schema.model_validate(parsed)
                    except (
                        httpx.HTTPError,
                        KeyError,
                        TypeError,
                        ValueError,
                        json.JSONDecodeError,
                        ValidationError,
                    ) as exc:
                        errors.append(f"{model}#{attempt + 1}:{type(exc).__name__}")
                        if attempt == 0:
                            await asyncio.sleep(0.5)

        raise LLMFailure("all configured models failed: " + ";".join(errors))
