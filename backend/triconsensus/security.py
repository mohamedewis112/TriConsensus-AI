from __future__ import annotations

import hmac

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader
from pydantic import SecretStr

from .config import settings


control_header = APIKeyHeader(
    name="X-TriConsensus-Key",
    scheme_name="TriConsensusControlKey",
    auto_error=False,
)


def valid_control_key(expected: SecretStr | None, provided: str | None) -> bool:
    if expected is None or not provided:
        return False
    expected_value = expected.get_secret_value()
    return bool(expected_value) and hmac.compare_digest(expected_value, provided)


async def require_control_key(
    provided: str | None = Security(control_header),
) -> None:
    if settings.control_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="control API is disabled until CONTROL_API_KEY is configured",
        )
    if not valid_control_key(settings.control_api_key, provided):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid control API key",
            headers={"WWW-Authenticate": "APIKey"},
        )
