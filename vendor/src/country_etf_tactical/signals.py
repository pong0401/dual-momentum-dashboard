"""Signal computation: momentum score, eligibility, moving averages, drawdown."""
from __future__ import annotations

import numpy as np
import pandas as pd

# Trading-day approximations for calendar horizons.
DAYS_1M = 21
DAYS_3M = 63
DAYS_6M = 126
DAYS_12M = 252
SMA_WINDOW = 200

MOM_WEIGHTS = {DAYS_1M: 0.20, DAYS_3M: 0.30, DAYS_6M: 0.40, DAYS_12M: 0.10}


def pct_return(prices: pd.DataFrame, window: int) -> pd.DataFrame:
    return prices / prices.shift(window) - 1.0


def sma(prices: pd.DataFrame, window: int) -> pd.DataFrame:
    return prices.rolling(window, min_periods=window).mean()


def rolling_drawdown(prices: pd.DataFrame, window: int) -> pd.DataFrame:
    """Drawdown of current price vs the trailing `window`-day running max."""
    running_max = prices.rolling(window, min_periods=window).max()
    return prices / running_max - 1.0


def valid_obs_count(prices: pd.DataFrame) -> pd.DataFrame:
    """Running count of non-null observations up to (and including) each date."""
    return prices.notna().cumsum()


def momentum_score(prices: pd.DataFrame) -> pd.DataFrame:
    ret_1m = pct_return(prices, DAYS_1M)
    ret_3m = pct_return(prices, DAYS_3M)
    ret_6m = pct_return(prices, DAYS_6M)
    ret_12m = pct_return(prices, DAYS_12M)
    score = (
        MOM_WEIGHTS[DAYS_1M] * ret_1m
        + MOM_WEIGHTS[DAYS_3M] * ret_3m
        + MOM_WEIGHTS[DAYS_6M] * ret_6m
        + MOM_WEIGHTS[DAYS_12M] * ret_12m
    )
    return score, ret_3m, ret_6m


def eligibility_mask(prices: pd.DataFrame) -> pd.DataFrame:
    """3M return > 0 AND 6M return > 0 AND price > SMA200."""
    _, ret_3m, ret_6m = momentum_score(prices)
    above_sma200 = prices > sma(prices, SMA_WINDOW)
    return (ret_3m > 0) & (ret_6m > 0) & above_sma200


def momentum_score_capped(prices: pd.DataFrame, cap: float = 1.0) -> pd.DataFrame:
    """Same weighted 1m/3m/6m/12m blend as `momentum_score`, but each lookback return is
    clipped to +/-`cap` (e.g. cap=1.0 == +/-100%) before weighting. Ranking-only change --
    `eligibility_mask` (sign-based) is unaffected by capping and should still be called
    on the raw prices, not derived from this score.

    Exists to stop a single extreme mover (e.g. a stock up several thousand percent over
    12m) from dominating the blended score through one term alone: uncapped, that term's
    contribution can dwarf the other three combined even at its nominal 10% weight, so a
    stock can keep ranking #1 for months after a sharp reversal simply because its
    trailing-year base was that low. Capping bounds each term's maximum contribution to
    MOM_WEIGHTS[window] * cap, restoring the intended relative weighting between windows."""
    ret_1m = pct_return(prices, DAYS_1M).clip(-cap, cap)
    ret_3m = pct_return(prices, DAYS_3M).clip(-cap, cap)
    ret_6m = pct_return(prices, DAYS_6M).clip(-cap, cap)
    ret_12m = pct_return(prices, DAYS_12M).clip(-cap, cap)
    return (
        MOM_WEIGHTS[DAYS_1M] * ret_1m
        + MOM_WEIGHTS[DAYS_3M] * ret_3m
        + MOM_WEIGHTS[DAYS_6M] * ret_6m
        + MOM_WEIGHTS[DAYS_12M] * ret_12m
    )


def momentum_score_risk_adjusted(prices: pd.DataFrame, vol_window: int = 126) -> pd.DataFrame:
    """The standard weighted momentum score (see `momentum_score`), divided by each
    ticker's own trailing annualized realized volatility (daily returns, `vol_window`-day
    rolling std) -- a "return per unit of risk" ranking instead of raw return. Ranking-only
    change; `eligibility_mask` should still be called on the raw prices.

    Directly targets Sharpe: a name with the same raw momentum score but much higher
    realized volatility (e.g. a stock that 25x'd over a year with wild swings along the
    way) ranks lower here than a steadier mover with the same cumulative return, which is
    exactly the risk/return tradeoff Sharpe measures."""
    score, _, _ = momentum_score(prices)
    daily_ret = prices.pct_change(fill_method=None)
    ann_vol = daily_ret.rolling(vol_window, min_periods=vol_window).std() * np.sqrt(252)
    return score / ann_vol.replace(0.0, np.nan)
