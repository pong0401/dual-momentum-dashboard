"""Two independent momentum sleeves (PIT individual-stock + growth-ETF) funded from SPY
on top of the gold-DD-boost base shape, plus a per-stock hard stop-loss.

Moved here verbatim from scripts/run_country_etf_tactical_dual_momentum_backtest.py so the
fail-closed cloud producer can reach it — a weights builder that feeds real target weights
cannot live in a research script that isn't packaged or frozen. The script still imports
these functions, so its backtests are unchanged.

`build_rebalance_schedule_dual_momentum_frozen` and `compute_live_stoploss_multipliers`
are new (not moved): the former is a `strategy_registry.ScheduleBuilder`-shaped wrapper
around the moved function, assembling the frozen dual_momentum_capped_sl config's inputs
the same way scripts/export_final_3_strategies_latest_weights.py's
export_dual_momentum_capped_sl() already does for the monthly (pre-overlay) export; the
latter is a live daily adaptation of pit_megacap's build_stock_stoploss_multipliers
(backtest-only there — see its docstring) for the fail-closed producer's daily overlay.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from . import rebalance, signals
from .universe import BIL, BTC, GOLD, GROWTH_FACTOR_UNIVERSE, SECTOR_GROWTH_UNIVERSE, SPY

REPO_ROOT = Path(__file__).resolve().parents[2]
PIT_PRICES_PATH = REPO_ROOT / "data" / "cache" / "country_etf_tactical_pit_megacap_prices.parquet"
MAIN_PRICES_PATH = REPO_ROOT / "data" / "cache" / "country_etf_tactical_prices.parquet"
BASELINE_PRICES_PATH = REPO_ROOT / "data" / "cache" / "country_etf_tactical_prices.parquet"

# scripts/run_country_etf_tactical_pit_megacap_momentum_backtest.py (compute_shared_signals,
# load_membership_intervals, build_pit_membership_mask) and
# scripts/run_country_etf_tactical_sector_growth_momentum_backtest.py
# (build_always_eligible_membership) are standalone scripts, not part of this package.
# Rather than also extracting those (a materially bigger, separately-scoped change to two
# more research scripts), this module reuses the same sys.path + script-import approach
# scripts/export_final_3_strategies_latest_weights.py already relies on for the same
# functions — proven working there for exactly this use (its own
# export_dual_momentum_capped_sl()). Imported lazily inside the wrapper below, not at
# module level, so importing this package module never has the side effect of loading
# scripts/ onto sys.path for unrelated callers (e.g. the two already-registered strategies).


def load_merged_prices() -> pd.DataFrame:
    pit_prices = pd.read_parquet(PIT_PRICES_PATH)
    main_prices = pd.read_parquet(MAIN_PRICES_PATH)
    extra_cols = [c for c in main_prices.columns if c not in pit_prices.columns]
    return pit_prices.join(main_prices[extra_cols], how="left")


def _fund_sleeve(w: dict, picks: list[str], sleeve_pct: float, momentum_weighted: bool) -> float:
    """Mutates `w` in place funding `picks` from w[SPY], same convention as every sleeve
    in this strategy: capped at whatever SPY actually has, equal or rank-weighted split.
    Returns the amount actually funded (for meta logging)."""
    if not picks:
        return 0.0
    total_funded = min(sleeve_pct, max(0.0, w[SPY]))
    w[SPY] -= total_funded
    if momentum_weighted:
        n = len(picks)
        rank_weights = [n - i for i in range(n)]
        denom = sum(rank_weights)
        for tkr, rw in zip(picks, rank_weights):
            w[tkr] = w.get(tkr, 0.0) + total_funded * rw / denom
    else:
        per_pick = total_funded / len(picks)
        for tkr in picks:
            w[tkr] = w.get(tkr, 0.0) + per_pick
    return total_funded


def build_rebalance_schedule_dual_momentum(
    prices: pd.DataFrame,
    stock_universe: list[str], stock_shared: dict, stock_top_k: int, stock_sleeve_pct: float,
    stock_momentum_weighted: bool,
    etf_universe: list[str], etf_shared: dict, etf_top_k: int, etf_sleeve_pct: float,
    etf_momentum_weighted: bool,
    include_btc: bool = True, as_of: pd.Timestamp | None = None, btc_pit: bool = False,
    spy_gate: bool = False, dynamic_bil: bool = False, bil_shift: float = rebalance.BIL_DYNAMIC_SHIFT,
    country_n_picks: int = rebalance.COUNTRY_SLEEVE_N_PICKS,
    country_pct_per_pick: float = rebalance.COUNTRY_SLEEVE_PCT_PER_PICK,
    funding_order: tuple[str, str, str] = ("stock", "etf", "country"),
    funding_mode: str = "spy_only",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`funding_order` controls which sleeve draws from SPY first when the
    combined ask exceeds what's available (each sleeve is still individually
    capped at "whatever SPY has left", same convention throughout this repo --
    this only changes WHO gets first claim on a shared, sometimes-insufficient
    pool).

    `funding_mode`:
      - "spy_only" (default, matches the frozen dual_momentum_capped075_stoploss25
        identity): sleeves draw only from SPY, each individually capped at
        whatever SPY has left when its turn in `funding_order` comes up. A
        sleeve can be starved down to 0% if earlier sleeves in the order
        already exhausted SPY (this is what left the country sleeve at 0% on
        a real date this session).
      - "proportional_all": sleeves ALWAYS get their full requested size
        (stock_sleeve_pct, etf_sleeve_pct, country_pct_per_pick per pick) --
        the shortfall is drawn proportionally from ALL base assets (SPY,
        Gold, BTC, BIL), not just SPY, i.e. Gold/BTC/BIL shrink too to make
        room instead of staying fixed. `funding_order` is unused in this
        mode (every sleeve is fully funded, so draw order doesn't matter).

    Do not change the default (`funding_order=("stock","etf","country")`,
    `funding_mode="spy_only"`) for the frozen candidate; pass explicit values
    to test a variant instead."""
    base_weights = rebalance.BASE_WEIGHTS if include_btc else rebalance.BASE_WEIGHTS_NO_BTC
    idx = prices.index
    rb_dates = rebalance.month_end_dates(idx, as_of=as_of)

    stock_score, stock_elig = stock_shared["stock_score"], stock_shared["stock_elig"]
    etf_score, etf_elig = etf_shared["stock_score"], etf_shared["stock_elig"]
    country_score, country_elig = stock_shared["country_score"], stock_shared["country_elig"]
    spy_sma200, spy_dd252 = stock_shared["spy_sma200"], stock_shared["spy_dd252"]

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

        stock_picks = [] if (spy_gate and triggered) else rebalance._top_n(stock_score.loc[d], stock_elig.loc[d], stock_top_k)
        etf_picks = [] if (spy_gate and triggered) else rebalance._top_n(etf_score.loc[d], etf_elig.loc[d], etf_top_k)
        country_picks = rebalance._top_n(country_score.loc[d], country_elig.loc[d], country_n_picks)

        if funding_mode == "proportional_all":
            total_request = (
                (stock_sleeve_pct if stock_picks else 0.0)
                + (etf_sleeve_pct if etf_picks else 0.0)
                + country_pct_per_pick * len(country_picks)
            )
            base_total = sum(w.values())
            scale = max(0.0, min(1.0, (base_total - total_request) / base_total)) if base_total > 0 else 0.0
            for k in list(w.keys()):
                w[k] *= scale
            if stock_picks:
                _fund_sleeve(w, stock_picks, stock_sleeve_pct, stock_momentum_weighted)
            if etf_picks:
                _fund_sleeve(w, etf_picks, etf_sleeve_pct, etf_momentum_weighted)
            for tkr in country_picks:
                w[tkr] = w.get(tkr, 0.0) + country_pct_per_pick
        else:
            def _fund_country() -> None:
                for tkr in country_picks:
                    funded = min(country_pct_per_pick, max(0.0, w[SPY]))
                    w[SPY] -= funded
                    w[tkr] = w.get(tkr, 0.0) + funded

            _sleeve_funders = {
                "stock": lambda: _fund_sleeve(w, stock_picks, stock_sleeve_pct, stock_momentum_weighted),
                "etf": lambda: _fund_sleeve(w, etf_picks, etf_sleeve_pct, etf_momentum_weighted),
                "country": _fund_country,
            }
            for sleeve_name in funding_order:
                _sleeve_funders[sleeve_name]()

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
            "stock_picks": ",".join(stock_picks) if stock_picks else "",
            "n_stock_picks": len(stock_picks),
            "etf_picks": ",".join(etf_picks) if etf_picks else "",
            "n_etf_picks": len(etf_picks),
            "country_picks": ",".join(country_picks) if country_picks else "",
            "n_country_picks": len(country_picks),
            "gold_boost_triggered": triggered,
            "gold_boost_pct": boost,
            "spy_weight": w[SPY],
            "gold_weight": w[GOLD],
            "btc_included": btc_available,
        })

    meta = pd.DataFrame(meta_rows).set_index("date")
    return weights, meta


