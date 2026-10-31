# Dual / Momentum Observatory

Public research dashboard for the frozen `dual_momentum_capped_sl` strategy. No brokerage credentials, holdings, personal balances or order submission.

- **Website:** https://pong0401.github.io/dual-momentum-dashboard/
- **Daily job:** Actions → Update Yahoo data and publish dashboard → Run workflow.
- **Schedule:** 22:23 UTC Mon–Fri / 05:23 Bangkok Tue–Sat, after summer and winter US closes. GitHub cron is best effort and can be delayed.
- **Views:** latest effective weights; monthly target allocations for the last year and all older years; USD growth versus SPY over the entire available history; gross, estimated net and 2x-cost scenarios.

## Local commands

```sh
python -m pip install -r requirements.txt
python scripts/pipeline.py refresh
python scripts/pipeline.py build
python -m pytest -q
python -m http.server 8765 --directory docs
```

## Data accumulation

The initial seed is the local 1993–2026 price cache (790 columns) with verified 2026-10-30 recent closes. `data/prices/YYYY-MM.parquet` stores one month per file. Daily refresh checks every current input symbol and only requests missing sessions plus a 14-day overlap. Never calls `period=max`. Old inactive symbols remain in historical partitions and are not repeatedly fetched.

Each successful symbol download is checkpointed under ignored `.refresh/<session>/`. Actions caches these checkpoints across interrupted attempts and commits accumulated price partitions even after a partial failure. Successful builds update `docs/data/`; failed input gates preserve the last valid dashboard and publish a visible health warning. Retry manually from Actions after a Yahoo outage.

Yahoo auto-adjusted return ratios are chained to the existing stored series at an overlap anchor. This handles new dividends/splits without repeatedly downloading full histories or mixing changed nominal price scales. Stored levels are research indexes, not current execution quotes. Historical seed adjustedness and vendor corrections remain inherited limitations; this does not claim fresh revalidation of every historical observation.

Only portfolio growth curves (Dual and SPY), aggregate return statistics, monthly weights and health/provenance metadata are published under `docs/`. Individual-stock price histories stay in the computation dataset and are not served by the website. The Yahoo symbol universe exists only to rank candidates and calculate each weight update; the web chart does not retain or render individual-stock price charts.

## Strategy and timing

The unmodified upstream engine is in `vendor/`, with SHA-256 provenance in `data/seed_manifest.json`. Monthly schedule: capped stock momentum, top 2 rank-weighted (25% sleeve), growth/factor top 5 (up to 30%), 25% stock stop-loss, SPY gate, dynamic BIL, one country pick funded from residual SPY. Daily SPY/gold/BTC overlays apply with a one-session lag. Zero funding may leave a selected country outside actual targets.

The engine normally waits for a calendar-month change. The reporting adapter permits the next-month boundary only when the final official NYSE session has completed. It uses `pandas_market_calendars` NYSE schedules as the documented research calendar fallback and waits 45 minutes after market close for daily data. No future price rows are used. Latest metadata records the calculation-as-of and real generation time separately.

The inherited engine uses constant model weights within each session; its transaction-cost estimate charges changes in those model weights at 10.7 bps per side, including initial entry. It is not a complete cash/position drift, daily rebalance, futures-roll, tax, FX or executable-fill simulation. Benchmark SPY uses identical dates and adjusted returns, with initial entry cost. Cash earns zero outside BIL. All historical periods are seen; these are research charts, not actual account returns or an unseen test. Full-history performance starts in 2007 when BIL/base inputs exist, while the longer seed supports lookbacks.

## Membership maintenance

`vendor/data/cache/pit_sp500_membership_intervals.csv` is a point-in-time interval snapshot dated in `config/settings.json`. Current daily prices do **not** automatically make membership history current. Changes require an explicit reviewed append/update of intervals, a new snapshot-as-of and hash in `data/seed_manifest.json`, and seeded price history for new symbols before they become eligible. Never replace historical membership with today's constituent list. The dashboard displays this limitation and the snapshot date.

## Provenance and reproducibility

`docs/data/dashboard.json`, `monthly_holdings.json`, `latest_manifest.json`, `health.json`, and daily return CSVs are generated, never manually edited. The repository and web page expose only strategy research data. Bootstrap provenance is local filenames/hashes; no cloud credentials or private broker state is copied. See [PLAN.md](PLAN.md) and [research/VALIDATION.md](research/VALIDATION.md).
