# Workflows for Champion-Challenger Model A/B Testing

Thresholds, formulas and their provenance are in `standards.md`. This page is
the procedure.

## 1. Pre-registration

Fix `min_sample_size` and `significance_level_alpha` **before** any data is
collected, and record them with the experiment. Both are inputs to the validity
of the $p$-value, not dials to turn once an interim result is visible.

`ABTestConfig` validates on construction and raises `ValueError` rather than
falling back to a default, and is **frozen** afterwards: validation runs once,
so a mutable config could be walked past every check it had just passed.
Rejected outright:

| Rejected | Why it cannot be allowed to default |
|---|---|
| `test_mode` outside `{'LIVE_SPLIT', 'SHADOW'}` (case-sensitive) | An unrecognised value falling through to `LIVE_SPLIT` sends real orders to an unvalidated model. |
| `traffic_split_ratio` outside $[0, 1]$, or non-finite | Silently clamps to all-champion or all-challenger, and the experiment quietly stops being an experiment. |
| `min_sample_size < 2` | The $(n-1)$ sample variance and the Welch-Satterthwaite denominator are undefined at $n = 1$. |
| `significance_level_alpha` outside $(0, 1)$ | Not a probability. |
| `champion_model_id == challenger_model_id`, or any blank identifier | Nothing to compare; the report would be self-referential. |

## 2. Traffic routing

`route_request(config, request_key)` returns the model that should **execute**.

```
score = blake2b(experiment_id || 0x00 || request_key)[:4] / 2**32   in [0, 1)
score < traffic_split_ratio  ->  champion, else challenger
```

Three properties matter and each was a defect when absent:

- **Salted by `experiment_id`.** Without the salt, two concurrent experiments
  bucket every key identically — allocations perfectly correlated rather than
  independent, and no way to re-randomise on a re-run.
- **Divided by `2**32`, not `0xFFFFFFFF`.** Dividing by the maximum attainable
  value makes a score of exactly `1.0` reachable and biases the top bucket.
- **Strict `<`.** `traffic_split_ratio = 0.0` then routes everything to the
  challenger and `1.0` everything to the champion, with no off-by-one bucket.

`request_key` must be a stable allocation unit — `symbol`, `account_id`, a
strategy instance. Hashing a per-order UUID re-randomises on every order, which
splits a single symbol's fills across both models and destroys the observation
independence the $t$-test assumes.

`blake2b` rather than `md5`: stdlib, no FIPS-mode availability caveat. It is
used purely as a uniform mapping, not for security.

## 3. Shadow mode

In `SHADOW` mode `route_request` always returns the champion — the challenger
never touches live capital. `shadow_challenger_id(config)` names the model to
score without executing.

**Do not feed shadow challenger returns to `evaluate_ab_test_results()` against
live champion returns.** Shadow returns are counterfactual: no market impact, no
queue position, no partial fills, no adverse selection. They are systematically
optimistic. Shadow mode answers "is the challenger broken?", not "is the
challenger better?".

## 4. Sample collection

Collect realised per-trade returns in basis points as `ModelExecutionResult`
records, each carrying the `model_id` that actually produced it. Run to the
pre-registered horizon.

Do **not** evaluate after every fill and stop at the first favourable result.
Measured under the null with the exact Welch test, evaluating after every new
sample from $N=30$ to $N=200$: the false-promotion rate rises from 2.5% to
12.0%. If continuous monitoring is a genuine operational requirement, the answer
is always-valid sequential inference, not this test with a tighter $\alpha$.

## 5. Evaluation

`evaluate_ab_test_results()` short-circuits in a fixed order. Each branch leaves
the statistics it did not compute as `None`.

| Order | Condition | `recommended_action` | `status` |
|---|---|---|---|
| 1 | Any sample's `model_id` mismatches the configured arm, or any return is non-finite | `ABORT_EXPERIMENT_INVALID_DATA` | `AB_TEST_INVALID_DATA` |
| 2 | $N < N_{\text{min}}$ in either arm | `CONTINUE_EXPERIMENT_INSUFFICIENT_SAMPLES` | `AB_TEST_INSUFFICIENT_SAMPLES` |
| 3 | **Both** arms have zero sample variance | `ABORT_EXPERIMENT_INVALID_DATA` | `AB_TEST_INVALID_DATA` |
| 3b | Sample variance overflows, or a computed statistic is non-finite | `ABORT_EXPERIMENT_INVALID_DATA` | `AB_TEST_INVALID_DATA` |
| 4 | $p < \alpha$ and $\bar{X}_B > \bar{X}_A$ | `PROMOTE_CHALLENGER_TO_CHAMPION` | `AB_TEST_COMPLETED` |
| 5 | $p < \alpha$ and $\bar{X}_B < \bar{X}_A$ | `REJECT_CHALLENGER` | `AB_TEST_COMPLETED` |
| 6 | otherwise | `CONTINUE_EXPERIMENT_NO_SIGNIFICANT_DIFFERENCE` | `AB_TEST_COMPLETED` |

Why this order:

- **Provenance before sample size.** A corrupt or mislabelled sample reported as
  "keep collecting" reads as a healthy experiment still in progress. It is not —
  the pipeline is broken and the count is meaningless.
- **Invalid data is not "no difference".** They are separate statuses so a
  broken feed can never be read off a dashboard as a clean null result.
- **Overflow is a data fault, not a crash.** A sample can be finite
  element-wise and still overflow its own variance; that surfaces as invalid
  data rather than an `OverflowError` escaping the evaluator.
- **One zero-variance arm is still testable.** Only the both-zero case makes
  Welch's statistic $0/0$; a single constant arm yields a well-defined $t$ with
  $\nu = n - 1$ of the other arm, and is evaluated normally.

Step 4's asymmetry is deliberate: because promotion requires a positive mean
difference *and* $p < \alpha$, the effective one-sided false-promotion rate is
$\alpha/2$.

`CONTINUE_EXPERIMENT_NO_SIGNIFICANT_DIFFERENCE` means "not distinguishable at
this sample size". It is not evidence of equivalence, and it is not a licence to
keep collecting past the pre-registered horizon in the hope the sign firms up.

## 6. Change control

The `ABTestReport` is an evidence artifact, not a deployment trigger. A
promotion is a change to a live trading algorithm; route the report into
whatever documented authorisation and retest process governs that, and record
the decision. Under EU MiFID II / RTS 6 this is explicitly a material change —
see `standards.md`, which also records the scope limits on that statement.

Before acting on a `PROMOTE_CHALLENGER_TO_CHAMPION`, confirm separately that:

- the edge survives transaction costs, borrow and the operational risk of the
  change (`backtesting-ml-models-against-transaction-costs`);
- observations were independent (`sample-weighting-for-overlapping-labels`);
- the family-wise error rate is controlled if this is one of many comparisons
  (`factor-research-multiple-testing-correction`);
- a rollback path exists (`model-versioning-and-rollback`).
