# Workflows — explainable-boosting-machines-for-regulated-signals

Deep procedure for composing a fitted EBM / GA²M and producing its governance record.
The engine is `scripts/explainable_boosting_pricer.py`; it scores, it does not fit.

## 0. Establish the score scale before anything else

The intercept and every term contribution live on the **link** scale.

| EBM type | `score_scale` | What the score means | Converting |
|---|---|---|---|
| Regression | `ScoreScale.IDENTITY` | Target units | Nothing to convert |
| Classification | `ScoreScale.LOGIT` | Log-odds | `logit_score_to_probability(score)` |

Getting this wrong is silent. A logit score of $0.635$ read as a probability is off by
two percentage points; a logit score of $2.5$ read as a probability is nonsense. The
scale is recorded on the engine and echoed onto every report so a downstream reader
cannot lose it.

Pull `intercept_` and the term tables from the fitted model (`term_scores_` in
InterpretML) and wrap each as a callable. The intercept is a required constructor
argument — a defaulted intercept yields a wrong score that passes every check.

## 1. Register univariate terms

```python
engine = ExplainableBoostingPricerEngine(
    model_id="EBM_SIGNAL_ALPHA",
    base_intercept_beta0=0.50,
    score_scale=ScoreScale.LOGIT,
    shape_table_version="2026-01-15-fold3",
)
engine.register_single_feature_shape("rsi_14", rsi_shape)
```

Re-registering an existing term raises unless `replace=True`. That is deliberate: a
shape function swapped in place changes the model behind audit records already issued
under the old one. `replace=True` also clears that feature's monotonicity constraint —
a constraint verified against the old curve says nothing about the new one — so
re-declare it in the same call.

## 2. Register pairwise terms

```python
engine.register_interaction_shape("rsi_14", "volatility", rsi_vol_shape)
```

Interaction terms are **unordered pairs**. The engine canonicalises the key, so
`("a","b")` and `("b","a")` name one term. Registering both orderings used to create two
terms that each contributed — a silent doubling of the interaction's weight. It now
raises. Self-pairs (`("a","a")`) raise: a univariate term is not an interaction.

A feature may appear only in an interaction, with no univariate term of its own. It is
still required in the input vector — `required_feature_names()` returns the union.

## 3. Declare monotonicity constraints

```python
engine.register_single_feature_shape(
    "rsi_14", rsi_shape,
    monotonic=MonotonicDirection.INCREASING,
    audit_grid=[0.0, 10.0, 20.0, ..., 100.0],
    scope=MonotonicScope.GLOBAL,
)
```

**Direction** — `INCREASING` requires $f(x_{k+1}) - f(x_k) \geq -\text{tolerance}$ across
consecutive grid points; `DECREASING` is the mirror. A flat step is monotone in both
directions, which matters because EBM lookup tables are piecewise constant and flat bins
are common.

**Grid** — mandatory, strictly ascending, at least two finite points. There is no
default and there deliberately is not one: the audit certifies the range it was handed
and nothing outside it. Grid the range the feature will actually take in production,
including the tails, or the audit will bless a curve that turns over just past the last
point you checked.

**Scope** — the part that is easy to get wrong:

| Scope | Claim | Certified when |
|---|---|---|
| `GLOBAL` | The model's output is monotone in this feature | No interaction term contains the feature. Otherwise → violation `INTERACTION_SHADOWS_GLOBAL_MONOTONICITY`. |
| `TERM` | Only the univariate shape $f_i$ is monotone | Always auditable; any interaction on the feature is recorded in `monotonicity_audit_limitations`. |

The reason is structural, not a limitation of this implementation: $f_{i,k}(x_i, x_k)$
can move the score against $f_i$'s direction. InterpretML applies the same rule to its
own post-fit `monotonize`, which "only adjusts a single term and will not modify
pairwise terms. When a feature needs to be globally monotonic, any pairwise terms that
include the feature should be excluded from the model."

If a feature must be globally monotone, drop its interaction terms and re-fit. If the
interaction is worth keeping, downgrade the claim to `TERM` and let the record say so.

