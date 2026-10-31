"""Country ETF Tactical + Gold DD Boost 16, with the momentum sleeve upgraded from a
single ETF pick to a point-in-time-correct basket of individual S&P 500 stocks.

Everything else is unchanged from the baseline (config/country_etf_tactical_gold_dd_boost16_config.json):
  - Base weights SPY 40 / Gold 30 / BTC 10 / BIL 20
  - Country sleeve: top-2 of 43 country ETFs, 4% each, funded from SPY
  - Gold drawdown boost, daily SPY/Gold/BTC exposure overlay

Only the momentum sleeve changes:
  - Baseline: top-1 of a 7-ticker ETF list (SPMO, MTUM, SCHG, XLK, EWY, EWJ, INDA), 15% of book
  - This variant: top-K of the S&P 500 constituents that were ACTUALLY MEMBERS on that
    rebalance date (avoids survivorship bias -- no picking today's mega-caps in 2015),
    same momentum formula + eligibility gate (3M>0, 6M>0, price>SMA200) as the rest of the
    strategy, 15% of book split evenly across the K picks.

Membership + prices sourced by scripts/build_pit_megacap_cache.py from a sibling repo's
S&P 500 PIT membership file and ~20y stock price cache; run that first if
data/cache/country_etf_tactical_pit_megacap_prices.parquet is missing or stale.

Usage:
  python scripts/run_country_etf_tactical_pit_megacap_momentum_backtest.py
  python scripts/run_country_etf_tactical_pit_megacap_momentum_backtest.py --top-k 1 3 5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from country_etf_tactical import backtest, metrics, overlay, rebalance, signals  # noqa: E402
from country_etf_tactical.alignment import align_to_master_calendar  # noqa: E402
from country_etf_tactical.universe import (  # noqa: E402
    BASE_ASSETS, BIL, BTC, COUNTRY_MIN_HISTORY_DAYS, COUNTRY_UNIVERSE, GOLD, SPY,
)

PRICES_PATH = REPO_ROOT / "data" / "cache" / "country_etf_tactical_pit_megacap_prices.parquet"
INTERVALS_PATH = REPO_ROOT / "data" / "cache" / "pit_sp500_membership_intervals.csv"
BASELINE_PRICES_PATH = REPO_ROOT / "data" / "cache" / "country_etf_tactical_prices.parquet"
RESULTS_DIR = REPO_ROOT / "results"

STOCK_SLEEVE_MIN_HISTORY_DAYS = 252  # 1y of price history required before a stock is eligible

# Per-stock daily exposure cut, mirroring overlay.py's SPY_MA300_WINDOW/CUT_MULT: a held
# stock's exposure is halved on any day its price is below its own N-day SMA (lag-1
# applied), independent of what SPY or the other sleeves are doing. Without this, a stock
# picked at rebalance is held at full weight all month even if it breaks down mid-month.
STOCK_DAILY_CUT_MULT = 0.5

STOCK_STOPLOSS_PCT = 0.15  # exit if a position is down >=15% from its entry price
STOCK_STOPLOSS_CUT_MULT = 0.0  # full exit once stopped out (vs. the MA overlay's 0.5x)


def build_stock_daily_multipliers(prices: pd.DataFrame, stock_universe: list[str], window: int = 300) -> pd.DataFrame:
    sma = signals.sma(prices[stock_universe], window)
    below_sma = prices[stock_universe] < sma
    mult = pd.DataFrame(1.0, index=prices.index, columns=stock_universe)
    mult = mult.mask(below_sma, STOCK_DAILY_CUT_MULT)
    mult = mult.mask(sma.isna(), 1.0)
    return mult


def build_stock_stoploss_multipliers(
    prices: pd.DataFrame, weights_schedule: pd.DataFrame, stock_universe: list[str],
    stoploss_pct: float = STOCK_STOPLOSS_PCT, cut_mult: float = STOCK_STOPLOSS_CUT_MULT,
) -> pd.DataFrame:
    """Per-position hard stop: once a stock held in the sleeve has drawn down
    `stoploss_pct` from the price on the rebalance date it was picked, cut its exposure
    to `cut_mult` for the rest of that holding period (a fresh entry price resets the
    stop at the next rebalance). Unlike the MA overlay this reacts to the position's own
    P&L directly rather than a slow trailing average, so it can catch a fast drawdown
    much sooner."""
    mult = pd.DataFrame(1.0, index=prices.index, columns=stock_universe)
    rb_dates = weights_schedule.index
    stock_cols = [c for c in weights_schedule.columns if c in stock_universe]

    for i, d in enumerate(rb_dates):
        next_d = rb_dates[i + 1] if i + 1 < len(rb_dates) else prices.index.max()
        period_idx = prices.index[(prices.index > d) & (prices.index <= next_d)]
        if len(period_idx) == 0:
            continue
        held = weights_schedule.loc[d, stock_cols]
        held_tickers = held[held > 0].index
        for t in held_tickers:
            entry_price = prices.loc[d, t]
            if pd.isna(entry_price) or entry_price <= 0:
                continue
            dd = prices.loc[period_idx, t] / entry_price - 1.0
            stopped = dd <= -stoploss_pct
            if stopped.any():
                first_stop = stopped[stopped].index.min()
                mult.loc[(mult.index >= first_stop) & (mult.index <= next_d), t] = cut_mult

    return mult


def load_membership_intervals() -> pd.DataFrame:
    df = pd.read_csv(INTERVALS_PATH, parse_dates=["start_date", "end_date"])
    return df


def build_pit_membership_mask(dates: pd.DatetimeIndex, intervals: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """True where `ticker` was an S&P 500 constituent on `date` (open-ended end_date = still a member)."""
    mask = pd.DataFrame(False, index=dates, columns=tickers)
    ticker_set = set(tickers)
    last_date = dates.max()
    for row in intervals.itertuples(index=False):
        if row.ticker not in ticker_set:
            continue
        start = row.start_date
        end = row.end_date if pd.notna(row.end_date) else last_date
        mask.loc[(dates >= start) & (dates <= end), row.ticker] = True
    return mask


def compute_shared_signals(prices: pd.DataFrame, stock_universe: list[str], pit_membership: pd.DataFrame) -> dict:
    """Momentum score / eligibility for the stock and country universes, and the SPY
    regime signal, all independent of `top_k` -- computed once and shared across every
    top-K variant instead of being recomputed (and re-allocating ~700-column arrays)
    on every call."""
    stock_score, _, _ = signals.momentum_score(prices[stock_universe])
    stock_hist_ok = signals.valid_obs_count(prices[stock_universe]) >= STOCK_SLEEVE_MIN_HISTORY_DAYS
    stock_elig = signals.eligibility_mask(prices[stock_universe]) & pit_membership[stock_universe] & stock_hist_ok

    # reindex, not [], so a delisted ticker Yahoo stops returning (GXG/PAK/EGPT, 2026-09)
    # becomes an all-NaN column that fails the history filter instead of a KeyError.
    country_prices = prices.reindex(columns=COUNTRY_UNIVERSE)
    country_score, _, _ = signals.momentum_score(country_prices)
    country_elig_signal = signals.eligibility_mask(country_prices)
    country_hist_ok = signals.valid_obs_count(country_prices) >= COUNTRY_MIN_HISTORY_DAYS
    country_elig = country_elig_signal & country_hist_ok

    spy_sma200 = signals.sma(prices[[SPY]], rebalance.SPY_MA200_WINDOW)[SPY]
    spy_dd252 = signals.rolling_drawdown(prices[[SPY]], rebalance.GOLD_DD_WINDOW)[SPY]

    return dict(
        stock_score=stock_score, stock_elig=stock_elig,
        country_score=country_score, country_elig=country_elig,
        spy_sma200=spy_sma200, spy_dd252=spy_dd252,
    )


def build_rebalance_schedule_stock_momentum(
    prices: pd.DataFrame,
    stock_universe: list[str],
    shared: dict,
    top_k: int,
    include_btc: bool = True,
    as_of: pd.Timestamp | None = None,
    btc_pit: bool = False,
    spy_gate: bool = False,
    dynamic_bil: bool = False,
    bil_shift: float = rebalance.BIL_DYNAMIC_SHIFT,
    momentum_weighted: bool = False,
    sleeve_pct: float = rebalance.MOMENTUM_SLEEVE_PCT,
    country_n_picks: int = rebalance.COUNTRY_SLEEVE_N_PICKS,
    country_pct_per_pick: float = rebalance.COUNTRY_SLEEVE_PCT_PER_PICK,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Identical to rebalance.build_rebalance_schedule except the momentum sleeve is
    top-K point-in-time-eligible S&P 500 stocks (equal split of the 15% sleeve) instead
    of the top-1 fixed momentum-ETF pick. `btc_pit` behaves as in rebalance.py: BTC's 10%
    stays in SPY until BTC has price data, ignoring `include_btc`.

    `spy_gate=True` closes the entire stock momentum sleeve (capital stays in SPY, same
    as a month with zero eligible picks) whenever the same regime signal that triggers
    the gold DD boost is active (SPY < SMA200 or SPY in a >=8% 252d drawdown). This
    reproduces, for the stock sleeve, the defensive fallback the small ETF momentum
    universe got "for free" in 2008-2009 by having every candidate fail eligibility at
    once -- a 707-name stock pool almost never goes fully empty on its own, so without
    this gate the sleeve keeps chasing narrow, unstable pockets of relative strength
    (e.g. energy names in H1 2008) straight through a systemic selloff.

    `shared` is the dict returned by compute_shared_signals() (stock/country momentum
    score + eligibility, SPY regime signal) -- passed in rather than recomputed here so
    a top-K sweep only computes it once.

    `momentum_weighted=True` splits the sleeve by rank instead of evenly: the top-scored
    pick gets a weight of K, the next K-1, ..., the last pick 1 (normalized to sum to
    `sleeve_pct`). Rank weights (not raw score-proportional) because eligible names can
    still have scores near zero or occasionally negative -- 3M/6M return being positive
    doesn't bound the 1M/12M terms in the blend -- so score-proportional weights could
    blow up or invert sign; rank order is monotonic and robust to that.

    `sleeve_pct` overrides rebalance.MOMENTUM_SLEEVE_PCT (0.15) to size the sleeve
    differently, e.g. 0.20/0.25 to test whether the sleeve is under-sized relative to
    how much edge the stock picks carry.

    `country_n_picks`/`country_pct_per_pick` override the country sleeve's pick count and
    per-pick size (defaults: rebalance.COUNTRY_SLEEVE_N_PICKS=2, COUNTRY_SLEEVE_PCT_PER_PICK
    =0.04, i.e. 8% total) -- lets a test shrink the country sleeve to free SPY headroom
    for a bigger momentum sleeve without touching the constants other backtests rely on."""
    base_weights = rebalance.BASE_WEIGHTS if include_btc else rebalance.BASE_WEIGHTS_NO_BTC
    idx = prices.index
    rb_dates = rebalance.month_end_dates(idx, as_of=as_of)

    stock_score = shared["stock_score"]
    stock_elig = shared["stock_elig"]
    country_score = shared["country_score"]
    country_elig = shared["country_elig"]
    spy_sma200 = shared["spy_sma200"]
    spy_dd252 = shared["spy_dd252"]

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

        mom_picks = [] if (spy_gate and triggered) else rebalance._top_n(stock_score.loc[d], stock_elig.loc[d], top_k)
        if mom_picks:
            total_funded = min(sleeve_pct, max(0.0, w[SPY]))
            w[SPY] -= total_funded
            if momentum_weighted:
                n = len(mom_picks)
                rank_weights = [n - i for i in range(n)]  # picks are already sorted best-first
                denom = sum(rank_weights)
                for tkr, rw in zip(mom_picks, rank_weights):
                    w[tkr] = w.get(tkr, 0.0) + total_funded * rw / denom
            else:
                per_pick = total_funded / len(mom_picks)
                for tkr in mom_picks:
                    w[tkr] = w.get(tkr, 0.0) + per_pick

        country_picks = rebalance._top_n(country_score.loc[d], country_elig.loc[d], country_n_picks)
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

        meta_rows.append(
            {
                "date": d,
                "momentum_picks": ",".join(mom_picks) if mom_picks else "",
                "n_momentum_picks": len(mom_picks),
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


def _drop_never_held_columns(weights_schedule: pd.DataFrame, prices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """weights_schedule carries a column for every ticker in the universe (mostly zero on
    any given date). Slicing both frames down to columns that are ever actually held
    (~dozens, not 700+ stocks) cuts the daily simulation's memory/compute by an order of
    magnitude without changing results -- a zero-weight column contributes nothing."""
    used = weights_schedule.columns[(weights_schedule != 0).any(axis=0)]
    return weights_schedule[used], prices[used]


def run_variant(prices: pd.DataFrame, stock_universe: list[str], shared: dict,
                 top_k: int, include_btc: bool = True, btc_pit: bool = False, spy_gate: bool = False,
                 stock_daily_mult: pd.DataFrame | None = None, dynamic_bil: bool = False,
                 bil_shift: float = rebalance.BIL_DYNAMIC_SHIFT, momentum_weighted: bool = False,
                 sleeve_pct: float = rebalance.MOMENTUM_SLEEVE_PCT,
                 country_n_picks: int = rebalance.COUNTRY_SLEEVE_N_PICKS,
                 country_pct_per_pick: float = rebalance.COUNTRY_SLEEVE_PCT_PER_PICK) -> dict:
    aligned = align_to_master_calendar(prices)
    weights_schedule, meta = build_rebalance_schedule_stock_momentum(
        aligned, stock_universe, shared, top_k, include_btc=include_btc, btc_pit=btc_pit,
        spy_gate=spy_gate, dynamic_bil=dynamic_bil, bil_shift=bil_shift,
        momentum_weighted=momentum_weighted, sleeve_pct=sleeve_pct,
        country_n_picks=country_n_picks, country_pct_per_pick=country_pct_per_pick,
    )
    weights_schedule, prices_used = _drop_never_held_columns(weights_schedule, prices)
    if stock_daily_mult is not None:
        stock_daily_mult = stock_daily_mult[[c for c in stock_daily_mult.columns if c in weights_schedule.columns]]
    return backtest.run_backtest(
        prices_used, include_btc=include_btc, weights_schedule=weights_schedule, rebalance_meta=meta,
        btc_pit=btc_pit, extra_daily_mult=stock_daily_mult,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, nargs="+", default=[1, 3, 5])
    parser.add_argument("--no-btc", action="store_true")
    parser.add_argument("--no-btc-pit", action="store_true", help="Use fixed BTC-from-inception weighting instead of point-in-time entry")
    parser.add_argument("--spy-gate", action="store_true", help="Close the stock momentum sleeve (capital stays in SPY) when SPY<SMA200 or in a >=8% drawdown")
    parser.add_argument("--stock-daily-overlay", action="store_true", help="Daily-cut each held stock's exposure by 0.5x on days it's below its own 300d SMA")
    parser.add_argument("--dynamic-bil", action="store_true", help="Shift BIL 20%%->10%% into SPY on any rebalance date not in the gold-DD-boost regime")
    parser.add_argument("--momentum-weighted", action="store_true", help="Rank-weight the sleeve picks (best pick gets K/sum(1..K), etc.) instead of equal split")
    parser.add_argument("--sleeve-pct", type=float, default=rebalance.MOMENTUM_SLEEVE_PCT, help="Stock momentum sleeve size as a fraction of book (default 0.15)")
    args = parser.parse_args()
    include_btc = not args.no_btc
    btc_pit = not args.no_btc_pit
    spy_gate = args.spy_gate
    dynamic_bil = args.dynamic_bil
    momentum_weighted = args.momentum_weighted
    sleeve_pct = args.sleeve_pct

    prices = pd.read_parquet(PRICES_PATH)
    intervals = load_membership_intervals()
    baseline_cols = set(pd.read_parquet(BASELINE_PRICES_PATH).columns)
    stock_universe = [c for c in prices.columns if c not in baseline_cols]
    print(f"Stock momentum universe: {len(stock_universe)} S&P 500 PIT tickers")

    aligned_for_mask = align_to_master_calendar(prices)
    pit_membership = build_pit_membership_mask(aligned_for_mask.index, intervals, stock_universe)
    stock_daily_mult = build_stock_daily_multipliers(aligned_for_mask, stock_universe) if args.stock_daily_overlay else None
    shared = compute_shared_signals(aligned_for_mask, stock_universe, pit_membership)
    del pit_membership

    baseline = backtest.run_backtest(
        pd.read_parquet(BASELINE_PRICES_PATH), include_btc=include_btc, btc_pit=btc_pit, dynamic_bil=dynamic_bil,
    )
    baseline_table = metrics.performance_table(baseline["port_return"], baseline["spy_return"])
    print(f"\n=== Baseline (ETF momentum sleeve, top-1), btc_pit={btc_pit} ===")
    print(f"Simulation start: {baseline['sim_start'].date()}")
    pd.set_option("display.float_format", lambda x: f"{x:.4f}")
    print(baseline_table.to_string())

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    baseline_table.to_csv(RESULTS_DIR / "country_etf_tactical_pit_megacap_baseline_performance.csv")

    gate_suffix = "_spygate" if spy_gate else ""
    overlay_suffix = "_stockoverlay" if args.stock_daily_overlay else ""
    bil_suffix = "_dynbil" if dynamic_bil else ""
    weighted_suffix = "_weighted" if momentum_weighted else ""
    sleeve_suffix = "" if sleeve_pct == rebalance.MOMENTUM_SLEEVE_PCT else f"_sleeve{int(round(sleeve_pct * 100))}"
    for k in args.top_k:
        result = run_variant(
            prices, stock_universe, shared, top_k=k, include_btc=include_btc, btc_pit=btc_pit, spy_gate=spy_gate,
            stock_daily_mult=stock_daily_mult, dynamic_bil=dynamic_bil, momentum_weighted=momentum_weighted,
            sleeve_pct=sleeve_pct,
        )
        table = metrics.performance_table(result["port_return"], result["spy_return"])
        print(f"\n=== PIT mega-cap stock momentum sleeve, top-{k}, spy_gate={spy_gate}, stock_daily_overlay={args.stock_daily_overlay}, dynamic_bil={dynamic_bil}, momentum_weighted={momentum_weighted}, sleeve_pct={sleeve_pct} ===")
        print(f"Simulation start: {result['sim_start'].date()}")
        print(table.to_string())

        suffix = f"top{k}{gate_suffix}{overlay_suffix}{bil_suffix}{weighted_suffix}{sleeve_suffix}"
        table.to_csv(RESULTS_DIR / f"country_etf_tactical_pit_megacap_{suffix}_performance.csv")
        result["equity"].to_frame("strategy_equity").join(
            result["spy_equity"].to_frame("spy_equity")
        ).to_csv(RESULTS_DIR / f"country_etf_tactical_pit_megacap_{suffix}_equity_curve.csv")
        result["rebalance_meta"].to_csv(RESULTS_DIR / f"country_etf_tactical_pit_megacap_{suffix}_rebalance_log.csv")

        sim_meta = result["rebalance_meta"].loc[result["sim_start"]:]
        picks_flat = sim_meta["momentum_picks"].str.split(",").explode()
        picks_flat = picks_flat[picks_flat != ""]
        print(f"Most-picked stocks (top-{k}, simulated period only): {picks_flat.value_counts().head(10).to_dict()}")
        empty_pick_months = (sim_meta["n_momentum_picks"] == 0).sum()
        print(f"Months with zero eligible stock picks (sleeve capital stays in SPY): {empty_pick_months} / {len(sim_meta)}")

    print(f"\nSaved all performance tables, equity curves, and rebalance logs to {RESULTS_DIR}")


if __name__ == "__main__":
    main()
