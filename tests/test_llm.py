import pytest

from triconsensus.config import Settings
from triconsensus.llm import (
    OpenRouterProvider,
    VercelAIGatewayProvider,
    _decode_json,
    _extract_structured_payload,
    build_llm_provider,
)


def test_decode_plain_json():
    assert _decode_json('{"decision":"WAIT"}') == {"decision": "WAIT"}


def test_extract_forced_tool_call():
    body = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "submit_structured_response",
                                "arguments": '{"confidence":0.8}',
                            }
                        }
                    ]
                }
            }
        ]
    }
    assert _extract_structured_payload(body) == {"confidence": 0.8}


def test_unexpected_tool_call_fails_closed():
    body = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {"function": {"name": "other_tool", "arguments": "{}"}}
                    ]
                }
            }
        ]
    }
    with pytest.raises(ValueError):
        _extract_structured_payload(body)


def test_provider_factory_defaults_to_openrouter():
    settings = Settings(_env_file=None)
    assert isinstance(build_llm_provider(settings), OpenRouterProvider)


def test_provider_factory_supports_vercel_ai_gateway():
    settings = Settings(_env_file=None, llm_provider="vercel_ai_gateway")
    provider = build_llm_provider(settings)
    assert isinstance(provider, VercelAIGatewayProvider)
    assert settings.vercel_ai_gateway_model == "nvidia/nemotron-3.5-lightning-free"