Monotonicity is a property of the model, not of one input, so `audit_monotonicity()` can
be called standalone at model-registration time — do that in CI, not only at scoring
time. `evaluate_ebm_signal` also attaches the result to every report, and because the
answer cannot change between instances it is computed once per model configuration and
invalidated whenever a term or constraint is registered or replaced. Grid density is
therefore free on the scoring path: measured at 8 constrained features, a score costs
~15 µs whether the grid holds 2 points or 1000 (it was ~6.9 ms at 1000 points before the
result was cached). Grid the range the feature will actually take; do not thin the grid
to protect inference latency.

## 4. Score

```python
report = engine.evaluate_ebm_signal("AAPL", {"rsi_14": 70.0, "volatility": 0.10})
```

Composition: $\hat{Y} = \beta_0 + \sum_i f_i(x_i) + \sum_{(i,j)} f_{ij}(x_i, x_j)$,
accumulated with `math.fsum` at full precision, terms visited in sorted order for a
deterministic record. Contributions are stored unrounded.

**Input contract, all hard errors:**

| Condition | Result |
|---|---|
| A registered term has no value | `ValueError` naming the missing features |
| A supplied name matches no term | `ValueError` naming the unknown features |
| A value is NaN / ±inf / non-numeric | `ValueError` / `TypeError` |
| A shape function raises | `ShapeFunctionError` naming the term and the input |
| No terms registered | `ValueError` |

The first two used to be silent. A registered feature absent from the input was skipped
and an unknown name was ignored, so the engine scored a *different model* than the one
registered and the report was indistinguishable from a complete evaluation. Never patch
around these by substituting a default value for a missing feature; a zero-contribution
term and an unavailable feature are different states.

## 5. Interpret the audit outcome

Read `status` before touching `total_predicted_score`.

| Status | Meaning | Response |
|---|---|---|
| `PASS_GOVERNANCE_AUDIT` | Composition reproduced; every declared constraint held on its grid | Record and use |
| `FAIL_ADDITIVE_IDENTITY` | A term returned a non-finite value, or re-evaluation disagreed | Do not trade the signal. Score may be NaN. |
| `FAIL_MONOTONICITY_VIOLATION` | A declared constraint failed | Model defect or an over-stated claim — fix the model or the claim |

The identity check re-evaluates every term a second time from the report's own recorded
feature values and compares **term by term**, then the composed total. A pure shape
function reconciles to zero. A stateful, cached or randomised one does not — which is
the point: a record that cannot be reproduced from its own inputs is not a record. It
also means shape functions are called twice per evaluation and must be free of side
effects.

What this check is *not*: it does not prove the shape functions are right. It proves the
published record is internally consistent and reproducible. State it that way in any
document that quotes it.

Violations carry the offending interval and signed step:

```
f_rsi_14 moved by -0.25 between x=0 and x=25, against a declared increasing constraint.
```

## 6. Persist

Retain per scored instance: `model_id`, `shape_table_version`, `term_fingerprint`,
`score_scale`, `base_intercept_beta0`, every `EbmFeatureContribution` and
`EbmInteractionContribution`, `total_predicted_score`, `additive_identity_residual`,
`status`, `monotonicity_violations`, `monotonicity_audit_limitations`.

`term_fingerprint()` is a SHA-256 digest (truncated to 16 hex chars) over the model's
**structure**: term names, interaction pairs, declared constraints and their grids, the
intercept, the scale and `shape_table_version`. It answers "which model configuration
produced this record?" It does **not** hash the lookup tables, so a recalibration behind
an unchanged `shape_table_version` produces an unchanged fingerprint. Version the tables
deliberately.

That matters beyond tidiness: ESMA's 2026 supervisory briefing lists "retraining or
modifying machine learning components" among the change types warranting retesting, and
warns that a series of small recalibrations can accumulate into a material change in
model output without being tested. A fingerprint that only tracks structure will not
catch that drift for you — the version string is where you make the change visible.

## 7. Re-audit on every model change

Run `audit_monotonicity()` and the full test suite whenever shape tables are re-fitted,
a term is added or dropped, an interaction is introduced, or a constraint changes. An
added interaction on a `GLOBAL`-constrained feature turns a previously passing model
into a failing one — which is the intended signal, not a regression in the tooling.
