# Deep Workflow Reference — online-learning-for-adaptive-signal-models

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

### 1. Choose the update rule

| Situation | Rule | Why |
|---|---|---|
| Coefficients genuinely shift between regimes; you need to *track* them | `"rls"` with $\lambda < 1$ | It is the exponentially weighted least-squares fit of the last $\approx 1/(1-\lambda)$ observations, recomputed each tick |
| Features are unscaled, heavy-tailed, or vary in energy across instruments | `"nlms"` | Stability region $0 < \mu < 2$ holds whatever the feature scale |
| Features are z-scored and the fit is roughly stationary | `"lms"` | Lowest variance, $O(n)$, no covariance to maintain |
| Feature vector is wide and the path is latency-critical | not `"rls"` | RLS is $O(n^2)$ per update in time and memory |

Set $\lambda$ from the horizon you intend to track, via $T_0 = 1/(1-\lambda)$:
$\lambda = 0.95 \Rightarrow 20$ observations, $0.99 \Rightarrow 100$,
$0.995 \Rightarrow 200$.

### 2. Predict at decision time and queue the sample

```python
prediction = model.predict(features)          # pure; mutates nothing
buffer.enqueue(features, feature_time=t, label_ready_time=t + horizon)
```

`label_ready_time` is when the target becomes **observable** — the close of bar
$t+h$ for an $h$-bar forward return — not when you happen to compute it.
`enqueue` requires non-decreasing label times and refuses to exceed
`max_pending`; a queue that only grows means the outcome feed has stalled.

### 3. Release and update only realised labels

```python
for sample in buffer.release_due(now):
    result = model.update(
        sample.features,
        realised_target(sample.label_ready_time),
        label_ready_time=sample.label_ready_time,
        now=now,
    )
```

Passing `label_ready_time` and `now` together arms the horizon check; passing one
alone raises, because silently disabling the check is the failure this exists to
prevent. `label_ready_time == now` is accepted; `> now` is refused.

### 4. Read what the update reports

| Field | Meaning | Act when |
|---|---|---|
| `step_ratio` | $\eta\lVert x_t\rVert^2$ for the gradient rules; `None` for RLS | $\ge 2$: this update magnified the error. Lower $\eta$ or switch to `"nlms"` |
| `weights_clipped` | The L2 projection was active | Repeatedly true: the estimator keeps trying to leave the ball |
| `drift_detected` | Page-Hinkley signalled an increase in error | See step 6 |
| `prediction_error` | $y_t - \hat y_t$, unrounded | — |

Non-finite input raises before touching the weights; an update that would install
a non-finite weight raises with the previous weights intact.

### 5. Weight projection

After each step, $w \leftarrow w \cdot \min(1, W_{\max}/\lVert w\rVert_2)$. This
bounds the **whole vector** and preserves direction. It is not gradient clipping
(the gradient is applied in full) and not a per-component cap
($|w_i| \le W_{\max}$ is a different, larger region). `clipped_update_count`
counts how often it was active; a rising count is a diagnostic, not something to
silence by raising $W_{\max}$.

### 6. Drift detection and response

```python
detector = PageHinkleyDetector(delta=..., threshold=..., min_samples=30)
```

Both `delta` and `threshold` are in the units of your absolute error. Size them
from an observed baseline: `delta` ≈ the per-observation error increase you
tolerate, `threshold` ≈ the cumulative excess error that constitutes evidence.
There are no defaults, because a threshold copied from a classification-oriented
example would never fire on errors near $10^{-3}$ and would read as reassurance.

On a signal:

- **RLS** — `reset_covariance_on_drift=True` restores $P_0$, recovering the
  plasticity that forgetting had already spent, without discarding the estimate.
- **Gradient rules** — there is no covariance to reset. Raise the step size
  deliberately, or escalate: the drift may not be the model's to fix.
- **Either** — classify the cause before remediating
  (`concept-drift-vs-staleness-differentiation`), and route any halting decision
  through `kill-switch-and-drawdown-circuit-breakers`.

The test is one-sided: a *falling* error never signals.

### 7. Checkpoint

```python
json.dump(model.to_state(), fh)                     # on a schedule and at shutdown
model = OnlineAdaptiveSignalModel.from_state(json.load(fh))   # at startup
```

`from_state` validates the version, the feature count, the covariance shape and
the finiteness of every value. Without checkpointing, a restart resumes from zero
weights and emits predictions of exactly 0.0 — which a downstream sizer reads as
"no edge", not as "no model".

### 8. Audit

`audit_performance()` compares a fixed early-window MAE (`baseline_window`
observations, captured once at the start of the stream) against a rolling
`recent_window`. The two are disjoint by construction, so the report is refused
until `baseline_window + recent_window` samples have been applied.

`is_converged` is a point comparison of two means — no significance test, no
confidence bound. Read it with `clipped_update_count`, `unstable_step_count`,
`drift_detection_count` and, for RLS, `covariance_trace`.

## Covariance windup, concretely

With $\lambda < 1$, forgetting is unconditional: $P$ is divided by $\lambda$ every
step whether or not the step carried information. In the degenerate case
$x_t = 0$, the gain is zero and the recursion is exactly $P_t = P_{t-1}/\lambda$,
so the trace grows as $\lambda^{-t}$ — after 500 steps at $\lambda = 0.9$ it has
grown by a factor of $0.9^{-500} \approx 10^{22}$. The estimator is then
explosively sensitive to whatever arrives next.

`rls_max_covariance_trace` freezes the covariance update once the trace crosses
it, and `covariance_frozen_count` records how often that happened. A rising count
means the excitation no longer supports your $\lambda$: either the market went
quiet or a feature stopped varying. The constructor also refuses a trace limit at
or below the initial trace $n \cdot c$, which would otherwise freeze $P$ before
the first update and silently turn RLS into a fixed-gain filter.

## Production Implementation Reference

- Reference code: `scripts/online_adaptive_model.py`
  (`OnlineAdaptiveSignalModel`, `PageHinkleyDetector`, `LabelHorizonBuffer`,
  `OnlinePredictionResult`, `OnlineModelAuditReport`, `OnlineLearningError`).
- Automated unit tests: `scripts/test_online_adaptive_model.py`.
- Run with
  `python -m unittest discover -s skills/online-learning-for-adaptive-signal-models/scripts`.
