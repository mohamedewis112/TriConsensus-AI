from __future__ import annotations

from pandas import DataFrame
from freqtrade.strategy import IStrategy


class TriConsensusDryRunStrategy(IStrategy):
    """
    Safety shell for Freqtrade dry-run validation.

    This strategy never creates autonomous entries. TriConsensus' own consensus/risk
    pipeline remains authoritative. External execution bridging stays disabled until
    a separate fail-closed bridge verifies Freqtrade is actually in dry-run mode.
    """

    INTERFACE_VERSION = 3
    timeframe = "15m"
    can_short = True
    process_only_new_candles = True
    startup_candle_count = 60

    minimal_roi = {"0": 100.0}
    stoploss = -0.99

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        dataframe["enter_tag"] = ""
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        dataframe["exit_tag"] = ""
        return dataframe
