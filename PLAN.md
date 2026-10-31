# Delivery plan — Dual Momentum dashboard

1. Bootstrap this separate repository from verified local historical caches. Copy the frozen strategy with file hashes; do not connect a brokerage or copy account data.
2. Store historical prices as immutable-by-default calendar-month Parquet partitions. Download only each symbol's missing recent interval plus 14 calendar days of overlap; retain checkpoints per symbol and persist partial progress.
3. Chain Yahoo adjusted return ratios to existing research price levels. Preserve old history without re-fetching or rewriting it for each dividend. These levels are research indexes, not market quotes.
4. Reproduce the frozen monthly weights and lag-1 daily overlays. Export both with-BTC and no-BTC variants, full-history comparable USD return curves against SPY, and every monthly allocation.
5. Render a static GitHub Pages dashboard: latest effective allocation; trailing 12-month holdings; year selector; month detail; full-history/1/3/5/10-year chart; log scale; gross/net/2x-cost controls; downloadable evidence.
6. Verify month-end timing, completed exchange sessions including DST, one-session signal lag, adjusted-return continuity, frozen-code hashes and weights/cash reconciliation. Historical replay is reporting continuation, not a new experiment or promotion.
7. Publish to a new public GitHub repository and enable GitHub Pages. Run GitHub Actions after US close at 22:23 UTC Mon–Fri (05:23 Bangkok Tue–Sat), with manual retry. Register this distinct owner-specific schedule in schedule_manager; do not replace existing portfolio jobs.

Known boundaries: inherited historical/delisted price coverage and adjustedness are not fully revalidated; no new unseen test; costs are model estimates; GC=F is a research proxy. Constituent intervals are seeded point-in-time history and must be maintained prospectively through reviewed updates. Current symbol freshness is a separate gate from membership coverage.
