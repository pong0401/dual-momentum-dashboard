"""Monthly rebalance: build target weights as of each month-end close.

All signals here are read off the month-end close itself; `backtest.py` is responsible for
applying the resulting target weights starting the *next* trading session (lag-1 execution).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from . import signals
from .universe import (
    BASE_ASSETS, BIL, BTC, COUNTRY_MIN_HISTORY_DAYS, COUNTRY_UNIVERSE, GOLD,
    MOMENTUM_UNIVERSE, SPY,
)

BASE_WEIGHTS = {SPY: 0.40, GOLD: 0.30, BTC: 0.10, BIL: 0.20}

# BTC's 10% redistributed proportionally across SPY/Gold/BIL (40:30:20 = 4:3:2) when BTC is excluded.
BASE_WEIGHTS_NO_BTC = {SPY: 0.40 / 0.9, GOLD: 0.30 / 0.9, BIL: 0.20 / 0.9}

MOMENTUM_SLEEVE_PCT = 0.15
COUNTRY_SLEEVE_PCT_PER_PICK = 0.04
COUNTRY_SLEEVE_N_PICKS = 2
SPY_FLOOR = 0.20
GOLD_DD_BOOST_MAX = 0.16
GOLD_DD_WINDOW = 252
GOLD_DD_TRIGGER = -0.08
SPY_MA200_WINDOW = 200

# Dynamic BIL ballast: BIL 20% -> 15% (freed 5pp added to SPY) on any rebalance date NOT
# in the gold-DD-boost regime (i.e. SPY >= SMA200 and SPY 252d drawdown > -8%) -- same
# trigger, inverted. BIL's job is a defensive cash floor; there's no reason to hold the
# full 20% as a static drag through a confirmed-healthy market. 5pp (vs. a larger 10pp
# shift also tested) is the sweet spot: on the 19.2y BTC-PIT backtest it adds +36bps CAGR
# with zero Sharpe or max-drawdown cost; 10pp adds more CAGR but starts giving back
# Sharpe/DD, and shorter windows show the same shape (10pp's cost is roughly double 5pp's
# for roughly double the CAGR gain, i.e. 5pp sits in the flat/free part of the trade-off).
BIL_DYNAMIC_SHIFT = 0.05
BIL_DYNAMIC_FLOOR = 0.10


def month_end_dates(
    index: pd.DatetimeIndex, as_of: pd.Timestamp | dt.date | str | None = None
) -> pd.DatetimeIndex:
    """Last available trading day of each calendar month present in `index`.

    A month is only eligible as a rebalance boundary once it has fully elapsed on the
    calendar. The trailing group whose (year, month) matches `as_of` is dropped: the
    last cached day of the current, still-in-progress month is not a month-end, even
    if it happens to be the newest row in the price cache. Without this guard, a
    partial-month cache (e.g. mid-July data) would be misread as a July rebalance
    instead of retaining the last completed month (June).
    """
    if as_of is None:
        as_of = pd.Timestamp.now()
    as_of = pd.Timestamp(as_of)

    s = pd.Series(index, index=index)
    ends = s.groupby([index.year, index.month]).max()
    ends = ends[~((ends.index.get_level_values(0) == as_of.year) & (ends.index.get_level_values(1) == as_of.month))]
    return ends.values


def _top_n(score_row: pd.Series, eligible_row: pd.Series, n: int) -> list[str]:
    elig = score_row[eligible_row.reindex(score_row.index).fillna(False)].dropna()
    return list(elig.sort_values(ascending=False).index[:n])


def build_rebalance_schedule(
    prices: pd.DataFrame,
    include_btc: bool = True,
    as_of: pd.Timestamp | dt.date | str | None = None,
    btc_pit: bool = False,
    dynamic_bil: bool = False,
    bil_shift: float = BIL_DYNAMIC_SHIFT,
) -> pd.DataFrame:
    """Return a DataFrame indexed by rebalance date with target weight columns for every
    ticker present in `prices`, plus metadata columns (momentum_pick, country_picks,
    gold_boost, gold_boost_triggered).

    `as_of` (defaults to now) gates out an incomplete current month; see `month_end_dates`.

    `btc_pit=True` makes BTC's 10% slot point-in-time instead of fixed: on any rebalance
    date where BTC has no price yet, its 10% stays in SPY (SPY at 50%) instead of being
    permanently excluded; once BTC prices start, it reverts to the normal 40/30/10/20
    split. This lets the simulation start as early as SPY/Gold/BIL allow instead of being
    gated by BTC's 2014 inception (see backtest.run_backtest's sim_start). `include_btc`
    is ignored when `btc_pit=True`.

    `dynamic_bil=True` shifts `bil_shift` (default BIL_DYNAMIC_SHIFT, 10pp) from BIL to
    SPY on any rebalance date where the gold-DD-boost regime signal is NOT active
    (SPY >= SMA200 and no >=8% 252d drawdown) -- i.e. a confirmed-healthy market. BIL
    reverts to its full weight the moment either trigger fires. The shift happens before
    sleeve funding, so momentum/country sleeves also get to draw on the extra SPY
    headroom in a bull regime. Never shifts BIL below BIL_DYNAMIC_FLOOR.
    """
    base_weights = BASE_WEIGHTS if include_btc else BASE_WEIGHTS_NO_BTC
    idx = prices.index
    rb_dates = month_end_dates(idx, as_of=as_of)

    mom_score, _, _ = signals.momentum_score(prices[MOMENTUM_UNIVERSE])
    mom_elig = signals.eligibility_mask(prices[MOMENTUM_UNIVERSE])

    # reindex, not [], so a delisted ticker Yahoo stops returning (GXG/PAK/EGPT, 2026-09)
    # becomes an all-NaN column that fails the history filter instead of a KeyError.
    country_prices = prices.reindex(columns=COUNTRY_UNIVERSE)
    country_score, _, _ = signals.momentum_score(country_prices)
    country_elig_signal = signals.eligibility_mask(country_prices)
    country_hist_ok = signals.valid_obs_count(country_prices) >= COUNTRY_MIN_HISTORY_DAYS
    country_elig = country_elig_signal & country_hist_ok

    spy_sma200 = signals.sma(prices[[SPY]], SPY_MA200_WINDOW)[SPY]
    spy_dd252 = signals.rolling_drawdown(prices[[SPY]], GOLD_DD_WINDOW)[SPY]

    excluded = {"IEF"} if (include_btc or btc_pit) else {"IEF", BTC}
    all_tickers = sorted(set(prices.columns) - excluded)
    weights = pd.DataFrame(0.0, index=rb_dates, columns=all_tickers)
    meta_rows = []

    for d in rb_dates:
        if btc_pit:
            w = {SPY: 0.40, GOLD: 0.30, BIL: 0.20}
            btc_available = bool(pd.notna(prices.loc[d, BTC])) if BTC in prices.columns else False
            if btc_available:
                w[BTC] = 0.10
            else:
                w[SPY] += 0.10
        else:
            w = dict(base_weights)
            btc_available = include_btc

        triggered = bool(
            (pd.notna(spy_sma200.loc[d]) and prices.loc[d, SPY] < spy_sma200.loc[d])
            or (pd.notna(spy_dd252.loc[d]) and spy_dd252.loc[d] <= GOLD_DD_TRIGGER)
        )

        if dynamic_bil and not triggered:
            shift = min(bil_shift, max(0.0, w[BIL] - BIL_DYNAMIC_FLOOR))
            w[BIL] -= shift
            w[SPY] += shift

        # Sleeves are funded from SPY, capped at what SPY actually has (never draws it
        # negative/short); if SPY's weight is smaller than the requested carve-out, the
        # sleeve is funded only up to that amount instead.
        mom_pick = _top_n(mom_score.loc[d], mom_elig.loc[d], 1)
        if mom_pick:
            funded = min(MOMENTUM_SLEEVE_PCT, max(0.0, w[SPY]))
            w[SPY] -= funded
            w[mom_pick[0]] = w.get(mom_pick[0], 0.0) + funded

        country_picks = _top_n(country_score.loc[d], country_elig.loc[d], COUNTRY_SLEEVE_N_PICKS)
        for tkr in country_picks:
            funded = min(COUNTRY_SLEEVE_PCT_PER_PICK, max(0.0, w[SPY]))
            w[SPY] -= funded
            w[tkr] = w.get(tkr, 0.0) + funded

        boost = 0.0
        if triggered:
            headroom = max(0.0, w[SPY] - SPY_FLOOR)
            boost = min(GOLD_DD_BOOST_MAX, headroom)
            w[SPY] -= boost
            w[GOLD] += boost

        for k, v in w.items():
            weights.loc[d, k] = v

        meta_rows.append(
            {
                "date": d,
                "momentum_pick": mom_pick[0] if mom_pick else None,
                "country_picks": ",".join(country_picks) if country_picks else "",
                "n_country_picks": len(country_picks),
                "gold_boost_triggered": triggered,
                "gold_boost_pct": boost,
                "spy_weight": w[SPY],
                "gold_weight": w[GOLD],
                "btc_included": btc_available,
            }
        )

    meta = pd.DataFrame(meta_rows).set_index("date")
    return weights, meta
