# Pre-Flight Checklist — Multi-Model Ensemble Weight Decay

Sign off before an ensemble weight vector from this engine sizes a live position.

## Parameters

- [ ] $\lambda$ chosen from a target **half-life** ($\ln 0.5 / \ln \lambda$), not by feel, and the half-life matches the horizon on which model skill actually changes — not how fast you want to react.
- [ ] $\lambda \in [0, 1]$. Outside that range the recursion is not a convex combination and diverges.
- [ ] $\beta$ finite and **strictly positive**. A non-positive $\beta$ inverts the softmax and awards the largest weight to the worst model, silently.
- [ ] `min_weight_floor` **strictly below $1/M$** for the current roster size. Re-checked whenever a model is added or removed.
- [ ] `weighting_method` is exactly `EXPONENTIAL_LOSS` or `IC_SOFTMAX` (case-sensitive).
- [ ] If `min_days_active > 1`, the threshold is a deliberate judgement about your telemetry, and `days_active` genuinely reflects live history.

## Inputs

- [ ] `recent_loss` and `recent_ic` are measured **out-of-sample**, on the same forward window the weights will govern.
- [ ] Every `model_id` is unique. Duplicates are rejected, not merged.
- [ ] No NaN or ±Inf in any of the four numeric telemetry fields; `recent_loss` non-negative.
- [ ] The roster contains no near-duplicate models — this engine has no notion of correlation and will award $k$ copies $k$ times the weight.

## Decay state

- [ ] `previous_decayed_loss` and `previous_decayed_ic` are persisted between periods and fed back. **Skipping this silently degrades the skill to a memoryless per-period softmax, with no error.**
- [ ] The first call is understood to be a plain softmax: seeded from the current reading, $\bar{X} = X$ and $\lambda$ has no effect.

## Circuit breakers

- [ ] The negative-IC breaker reads the **discounted** IC, never a single period's ($\mathrm{SE}(IC) \approx 1/\sqrt{N-1}$ makes one period's sign near-coin-flip).
- [ ] The IC breaker is active under `EXPONENTIAL_LOSS` too — low MSE and inverted sign coexist, and loss weighting cannot see it.
- [ ] It is understood that softmax alone **never** demotes: $\exp(-\beta \bar{L}) > 0$ always, so without a floor and an IC breaker the worst model keeps a live allocation forever.

## Halt path

- [ ] `ENSEMBLE_HALTED_ALL_DEMOTED` is handled explicitly by the caller — flatten, fall back to a non-ensemble source, or halt. It is **not** treated as a success with zero weights.
- [ ] No equal-weight fallback has been reintroduced anywhere downstream. Spreading capital evenly across models the breakers just rejected is the failure this design exists to prevent.
- [ ] The ERROR-level halt log is routed somewhere a human will see it.

## Output invariants (assert in integration tests)

- [ ] `math.fsum` of reported weights is exactly $1.0$ (a naive `sum()` may sit an ULP or two away — that is caller-side IEEE-754 accumulation).
- [ ] Every active weight $\ge$ `min_weight_floor`; every non-active weight exactly $0.0$.
- [ ] `active_model_count + demoted_model_count + pending_warmup_model_count == len(models)`, with warm-up counted apart from demotion.
- [ ] No finite input produces a non-finite weight.
- [ ] Two identical calls return identical reports.

## Operations

- [ ] The new `decayed_metric` / `decayed_ic` are written back for the next period.
- [ ] Turnover implied by the chosen $\lambda$ and $\beta$ has been costed — this engine models no transaction cost.
- [ ] `audit_notes`, `weighting_method`, and `decay_half_life_periods` are retained so a reviewer can reconstruct any historical allocation.
