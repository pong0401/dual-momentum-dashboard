"""Standalone frozen-strategy reporting and resumable incremental Yahoo ingestion."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'vendor/src'))
sys.path.insert(0, str(ROOT / 'vendor/scripts'))
from country_etf_tactical import backtest, cloud_producer, metrics, strategy_registry
from country_etf_tactical.alignment import align_to_master_calendar
from country_etf_tactical.config_export import build_config
from country_etf_tactical.universe import ALL_TICKERS, GROWTH_FACTOR_UNIVERSE, SPY
import run_country_etf_tactical_pit_megacap_momentum_backtest as pit

SETTINGS = json.loads((ROOT / 'config/settings.json').read_text())
SPEC = strategy_registry.get_spec(SETTINGS['strategy_id'])


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, allow_nan=False, default=str), encoding='utf-8')
    os.replace(temp, path)


def read_prices():
    frames = [pd.read_parquet(p) for p in sorted((ROOT / 'data/prices').glob('*.parquet'))]
    frame = pd.concat(frames).sort_index()
    if frame.index.has_duplicates or frame.columns.has_duplicates:
        raise ValueError('Duplicate date or ticker in stored prices')
    return frame


def save_prices(frame):
    """Only rewrite changed month partitions; older years never download again."""
    for month, part in frame.groupby(frame.index.to_period('M')):
        path = ROOT / 'data/prices' / f'{month}.parquet'
        if path.exists() and pd.read_parquet(path).equals(part):
            continue
        temp = path.with_suffix('.tmp')
        part.to_parquet(temp)
        os.replace(temp, path)


def last_completed_session(now=None):
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    schedule = mcal.get_calendar('NYSE').schedule(
        start_date=(now - pd.Timedelta(days=15)).date(), end_date=now.date())
    # Allow 45 minutes after the official close for Yahoo daily publication.
    done = schedule[schedule.market_close + pd.Timedelta(minutes=45) <= now]
    if done.empty:
        raise ValueError('No completed exchange session')
    return done.index[-1].normalize()


def calculation_asof(last_session, now=None):
    """Permit a completed last exchange session even before calendar month changes."""
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    d = pd.Timestamp(last_session)
    month = mcal.get_calendar('NYSE').schedule(
        start_date=d.replace(day=1), end_date=d + pd.offsets.MonthEnd(0))
    if d == month.index[-1] and pd.Timestamp(month.iloc[-1].market_close) <= now:
        return d + pd.offsets.MonthBegin(1)
    return pd.Timestamp(now.date())


def expected_symbols(frame, session):
    intervals = pit.load_membership_intervals()
    active = intervals[(intervals.start_date <= session) &
                       (intervals.end_date.isna() | (intervals.end_date >= session))]
    return sorted((set(active.ticker) | set(ALL_TICKERS)) & set(frame.columns))


def merge_adjusted(old, fresh):
    """Chain recent dividend/split-adjusted ratios onto the stored price scale.

    No historical redownload or whole-history rewrite for dividend adjustments.
    Stored levels are research indexes, never execution prices.
    """
    fresh = fresh.dropna().sort_index()
    if fresh.empty or (fresh <= 0).any() or not np.isfinite(fresh).all():
        raise ValueError('Empty/nonpositive/nonfinite Yahoo series')
    common = old.dropna().index.intersection(fresh.index)
    if common.empty:
        raise ValueError('No overlap: a new symbol needs an explicit seeded history')
    anchor = common[0]
    scaled = fresh.loc[anchor:] * (old.loc[anchor] / fresh.loc[anchor])
    result = old.reindex(old.index.union(scaled.index)).copy()
    result.loc[scaled.index] = scaled
    return result


def refresh():
    import yfinance as yf
    prices = read_prices()
    session = last_completed_session()
    symbols = expected_symbols(prices, session)
    checkpoint = ROOT / '.refresh' / str(session.date())
    checkpoint.mkdir(parents=True, exist_ok=True)
    failures = []
    todo = [t for t in symbols if pd.isna(prices[t].get(session, np.nan))]
    print(f'{session.date()}: {len(todo)} symbols need incremental refresh / {len(symbols)}', flush=True)

    def fetch(t):
        path = checkpoint / f'{t.replace("=", "_")}.parquet'
        if path.exists():
            return t, pd.read_parquet(path)['close']
        last = prices[t].last_valid_index()
        start = max(last - pd.Timedelta(days=SETTINGS['overlap_days']),
                    session - pd.Timedelta(days=60))
        raw = yf.download(t, start=str(start.date()),
                          end=str((session + pd.Timedelta(days=1)).date()),
                          auto_adjust=True, interval='1d', threads=False,
                          progress=False, timeout=25, multi_level_index=False)
        if raw is None or raw.empty:
            raise ValueError('Yahoo returned no rows')
        s = raw['Close'].dropna()
        s.index = pd.to_datetime(s.index).tz_localize(None)
        s = s.loc[:session]
        if session not in s.index:
            raise ValueError(f'Missing completed close {session.date()}')
        return t, s

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, t): t for t in todo}
        for n, future in enumerate(as_completed(futures), 1):
            t = futures[future]
            try:
                t, fresh = future.result()
                # Main thread persists every unit before it can be lost.
                path = checkpoint / f'{t.replace("=", "_")}.parquet'
                temp = path.with_suffix('.tmp')
                fresh.to_frame('close').to_parquet(temp)
                os.replace(temp, path)
                merged = merge_adjusted(prices[t], fresh)
                prices = prices.reindex(prices.index.union(merged.index))
                prices[t] = merged
            except Exception as exc:
                failures.append({'ticker': t, 'error': str(exc)[:220]})
            if n % 25 == 0:
                save_prices(prices.sort_index())
                print(f'{n}/{len(todo)} saved; failures={len(failures)}', flush=True)
    save_prices(prices.sort_index())
    missing = [t for t in symbols if pd.isna(prices[t].get(session, np.nan))]
    status = {'checked_at_utc': datetime.now(timezone.utc).isoformat(),
              'expected_session': str(session.date()), 'requested': len(todo),
              'fresh_symbols': len(symbols)-len(missing), 'expected_symbols': len(symbols),
              'missing': missing, 'failures': failures,
              'status': 'ok' if not missing and not failures else 'blocked'}
    atomic_json(ROOT / 'data/refresh_status.json', status)
    atomic_json(ROOT / 'docs/data/health.json', status)
    if missing or failures:
        raise RuntimeError(f'Fail closed: {len(missing)} stale symbols, {len(failures)} fetch failures')


def stats(returns):
    returns = returns.dropna()
    if len(returns) < 2:
        return None
    wealth = (1 + returns).cumprod()
    years = len(returns) / 252
    vol = float(returns.std(ddof=1) * np.sqrt(252))
    drawdown = wealth / wealth.cummax().clip(lower=1) - 1
    return {'total_return': float(wealth.iloc[-1]-1),
            'cagr': float(wealth.iloc[-1] ** (1/years)-1),
            'volatility': vol, 'sharpe': float(returns.mean()*252/vol) if vol else 0,
            'max_drawdown': float(drawdown.min()), 'sessions': len(returns)}


def build():
    prices = read_prices()
    session = last_completed_session()
    prices = prices.loc[:session]
    symbols = expected_symbols(prices, session)
    missing = [t for t in symbols if pd.isna(prices[t].get(session, np.nan))]
    if missing:
        atomic_json(ROOT/'docs/data/health.json', {'status':'blocked', 'missing': missing,
                    'expected_session':str(session.date()), 'checked_at_utc':datetime.now(timezone.utc).isoformat()})
        raise ValueError(f'Refusing to publish fresh weights with stale candidates: {missing}')
    aligned = align_to_master_calendar(prices)
    assert aligned.index.max() == session
    assert not (aligned.index.dayofweek >= 5).any()
    gate = calculation_asof(session)
    signal = cloud_producer.compute_latest_weights(prices, as_of=gate, spec=SPEC)
    now = datetime.now(timezone.utc)
    manifest, latest, _ = cloud_producer.build_artifacts(signal, config=build_config(),
        source_hash=hashlib.sha256(pd.util.hash_pandas_object(prices, index=True).values.tobytes()).hexdigest(),
        run_id=f'dashboard_{now:%Y%m%dT%H%M%SZ}', generated_at_utc=now, spec=SPEC)
    manifest.update({'calculation_asof':str(gate.date()), 'live_authority':False,
                     'membership_asof':SETTINGS['membership_asof']})
    variants = {}
    history = {}
    frames = {}
    for name, btc in [('with_btc', True), ('no_btc', False)]:
        schedule, meta = SPEC.schedule_builder(aligned, include_btc=btc, as_of=gate)
        stock_universe = list(set(aligned.columns) - set(ALL_TICKERS))
        stop = pit.build_stock_stoploss_multipliers(aligned, schedule, stock_universe,
                                                  stoploss_pct=0.25)
        result = backtest.run_backtest(prices, include_btc=btc, btc_pit=btc,
                    weights_schedule=schedule, rebalance_meta=meta, extra_daily_mult=stop)
        effective = result['effective_weights']
        # Charge first entry too; benchmark likewise pays initial entry only.
        turnover = effective.diff().abs().sum(axis=1)
        turnover.iloc[0] = effective.iloc[0].abs().sum()
        cost = SETTINGS['cost_bps_per_side']/10000
        gross = result['port_return']
        net = gross-turnover*cost
        spy = result['spy_return'].copy()
        spy.iloc[0] -= cost
        frame = pd.DataFrame({'dual_net':net,'dual_gross':gross,'spy':spy,
                              'dual_2x_cost':gross-turnover*cost*2})
        # Prepend a common starting NAV of 1, before the first return.
        curves = (1+frame).cumprod()
        records = [{'date': str(d.date()), **{c:round(float(v),8) for c,v in row.items()}}
                   for d,row in curves.iterrows()]
        first_base = aligned.index[aligned.index < frame.index[0]][-1]
        records.insert(0, {'date':str(first_base.date()), **{c:1 for c in frame.columns}})
        windows = {}
        for label, years in [('ALL',None),('10Y',10),('5Y',5),('3Y',3),('1Y',1)]:
            sub = frame if years is None else frame.loc[frame.index > frame.index.max()-pd.DateOffset(years=years)]
            windows[label] = {c:stats(sub[c]) for c in frame.columns}
        monthly_returns = ((1+frame).groupby(frame.index.to_period('M')).prod()-1)
        monthly = []
        valid_schedule = schedule.loc[schedule.index >= frame.index[0]]
        for d,row in valid_schedule.iterrows():
            held = {t:round(float(v),6) for t,v in row.items() if v > 1e-9}
            period = d.to_period('M') + 1
            observed = monthly_returns.loc[period].to_dict() if period in monthly_returns.index else None
            monthly.append({'month':str(period), 'decision_date':str(d.date()),
                'weights':held, 'cash':round(float(1-row.sum()),6),
                'stock_picks':str(meta.loc[d,'stock_picks']),
                'etf_picks':str(meta.loc[d,'etf_picks']),
                'returns':observed,
            })
        variants[name] = {'curves':records, 'metrics':windows, 'months':monthly,
                          'latest': latest['research_context']['with_btc'] if btc else latest}
        history[name] = monthly
        frames[name] = frame
        frame.to_csv(ROOT / 'docs/data' / f'returns_{name}.csv', index_label='date')
    payload = {'generated_at_utc':now.isoformat(), 'data_asof':str(session.date()),
        'manifest':manifest,'cost_bps_per_side':SETTINGS['cost_bps_per_side'],
        'currency':'USD','variants':variants,
        'methodology':[
            'Frozen Dual rules; completed NYSE sessions; signal close D, weights lagged to D+1.',
            'Historical research simulation, not real account performance; all historical periods are seen.',
            'Estimated costs apply 10.7 bps per side to changes in model weights; 2x scenario also shown. Actual fees, daily constant-weight rebalancing, FX, futures roll and tracking are not fully modeled.',
            'SPY uses the same USD dates and adjusted returns; initial entry charged; cash earns zero outside BIL.',
            'GC=F is a gold futures research proxy, not GLD or a verified tradable position.',
            'Inherited data includes forward-filled and incomplete historical/delisted coverage. Current membership snapshot is pinned; update intervals prospectively when membership changes.',
            'Monthly tables show pre-overlay targets. The latest card shows current effective weights and cash.',
            'Yahoo recent adjusted return ratios are chained to inherited levels; no repeated full-history download.'
        ]}
    atomic_json(ROOT/'docs/data/dashboard.json', payload)
    atomic_json(ROOT/'docs/data/latest_manifest.json', manifest)
    atomic_json(ROOT/'docs/data/monthly_holdings.json', history)
    atomic_json(ROOT/'docs/data/health.json', {'status':'ok','expected_session':str(session.date()),
                'fresh_symbols':len(symbols),'expected_symbols':len(symbols),'missing':[],
                'checked_at_utc':now.isoformat()})
    print(json.dumps({'data_asof':str(session.date()),'decision':manifest['month_end_decision_date'],
                      'months':{k:len(v) for k,v in history.items()},'status':'ok'}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['refresh','build'])
    args = parser.parse_args()
    if args.command == 'refresh':
        refresh()
    else:
        (ROOT/'docs/data').mkdir(parents=True,exist_ok=True)
        build()
