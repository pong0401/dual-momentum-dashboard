"""Full daily simulation: monthly target weights + daily exposure overlay, lag-1 execution."""
from __future__ import annotations

import pandas as pd

from . import overlay, rebalance
from .alignment import align_to_master_calendar
from .universe import BASE_ASSETS, BIL, BTC, GOLD, SPY


def run_backtest(
    prices: pd.DataFrame,
    include_btc: bool = True,
    weights_schedule: pd.DataFrame | None = None,
    rebalance_meta: pd.DataFrame | None = None,
    btc_pit: bool = False,
    extra_daily_mult: pd.DataFrame | None = None,
    dynamic_bil: bool = False,
    bil_shift: float = rebalance.BIL_DYNAMIC_SHIFT,
    apply_daily_overlay: bool = True,
) -> dict:
    """`weights_schedule`/`rebalance_meta` let a caller substitute an alternate rebalance
    schedule (e.g. a momentum sleeve built over a different universe) while reusing the
    same lag-1 daily simulation and overlay logic. Both are computed internally via
    `rebalance.build_rebalance_schedule` when omitted (the original behavior).

    `btc_pit=True` drops BTC from the sim_start binding constraint (its 10% slot lives in
    SPY until BTC has data -- see rebalance.build_rebalance_schedule) so the simulation
    can start as early as SPY/Gold/BIL allow instead of BTC's 2014 inception.

    `extra_daily_mult` is a caller-supplied daily multiplier (values in [0, 1], indexed
    like `prices`) applied on top of `daily_target` for whichever of its columns are
    present in the schedule -- e.g. a per-stock trend cut for a momentum stock sleeve,
    which otherwise only gets the built-in SPY/Gold/BTC overlay. Computed off today's
    close but applied lag-1, same convention as `overlay.build_daily_multipliers`.

    `apply_daily_overlay=False` skips the built-in SPY/Gold/BTC daily exposure overlay
    entirely (positions are held at their monthly target weight until the next rebalance,
    no intra-month de-risking) -- lets a caller A/B the overlay's contribution to a given
    strategy's performance. `extra_daily_mult`, if supplied, still applies either way."""
    aligned = align_to_master_calendar(prices)

    if btc_pit:
        base_assets = [t for t in BASE_ASSETS if t != BTC]
    else:
        base_assets = BASE_ASSETS if include_btc else [t for t in BASE_ASSETS if t != BTC]
    sim_start = max(aligned[t].first_valid_index() for t in base_assets)

    if weights_schedule is None:
        weights_schedule, meta = rebalance.build_rebalance_schedule(
            aligned, include_btc=include_btc, btc_pit=btc_pit, dynamic_bil=dynamic_bil,
            bil_shift=bil_shift,
        )
    else:
        meta = rebalance_meta
    mult = overlay.build_daily_multipliers(aligned, include_btc=(include_btc or btc_pit))

    if btc_pit:
        base_weights = {SPY: 0.50, GOLD: 0.30, BIL: 0.20}
    else:
        base_weights = rebalance.BASE_WEIGHTS if include_btc else rebalance.BASE_WEIGHTS_NO_BTC
    tickers = weights_schedule.columns
    daily_target_raw = weights_schedule.reindex(aligned.index, method="ffill")
    daily_target = daily_target_raw.shift(1)
    daily_target.loc[daily_target.index < weights_schedule.index[0]] = 0.0
    for t, w in base_weights.items():
        pre_first = daily_target.index < weights_schedule.index[0]
        daily_target.loc[pre_first, t] = w

    mult_effective = mult.shift(1).fillna(1.0)

    overlay_cols = (SPY, GOLD, BTC) if (include_btc or btc_pit) else (SPY, GOLD)
    effective_weights = daily_target.copy()
    if apply_daily_overlay:
        for t in overlay_cols:
            effective_weights[t] = daily_target[t] * mult_effective[t]

    if extra_daily_mult is not None:
        extra_mult_effective = extra_daily_mult.reindex(aligned.index).shift(1).fillna(1.0)
        for t in extra_daily_mult.columns:
            if t in tickers:
                effective_weights[t] = daily_target[t] * extra_mult_effective[t]

    asset_returns = aligned[tickers].pct_change().fillna(0.0)

    weighted_returns = effective_weights[tickers] * asset_returns[tickers]
    port_return = weighted_returns.sum(axis=1)
    cash_weight = 1.0 - effective_weights[tickers].sum(axis=1)

    port_return = port_return.loc[sim_start:]
    effective_weights = effective_weights.loc[sim_start:]
    cash_weight = cash_weight.loc[sim_start:]
    spy_return = aligned[SPY].pct_change().fillna(0.0).loc[sim_start:]

    equity = (1.0 + port_return).cumprod()
    spy_equity = (1.0 + spy_return).cumprod()

    return {
        "sim_start": sim_start,
        "port_return": port_return,
        "equity": equity,
        "spy_return": spy_return,
        "spy_equity": spy_equity,
        "effective_weights": effective_weights,
        "cash_weight": cash_weight,
        "rebalance_meta": meta,
        "weights_schedule": weights_schedule,
    }
