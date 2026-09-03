---
name: model-serving-infrastructure-ab-testing
description: >-
  Use when a challenger model is evaluated against the champion in production;
  deterministic salted traffic routing, shadow execution and a Welch two-sample t-test,
  so promotion rests on a test rather than on luck.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: financial-ml
  tags: model-serving, ab-testing, champion-challenger, welchs-t-test, shadow-mode, traffic-routing, model-promotion
  brokers_frameworks: "Welch's Two-Sample t-Test; SciPy; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a candidate ML alpha or execution algorithm ("challenger") is being evaluated against the model currently in production ("champion"), and you need a defensible answer to *is the challenger actually better, or did we get lucky?* before live capital moves behind it.

The module does three things: it allocates traffic deterministically between the two models, it runs Welch's two-sample $t$-test over realised per-trade returns in basis points, and it emits an **advisory** recommendation — `PROMOTE_CHALLENGER_TO_CHAMPION`, `REJECT_CHALLENGER`, or a reason to keep collecting.

The recommendation is advisory in the strict sense: nothing here promotes a model, cancels orders, or moves capital. Promoting a challenger is a change to a live trading algorithm and belongs in a governed change-control process with a documented human authorisation — see `references/standards.md`.

## When NOT to Use

- **As an automatic promotion trigger.** Wiring `recommended_action` straight into a deployment pipeline turns a statistical test into an unsupervised capital-allocation loop. ESMA specifically flags the risk that "a series of minor or small changes due to recalibrations could accumulate over time, when uncontrolled or unchecked, into a material change in the model output without it being tested" (`references/standards.md`).
- **To compare a shadow-mode challenger against a live champion.** Shadow returns are counterfactual — no market impact, no queue position, no partial fills — and are systematically optimistic. Shadow mode establishes that a challenger is *not broken*; it cannot establish that it is *better*. `shadow_challenger_id()` exists to name the model to score, not to feed `evaluate_ab_test_results()`.
- **On overlapping or autocorrelated returns.** Welch's test assumes independent observations. Overlapping holding periods, one signal fanned out across correlated symbols, or several trades from one intraday move violate that and understate the true variance. Use `sample-weighting-for-overlapping-labels` first.
- **To compare more than two models, or one challenger across many slices.** Every extra comparison inflates the family-wise false-promotion rate. Correct for it — `factor-research-multiple-testing-correction`.
- **On a per-trade return series whose sign is dominated by a handful of outliers.** The $t$-test compares means; a 30-sample mean of a fat-tailed return distribution is not a stable estimate. Prefer a bootstrap or a longer horizon.
- **As a risk control.** A challenger can be statistically better and still breach exposure or drawdown limits. Those gates are `kill-switch-and-drawdown-circuit-breakers` and run out-of-band.

## Prerequisites

- `scipy` (the $t$-distribution survival function; already a repo-level dependency).
- A **pre-registered** `ABTestConfig`: `experiment_id`, `champion_model_id`, `challenger_model_id`, `traffic_split_ratio` (fraction routed to the champion, e.g. `0.80`), `test_mode` (`TestMode.LIVE_SPLIT` or `TestMode.SHADOW`), `min_sample_size` (default `30`, minimum `2`), `significance_level_alpha` (default `0.05`). `min_sample_size` and `significance_level_alpha` must be fixed **before** data collection starts.
- Realised per-trade returns in basis points as `ModelExecutionResult` records, each carrying the `model_id` that actually produced it. Provenance is checked, not trusted.

## Workflow

1. **Configure and pre-register.** `ABTestConfig` validates on construction and raises `ValueError` rather than defaulting. The `test_mode` comparison is case-sensitive by design: `'shadow'` is rejected outright, because a mode string that silently falls through to `LIVE_SPLIT` sends real orders to an unvalidated model.
2. **Route deterministically.** `route_request(config, request_key)` hashes `experiment_id` **and** the request key, and returns the model that should *execute*. The `experiment_id` salt matters: without it every concurrent experiment buckets every key identically, so allocations are perfectly correlated rather than independent, and a re-run cannot re-randomise. Choose a `request_key` that is stable for the life of the experiment (`symbol`, `account_id`) — hashing a per-order UUID re-splits a single symbol's fills across both models and destroys the independence the test assumes.
3. **Shadow, if shadowing.** In `SHADOW` mode `route_request` always returns the champion; `shadow_challenger_id(config)` names the model to score without executing. Keep shadow returns in their own experiment.
4. **Collect returns to the pre-registered horizon.** Do not evaluate on every new fill and stop at the first favourable result. Measured under the null with the *correct* test: peeking after every sample from $N=30$ to $N=200$ raises the false-promotion rate from 2.5% to **12.0%** (`references/standards.md`).
5. **Evaluate.** `evaluate_ab_test_results()` short-circuits in a fixed order, and each branch leaves the statistics it did not compute as `None` rather than `0.0`:
   - Sample provenance and finiteness $\implies$ `ABORT_EXPERIMENT_INVALID_DATA`. Checked *first*: a corrupt or mislabelled sample must never be reported as "keep collecting", which reads as a healthy experiment.
   - $N < N_{\text{min}}$ in either arm $\implies$ `CONTINUE_EXPERIMENT_INSUFFICIENT_SAMPLES`.
   - Both arms zero-variance, an overflowing variance, or a non-finite statistic $\implies$ `ABORT_EXPERIMENT_INVALID_DATA`. Welch's statistic is $0/0$ in the first case; a stubbed or replayed feed is not a result.
   - Otherwise compute $t = (\bar{X}_B - \bar{X}_A) / \sqrt{s_A^2/n_A + s_B^2/n_B}$, the Welch-Satterthwaite $\nu$, and a two-tailed $p$ **from the $t$ distribution with $\nu$ degrees of freedom** — never from the normal distribution.
