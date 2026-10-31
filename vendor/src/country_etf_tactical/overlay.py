"""Daily exposure overlay: SPY/Gold/BTC exposure scaling applied to standing target weights."""
from __future__ import annotations

import pandas as pd

from . import signals
from .universe import BTC, GOLD, SPY

SPY_MA300_WINDOW = 300
SPY_MA300_CUT_MULT = 0.5
GOLD_DD_WINDOW = 252
GOLD_DD_TRIGGER = -0.08
GOLD_DD_CUT_MULT = 0.5
BTC_MA50_WINDOW = 50
BTC_MA50_CUT_MULT = 0.0


def build_daily_multipliers(prices: pd.DataFrame, include_btc: bool = True) -> pd.DataFrame:
    """Multiplier (1.0 normally) for SPY, Gold, (BTC), computed off *today's* close but
    applied lag-1 (shifted forward one session) by the caller so no look-ahead occurs."""
    spy_sma300 = signals.sma(prices[[SPY]], SPY_MA300_WINDOW)[SPY]
    gold_dd = signals.rolling_drawdown(prices[[GOLD]], GOLD_DD_WINDOW)[GOLD]

    cols = [SPY, GOLD, BTC] if include_btc else [SPY, GOLD]
    mult = pd.DataFrame(1.0, index=prices.index, columns=cols)
    mult.loc[prices[SPY] < spy_sma300, SPY] = SPY_MA300_CUT_MULT
    mult.loc[gold_dd <= GOLD_DD_TRIGGER, GOLD] = GOLD_DD_CUT_MULT
    mult.loc[spy_sma300.isna(), SPY] = 1.0
    mult.loc[gold_dd.isna(), GOLD] = 1.0

    if include_btc:
        btc_sma50 = signals.sma(prices[[BTC]], BTC_MA50_WINDOW)[BTC]
        mult.loc[prices[BTC] < btc_sma50, BTC] = BTC_MA50_CUT_MULT
        mult.loc[btc_sma50.isna(), BTC] = 1.0
    return mult
