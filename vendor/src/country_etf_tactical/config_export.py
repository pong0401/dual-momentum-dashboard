"""Dump the strategy's exact parameters (universe, weights, thresholds, assumptions) as a
plain dict, generated from the actual source-of-truth constants in this package so it
never drifts from the code. Shared by scripts/export_strategy_config.py (writes it to
disk for local inspection) and cloud_producer (hashes it for the config_hash identity
field), so both always see the same content.
"""
from __future__ import annotations

from . import overlay, rebalance, signals
from . import universe as u


def build_config() -> dict:
    return {
        "strategy_name": "Country ETF tactical + Gold DD boost 16",
        "universe": {
            "base_assets": u.BASE_ASSETS,
            "aux_price_only": u.AUX_PRICE_ONLY,
            "momentum_universe": u.MOMENTUM_UNIVERSE,
            "country_universe": u.COUNTRY_UNIVERSE,
            "country_min_history_trading_days": u.COUNTRY_MIN_HISTORY_DAYS,
        },
        "base_allocation": {
            "with_btc": rebalance.BASE_WEIGHTS,
            "no_btc_variant": {
                "note": "BTC's 10% redistributed proportionally across SPY/Gold/BIL (45:30:15 ratio preserved)",
                "weights": rebalance.BASE_WEIGHTS_NO_BTC,
            },
        },
        "sleeves": {
            "momentum_sleeve_pct_from_spy": rebalance.MOMENTUM_SLEEVE_PCT,
            "momentum_sleeve_n_picks": 1,
            "country_sleeve_pct_per_pick_from_spy": rebalance.COUNTRY_SLEEVE_PCT_PER_PICK,
            "country_sleeve_n_picks": rebalance.COUNTRY_SLEEVE_N_PICKS,
            "spy_floor_after_funding_and_boost": rebalance.SPY_FLOOR,
        },
        "signal_definitions": {
            "momentum_score_weights": {
                "1m_pct": signals.MOM_WEIGHTS[signals.DAYS_1M],
                "3m_pct": signals.MOM_WEIGHTS[signals.DAYS_3M],
                "6m_pct": signals.MOM_WEIGHTS[signals.DAYS_6M],
                "12m_pct": signals.MOM_WEIGHTS[signals.DAYS_12M],
            },
            "lookback_trading_days": {
                "1m": signals.DAYS_1M, "3m": signals.DAYS_3M,
                "6m": signals.DAYS_6M, "12m": signals.DAYS_12M,
            },
            "eligibility_sma_window": signals.SMA_WINDOW,
            "eligibility_rule": "3M return > 0 AND 6M return > 0 AND price > SMA200",
            "signal_execution": "lag-1 close (signal computed at close D, applied from session D+1)",
            "rebalance_frequency": "monthly, last trading day of month",
        },
        "gold_dd_boost": {
            "trigger": "SPY close < SMA200 OR SPY 252-trading-day drawdown <= -8%",
            "trigger_evaluated": "at monthly rebalance (not re-evaluated intra-month)",
            "max_boost_pct": rebalance.GOLD_DD_BOOST_MAX,
            "boost_type": "binary (full max_boost_pct when triggered, 0 otherwise), clipped so SPY stays >= floor",
            "dd_window_trading_days": rebalance.GOLD_DD_WINDOW,
            "dd_trigger_threshold": rebalance.GOLD_DD_TRIGGER,
        },
        "daily_exposure_overlay": {
            "applies_to": "standing target weights between rebalances, evaluated off lag-1 close",
            "spy": {
                "condition": f"close < SMA{overlay.SPY_MA300_WINDOW}",
                "multiplier_when_triggered": overlay.SPY_MA300_CUT_MULT,
            },
            "gold": {
                "condition": f"{overlay.GOLD_DD_WINDOW}d drawdown <= {overlay.GOLD_DD_TRIGGER}",
                "multiplier_when_triggered": overlay.GOLD_DD_CUT_MULT,
                "note": "applies to combined base+boost Gold weight",
            },
            "btc": {
                "condition": f"close < SMA{overlay.BTC_MA50_WINDOW}",
                "multiplier_when_triggered": overlay.BTC_MA50_CUT_MULT,
                "note": "not applicable in --no-btc variant",
            },
            "momentum_etf_country_etfs_bil": "held at full target weight until next rebalance, no daily discount",
            "freed_capacity_destination": "Cash (0% return), separate from the BIL sleeve",
        },
        "assumptions": [
            "Gold DD boost is decided at monthly rebalance, not re-evaluated daily; only the combined "
            "Gold exposure (base+boost) is subject to the daily 50%-cut overlay rule.",
            "Boost is binary 16% (not graded/partial) when triggered.",
            "Unfilled momentum/country sleeve allocations (too few eligible names) revert to SPY, not cash.",
            "Daily-overlay 'reduced exposure' cash earns 0%, separate from the BIL sleeve.",
            "Sharpe ratio uses risk-free rate = 0%.",
            "IEF is loaded as price data (per spec) but is never used as a target weight.",
            "--no-btc variant redistributes BTC's 10% proportionally across SPY/Gold/BIL (45:30:15 ratio).",
            "Gold signal ticker GC=F is a research signal only; any execution-side mapping to a "
            "tradable instrument (e.g. GLD) is a downstream execution concern, not part of this config.",
        ],
        "data": {
            "source": "yfinance, auto_adjust=True daily Close",
            "master_trading_calendar": "SPY's trading days; other tickers forward-filled onto it",
            "true_max_backtest_start_with_btc": "2014-09-17 (binding constraint: BTC-USD)",
            "true_max_backtest_start_no_btc": "2007-05-30 (binding constraint: BIL)",
            "delisted_tickers_noted": {"EGPT": "last data 2024-04-04", "PAK": "last data 2024-03-05"},
        },
    }
