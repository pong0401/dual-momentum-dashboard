"""Country ETF Tactical + Gold DD Boost 16, with the momentum sleeve built from a pool of
sector + broad-growth ETFs instead of individual PIT S&P 500 stocks or the old 7-ticker
mixed factor/sector/country list.

Reuses the generic rebalance/backtest machinery already built for the PIT mega-cap stock
sleeve (scripts/run_country_etf_tactical_pit_megacap_momentum_backtest.py) -- that code
only cares about a "candidate ticker pool" and doesn't assume the candidates are
individual stocks, so no new rebalance logic is needed here. The only difference is the
candidate pool itself and that there's no PIT-membership concept for ETFs (an ETF doesn't
stop being "eligible" the way an S&P 500 constituent can drop out of the index -- it's
just present or absent from the price history), so a PIT membership mask isn't needed
here; a plain "always eligible once it has price history" mask stands in for it.

Candidate pool: SECTOR_GROWTH_UNIVERSE (src/country_etf_tactical/universe.py) = the 11
SPDR Select Sector ETFs + QQQ/VUG/IWF/SPYG/VGT + the existing SPMO/MTUM/SCHG factor ETFs.
Deliberately excludes the single-country tickers (EWY/EWJ/INDA) that leak into the old
MOMENTUM_UNIVERSE, since country exposure already has its own dedicated 43-ETF sleeve --
this pool is meant to be pure US sector/factor/growth rotation.

Also exposes --country-n-picks / --country-pct-per-pick to shrink the country sleeve
(default 2 picks x 4% = 8%) and free that SPY headroom for the momentum sleeve, per the
"reduce country, grow sector/growth" experiment this script was built to run.

Usage:
  python scripts/run_country_etf_tactical_sector_growth_momentum_backtest.py --top-k 1 2 3 --spy-gate --dynamic-bil
  python scripts/run_country_etf_tactical_sector_growth_momentum_backtest.py --top-k 2 --spy-gate --dynamic-bil --momentum-weighted --country-n-picks 1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from country_etf_tactical import backtest, metrics, rebalance  # noqa: E402
from country_etf_tactical.alignment import align_to_master_calendar  # noqa: E402
from country_etf_tactical.universe import SECTOR_GROWTH_UNIVERSE  # noqa: E402
import run_country_etf_tactical_pit_megacap_momentum_backtest as pit  # noqa: E402

PRICES_PATH = REPO_ROOT / "data" / "cache" / "country_etf_tactical_prices.parquet"
RESULTS_DIR = REPO_ROOT / "results"


def build_always_eligible_membership(dates: pd.DatetimeIndex, tickers: list[str]) -> pd.DataFrame:
    """Stand-in for PIT S&P 500 membership: an ETF is "eligible" (by this signal, before
    the momentum/history checks) for every date -- compute_shared_signals still applies
    valid_obs_count/eligibility_mask on top, so pre-inception dates are excluded via NaN
    prices exactly as they are for any other ticker."""
    return pd.DataFrame(True, index=dates, columns=tickers)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--no-btc", action="store_true")
    parser.add_argument("--no-btc-pit", action="store_true")
    parser.add_argument("--spy-gate", action="store_true")
    parser.add_argument("--dynamic-bil", action="store_true")
    parser.add_argument("--momentum-weighted", action="store_true")
    parser.add_argument("--sleeve-pct", type=float, default=rebalance.MOMENTUM_SLEEVE_PCT)
    parser.add_argument("--country-n-picks", type=int, default=rebalance.COUNTRY_SLEEVE_N_PICKS)
    parser.add_argument("--country-pct-per-pick", type=float, default=rebalance.COUNTRY_SLEEVE_PCT_PER_PICK)
    args = parser.parse_args()
    include_btc = not args.no_btc
    btc_pit = not args.no_btc_pit

    prices = pd.read_parquet(PRICES_PATH)
    stock_universe = SECTOR_GROWTH_UNIVERSE
    print(f"Sector/growth ETF momentum universe: {len(stock_universe)} tickers: {stock_universe}")

    aligned_for_mask = align_to_master_calendar(prices)
    always_elig = build_always_eligible_membership(aligned_for_mask.index, stock_universe)
    shared = pit.compute_shared_signals(aligned_for_mask, stock_universe, always_elig)

    baseline = backtest.run_backtest(prices, include_btc=include_btc, btc_pit=btc_pit, dynamic_bil=args.dynamic_bil)
    baseline_table = metrics.performance_table(baseline["port_return"], baseline["spy_return"])
    print(f"\n=== Baseline (ETF momentum sleeve, top-1, original MOMENTUM_UNIVERSE), btc_pit={btc_pit} ===")
    print(f"Simulation start: {baseline['sim_start'].date()}")
    pd.set_option("display.float_format", lambda x: f"{x:.4f}")
    print(baseline_table.to_string())

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    gate_suffix = "_spygate" if args.spy_gate else ""
    bil_suffix = "_dynbil" if args.dynamic_bil else ""
    weighted_suffix = "_weighted" if args.momentum_weighted else ""
    sleeve_suffix = "" if args.sleeve_pct == rebalance.MOMENTUM_SLEEVE_PCT else f"_sleeve{int(round(args.sleeve_pct * 100))}"
    country_suffix = (
        "" if (args.country_n_picks == rebalance.COUNTRY_SLEEVE_N_PICKS
               and args.country_pct_per_pick == rebalance.COUNTRY_SLEEVE_PCT_PER_PICK)
        else f"_country{args.country_n_picks}x{int(round(args.country_pct_per_pick * 100))}"
    )

    for k in args.top_k:
        result = pit.run_variant(
            prices, stock_universe, shared, top_k=k, include_btc=include_btc, btc_pit=btc_pit,
            spy_gate=args.spy_gate, dynamic_bil=args.dynamic_bil, momentum_weighted=args.momentum_weighted,
            sleeve_pct=args.sleeve_pct, country_n_picks=args.country_n_picks,
            country_pct_per_pick=args.country_pct_per_pick,
        )
        table = metrics.performance_table(result["port_return"], result["spy_return"])
        print(
            f"\n=== Sector/growth ETF momentum sleeve, top-{k}, spy_gate={args.spy_gate}, "
            f"dynamic_bil={args.dynamic_bil}, momentum_weighted={args.momentum_weighted}, "
            f"sleeve_pct={args.sleeve_pct}, country={args.country_n_picks}x{args.country_pct_per_pick} ==="
        )
        print(f"Simulation start: {result['sim_start'].date()}")
        print(table.to_string())

        suffix = f"top{k}{gate_suffix}{bil_suffix}{weighted_suffix}{sleeve_suffix}{country_suffix}"
        table.to_csv(RESULTS_DIR / f"country_etf_tactical_sector_growth_{suffix}_performance.csv")
        result["equity"].to_frame("strategy_equity").join(
            result["spy_equity"].to_frame("spy_equity")
        ).to_csv(RESULTS_DIR / f"country_etf_tactical_sector_growth_{suffix}_equity_curve.csv")
        result["rebalance_meta"].to_csv(RESULTS_DIR / f"country_etf_tactical_sector_growth_{suffix}_rebalance_log.csv")

        sim_meta = result["rebalance_meta"].loc[result["sim_start"]:]
        picks_flat = sim_meta["momentum_picks"].str.split(",").explode()
        picks_flat = picks_flat[picks_flat != ""]
        print(f"Most-picked ETFs (top-{k}, simulated period only): {picks_flat.value_counts().head(10).to_dict()}")
        empty_pick_months = (sim_meta["n_momentum_picks"] == 0).sum()
        print(f"Months with zero eligible picks (sleeve capital stays in SPY): {empty_pick_months} / {len(sim_meta)}")

    print(f"\nSaved all performance tables, equity curves, and rebalance logs to {RESULTS_DIR}")


if __name__ == "__main__":
    main()
