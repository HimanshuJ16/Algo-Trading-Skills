# Workflows for Multi-Model Ensemble Weight Decay

The engine is stateless. One call consumes the carried decay state, produces a
weight vector, and returns the new decay state for the caller to persist.

```
carried state ──► 1. validate ──► 2. advance both EWMAs ──► 3. stable softmax
                                                                   │
   persist ◄── 7. report ◄── 6. renormalise or halt ◄── 4/5. circuit breakers
```

## 0. Choose the parameters before the first call

`min_weight_floor` must be strictly below $1/M$ — with 30 models a 0.05 floor
exceeds the 0.0333 equal share and every model becomes demotable regardless of
performance. The engine rejects this rather than silently demoting the roster.

Choose $\lambda$ from a half-life: $t_{1/2} = \ln(0.5)/\ln(\lambda)$.

| $\lambda$ | Half-life (periods) | Effective window $1/(1-\lambda)$ |
|---|---|---|
| 0.80 | 3.11 | 5.0 |
| 0.90 | 6.58 | 10.0 |
| 0.94 | 11.20 | 16.7 |
| 0.95 | 13.51 | 20.0 |
| 0.97 | 22.76 | 33.3 |
| 0.99 | 68.97 | 100.0 |

Match the half-life to the horizon on which model skill genuinely changes. A
shorter half-life does not react faster to real decay — it reacts faster to
*noise*, and pays spread on each resulting rotation.

## 1. Validate at the boundary

Rejected with `EnsembleWeightError` (a `ValueError` subclass):

- empty roster; blank `ensemble_id`; blank or duplicate `model_id`
- NaN or ±Inf in `recent_loss`, `recent_ic`, `previous_decayed_loss`,
  `previous_decayed_ic`; non-numeric values; booleans
- negative `recent_loss` (MSE and LogLoss are non-negative by construction, so a
  negative value is a sign error upstream)
- negative `days_active`
- `min_weight_floor >= 1 / M`

Config bounds ($\lambda \in [0,1]$, $\beta > 0$ and finite, floor $\in [0,1)$,
`min_days_active` $\ge 1$, a recognised `weighting_method`) are checked when
`EnsembleConfig` is constructed, so a malformed policy fails before any
telemetry is touched.

**Duplicates are rejected, not merged.** Weights are keyed by `model_id`; a
duplicate would make the first entry inherit the second's metrics and be demoted
in its place.

## 2. Advance both EWMAs

$$\bar{L}_{m,t} = \lambda \bar{L}_{m,t-1} + (1-\lambda) L_{m,t}, \qquad
\overline{IC}_{m,t} = \lambda \overline{IC}_{m,t-1} + (1-\lambda) IC_{m,t}$$

Both are advanced on every call regardless of `weighting_method`, because the IC
circuit breaker needs a discounted IC even when weighting runs on loss.

When `previous_decayed_*` is `None` the recursion is seeded from the current
reading, giving $\bar{X} = X$ exactly. $\lambda$ has no observable effect until
the second call — correct seeding, not a broken decay factor.

## 3. Numerically stable softmax

$$s_m = \begin{cases} -\beta \bar{L}_m & \texttt{EXPONENTIAL\_LOSS} \\ +\beta \overline{IC}_m & \texttt{IC\_SOFTMAX} \end{cases}
\qquad w_m = \frac{\exp(s_m - \max_j s_j)}{\sum_k \exp(s_k - \max_j s_j)}$$

The shift is exact — it factors out of numerator and denominator — and pins the
largest term at $\exp(0) = 1$, so the denominator is always $\ge 1$.

Without it: a loss of 400 at $\beta = 2$ underflows *every* exponential to `0.0`
and the normalisation raises `ZeroDivisionError`; an IC of 400 raises
`OverflowError`. Both happen in production on precisely the bar where a model
has blown up.

These `w_m` are reported as `raw_weight` — the softmax share over the **whole**
roster, before any demotion.

## 4. Circuit breakers, most-specific first

| Order | Condition | Status |
|---|---|---|
| 1 | `days_active < min_days_active` | `PENDING_WARMUP` |
| 2 | `demote_on_negative_ic` and $\overline{IC}_m \le 0$ | `DEMOTED_NEGATIVE_IC` |
| 3 | `raw_weight < min_weight_floor` | `DEMOTED_BELOW_FLOOR` |
| — | otherwise | `ACTIVE` |

Two properties of breaker 2 matter more than the rest of this document:

- **It reads $\overline{IC}$, the discounted IC — never `recent_ic`.** A
  cross-sectional IC over $N$ names carries $\mathrm{SE} \approx 1/\sqrt{N-1}$
  under the null; at $N = 100$ that is $\approx 0.10$. The sign of a single
  period's reading is close to a coin flip for any realistic true IC, so
  triggering on it would discard skilled models about as often as broken ones.
- **It runs under `EXPONENTIAL_LOSS` as well as `IC_SOFTMAX`.** A model
  predicting small moves with an inverted sign posts a competitive MSE. Loss
  weighting is blind to the sign error and will hand it the book.

`PENDING_WARMUP` is separated from the two `DEMOTED_*` statuses deliberately:
insufficient history is not evidence of failure, and an operator reading the
audit trail should not confuse the two.

## 5. Renormalise the survivors, or halt

$$w^{\text{final}}_m = \frac{w_m}{\sum_{j \in \text{active}} w_j}$$

One pass is sufficient. The denominator is $\le 1$, so every surviving weight
can only increase; nothing that cleared the floor can fall below it afterwards.
Iterating the breakers would change nothing.

Weights are rounded to `WEIGHT_PRECISION` (6 dp) and the rounding residual is
applied to the largest active weight, where it is proportionally smallest. The
result sums to exactly $1.0$ under `math.fsum`.

**If no model is eligible**, the engine does **not** fall back to equal weights.
Doing so would re-admit every model the breakers just rejected — including
anti-predictive ones — under a success status, which is the single most
dangerous behaviour this module can exhibit. Instead:

- `status = ENSEMBLE_HALTED_ALL_DEMOTED`
- `active_model_count = 0`, every `final_normalized_weight = 0.0`
- logged at ERROR with a per-model reason breakdown

The halt is reached either because every model failed a breaker or because every
model is still warming up. `demoted_model_count` and `pending_warmup_model_count`
say which — a brand-new ensemble halting on warm-up reports
`demoted_model_count = 0`, and must not be read as a roster of broken models.

The caller decides what happens next — flatten, fall back to a non-ensemble
signal source, or halt trading. That decision is not the engine's to make
silently.

## 6. Persist the new decay state

Write each status's `decayed_metric` and `decayed_ic` back as next period's
`previous_decayed_loss` / `previous_decayed_ic`. **If this step is skipped the
skill silently degrades to a per-period softmax with no memory at all** — no
error is raised, and the weights will simply look noisier than expected.

## 7. Audit trail

`EnsembleWeightReport` carries `weighting_method` and
`decay_half_life_periods` alongside the per-model statuses so a reviewer can
reconstruct why a given weight was assigned without needing the config object.
`audit_notes` restates $\lambda$, its half-life, $\beta$, the floor, and the
active/withheld counts.
