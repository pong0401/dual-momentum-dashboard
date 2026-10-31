"""Country-sleeve-only monthly rebalance: the gold-DD-boost shape with the momentum-ETF
sleeve dropped and an SPY gate added on the country sleeve.

Moved here verbatim from scripts/run_country_etf_tactical_country_only_backtest.py so the
fail-closed cloud producer can reach it — a weights builder that feeds real target weights
cannot live in a research script that isn't packaged or frozen. The script still imports
this function, so its backtests are unchanged.
"""
from __future__ import annotations

import pandas as pd

from . import rebalance, signals
from .universe import BIL, BTC, COUNTRY_MIN_HISTORY_DAYS, COUNTRY_UNIVERSE, GOLD, SPY


def build_rebalance_schedule_country_only(
    prices: pd.DataFrame,
    include_btc: bool = True,
    as_of: pd.Timestamp | None = None,
    btc_pit: bool = False,
    dynamic_bil: bool = False,
    bil_shift: float = rebalance.BIL_DYNAMIC_SHIFT,
    spy_gate: bool = True,
    country_n_picks: int = rebalance.COUNTRY_SLEEVE_N_PICKS,
    country_pct_per_pick: float = rebalance.COUNTRY_SLEEVE_PCT_PER_PICK,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Same monthly-rebalance shape as rebalance.build_rebalance_schedule, but with the
    MOMENTUM_UNIVERSE sleeve dropped entirely and an SPY gate added on the country sleeve
    (empty picks, capital stays in SPY, whenever the gold-DD-boost trigger is active)."""
    base_weights = rebalance.BASE_WEIGHTS if include_btc else rebalance.BASE_WEIGHTS_NO_BTC
    idx = prices.index
    rb_dates = rebalance.month_end_dates(idx, as_of=as_of)

    # reindex, not [], so a delisted ticker Yahoo stops returning (GXG/PAK/EGPT, 2026-09)
    # becomes an all-NaN column that fails the history filter instead of a KeyError.
    country_prices = prices.reindex(columns=COUNTRY_UNIVERSE)
    country_score, _, _ = signals.momentum_score(country_prices)
    country_elig_signal = signals.eligibility_mask(country_prices)
    country_hist_ok = signals.valid_obs_count(country_prices) >= COUNTRY_MIN_HISTORY_DAYS
    country_elig = country_elig_signal & country_hist_ok

    spy_sma200 = signals.sma(prices[[SPY]], rebalance.SPY_MA200_WINDOW)[SPY]
    spy_dd252 = signals.rolling_drawdown(prices[[SPY]], rebalance.GOLD_DD_WINDOW)[SPY]

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
            or (pd.notna(spy_dd252.loc[d]) and spy_dd252.loc[d] <= rebalance.GOLD_DD_TRIGGER)
        )

        if dynamic_bil and not triggered:
            shift = min(bil_shift, max(0.0, w[BIL] - rebalance.BIL_DYNAMIC_FLOOR))
            w[BIL] -= shift
            w[SPY] += shift

        country_picks = (
            [] if (spy_gate and triggered)
            else rebalance._top_n(country_score.loc[d], country_elig.loc[d], country_n_picks)
        )
        for tkr in country_picks:
            funded = min(country_pct_per_pick, max(0.0, w[SPY]))
            w[SPY] -= funded
            w[tkr] = w.get(tkr, 0.0) + funded

        boost = 0.0
        if triggered:
            headroom = max(0.0, w[SPY] - rebalance.SPY_FLOOR)
            boost = min(rebalance.GOLD_DD_BOOST_MAX, headroom)
            w[SPY] -= boost
            w[GOLD] += boost

        for k, v in w.items():
            weights.loc[d, k] = v

        meta_rows.append({
            "date": d,
            "country_picks": ",".join(country_picks) if country_picks else "",
            "n_country_picks": len(country_picks),
            "spy_gated": bool(spy_gate and triggered),
            "gold_boost_triggered": triggered,
            "gold_boost_pct": boost,
            "spy_weight": w[SPY],
            "gold_weight": w[GOLD],
            "btc_included": btc_available,
        })

    meta = pd.DataFrame(meta_rows).set_index("date")
    return weights, meta
