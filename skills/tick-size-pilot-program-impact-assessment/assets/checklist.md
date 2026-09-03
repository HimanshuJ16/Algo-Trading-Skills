# Tick Size Regime Impact Assessment — Sign-off Checklist

## Data hygiene
- [ ] **L1 quote and trade capture**: sub-second quote and trade messages ingested for both the baseline and test windows.
- [ ] **Aggressor tagging**: trades signed from exchange execution records where available; where a signing rule (Lee-Ready, tick test) was used instead, its error rate is recorded — signing error propagates straight into effective and realized spread.
- [ ] **Trade size present on every trade**: required for Rule 605 share weighting. If any trade lacks a size, `TickMetrics.weighting` reads `EQUAL_WEIGHTED` and the result is not comparable to a Rule 605 figure.
- [ ] **5-minute midpoints resolved**: the end-of-session proviso (17 CFR 242.600(b)(13)) applied — the session's final NBBO midpoint used where fewer than 5 minutes of regular trading hours remain; `None` passed where the horizon is genuinely unobservable.
- [ ] **Auction periods excluded**: opening and closing single-priced auctions removed from the continuous-session sample.
- [ ] **Control group captured**: comparable securities whose tick did not change, over the identical window, through the identical pipeline.

## Measurement
- [ ] **Exclusion count reviewed**: `excluded_snapshot_count` inspected on both regimes. A material exclusion rate is a data problem to fix, not a market finding to report.
- [ ] **Weighting matched**: baseline and test both `SHARE_WEIGHTED`, or the mismatch is explicitly disclosed in the write-up.
- [ ] **Quoted spread** computed across continuous trading hours.
- [ ] **Effective spread** computed and reported separately from quoted spread — they diverge, and the divergence is the finding.
- [ ] **Realized spread and adverse selection** computed; `realized_sample_count` compared against `trade_sample_count` to see how much of the sample lacked an observable horizon.
- [ ] **Benchmark convention stated**: this engine measures effective spread against the trade-time quote midpoint, not the Rule 605 order-receipt midpoint. Any comparison to a published Rule 605 report notes the difference.
- [ ] **Additional horizons considered**: amended Rule 605 requires 50 ms, 1 s, 15 s, 1 min and 5 min. Conclusions that hold only at 5 minutes are labelled as such.
- [ ] **L1 depth** computed at the NBBO, in both shares and dollar terms — share-denominated depth is inflated by low-priced, high-volume names.
- [ ] **OTR and fill rate** computed from their own denominators: `messages / fills` and `executed shares / ordered shares` respectively. Neither is reported under the other's name.

## Interpretation
- [ ] **`undefined_metrics` read**: no `None` result has been silently rendered as zero or as "no change".
- [ ] **Difference-in-differences applied** against the control group, not a bare pre/post delta.
- [ ] **Results stratified** by pre-change spread class, share price and volume. A single pooled number hides effects that ranged from −17% to +203% in the Pilot.
- [ ] **Significance tested** before any parameter is moved.
- [ ] **No effect size imported** from the Pilot or any other study as a coefficient.
- [ ] **Units checked**: `fill_rate_change_pp` is percentage points; the other change fields are percentages.

## Recalibration and deployment
- [ ] **Thresholds reviewed**: the module-level screening constants (`SPREAD_FINDING_THRESHOLD_PCT`, `DEPTH_FINDING_THRESHOLD_PCT`, `ADVERSE_SELECTION_FINDING_BPS`, `MARKET_MAKING_ADVERSE_SELECTION_BPS`, `SLICING_SPREAD_THRESHOLD_PCT`) are this engine's reporting triggers, not regulatory or empirical limits, and have been set to the desk's tolerances.
- [ ] **Recommendations treated as advisory**: no routing change made on `recommend_strategy_tuning` output alone, without backtest or human review.
- [ ] **Passive queue parameters** re-derived from measured queue position and fill probability, not from the tick ratio.
- [ ] **Price caps** derived from the measured effective spread, not from the nominal tick change.
- [ ] **Backtest cost model updated** with post-change spread and depth parameters, and the change dated so historical backtests keep the regime that was actually in force.
- [ ] **Regime status verified**: for US work, the amended Rule 612 $0.005 tier is deferred to the first business day of November 2027 and is a per-symbol listing-exchange assignment — confirm what is actually in force for the sample window.
