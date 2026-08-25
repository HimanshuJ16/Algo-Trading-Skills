# Workflows for Execution Algo Parameter Optimization

## 0. Assemble the sample

1. Pull historical **parent** orders, not child fills: quantity, side, arrival
   (decision) price, and the observed intraday market price path over the execution
   horizon.
2. Attach per-interval market volume. A uniform split of ADV is the fallback, and it
   understates the capacity genuinely available at the open and the close — supply a
   real volume curve wherever the schedule's behaviour near the auctions matters.
3. Set `execution_horizon_days` to the fraction of a trading day the path actually
   spans. It is the $T$ in the participation rate $X/(V \cdot T)$; leaving a one-hour
   path at the 1.0 default makes every candidate look cheaper and more fillable.
4. Attach `shares_outstanding` if permanent impact is to be modelled at all. Without
   it the ATHL permanent term is skipped and total cost is understated; the report
   raises `PERMANENT IMPACT OMITTED` rather than letting that pass silently.
5. **Split before you search.** Reserve a holdout set that takes no part in
   selection — chronologically, so the holdout is genuinely out of sample rather than
   interleaved. See `walk-forward-optimization-window-management`.

## 1. Calibrate the impact model

The ATHL (2005) defaults are a starting point, not a result: fitted to one broker's US
large-cap flow in 2001-2003, with an $R^2$ under one percent. Before an optimum is
actionable, refit `ImpactModelCoefficients` against your own realized TCA — see
`execution-cost-model-recalibration-cadence`. Record which calibration produced which
optimum; an optimum is only meaningful relative to the cost model that generated it.

## 2. Define the grid

1. Candidate ranges for $\alpha_{\max}$, $\lambda$, and peg offset in ticks.
2. Set `max_allowed_participation_rate` from written policy before searching, not
   after seeing which candidate won. Candidates above it are excluded from selection
   and recorded in `rejected_configs`; a grid entirely above it raises.
3. Keep the grid coarse enough that the candidates are genuinely distinguishable given
   the sample size. A fine grid over a noisy objective manufactures spurious winners —
   see `backtest-parameter-sensitivity-analysis`.

## 3. Replay

For each candidate, for each order:

1. Compute $\kappa T$ from $\lambda$ (AC Eq. 19) and take the target inventory
   trajectory from AC Eq. (17). $\kappa \to 0$ is the risk-neutral linear/TWAP limit.
2. Walk the intervals. The interval's target slice is capped at
   $\alpha_{\max} \times$ observed interval volume; any shortfall against the target
   rolls forward to the next interval.
3. Price each slice off the observed market price, displaced by permanent impact
   accumulated from prior fills (valued at the slice midpoint), plus ATHL temporary
   impact at that slice's participation rate, plus the peg concession
   $\text{peg\_ticks} \times \text{tick\_size}$ signed against the side.
4. Accumulate filled quantity and notional; the achieved VWAP falls out.

## 4. Shortfall and fill audit

1. Execution cost on the filled portion: $s (P_{\text{VWAP}} - P_{\text{arr}}) / P_{\text{arr}} \times 10^4$.
2. Opportunity cost on the unfilled portion: $s (P_{\text{final}} - P_{\text{arr}}) / P_{\text{arr}} \times 10^4$.
3. Combine by fill fraction (Perold 1988). Charging only the filled part rewards a
   schedule that quietly gives up on hard orders.
4. Across the sample, record mean, sample standard deviation, **standard error of the
   mean**, worst case, mean fill and minimum fill.

## 5. Select — and then test whether the selection means anything

1. Score: $\overline{\text{IS}} + \gamma_{\text{vol}} \sigma_{\text{IS}} + (1 - \bar{f}) w_{\text{fill}}$. Lowest wins; ties go to the earliest candidate in grid order.
2. **Compare the winner's margin to the combined standard error of the two leading
   candidates' mean shortfalls.** With $\sigma_{\text{IS}} \approx 45$ bps and 40
   orders the standard error is roughly 7 bps, so a margin of 0.01 is noise. When
   `selection_is_separated` is false the correct conclusion is "these configurations
   are equivalent on this evidence", and the remedies are more samples or a coarser
   grid — not a tie-break on the reported ordering.
3. Re-score the winner on the holdout. Read `holdout_is_degradation_bps`; material
   degradation means the selection fitted the in-sample period.
4. Re-run across distinct volatility regimes before promotion — see
   `multi-year-regime-coverage-requirement`.

## 6. Audit trail

Persist the whole `AlgoOptimizationAuditReport`, not just the winner: every
candidate's score, the rejected configurations and the reason each was excluded, the
impact coefficients in force, the holdout comparison, and every warning raised. A
parameter set that reaches production should be traceable to the run, the sample, and
the calibration that produced it — see `backtest-audit-trail-for-regulatory-review`.

## 7. Promotion

Optimizer output is a candidate, not a deployment decision. Route it through
`new-strategy-onboarding-checklist` and `paper-to-live-promotion-checklist`, and
confirm the live algorithm's kill switch and participation guards are independent of
anything this search produced — see `execution-algorithm-kill-switch-integration`.
