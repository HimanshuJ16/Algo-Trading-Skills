---
name: explainable-boosting-machines-for-regulated-signals
description: >-
  Use when a trading signal must be explainable to a model-risk reviewer or a
  regulator — composing an already-fitted Explainable Boosting Machine (EBM /
  GA2M) from its shape functions, auditing declared monotonicity constraints,
  and emitting a reproducible per-prediction governance record.
domain: algorithmic-trading
subdomain: financial-ml
tags:
- financial-ml
- ebm
- ga2m
- glass-box-ml
- interpretml
- model-governance
- model-risk-management
brokers_frameworks:
- InterpretML EBM
- GA2M
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when a signal model's *attribution* has to survive review — by an
internal model-risk function, a compliance officer reconstructing why an order fired,
or a supervisor asking how the model reached its output. An EBM is a generalized
additive model with pairwise interactions:

$$g(E[y]) = \beta_0 + \sum_i f_i(x_i) + \sum_{(i,j)} f_{ij}(x_i, x_j)$$

Each $f$ is a learned lookup table over the feature's bins, so the model **is** the sum
of its term contributions. Reading an attribution off an EBM is a lookup, not a sampled
estimate as in SHAP or LIME — the same number every time, with no sampling variance for
a reviewer to challenge.

Use it when the model's inputs are tabular, the interpretability requirement is at least
as binding as the last few points of accuracy, and someone will have to justify the
model's shape to a third party. This module scores an EBM that has already been fitted
and produces the governance record; it does not fit one (`interpret` does that).

## When NOT to Use

- **As evidence of regulatory compliance.** This produces an audit *record*. Whether
  that record satisfies anyone's obligations depends on the entity, the jurisdiction
  and the model's use — see `references/standards.md`, which quotes what the current
  guidance actually says rather than asserting a mandate.
- **When the accuracy gap matters more than the shape plots.** A GA²M restricted to
  univariate and pairwise terms cannot represent higher-order interactions. If the
  signal genuinely lives in a three-way interaction, an EBM will not find it; compare
  against a black-box baseline before committing — see
  `gradient-boosted-tree-vs-neural-net-tradeoffs`.
- **On non-tabular inputs.** Shape functions over raw text, images or order-book
  sequences are not interpretable in the sense this skill relies on.
- **To explain a model you did not fit as an EBM.** Post-hoc explanation of an existing
  black box is a different problem with different caveats — see
  `explainability-for-live-trading-signals`.
- **As the thing that stops a bad order.** An audit record is written after the score
  is composed. Position and loss limits are enforced elsewhere; see
  `kill-switch-and-drawdown-circuit-breakers`.

## Prerequisites

- Fitted intercept $\beta_0$ and shape tables $f_i$, $f_{ij}$, exposed as callables.
  The intercept is a **required** argument — there is no sensible library default, and
  a defaulted one produces a wrong score that still passes every check in the report.
- The **score scale**. Classification EBM contributions are log-odds; regression
  contributions are in target units. InterpretML's own term graphs are plotted "in
  logits ... because these graphs are in logarithm space". Pass `ScoreScale.LOGIT` or
  `ScoreScale.IDENTITY`, and convert with `logit_score_to_probability` rather than
  doing arithmetic on the raw score.
- A `shape_table_version` identifying the fitted tables, so an audit record can be tied
  back to a specific calibration.
- For every monotonicity claim: a direction, a **scope**, and an explicit `audit_grid`
  of feature values to check it at.

## Workflow

1. **Register the terms.**
   - Univariate terms first, then pairwise. Interaction terms are *unordered* pairs:
     `("rsi_14", "volatility")` and `("volatility", "rsi_14")` are the same term, and
     the engine canonicalises the key so the same interaction cannot be registered
     twice and counted twice.
   - **Decision point — re-registering a term is a model change, not an edit.** It
     requires `replace=True` and invalidates any audit record already issued under the
     old term. It also clears that feature's monotonicity constraint, so re-declare it.

2. **Declare monotonicity constraints, with the scope you can actually defend.**
   - `scope=GLOBAL` claims the *model* is monotone in the feature. It is certified only
     if no interaction term contains that feature.
   - `scope=TERM` claims only that the univariate shape $f_i$ is monotone. Any
     interaction on the feature is then recorded as a stated limitation of the audit.
   - **Decision point — a monotone $f_i$ does not make the model monotone in $x_i$.**
     If $f_{i,k}$ exists it can move the score the other way. InterpretML documents the
     same limit for its own post-fit `monotonize`: it "only adjusts a single term and
     will not modify pairwise terms. When a feature needs to be globally monotonic, any
     pairwise terms that include the feature should be excluded from the model." A
     `GLOBAL` constraint on a feature that also appears in an interaction is therefore
     reported as a **violation**, not quietly certified.
   - The `audit_grid` is mandatory and there is no default. The audit certifies the
     range it was given and nothing beyond it — an RSI grid stopping at 50 says nothing
     about the curve at 80.

