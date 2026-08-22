# Pre-Flight Checklist

## Ordering

- [ ] Is a **future-dated** feature timestamp caught before the staleness test? A negative age passes `age > limit` unchallenged.
- [ ] Is feature staleness resolved **before** any distribution statistic? A frozen feed replays the reference distribution and scores as pristine.
- [ ] Is retraining blocked when the verdict is `DATA_STALENESS`?

## Feature shift, $P(X)$

- [ ] Are the outer PSI bin edges **unbounded** ($-\infty$/$+\infty$)? `numpy.histogram` silently discards values outside the supplied edges — the very observations that signal a feature has left its historical support.
- [ ] Do quantile bin edges get checked for **collapse**? A 95/5 indicator over 10 bins de-duplicates to a single bin and scores PSI 0.0 no matter what the current sample does.
- [ ] Is the trigger the **per-feature maximum**, not the mean across the universe?
- [ ] Does the alert **name** the shifted features? "Covariate shift detected" is unactionable otherwise.
- [ ] Are constant reference features reported as undefined rather than scored 0.0?
- [ ] Is the PSI threshold calibrated to the sample size (chi-square benchmark), or has the fixed 0.25 band been consciously accepted knowing its power *falls* as samples grow?

## Concept drift, $P(Y \mid X)$

- [ ] Is the residual error ratio $\text{MSE}_{curr}/\text{MSE}_{ref}$ computed from **out-of-sample** reference residuals?
- [ ] Is $\text{MSE}_{ref} = 0$ handled as undefined rather than divided by a fudge constant?
- [ ] Has `error_ratio_threshold` been calibrated against this strategy's own historical rolling-MSE distribution, rather than left at the 1.50 default?

## Failure modes that read as healthy

- [ ] Does NaN/Inf input return its own status? Comparisons against `NaN` are `False`, so a naive threshold ladder reports `STABLE` on poisoned data.
- [ ] Does an undersized window return its own status rather than a number nobody should trust?
- [ ] Are unmeasured result fields `None` rather than `0.0`/`1.0`? A short-circuited diagnosis must not put "no drift observed" on a dashboard.
- [ ] Is `INSUFFICIENT_DATA` alerted on, and understood as *unverified* rather than *healthy*?

## Scope

- [ ] Is remediation segregated — pipeline fix for staleness, refit for covariate shift, re-specification or retirement for concept drift?
- [ ] Is it understood that when both signals breach, the verdict is `CONCEPT_DRIFT` but the true cause may be extrapolation under a large $P(X)$ move, and needs human adjudication?
- [ ] Is prior probability shift ($P(Y)$ moving) recognised as a fourth regime this diagnosis does not separate?
