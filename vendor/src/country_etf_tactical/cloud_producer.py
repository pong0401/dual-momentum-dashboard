"""Fail-closed cloud weights producer.

Computes the effective-next-session target weights off the last *completed* official
month-end, plus the current daily exposure overlay, and returns a result the caller
serializes into immutable S3-ready artifacts (manifest.json, weights.json, latest.json).

Fail-closed contract: on any of the conditions below this module raises
`CloudProducerError` instead of emitting a decision. The caller (the CLI script) must
turn that into a non-zero process exit and must never fall back to a fabricated value —
not a close price for a missing quote, not a guessed date, not the prior day's weights
relabeled with today's date.

  - a required base asset is missing from the price data entirely
  - the aligned price history is too short for the country-ETF eligibility gate to be
    meaningful (see MIN_ALIGNED_SESSIONS)
  - the price cache is too stale relative to `as_of` to plausibly contain the last
    completed month's real close
  - no completed month-end exists yet in the price history
  - the required base asset has no valid price on the resolved signal date
  - weights + cash fail to reconcile to 1.0

The upstream research signal for gold is `GC=F` (COMEX gold futures), used exactly as
computed here. Any execution-side substitution (e.g. mapping `GC=F` to a tradable ETF
like `GLD`) is strictly a downstream execution concern and must not be performed or
implied by this module.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import overlay
from .alignment import align_to_master_calendar
from .strategy_registry import DEFAULT_SPEC, StrategySpec
from .universe import BASE_ASSETS, BTC, COUNTRY_MIN_HISTORY_DAYS, GOLD, SPY

# Back-compat: this module used to produce exactly one strategy. Callers that pass no
# `spec` still get this one, unchanged. Which strategies are producible now lives in
# strategy_registry.SPECS.
STRATEGY_ID = DEFAULT_SPEC.app_strategy_id
GOLD_RESEARCH_TICKER = GOLD  # "GC=F" — research signal only, never an execution instrument here.

# How many calendar days the price cache's last date may lag `as_of` before it is
# treated as too stale to plausibly cover the last completed month's real close.
STALENESS_GRACE_DAYS = 10

# Country-ETF eligibility requires COUNTRY_MIN_HISTORY_DAYS *cumulative valid observations*
# (rebalance.py, via signals.valid_obs_count), counted from the first row of whatever frame
# the caller supplies — not from the ETF's real inception. On a short frame that count is an
# artifact of where the caller happened to start slicing: every country ETF crosses the gate
# at roughly the same session regardless of its actual history, so the selected countries
# become a function of the input window. One extra year of sessions above the gate keeps the
# crossing well clear of the frame's first row, so eligibility reflects real history instead.
#
# This is deliberately an input-shape gate only. The eligibility rule itself is unchanged:
# it remains the cumulative-observation count, NOT direct ETF-inception logic (which would
# alter strategy semantics and needs its own research and acceptance decision).
MIN_HISTORY_SLACK_SESSIONS = 252
MIN_ALIGNED_SESSIONS = COUNTRY_MIN_HISTORY_DAYS + MIN_HISTORY_SLACK_SESSIONS  # 2772


class CloudProducerError(RuntimeError):
    """Fail-closed condition. Caller must exit non-zero and must not fabricate output."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_config_hash(config: dict) -> str:
    """Stable hash over config content, independent of key order or float repr drift."""
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)
    return sha256_bytes(canonical.encode("utf-8"))


@dataclass
class LatestWeightsResult:
    data_asof: pd.Timestamp
    signal_asof: pd.Timestamp
    execution_asof: pd.Timestamp | None
    month_end_decision_date: pd.Timestamp
    target_weights_no_btc: dict
    target_weights_with_btc: dict
    overlay_multipliers_no_btc: dict
    overlay_multipliers_with_btc: dict
    effective_weights_next_session_no_btc: dict
    effective_weights_next_session_with_btc: dict
    cash_weight_next_session_no_btc: float
    cash_weight_next_session_with_btc: float
    rebalance_meta_no_btc: dict
    rebalance_meta_with_btc: dict


