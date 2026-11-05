import asyncio

from triconsensus.derivatives_context import (
    AccountRatioPoint,
    BybitDerivativesContextClient,
    DerivativesPoint,
)


class FakeClient(BybitDerivativesContextClient):
    def __init__(self):
        super().__init__(base_url="https://example.invalid")
        self.calls = []

    async def _get(self, path, params):
        self.calls.append((path, dict(params)))
        if path.endswith("/open-interest"):
            if params.get("cursor") == "next":
                return {
                    "retCode": 0,
                    "result": {
                        "list": [
                            {"timestamp": "2000", "openInterest": "120"},
                        ],
                        "nextPageCursor": "",
                    },
                }
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        {"timestamp": "1000", "openInterest": "100"},
                    ],
                    "nextPageCursor": "next",
                },
            }
        if path.endswith("/account-ratio"):
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "timestamp": "1000",
                            "buyRatio": "0.60",
                            "sellRatio": "0.40",
                        }
                    ],
                    "nextPageCursor": "",
                },
            }
        if path.endswith("/funding/history"):
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "fundingRateTimestamp": "1000",
                            "fundingRate": "0.0001",
                        }
                    ]
                },
            }
        if path.endswith("/premium-index-price-kline"):
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        ["1000", "0.1", "0.2", "0.0", "0.15"],
                    ]
                },
            }
        raise AssertionError(path)


def test_open_interest_paginates_and_sorts():
    client = FakeClient()
    rows = asyncio.run(
        client.open_interest_15m(
            "BTCUSDT",
            start_ms=1,
            end_ms=3000,
        )
    )
    assert rows == [
        DerivativesPoint(timestamp_ms=1000, value=100.0),
        DerivativesPoint(timestamp_ms=2000, value=120.0),
    ]


def test_account_ratio_parses_percentages():
    client = FakeClient()
    rows = asyncio.run(
        client.account_ratio_15m(
            "ETHUSDT",
            start_ms=1,
            end_ms=3000,
        )
    )
    assert rows == [
        AccountRatioPoint(
            timestamp_ms=1000,
            buy_ratio=0.60,
            sell_ratio=0.40,
        )
    ]


def test_snapshot_history_returns_all_context_series():
    client = FakeClient()
    result = asyncio.run(
        client.snapshot_history(
            "BTCUSDT",
            start_ms=1,
            end_ms=3000,
        )
    )
    assert set(result) == {
        "open_interest",
        "account_ratio",
        "funding",
        "premium_index",
    }
    assert result["funding"][0].value == 0.0001
    assert result["premium_index"][0].value == 0.15