3. **Score, and require complete feature coverage.**
   - $\hat{Y} = \beta_0 + \sum_i f_i(x_i) + \sum_{(i,j)} f_{ij}(x_i, x_j)$, summed with
     `math.fsum` at full precision. Contributions are recorded unrounded, so the record
     reconciles to the score exactly.
   - **Decision point — a missing or unknown feature is a caller error, not a
     zero-contribution term.** Scoring a subset of the model's terms understates the
     score in a way the report cannot distinguish from a complete evaluation, so both
     raise. Do not "fill in" an absent feature with a default to get past the error;
     find out why the feature is missing.

4. **Read the audit outcome before the score.**
   - `PASS_GOVERNANCE_AUDIT` — composition reproduced and every declared constraint
     held on its grid.
   - `FAIL_ADDITIVE_IDENTITY` — a term returned a non-finite value, or the second,
     independent evaluation of the same recorded inputs disagreed with the first. The
     latter means a shape function is stateful, cached or randomised: an audit record
     that cannot be reproduced from its own inputs is not an audit record.
   - `FAIL_MONOTONICITY_VIOLATION` — a declared constraint failed, with the offending
     grid interval and the signed step recorded.
   - **Decision point — the score field is populated on a failed audit and is NaN when
     a term returned NaN.** Gate on `status`, never on the presence of a number.

5. **Persist the record.** `model_id`, `shape_table_version`, `term_fingerprint`,
   `score_scale`, every term contribution, the residual, and any violations or stated
   limitations. Re-fitting the shape tables is a material model change and needs a new
   `shape_table_version` — the fingerprint covers the model's *structure*, so it cannot
   detect a recalibration hidden behind an unchanged version string.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **A governance flag that can only say PASS.** The previous version of this engine
  hard-coded `is_monotonicity_audit_passed=True` and `status="PASS_GOVERNANCE_AUDIT"`,
  so a wildly non-monotonic shape was certified. A check that cannot fail is worse than
  no check: it puts a signed clean bill of health on an unexamined model.
- **Auditing $f_i$ and claiming the model is monotone in $x_i$.** Only true if no
  interaction term contains $x_i$. This is the single most common way a monotonicity
  claim on a GA²M turns out to be unsupported.
- **Summing logit contributions and reading the result as a probability.** For a
  classification EBM the terms are additive in log-odds only. $0.635$ in logit space is
  a probability of $0.654$, not $63.5\%$ — and an intercept that looks like a
  probability (0.50) is exactly how that confusion starts.
- **Certifying a range the grid never covered.** A shape that is monotone on
  $[0, 50]$ and turns over at $80$ passes an audit gridded to $50$. The grid is a claim
  about scope; make it cover the feature range the model will actually see.
- **Silently dropping a feature.** A registered term with no supplied value used to be
  skipped, producing a lower score from a smaller model, reported as a clean pass. A
  misspelled feature name did the same thing. Both are now hard errors.
- **Rounding contributions before summing them.** The old engine rounded each term to
  4 dp and accumulated the rounded values, so the number the audit certified was not
  the number the model produced. Round for display, never for the record.
- **Confusing "exact attribution" with "correct model".** Exactness is a property of
  the *decomposition*: the terms sum to the score with no sampling error. It says
  nothing about whether the shape functions were fitted on point-in-time data, or
  generalise at all. See `feature-engineering-without-leakage` and
  `walk-forward-validation-setup`.
- **Treating recalibration as a non-event.** ESMA's 2026 supervisory briefing lists
  "retraining or modifying machine learning components" as a change type warranting
  retesting, and warns that a series of small recalibrations can accumulate into a
  material change in model output without ever being tested.

## Verification

- Compose the worked example: $\beta_0 = 0.50$, $f_{\text{rsi}}(x) = (x-50)/100$,
  $f_{\text{vol}}(v) = -v$, $f_{\text{rsi,vol}}(x,v) = 0.5(x/100)v$. At
  $\text{RSI}=70$, $\text{vol}=0.10$ the terms are $+0.20$, $-0.10$, $+0.035$ and the
  score is $0.635$ — derived by hand, matched to 12 decimal places, with the report's
  own recorded components re-summing to the same value.
- Negative checks that must **fail** the audit rather than pass it: a V-shaped curve
  declared increasing; a `GLOBAL` constraint on a feature that also appears in an
  interaction; a shape function returning NaN; a stateful shape function that returns a
  different value on re-evaluation.
- Negative checks that must **raise**: a missing registered feature, an unknown feature
  name, a non-finite feature value, a monotonicity constraint with no `audit_grid`, a
  non-ascending grid, a duplicate interaction registered in reverse order, and a shape
  function that raises (surfaced as `ShapeFunctionError` naming the offending term).
- Verify `logit_score_to_probability` against the definition $\sigma(\ln k)=k/(1+k)$:
  $\sigma(0)=0.5$, $\sigma(\ln 3)=0.75$, $\sigma(-\ln 3)=0.25$, and no overflow at
  $\pm 1000$.
- Run `python scripts/test_explainable_boosting_pricer.py` and confirm 100% pass rate.

## Related Skills

- `explainability-for-live-trading-signals`
- `gradient-boosted-tree-vs-neural-net-tradeoffs`
- `model-card-documentation-for-trading-models`
- `model-versioning-and-rollback`
- `ensemble-signal-combination-without-overfitting`
