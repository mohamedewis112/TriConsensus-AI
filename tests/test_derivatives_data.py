import asyncio

import pytest

from triconsensus.config import Settings
from triconsensus.derivatives_data import (
    AccountRatioPoint,
    BybitDerivativesClient,
    FundingPoint,
    OpenInterestPoint,
    align_derivatives_to_15m,
)


class FakeClient(BybitDerivativesClient):
    def __init__(self, responses):
        super().__init__(Settings())
        self.responses = list(responses)
        self.calls = []

    async def _get(self, path, params):
        self.calls.append((path, dict(params)))
        if not self.responses:
            raise AssertionError("unexpected extra request")
        return self.responses.pop(0)


def test_funding_paginates_backwards_without_duplicates():
    client = FakeClient([
        {
            "list": [
                {"fundingRateTimestamp": "3000", "fundingRate": "0.0002"},
                {"fundingRateTimestamp": "2000", "fundingRate": "0.0001"},
            ]
        },
        {
            "list": [
                {"fundingRateTimestamp": "1000", "fundingRate": "-0.0001"},
            ]
        },
    ])
    rows = asyncio.run(
        client.funding_history(
            "BTCUSDT",
            start_ms=1000,
            end_ms=3000,
        )
    )
    assert [row.timestamp_ms for row in rows] == [1000, 2000, 3000]
    assert client.calls[1][1]["endTime"] == 1999


def test_open_interest_uses_cursor_and_sorts():
    client = FakeClient([
        {
            "list": [{"timestamp": "2000", "openInterest": "12"}],
            "nextPageCursor": "abc",
        },
        {
            "list": [{"timestamp": "1000", "openInterest": "10"}],
            "nextPageCursor": "",
        },
    ])
    rows = asyncio.run(
        client.open_interest_history(
            "ETHUSDT",
            start_ms=1000,
            end_ms=3000,
        )
    )
    assert [row.open_interest for row in rows] == [10.0, 12.0]
    assert client.calls[1][1]["cursor"] == "abc"


def test_account_ratio_calculates_long_short_ratio():
    client = FakeClient([
        {
            "list": [
                {
                    "timestamp": "1000",
                    "buyRatio": "0.6",
                    "sellRatio": "0.4",
                }
            ],
            "nextPageCursor": "",
        }
    ])
    rows = asyncio.run(
        client.account_ratio_history(
            "BTCUSDT",
            start_ms=1000,
            end_ms=2000,
        )
    )
    assert rows[0].long_short_ratio == pytest.approx(1.5)


def test_alignment_forward_fills_only_funding():
    aligned = align_derivatives_to_15m(
        timestamps_ms=[1000, 2000, 3000],
        funding=[FundingPoint("BTCUSDT", 1000, 0.0001)],
        open_interest=[
            OpenInterestPoint("BTCUSDT", 2000, 20.0),
        ],
        account_ratio=[
            AccountRatioPoint("BTCUSDT", 3000, 0.55, 0.45, 0.55 / 0.45),
        ],
    )
    assert aligned[0]["funding_rate"] == pytest.approx(0.0001)
    assert aligned[1]["funding_rate"] == pytest.approx(0.0001)
    assert aligned[1]["open_interest"] == 20.0
    assert aligned[0]["open_interest"] is None
    assert aligned[2]["long_short_ratio"] == pytest.approx(0.55 / 0.45)


def test_rejects_unapproved_symbol():
    client = FakeClient([])
    with pytest.raises(ValueError):
        asyncio.run(
            client.funding_history(
                "SOLUSDT",
                start_ms=1000,
                end_ms=2000,
            )
        )
