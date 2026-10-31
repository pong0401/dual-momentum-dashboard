from pathlib import Path
import hashlib
import importlib.util
import json

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pipeline', ROOT/'scripts/pipeline.py')
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


def test_adjusted_chain_preserves_old_history_and_handles_split():
    old = pd.Series([90.,100.,102.],index=pd.to_datetime(['2026-10-26','2026-10-27','2026-10-28']))
    fresh = pd.Series([50.,51.,52.5],index=pd.to_datetime(['2026-10-27','2026-10-28','2026-10-29']))
    result = pipeline.merge_adjusted(old,fresh)
    assert result.loc['2026-10-26'] == 90
    assert result.loc['2026-10-29'] == 105
    assert result.loc['2026-10-28'] == 102


def test_new_ticker_requires_explicit_seed_and_bad_prices_rejected():
    old = pd.Series([100.],index=pd.to_datetime(['2026-10-26']))
    with pytest.raises(ValueError,match='overlap'):
        pipeline.merge_adjusted(old,pd.Series([101.],index=pd.to_datetime(['2026-10-29'])))
    with pytest.raises(ValueError,match='nonpositive'):
        pipeline.merge_adjusted(old,pd.Series([0.],index=old.index))


def test_month_end_only_after_final_exchange_session():
    assert pipeline.calculation_asof('2026-10-30','2026-10-31T07:00Z') == pd.Timestamp('2026-11-01')
    assert pipeline.calculation_asof('2026-10-29','2026-10-30T07:00Z') == pd.Timestamp('2026-10-30')


def test_unfinished_close_and_dst_are_excluded():
    assert pipeline.last_completed_session('2026-10-30T19:00Z') == pd.Timestamp('2026-10-29')
    assert pipeline.last_completed_session('2026-10-30T20:46Z') == pd.Timestamp('2026-10-30')
    assert pipeline.last_completed_session('2026-11-02T20:46Z') == pd.Timestamp('2026-10-30')
    assert pipeline.last_completed_session('2026-11-02T21:46Z') == pd.Timestamp('2026-11-02')


def test_unchanged_month_partition_is_not_rewritten(tmp_path,monkeypatch):
    monkeypatch.setattr(pipeline,'ROOT',tmp_path)
    (tmp_path/'data/prices').mkdir(parents=True)
    frame=pd.DataFrame({'SPY':[100.,101.]},index=pd.to_datetime(['2026-09-30','2026-10-01']))
    pipeline.save_prices(frame)
    old=(tmp_path/'data/prices/2026-09.parquet').stat().st_mtime_ns
    frame.loc[pd.Timestamp('2026-10-02')]=102
    pipeline.save_prices(frame)
    assert (tmp_path/'data/prices/2026-09.parquet').stat().st_mtime_ns == old


def test_frozen_source_files_match_seed_hashes():
    seed=json.loads((ROOT/'data/seed_manifest.json').read_text())
    for name,expected in seed['files'].items():
        assert hashlib.sha256((ROOT/'vendor'/name).read_bytes()).hexdigest()==expected,name


def test_monthly_signal_takes_effect_next_session():
    from country_etf_tactical import backtest
    dates=pd.to_datetime(['2020-01-30','2020-01-31','2020-02-03','2020-02-04'])
    prices=pd.DataFrame({'SPY':[100.]*4,'GC=F':[100.]*4,'BIL':[100.]*4,'BTC-USD':[100.]*4,'TEST':[100.,110.,121.,121.]},index=dates)
    schedule=pd.DataFrame({'SPY':[0.],'GC=F':[0.],'BIL':[0.],'BTC-USD':[0.],'TEST':[1.]},index=pd.to_datetime(['2020-01-31']))
    result=backtest.run_backtest(prices,weights_schedule=schedule)
    # The inherited engine represents the first lagged target as NaN (flat).
    assert result['effective_weights'].fillna(0).loc['2020-01-31','TEST']==0
    assert result['effective_weights'].loc['2020-02-03','TEST']==1
    assert result['port_return'].loc['2020-01-31']==0
    assert result['port_return'].loc['2020-02-03']==pytest.approx(.1)


def test_published_comparisons_have_identical_dates_and_start_value():
    path=ROOT/'docs/data/dashboard.json'
    if not path.exists():
        pytest.skip('Run build first for artifact checks')
    data=json.loads(path.read_text())
    for variant in data['variants'].values():
        assert len(variant['months'])>=12
        assert all(variant['curves'][0][s]==1 for s in ['dual_net','dual_gross','spy','dual_2x_cost'])
        v=variant['latest']
        assert sum(v['effective_weights_next_session'].values())+v['cash_weight_next_session']==pytest.approx(1,abs=2e-6)
        for row in variant['months']:
            assert sum(row['weights'].values())+row['cash']==pytest.approx(1,abs=5e-6)
        assert variant['metrics']['ALL']['dual_gross']['total_return'] >= variant['metrics']['ALL']['dual_net']['total_return']
        assert variant['metrics']['ALL']['dual_net']['total_return'] >= variant['metrics']['ALL']['dual_2x_cost']['total_return']