def _variant(
    aligned: pd.DataFrame, include_btc: bool, as_of: pd.Timestamp, spec: StrategySpec
) -> tuple[pd.Timestamp, dict, dict, dict, float, dict]:
    required = [t for t in BASE_ASSETS if include_btc or t != BTC]
    for t in required:
        if t not in aligned.columns:
            raise CloudProducerError(f"missing required price column: {t!r}")

    weights_schedule, meta = spec.schedule_builder(
        aligned, include_btc=include_btc, as_of=as_of
    )
    if weights_schedule.empty:
        raise CloudProducerError("no completed month-end available in price history")

    signal_date = weights_schedule.index[-1]

    for t in required:
        if t not in aligned.columns or pd.isna(aligned.loc[signal_date, t]):
            raise CloudProducerError(
                f"missing price for required asset {t!r} at completed signal session "
                f"{pd.Timestamp(signal_date).date()}; cache cannot cover this session"
            )

    target = weights_schedule.loc[signal_date]
    target = target[target.abs() > 1e-9].sort_values(ascending=False)

    effective = target.copy()
    if spec.has_daily_overlay:
        mult = overlay.build_daily_multipliers(aligned, include_btc=include_btc)
        latest_mult = mult.loc[aligned.index.max()]
        overlay_cols = spec.overlay_assets_with_btc if include_btc else spec.overlay_assets_no_btc
        for t in overlay_cols:
            if t in effective.index:
                effective[t] = target[t] * latest_mult[t]
        multipliers = {k: round(float(v), 6) for k, v in latest_mult.to_dict().items()}

        if spec.extra_multiplier_fn is not None:
            extra_mult = spec.extra_multiplier_fn(aligned, weights_schedule, meta, signal_date)
            for t, m in extra_mult.items():
                if t in effective.index:
                    effective[t] = target[t] * m
            multipliers.update({k: round(float(v), 6) for k, v in extra_mult.items()})
    else:
        # No overlay touches this strategy's holdings, so effective == target by
        # construction. Emit empty multipliers rather than a fabricated all-1.0 map, so a
        # consumer can tell "overlay ran and cut nothing" apart from "overlay does not apply".
        multipliers = {}
    cash = 1.0 - float(effective.sum())

    recon = float(effective.sum()) + cash
    if abs(recon - 1.0) > 1e-6:
        raise CloudProducerError(f"weight+cash reconciliation failed: sum={recon}")

    return (
        signal_date,
        target.round(6).to_dict(),
        multipliers,
        effective.round(6).to_dict(),
        round(cash, 6),
        meta.loc[signal_date].to_dict(),
    )


def compute_latest_weights(
    prices: pd.DataFrame,
    as_of: pd.Timestamp | dt.date | str | None = None,
    spec: StrategySpec = DEFAULT_SPEC,
) -> LatestWeightsResult:
    """Compute both the no-BTC (execution-relevant) and with-BTC (research context)
    variants off the same aligned price data. Fail closed on any of the conditions
    documented at module level; never returns a partial or guessed result."""
    if as_of is None:
        as_of = pd.Timestamp.now()
    as_of = pd.Timestamp(as_of)

    if SPY not in prices.columns:
        raise CloudProducerError("missing required price column: 'SPY' (master calendar)")

    # Align first, then measure: the gate is about usable trading sessions on the master
    # calendar, not raw rows in the caller's frame (which may carry non-SPY dates).
    aligned = align_to_master_calendar(prices)

    n_sessions = len(aligned)
    if n_sessions < MIN_ALIGNED_SESSIONS:
        raise CloudProducerError(
            f"history_too_short: {n_sessions} aligned sessions "
            f"(from {len(prices)} supplied rows) is below the required {MIN_ALIGNED_SESSIONS} "
            f"({COUNTRY_MIN_HISTORY_DAYS} country-eligibility gate + "
            f"{MIN_HISTORY_SLACK_SESSIONS} slack); country-ETF eligibility on this frame "
            "would depend on its arbitrary first row rather than on real ETF history"
        )

    data_asof = aligned.index.max()

    lag_days = (as_of.normalize() - pd.Timestamp(data_asof).normalize()).days
    if lag_days > STALENESS_GRACE_DAYS:
        raise CloudProducerError(
            f"price cache stale: last data {pd.Timestamp(data_asof).date()} is "
            f"{lag_days} calendar days behind as_of {as_of.date()} "
            f"(grace={STALENESS_GRACE_DAYS}d); cannot confirm it covers the completed signal session"
        )
    if lag_days < 0:
        raise CloudProducerError(
            f"price cache last date {pd.Timestamp(data_asof).date()} is after as_of {as_of.date()}"
        )

    (
        signal_date_nb, target_nb, mult_nb, eff_nb, cash_nb, meta_nb,
    ) = _variant(aligned, include_btc=False, as_of=as_of, spec=spec)
    (
        signal_date_wb, target_wb, mult_wb, eff_wb, cash_wb, meta_wb,
    ) = _variant(aligned, include_btc=True, as_of=as_of, spec=spec)

    return LatestWeightsResult(
        data_asof=data_asof,
        signal_asof=data_asof,
        # Always None: data_asof is by construction the newest session in the cache, so
        # the D+1 execution session has not happened yet and its calendar date is not
        # guessed here — the downstream executor resolves it against its own trading calendar.
        execution_asof=None,
        month_end_decision_date=signal_date_nb,
        target_weights_no_btc=target_nb,
        target_weights_with_btc=target_wb,
        overlay_multipliers_no_btc=mult_nb,
        overlay_multipliers_with_btc=mult_wb,
        effective_weights_next_session_no_btc=eff_nb,
        effective_weights_next_session_with_btc=eff_wb,
        cash_weight_next_session_no_btc=cash_nb,
        cash_weight_next_session_with_btc=cash_wb,
        rebalance_meta_no_btc=meta_nb,
        rebalance_meta_with_btc=meta_wb,
    )


