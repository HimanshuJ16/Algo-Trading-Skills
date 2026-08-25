# Pre-Flight / Sign-off Checklist — feature-engineering-without-leakage

Use this before considering the skill's implementation complete.

**This checklist certifies that the leakage screens were run and returned nothing, not
that the feature set is leakage-free.** See "Audit Limitations" in
`references/standards.md`. Sign it on that basis.

## Specification (do this before any screen)

- [ ] **Target Definition Recorded:** the target's realization timestamp is written down, separately from the feature-computation cutoff.
- [ ] **Knowability Timestamps Stated:** every feature has a stated timestamp at which it becomes observable, and it precedes the cutoff (the no-time-machine requirement).
- [ ] **Publication Timestamps Present:** every non-price source carries a publication/availability time, not just an effective date.

## Screens

- [ ] **Detector Calibrated First:** `run_intentional_leakage_calibration()` returned a **non-zero** strength. A `0.0` return means the injected known leak was missed and no clean verdict below can be trusted.
- [ ] **Structural Causality Audit:** `audit_feature_causality()` returned zero `UNSHIFTED_ROLLING` findings for the feature-construction function, with `cut_fractions` widened if the raw data has irregular gaps.
- [ ] **Row Ordering Verified:** `audit_dataframe()` was called with `timestamp_col=` (not relying on assumed row order) and did not raise.
- [ ] **Association Audit:** `audit_dataframe()` returned zero `SAME_BAR_CONTAMINATION` and zero `FUTURE_LOOKAHEAD` findings.
- [ ] **UNDETERMINED Findings Reviewed:** every `UNDETERMINED` finding was individually resolved. These columns were **not screened** — absence of a leakage finding for them means nothing.
- [ ] **Point-In-Time Merge Check:** multi-frequency data was joined with `point_in_time_asof_merge()`; any use of `allow_exact_matches=True` is justified in writing by dissemination-latency-adjusted receipt timestamps.
- [ ] **Upstream Joins Verified:** data joined outside this pipeline passed `verify_asof_timing()` with zero `ASOF_TIMING_VIOLATION` findings.
- [ ] **Shift Direction Audit:** feature shifts use positive periods and label shifts negative, enforced via `verify_shift_direction()`.

## Residual Risk Acknowledgement

- [ ] **Restated History Considered:** confirmed no feature derives from a vendor field revised after its nominal timestamp (adjusted close, backfilled fundamentals) — the screens cannot detect this.
- [ ] **Out-of-Scope Leakage Delegated:** train/test contamination is covered by `walk-forward-validation-setup` and `hyperparameter-tuning-without-target-leakage`, not by this audit.
- [ ] **Out-of-Sample Gap Inspected:** walk-forward performance is not implausibly close to in-sample, and neither is implausibly good for the instrument and horizon.

## Automated Testing

- [ ] **Unit Tests:** `python -m unittest discover -s skills/feature-engineering-without-leakage/scripts` passes 100%.
- [ ] **Repository Validation:** `python tools/validate_skills.py` passes.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
- Calibration strength returned: ___________________________
