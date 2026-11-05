import pytest

from triconsensus.freqtrade_guard import (
    assert_freqtrade_safe,
    validate_freqtrade_runtime,
)


def safe_config():
    return {
        "dry_run": True,
        "trading_mode": "futures",
        "margin_mode": "isolated",
        "max_open_trades": 1,
        "force_entry_enable": False,
        "exchange": {
            "name": "bybit",
            "pair_whitelist": ["BTC/USDT:USDT", "ETH/USDT:USDT"],
        },
    }


def test_safe_freqtrade_config_passes():
    status = validate_freqtrade_runtime(safe_config())
    assert status.safe is True
    assert status.force_entry_enabled is False


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("dry_run", False, "dry_run_not_enabled"),
        ("trading_mode", "spot", "not_futures_mode"),
        ("margin_mode", "cross", "margin_mode_not_isolated"),
        ("max_open_trades", 2, "max_open_trades_not_one"),
    ],
)
def test_unsafe_modes_fail_closed(field, value, reason):
    config = safe_config()
    config[field] = value
    status = validate_freqtrade_runtime(config)
    assert status.safe is False
    assert status.reason == reason


def test_unapproved_pair_fails_closed():
    config = safe_config()
    config["exchange"]["pair_whitelist"].append("SOL/USDT:USDT")
    assert validate_freqtrade_runtime(config).reason == "unapproved_pair_in_whitelist"


def test_force_entry_cannot_be_used_until_explicitly_enabled():
    with pytest.raises(RuntimeError, match="force_entry_disabled"):
        assert_freqtrade_safe(safe_config(), require_force_entry=True)


def test_live_config_is_never_accepted_even_if_force_entry_enabled():
    config = safe_config()
    config["dry_run"] = False
    config["force_entry_enable"] = True
    with pytest.raises(RuntimeError, match="dry_run_not_enabled"):
        assert_freqtrade_safe(config, require_force_entry=True)
