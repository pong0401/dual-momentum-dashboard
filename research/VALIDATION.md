# Reporting replay validation — 2026-10-31

Scope: reporting reproduction of the existing frozen Dual baseline. No new hypothesis, parameter selection, alternate timing experiment, or unseen-period claim. Prior index audit found the alternate-monthly-timing family CLOSED; it is not rerun here.

| Gate | Status | Evidence / limitation |
|---|---|---|
| A. Data validity | WARNING | Current expected inputs must have the latest completed close, positive finite Yahoo data, unique dates/columns and checkpoint provenance. Initial seed is inherited and historical delistings/forward fills are not revalidated. Membership snapshot remains pinned. |
| B. Temporal integrity | PASS for reporting timing | Completed NYSE sessions with 45-minute publication allowance; final-session month boundary only after close; monthly and daily overlays lag one session; tests include DST and unfinished sessions. |
| C. Execution realism | WARNING | 10.7 bps/side model-weight changes, first entry and 2x scenario. No verified historical quotes/fills, full drift-based accounting, futures rolls, FX or taxes. |
| D. Statistical robustness | WARNING | Full/10/5/3/1-year windows on identical strategy/benchmark dates. All history seen. No new OOS claim, uncertainty interval or promotion. |
| E. Concentration | WARNING | Monthly complete weights/cash exported for inspection. Frozen top-2 stock strategy can be concentrated; no new cap or diversification claim. |
| F. Regime stability | WARNING | Full historical curve and monthly archive available; no independent regime robustness conclusion is inferred from these charts. |

Final validation status: **PROVISIONAL**. Displayed figures are research simulations with explicitly approximate costs.

Reviewer disposition: **REFINE** the evidence quality before any future promotion. The inherited strategy is not reselected or rejected by this reporting job. Hypothesis status remains unresolved here because this task tests publication/reproduction, not alpha. Further promotion evidence belongs to the existing portfolio task; no extra parameter experiments or duplicate handoff are created.

Required evidence improvements for any future live decision: reconcile inherited adjustment/delisting coverage, maintain prospective constituent intervals, and verify executable instrument costs/position accounting against actual fills. Until then, dashboard success authorizes no order or allocation increase.
