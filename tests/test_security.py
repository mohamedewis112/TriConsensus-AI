from pydantic import SecretStr

from triconsensus.security import valid_control_key


def test_control_key_requires_configured_secret():
    assert valid_control_key(None, "anything") is False


def test_control_key_rejects_wrong_value():
    assert valid_control_key(SecretStr("correct"), "wrong") is False


def test_control_key_accepts_exact_value():
    assert valid_control_key(SecretStr("correct"), "correct") is True
