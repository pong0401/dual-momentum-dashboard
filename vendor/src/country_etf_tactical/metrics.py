"""Performance metrics over trailing windows (Max, 10y, 5y, 3y, 1y)."""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252

WINDOWS = {
    "Max": None,
    "10y": 10,
    "5y": 5,
    "3y": 3,
    "1y": 1,
}


def _slice_window(returns: pd.Series, years: int | None) -> pd.Series:
    if years is None:
        return returns
    end = returns.index.max()
    start = end - pd.DateOffset(years=years)
    return returns.loc[returns.index > start]


def compute_stats(returns: pd.Series) -> dict:
    if returns.empty:
        return {k: np.nan for k in
                ["total_return", "cagr", "ann_vol", "sharpe", "max_dd", "calmar",
                 "best_year", "worst_year", "n_years"]}

    equity = (1.0 + returns).cumprod()
    n_days = len(returns)
    n_years = n_days / TRADING_DAYS
    total_return = equity.iloc[-1] / 1.0 - 1.0
    cagr = equity.iloc[-1] ** (1.0 / n_years) - 1.0 if n_years > 0 else np.nan
    ann_vol = returns.std() * np.sqrt(TRADING_DAYS)
    sharpe = (returns.mean() * TRADING_DAYS) / ann_vol if ann_vol > 0 else np.nan

    running_max = equity.cummax()
    dd = equity / running_max - 1.0
    max_dd = dd.min()
    calmar = cagr / abs(max_dd) if max_dd < 0 else np.nan

    yearly = (1.0 + returns).groupby(returns.index.year).prod() - 1.0
    best_year = yearly.max()
    worst_year = yearly.min()

    return {
        "total_return": total_return,
        "cagr": cagr,
        "ann_vol": ann_vol,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "calmar": calmar,
        "best_year": best_year,
        "worst_year": worst_year,
        "n_years": n_years,
    }


def transaction_cost_drag(effective_weights: pd.DataFrame, cost_rate_per_side: float) -> pd.Series:
    """Daily round-trip transaction cost, from day-over-day change in effective weights.

    `run_backtest`'s weights are flat within a month except when the daily SPY/Gold/BTC
    overlay multiplier flips, so a day-over-day diff here picks up exactly the trades
    this model implies: monthly sleeve reshuffles plus overlay-driven de-risk/re-risk --
    without needing the simulation loop itself to track cost. `0.5 * sum(abs(diff))` is
    one-way turnover (fraction of the book bought or sold, whichever is smaller); `2x`
    accounts for both the sell and the buy leg, each charged at `cost_rate_per_side`.
    """
    one_way_turnover = effective_weights.diff().abs().sum(axis=1) * 0.5
    one_way_turnover.iloc[0] = 0.0
    return 2.0 * one_way_turnover * cost_rate_per_side


def performance_table(strategy_returns: pd.Series, bench_returns: pd.Series) -> pd.DataFrame:
    rows = {}
    for label, years in WINDOWS.items():
        s_slice = _slice_window(strategy_returns, years)
        b_slice = _slice_window(bench_returns, years)
        s_stats = compute_stats(s_slice)
        b_stats = compute_stats(b_slice)
        rows[(label, "Strategy")] = s_stats
        rows[(label, "SPY B&H")] = b_stats
    df = pd.DataFrame(rows).T
    df.index.names = ["Window", "Series"]
    return df