6. **Decide.** Promotion requires $p < \alpha$ *and* $\bar{X}_B > \bar{X}_A$, so the effective one-sided false-promotion rate is $\alpha/2$, not $\alpha$. $p < \alpha$ with $\bar{X}_B < \bar{X}_A$ $\implies$ `REJECT_CHALLENGER`. Otherwise `CONTINUE_EXPERIMENT_NO_SIGNIFICANT_DIFFERENCE`.
7. **Route the report into change control.** The `ABTestReport` is the evidence artifact; the promotion itself needs a separate documented authorisation.

> Full procedure: see `references/workflows.md`.
> Standards, formulas and their provenance: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Drawing the $p$-value from the normal distribution.** The normal CDF understates $p$ at every finite $\nu$, and is worst at exactly the sample sizes a promotion gate runs at. Measured under the null: **2.79%** false promotions at $N=30$ per arm and **4.43%** at $N=5$, against a 2.50% nominal rate. A concrete case at $N=30$: $t = 1.9616$ on $\nu = 58$ gives an exact $p$ of $0.0546$ — not significant — while the normal approximation returns $0.0498$ and promotes.
- **Flooring a zero variance to keep the division alive.** Two constant return series carry no information about sampling variability. Substituting $10^{-6}$ for a zero variance manufactures $|t| \approx 35{,}000$ and $p = 0$ out of a stubbed feed. Return an explicit invalid-data status instead.
- **Peeking and early stopping.** Evaluating continuously and stopping at the first $p < 0.05$ inflates the false-promotion rate roughly fivefold (2.5% $\to$ 12.0%, measured). Fix $N_{\text{min}}$ before data collection; if you genuinely need to monitor continuously, you need always-valid sequential inference, not this fixed-horizon test (Johari et al., 2022).
- **Reporting placeholder statistics for a test that never ran.** A report carrying `p_value = 1.0` and `t = 0.0` because it short-circuited on insufficient samples is indistinguishable on a dashboard from a test that genuinely found nothing.
- **Trusting the caller to pass the arms in the right order.** Swapping `champion_results` and `challenger_results` inverts every recommendation — the engine confidently advises rejecting the *better* model — and nothing downstream catches it. Verify `model_id` provenance on every sample.
- **A `test_mode` typo routing live capital.** `'shadow'` is not `'SHADOW'`. Any comparison that falls through to the live branch on an unrecognised value has a live-money failure mode.
- **Comparing a rounded $p$-value against $\alpha$.** Rounding to 4dp before the comparison makes $p = 0.049996$ report as `0.0500` and fail a gate it should pass.
- **Using an unsalted hash for allocation.** Every concurrent experiment then produces identical buckets: measured 100% allocation agreement between two nominally independent 50/50 experiments.
- **Editing the config mid-experiment.** `ABTestConfig` is frozen after validation, and both reasons matter: a pre-registered experiment whose $N_{\text{min}}$ can be raised once you have seen an interim result is not pre-registered, and validation that runs once at construction is trivially walked past by a later assignment.
- **Statistical significance mistaken for economic significance.** A significant $+0.4$ bps edge is not a promotion case once you net off transaction costs, borrow and the operational risk of the change itself — `backtesting-ml-models-against-transaction-costs`.

## Verification

- `test_welch_statistics_match_hand_computed_values` pins $t$, $\nu$ and $p$ against values derived by hand from the NIST definitions, not by re-running the implementation's own arithmetic.
- `test_statistics_match_scipy_welch_reference` cross-checks 50 unequal-size, unequal-variance cases against `scipy.stats.ttest_ind(equal_var=False)`; maximum absolute deviation across those cases measured at $5.7 \times 10^{-14}$.
- `test_p_value_uses_t_distribution_not_normal_approximation` and `test_zero_variance_samples_are_not_declared_significant` are regression tests for the two v1 defects that produced false promotions; both fail against v1.
- Corrupt-input tests cover NaN/±Inf returns, swapped result lists, and every rejected configuration value.
- Run `python -m unittest discover -s skills/model-serving-infrastructure-ab-testing/scripts`.

## Related Skills

- `model-versioning-and-rollback`
- `model-card-documentation-for-trading-models`
- `canary-releases-for-strategy-code-changes`
- `factor-research-multiple-testing-correction`
- `sample-weighting-for-overlapping-labels`
- `backtesting-ml-models-against-transaction-costs`