def build_rebalance_schedule_dual_momentum_frozen(
    prices: pd.DataFrame,
    include_btc: bool = True,
    as_of: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`strategy_registry.ScheduleBuilder`-shaped entry point for the frozen
    dual_momentum_capped075_stoploss25 identity (config/country_etf_tactical_dual_momentum_config.json,
    reproduce_command: --stock-top-k 2 --stock-weighted --stock-sleeve-pct 0.25 --etf-top-k 5
    --etf-sleeve-pct 0.30 --spy-gate --dynamic-bil --country-n-picks 1 --stock-momentum-cap 0.75).

    `--stock-stoploss-pct 0.25` is deliberately NOT applied here -- that flag only ever
    affected scripts/run_country_etf_tactical_dual_momentum_backtest.py's backtest P&L
    simulation (via pit_megacap's build_stock_stoploss_multipliers + extra_daily_mult), never
    the target weights this function computes. The live equivalent is
    compute_live_stoploss_multipliers below, wired in as
    strategy_registry.StrategySpec.extra_multiplier_fn instead -- cloud_producer applies it
    the same way it applies the SPY/Gold/BTC overlay, after this function returns.

    `prices` is used directly as the already-aligned frame (cloud_producer.compute_latest_weights
    aligns before calling any schedule_builder, same convention as build_rebalance_schedule_country_only)
    -- in production this is StrategySpec.prices_filename's content, i.e. the persisted merged
    cache build_dual_momentum_merged_prices.py writes (see that script and
    country_etf_tactical.strategy_registry). Does NOT reload from disk itself: that would make
    this function ignore whatever `prices` its caller (production or a test) actually passed
    in, which is both wrong and (for the fail-closed staleness/missing-column contract tests
    every registered spec is parametrized over -- test_strategy_registry.py) a crash risk, since
    `signal_date` would then come from real on-disk dates absent from the caller's frame.
    """
    # Lazy, function-local import -- see module docstring for why these two come from
    # scripts/ rather than this package.
    scripts_dir = REPO_ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import run_country_etf_tactical_pit_megacap_momentum_backtest as pit  # noqa: E402
    import run_country_etf_tactical_sector_growth_momentum_backtest as sg  # noqa: E402

    intervals = pit.load_membership_intervals()
    baseline_cols = set(pd.read_parquet(BASELINE_PRICES_PATH).columns)
    stock_universe = [
        c for c in prices.columns if c not in baseline_cols and c not in SECTOR_GROWTH_UNIVERSE
    ]
    etf_universe = [c for c in GROWTH_FACTOR_UNIVERSE if c in prices.columns]

    pit_membership = pit.build_pit_membership_mask(prices.index, intervals, stock_universe)
    stock_shared = dict(pit.compute_shared_signals(prices, stock_universe, pit_membership))
    always_elig = sg.build_always_eligible_membership(prices.index, etf_universe)
    etf_shared = pit.compute_shared_signals(prices, etf_universe, always_elig)

    # --stock-momentum-cap 0.75
    if stock_universe:
        stock_shared["stock_score"] = signals.momentum_score_capped(prices[stock_universe], cap=0.75)

    # btc_pit tied to include_btc, NOT hardcoded True: btc_pit=True makes BTC inclusion a
    # function of that day's raw price availability (`pd.notna(prices.loc[d, BTC])`),
    # completely ignoring `include_btc` -- correct for reproducing the frozen candidate's
    # own with-BTC identity (export_dual_momentum_capped_sl() always passes
    # include_btc=True, btc_pit=True together), but it means a hardcoded btc_pit=True
    # silently keeps BTC-USD at 10% even when cloud_producer asks for the no-BTC primary
    # variant (include_btc=False) -- caught by hand-inspecting a real produced artifact,
    # see PROGRESS.md. Tying the two together instead makes include_btc=False correctly
    # fall through to the static BASE_WEIGHTS_NO_BTC branch (same mechanism
    # country_only_gate/gold_dd_boost16 already rely on for their own no-BTC variant).
    return build_rebalance_schedule_dual_momentum(
        prices, stock_universe, stock_shared, stock_top_k=2, stock_sleeve_pct=0.25,
        stock_momentum_weighted=True,
        etf_universe=etf_universe, etf_shared=etf_shared, etf_top_k=5, etf_sleeve_pct=0.30,
        etf_momentum_weighted=False,
        include_btc=include_btc, as_of=as_of, btc_pit=include_btc, spy_gate=True, dynamic_bil=True,
        country_n_picks=1,
    )


def compute_live_stoploss_multipliers(
    aligned: pd.DataFrame,
    weights_schedule: pd.DataFrame,
    meta: pd.DataFrame,
    signal_date: pd.Timestamp,
    stoploss_pct: float = 0.25,
    cut_mult: float = 0.0,
) -> dict[str, float]:
    """Today's per-stock hard-stop multiplier for the stock sleeve picked at
    `signal_date` -- same entry-price/hard-cliff/no-reset semantics as pit_megacap's
    build_stock_stoploss_multipliers (a fresh entry price at each rebalance; once the
    close has drawn down `stoploss_pct` from that entry on ANY day since, cut to
    `cut_mult` for the rest of the holding period, no recovery reset within it).
    Duplicated rather than imported from that scripts/ module -- see this module's
    docstring; test_rebalance_dual_momentum.py pins this to the same behavior with a
    synthetic price series.

    Only evaluates the single most-recent, still-open holding window (signal_date ->
    today) -- that's all the daily cloud producer needs, not a full historical schedule.
    Which tickers to check comes from `meta`'s `stock_picks` column (already exactly the
    stock-sleeve-only subset of that rebalance's holdings -- see
    build_rebalance_schedule_dual_momentum's meta_rows), not a separate stock_universe
    argument.
    """
    picks_str = meta.loc[signal_date, "stock_picks"]
    held_tickers = [t for t in picks_str.split(",") if t] if picks_str else []

    today = aligned.index.max()
    period_idx = aligned.index[(aligned.index > signal_date) & (aligned.index <= today)]

    result: dict[str, float] = {}
    for t in held_tickers:
        entry_price = aligned.loc[signal_date, t] if t in aligned.columns else None
        if entry_price is None or pd.isna(entry_price) or entry_price <= 0 or len(period_idx) == 0:
            result[t] = 1.0
            continue
        dd = aligned.loc[period_idx, t] / entry_price - 1.0
        result[t] = cut_mult if bool((dd <= -stoploss_pct).any()) else 1.0
    return result
