from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .config import Settings

T = TypeVar("T", bound=BaseModel)


class LLMFailure(RuntimeError):
    pass


class LLMProvider(ABC):
    @abstractmethod
    async def structured(self, *, system: str, user: str, schema: type[T]) -> T:
        raise NotImplementedError


class OpenRouterProvider(LLMProvider):
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    async def structured(self, *, system: str, user: str, schema: type[T]) -> T:
        if not self.s.openrouter_api_key:
            raise LLMFailure("OPENROUTER_API_KEY missing")

        payload = {
            "model": self.s.model_id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.s.openrouter_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                response = await client.post(
                    f"{self.s.openrouter_base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                return schema.model_validate(json.loads(content))
        except (httpx.HTTPError, KeyError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            raise LLMFailure(str(exc)) from exc
