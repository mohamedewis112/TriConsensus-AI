import pytest

from triconsensus.llm import _decode_json, _extract_structured_payload


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