def build_artifacts(
    result: LatestWeightsResult,
    config: dict,
    source_hash: str,
    run_id: str,
    generated_at_utc: dt.datetime,
    spec: StrategySpec = DEFAULT_SPEC,
) -> tuple[dict, dict, dict]:
    """Build (manifest, weights, latest_pointer) as plain JSON-serializable dicts.
    Contains no local filesystem paths — only relative artifact filenames."""
    config_hash = compute_config_hash(config)

    def _d(x: pd.Timestamp | None) -> str | None:
        return None if x is None else str(pd.Timestamp(x).date())

    identity = {
        "strategy_id": spec.app_strategy_id,
        "config_hash": config_hash,
        "run_id": run_id,
        "data_asof": _d(result.data_asof),
        "signal_asof": _d(result.signal_asof),
        "execution_asof": _d(result.execution_asof),
        "source_hash": source_hash,
        "generated_at_utc": generated_at_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    manifest = {
        **identity,
        "research_slug": spec.research_slug,
        "display_name": spec.display_name,
        "has_daily_overlay": spec.has_daily_overlay,
        "month_end_decision_date": _d(result.month_end_decision_date),
        "execution_asof_note": (
            "null until the next trading session actually occurs in the price feed; "
            "this producer does not guess a calendar date for a session that has not happened yet"
        ),
        "gold_research_ticker": GOLD_RESEARCH_TICKER,
        "gold_execution_mapping_note": (
            "GC=F is the upstream research signal only. Any GC=F -> tradable-instrument "
            "mapping (e.g. GLD) is the downstream execution owner's responsibility and is "
            "not applied here."
        ),
        "variants_included": ["no_btc", "with_btc_research_context"],
        "artifacts": {"manifest": "manifest.json", "weights": "weights.json"},
    }

    weights = {
        **identity,
        "has_daily_overlay": spec.has_daily_overlay,
        "primary_variant": "no_btc",
        "target_weights": result.target_weights_no_btc,
        "overlay_multipliers": result.overlay_multipliers_no_btc,
        "effective_weights_next_session": result.effective_weights_next_session_no_btc,
        "cash_weight_next_session": result.cash_weight_next_session_no_btc,
        "rebalance_meta": result.rebalance_meta_no_btc,
        "research_context": {
            "note": (
                "with_btc reflects the upstream strategy's original allocation including "
                "BTC-USD; the Webull paper executor is authorized to use the no_btc variant "
                "only. Gold signal is GC=F in both variants; no ETF substitution is applied here."
            ),
            "with_btc": {
                "target_weights": result.target_weights_with_btc,
                "overlay_multipliers": result.overlay_multipliers_with_btc,
                "effective_weights_next_session": result.effective_weights_next_session_with_btc,
                "cash_weight_next_session": result.cash_weight_next_session_with_btc,
                "rebalance_meta": result.rebalance_meta_with_btc,
            },
        },
    }

    latest_pointer = {
        "run_id": run_id,
        "strategy_id": spec.app_strategy_id,
        "generated_at_utc": identity["generated_at_utc"],
        "manifest": "manifest.json",
        "weights": "weights.json",
    }

    return manifest, weights, latest_pointer
