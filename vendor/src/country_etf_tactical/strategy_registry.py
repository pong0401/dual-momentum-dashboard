"""Which strategies the fail-closed cloud producer can emit weights for.

One `StrategySpec` per strategy that a downstream executor is allowed to consume. The
`app_strategy_id` is the id the AI_port_manager_webull app uses (`portfolio_service.STRATEGIES`);
`research_slug` is this repo's result-file naming, kept so an artifact can always be traced
back to the backtest that justified it.

Adding a strategy here is what makes it producible — a strategy absent from this registry
has no cloud artifact and the downstream app shows "no daily exposure available" rather
than falling back to the pre-overlay monthly weights.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Callable

import pandas as pd

from . import rebalance
from .rebalance_country_only import build_rebalance_schedule_country_only
from .rebalance_dual_momentum import (
    build_rebalance_schedule_dual_momentum_frozen,
    compute_live_stoploss_multipliers,
)
from .universe import BTC, GOLD, SPY

# Signature every builder must satisfy: (prices, include_btc, as_of) -> (weights, meta).
# Strategy-specific knobs are bound with functools.partial in the spec below, so the
# producer never needs to know what they are.
ScheduleBuilder = Callable[..., "tuple[pd.DataFrame, pd.DataFrame]"]

# (aligned, weights_schedule, meta, signal_date) -> {ticker: multiplier}. Same shape as
# the base SPY/Gold/BTC overlay's per-ticker multiplier, but for a signal that (unlike the
# base overlay) depends on which tickers a strategy's own schedule_builder happened to
# pick this rebalance -- e.g. dual_momentum's per-stock stop-loss. See
# rebalance_dual_momentum.compute_live_stoploss_multipliers.
ExtraMultiplierFn = Callable[[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.Timestamp], "dict[str, float]"]


@dataclass(frozen=True)
class StrategySpec:
    app_strategy_id: str
    research_slug: str
    display_name: str
    schedule_builder: ScheduleBuilder
    # Tickers whose standing weight the daily overlay scales. Must be a subset of what
    # overlay.build_daily_multipliers actually computes, or the producer would silently
    # skip a cut it was supposed to apply.
    overlay_assets_no_btc: tuple[str, ...]
    overlay_assets_with_btc: tuple[str, ...]
    prices_filename: str
    # False for a strategy whose holdings the overlay does not touch at all — downstream
    # must then never present a "daily exposure signal" for it.
    has_daily_overlay: bool = True
    # Optional second daily-multiplier source beyond the base SPY/Gold/BTC overlay, for a
    # strategy whose overlay-eligible tickers aren't fixed in advance (e.g. dynamically
    # picked stock-sleeve members). None for every strategy that doesn't need one — zero
    # behavior change for country_only_gate/gold_dd_boost16.
    extra_multiplier_fn: ExtraMultiplierFn | None = None


_COUNTRY_ONLY_GATE = StrategySpec(
    app_strategy_id="country_only_gate",
    research_slug="country_etf_tactical_country_only_top1_4_with_daily_overlay",
    display_name="Country ETF Tactical (SPY-Gated)",
    # top-1 country pick at 4% — the headline config behind the app's backtest numbers
    # (config/country_etf_tactical_country_only_config.json, reproduce_command).
    schedule_builder=partial(
        build_rebalance_schedule_country_only,
        spy_gate=True,
        country_n_picks=1,
        country_pct_per_pick=rebalance.COUNTRY_SLEEVE_PCT_PER_PICK,
    ),
    overlay_assets_no_btc=(SPY, GOLD),
    overlay_assets_with_btc=(SPY, GOLD, BTC),
    prices_filename="country_etf_tactical_prices.parquet",
)

_GOLD_DD_BOOST16 = StrategySpec(
    app_strategy_id="country_etf_tactical_gold_dd_boost16",
    research_slug="country_etf_tactical_gold_dd_boost16",
    display_name="Country ETF tactical + Gold DD boost 16",
    schedule_builder=rebalance.build_rebalance_schedule,
    overlay_assets_no_btc=(SPY, GOLD),
    overlay_assets_with_btc=(SPY, GOLD, BTC),
    prices_filename="country_etf_tactical_prices.parquet",
)

# Frozen dual_momentum_capped075_stoploss25 identity (config/country_etf_tactical_dual_momentum_config.json).
# research-only candidate, promoted here after explicit user sign-off despite no
# paper-trading track record -- same asset universe/cost profile as the already-promoted
# country_only_gate. overlay_assets_* cover only the fixed SPY/Gold/BTC base; the
# dynamically-picked stock sleeve's 25% stop-loss is handled separately via
# extra_multiplier_fn (see rebalance_dual_momentum.compute_live_stoploss_multipliers) since
# which tickers it applies to changes every rebalance.
_DUAL_MOMENTUM_CAPPED_SL = StrategySpec(
    app_strategy_id="dual_momentum_capped_sl",
    research_slug=(
        "country_etf_tactical_dual_momentum_stock_top2_25w_sl25_cap0p75_etf_top5_30_country1"
    ),
    display_name="Dual Momentum (Capped, Stop-Loss)",
    schedule_builder=build_rebalance_schedule_dual_momentum_frozen,
    overlay_assets_no_btc=(SPY, GOLD),
    overlay_assets_with_btc=(SPY, GOLD, BTC),
    prices_filename="country_etf_tactical_dual_momentum_merged_prices.parquet",
    extra_multiplier_fn=compute_live_stoploss_multipliers,
)

# The producer's original single strategy. Kept as the default so every existing caller
# (scripts, tests) behaves exactly as before this module existed.
DEFAULT_SPEC = _GOLD_DD_BOOST16

SPECS: dict[str, StrategySpec] = {
    spec.app_strategy_id: spec
    for spec in (_GOLD_DD_BOOST16, _COUNTRY_ONLY_GATE, _DUAL_MOMENTUM_CAPPED_SL)
}


class UnknownStrategyError(LookupError):
    """Asked to produce weights for a strategy that is not registered here."""


def get_spec(app_strategy_id: str) -> StrategySpec:
    try:
        return SPECS[app_strategy_id]
    except KeyError as exc:
        known = ", ".join(sorted(SPECS))
        raise UnknownStrategyError(
            f"unknown strategy id {app_strategy_id!r}; registered: {known}"
        ) from exc


def all_strategy_ids() -> list[str]:
    return sorted(SPECS)
