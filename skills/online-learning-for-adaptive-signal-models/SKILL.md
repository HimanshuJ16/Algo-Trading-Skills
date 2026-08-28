---
name: online-learning-for-adaptive-signal-models
description: Use when a live linear signal model must be updated observation-by-observation
  as market dynamics shift, rather than by periodic batch refit - LMS, energy-normalised
  LMS or recursive least squares with an exponential forgetting factor, with the
  label horizon enforced structurally so the model is never trained on a return that
  has not happened yet, and a Page-Hinkley test on the model's own error to decide
  when the adaptation itself needs resetting
domain: algorithmic-trading
subdomain: financial-ml
tags:
- financial-ml
- online-learning
- adaptive-model
- incremental-learning
- rls
- nlms
- concept-drift
- page-hinkley
brokers_frameworks:
- Online Adaptive Model Engine
- Python standard library (math, collections)
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this when a **linear** signal model is already live and the relationship it
encodes is expected to move faster than your retraining cadence. Batch refitting
has a floor on how current it can be — the pipeline latency plus the schedule
interval — and in a regime shift that floor is where the losses accrue. An online
estimator closes that gap by folding each realised outcome into the weight vector
as it arrives.

Three update rules are offered because they trade the same two things differently:

| Rule | Weight increment | Adaptation speed | What it costs |
|---|---|---|---|
| `"lms"` | $\eta \, e_t x_t$ | Slow, and set by $\eta$ | The stable range of $\eta$ depends on $E[\lVert x\rVert^2]$, which you do not control |
| `"nlms"` | $\dfrac{\mu}{\epsilon + \lVert x_t\rVert^2} e_t x_t$ | Same, scale-free | Amplifies the step when the feature vector is quiet |
| `"rls"` | $P_t x_t / (\lambda + x_t^{\mathsf T} P_t x_t) \cdot e_t$ | Fast — memory $1/(1-\lambda)$ | $O(n^2)$ per sample, and covariance windup under poor excitation |

RLS with $\lambda < 1$ is the one that actually *tracks* a moving coefficient: it
is the exponentially weighted least-squares fit of the last $\approx 1/(1-\lambda)$
observations, recomputed every tick. LMS and NLMS descend toward the same fit but
do not weight recency explicitly.

## When NOT to Use

- **On a non-linear relationship.** Every rule here fits $\hat y = x^{\mathsf T}w$.
  Online-updating a gradient-boosted tree or a neural net is a different problem
  with different failure modes; this module will happily fit a straight line
  through a curve and report a falling MAE while doing it.

- **When labels are not yet realised.** The whole apparatus needs $y_t$. A
  20-day-horizon target produces its first trainable observation 20 trading days
  after deployment, and until then there is nothing to adapt on. If you need to
  act before the outcome exists, that is a monitoring problem
  (`model-staleness-detection`), not an online-learning one.

- **As a diagnosis of *why* performance decayed.** The Page-Hinkley test says the
  error mean rose. It does not distinguish a stalled feature pipeline from a
  shifted $P(X)$ from a decayed $P(Y \mid X)$ — three causes with three different
  remediations. That separation is `concept-drift-vs-staleness-differentiation`.

- **As a risk control.** A drift signal is information, not an action. Halting
  the strategy, cancelling working orders and flattening positions belong to
  `kill-switch-and-drawdown-circuit-breakers`. An adaptive model *by construction*
  keeps producing a confident-looking signal while it re-learns a regime it has
  not seen enough of; nothing here bounds the loss it takes doing that.

- **When you cannot audit what the model became.** Online weights change every
  tick, so "which model traded at 14:32" is only answerable if you checkpoint
  (`to_state`) and version (`model-versioning-and-rollback`) them. Without that
  the post-mortem of a bad fill is not reconstructible.

- **On a stationary relationship.** If the coefficients genuinely are not moving,
  a periodically refitted batch model has lower variance and is easier to reason
  about. Adaptation you do not need is just added noise in the weights.

## Prerequisites

- A streaming feature vector $X_t$ whose **components are on comparable scales**.
  Under `"lms"` the stable step size depends on $E[\lVert X\rVert^2]$; under
  `"rls"` a badly conditioned Gram matrix makes $P$ ill-conditioned.
- A realised target $y_t$ and, critically, **the timestamp at which it becomes
  observable** — for an $h$-bar forward return that is the close of bar $t+h$,
  not bar $t$.
- A centred target, or a constant appended to the feature vector. The model has
  **no intercept**; predictions pass through the origin.
- For `"rls"`: a forgetting factor $\lambda$ chosen from the horizon you want to
  track, via $T_0 = 1/(1-\lambda)$ — not picked for looking reasonable.
- For drift detection: a $\delta$ and a threshold **in the units of your absolute
  error**. There are no defaults, deliberately — see the pitfalls.

## Workflow

1. **Choose the rule from the shape of the problem, not the fashion.**
   - **Decision point — is the coefficient moving, or just noisy?** If you need
     to *track* a coefficient that genuinely shifts between regimes, use `"rls"`
     with $\lambda$ derived from the number of observations the new regime is
     expected to last. If you only need to refine a roughly stationary fit, a
     gradient rule with a small step has lower variance and $O(n)$ cost.
   - **Decision point — are the features scaled?** If you cannot guarantee
     $\lVert x\rVert^2$ stays in a known band — raw prices, volumes, order-book
     depths, anything heavy-tailed — use `"nlms"`. Its stability region
     $0 < \mu < 2$ holds whatever the feature scale, because the normalisation
     makes the step ratio equal $\mu$ by construction. `"lms"` has no such
     guarantee, so this module measures $\eta\lVert x_t\rVert^2$ on every update
     and counts the breaches rather than letting them pass.

2. **Predict before the label exists, and queue the sample.** Call `predict()` at
   decision time. Then `LabelHorizonBuffer.enqueue(features, feature_time,
   label_ready_time)` — the buffer holds the feature vector until its outcome is
   actually observable.

3. **Release and update only what has realised.**
   - **Decision point — the horizon gate is the difference between adaptation
     and look-ahead.** `release_due(now)` returns only samples whose
     `label_ready_time <= now`; passing `label_ready_time` and `now` to
     `update()` makes the model refuse anything else outright. Training a *live*
     model on a return that has not happened is not a backtest artefact you
     discover later — it is a model whose weights encode the future, trading real
     capital now, and it leaves no trace in any backtest you have already run.
   - The buffer requires non-decreasing label times and refuses to grow past
     `max_pending`. A queue that only grows means the outcome feed has stalled;
     that is a data incident, not a reason to buffer harder.

4. **Let the update run, and read what it reports.** Every `update()` returns
   `step_ratio`, `weights_clipped` and `drift_detected`. Non-finite input is
   rejected before it reaches the weights, and an update that would install a
   non-finite weight raises with the previous weights intact.

5. **Bound the weights, and know what the bound is.** After each step the vector
   is projected onto $\lVert w\rVert_2 \le W_{\max}$.
   - **Decision point — this is not gradient clipping and not a per-component
     cap.** The gradient is applied in full and the *result* is pulled back onto
     the ball, preserving direction. A rising `clipped_update_count` means the
     estimator is repeatedly trying to leave the ball — investigate the step size
     or the features, rather than raising $W_{\max}$ until it stops.

6. **Watch the error stream for a change in its mean.** Feed a
   `PageHinkleyDetector` and act on the signal.
   - **Decision point — a drift signal is a plasticity problem, not a weight
     problem.** For `"rls"`, `reset_covariance_on_drift=True` restores $P_0$ and
     lets the estimator move quickly again *without discarding the estimate it
     has*. Resetting the weights instead throws away everything the model knows
     because its recent errors rose.

7. **Checkpoint the state.** `to_state()` is JSON-serialisable and
   `from_state()` validates every field. A process that restarts without it
   silently resumes from zero weights and trades a signal it has not learned yet
   — with no exception, no alert, and predictions of exactly 0.0 that a
   downstream sizer will read as "no edge" rather than "no model".

8. **Audit against a fixed baseline, and read the caveat.** `audit_performance()`
   compares a fixed early-window MAE against the rolling recent-window MAE.
   `is_converged` is a point comparison of two means with **no** significance
   test — treat it as a smoke alarm, not a bill of health.

> Full procedure: see `references/workflows.md`.
> Standards, citations and stated limitations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Updating on a target whose horizon has not elapsed.** The defining failure of
  online learning in trading, and the one that leaves no trace: the backtest was
  clean, the live model is not. A 5-minute forward return known at 10:00 belongs
  to the feature vector from 09:55, and applying it to 10:00's features trains
  the model on the future, live, with capital at risk.
- **Assuming the weight-norm cap catches a bad tick.** It does not catch NaN:
  `nan > max_norm` is `False`, so the projection never fires. Before 2.0 a single
  NaN feature turned every weight into NaN permanently and silently, and every
  subsequent prediction was NaN with no exception raised. Validate the tick.
- **Porting an LMS learning rate across instruments.** $\eta$ that is stable on a
  z-scored feature diverges on a raw price. The stable range is
  $0 < \eta\lVert x\rVert^2 < 2$ and only the first factor is yours.
- **Copying Page-Hinkley thresholds from a library example.** `delta` and
  `threshold` carry the units of the monitored quantity. Defaults chosen for
  classification error rates in $[0,1]$ will never fire on absolute errors near
  $10^{-3}$, and a detector that never fires reads as reassurance rather than as
  a misconfiguration.
- **Leaving a Page-Hinkley detector running for months without a reset.** Its
  mean is cumulative, so after a long stationary run a genuine change has to move
  a heavily anchored mean before the statistic climbs, and the signal arrives
  late. Reset it on a schedule.
- **Setting $\lambda$ by feel.** $\lambda = 0.9$ is not "a bit more adaptive"
  than $\lambda = 0.99$ — it is a 10-observation memory against a 100-observation
  one. Derive it from $T_0 = 1/(1-\lambda)$ and the horizon you mean to track.
- **Running RLS with $\lambda < 1$ through a quiet market.** Forgetting is
  unconditional: old information decays whether or not new information arrives.
  Under poor excitation $P$ grows without bound — covariance windup — and the
  estimator becomes explosively sensitive to the next tick. The trace limit
  freezes $P$ instead; a rising `covariance_frozen_count` means the excitation
  no longer supports your $\lambda$.
- **Expecting `l2_penalty` to regularise RLS.** It does not; RLS is exact
  weighted least squares. Its only regularisation is the $P_0$ initialisation,
  whose influence decays as $\lambda^t$ and then is gone. The module logs a
  warning rather than pretending the parameter did something.
- **Letting the error history grow with the stream.** A live model runs for
  months. Before 2.0 every absolute error was retained forever, and the audit's
  "initial" window meant a different span at every sample count — after 20,000
  samples it compared samples 1–5,000 against 15,000–20,000 and called the result
  convergence.
- **Reading a falling MAE as a working signal.** MAE falls when the model
  predicts the *conditional mean* better. It says nothing about the sign
  accuracy, the payoff asymmetry, or whether the P&L survives costs.
- **Treating an adaptive model as self-correcting.** It re-learns *after* taking
  the losses that taught it. In a regime shift it will confidently size on a
  relationship that has already inverted. Keep the circuit breaker upstream of
  it.

## Verification

- **LMS step, closed form.** From $w = 0$ with $x = [2, -1]$, $y = 3$,
  $\eta = 0.1$, no leakage: $\hat y = 0$, $e = 3$, $w = [0.6, -0.3]$, and
  `step_ratio` $= \eta\lVert x\rVert^2 = 0.5$.
- **NLMS defining property.** With $\mu = 1$ and $\epsilon \to 0$, one step makes
  the a posteriori error exactly zero — $w^{\mathsf T}x = y$ — at feature scales
  $10^{-3}$, $1$ and $10^{3}$ alike. Confirm `step_ratio` stays at $\mu$ across
  those scales while the LMS ratio moves by four orders of magnitude, and that
  $\mu \ge 2$ is refused at construction.
- **RLS defining property.** With $\lambda = 1$ and a diffuse $P_0$, RLS must
  equal the ordinary least-squares fit of everything seen. Check it against the
  $2\times2$ normal equations solved independently by Cramer's rule; the residual
  is the prior's weight $1/c$.
- **Tracking a regime shift.** Stream 300 samples of $y = 2x_0 - 1.5x_1$ then 300
  of $y = -x_0 + 3x_1$. RLS at $\lambda = 0.95$ must recover $[-1, 3]$ to within
  0.01; RLS at $\lambda = 1$ — pooled least squares — must remain more than 0.5
  away, because it is still fitting both regimes at once.
- **Effective memory.** $1/(1-\lambda)$ is 100 at $\lambda = 0.99$, 20 at 0.95,
  infinite at 1.0, and `None` for the gradient rules rather than a fabricated
  number.
- **Covariance windup (regression).** With $x = 0$ the gain is zero and
  $P \leftarrow P/\lambda$ each step, so the trace grows geometrically. Confirm
  the trace stays under the limit over 500 steps at $\lambda = 0.9$ while the
  unguarded value would reach $2 \times 10^3 \cdot 0.9^{-500}$, and that a trace
  limit below the initial trace is refused at construction rather than silently
  freezing $P$ from sample 1.
- **Non-finite input (regression).** A NaN or Inf feature, a NaN or Inf target, a
  non-numeric entry, and an overflowing step ($x = 10^{200}$, $y = 10^{200}$)
  must each raise `OnlineLearningError` with the previous weights **unchanged**.
  Against the pre-2.0 implementation every one of these silently set all weights
  to NaN.
- **Horizon gate.** `label_ready_time > now` must raise and leave
  `total_samples` at zero; `label_ready_time == now` must be accepted; supplying
  only one of the pair must raise rather than quietly skipping the check.
- **Buffer semantics.** Release is FIFO and inclusive at the boundary; backdated
  and out-of-order label times are refused; the queue refuses to exceed
  `max_pending`.
- **Page-Hinkley, hand-evaluated.** For $x = [1,1,1,4]$, $\delta = 0$: the
  statistic is 0 for the first three observations and exactly 2.25 on the fourth.
  Confirm a stationary stream never signals, a step increase does, a *decrease*
  never does (the test is one-sided), and that detection auto-resets so one
  change yields one signal.
- **Bounded memory (regression).** After 20,000 updates the retained baseline is
  exactly `baseline_window` errors and the recent window exactly `recent_window`.
  The pre-2.0 implementation retained all 20,000.
- **Audit honesty (regression).** A zero baseline MAE must report a 0.0 change,
  not the $-1{,}000{,}000\%$ the pre-2.0 divisor floor produced; the report's
  `weights` must be a copy, not the live list the pre-2.0 short-circuit returned.
- **State round-trip.** Through `json.dumps`/`loads`, for all three rules, the
  restored model must reproduce predictions exactly and continue learning to
  identical weights. Mismatched feature counts, unknown `state_version`, missing
  keys and NaN weights must all be refused.
- **Determinism and purity.** Two identical runs give identical weights for all
  three rules; `predict()` mutates nothing; mutating the caller's feature list
  after an update cannot reach model state.
- Run `python -m unittest discover -s skills/online-learning-for-adaptive-signal-models/scripts`
  and confirm a 100% pass rate.

## Related Skills

- `lookahead-bias-elimination`
- `feature-engineering-without-leakage`
- `model-staleness-detection`
- `concept-drift-vs-staleness-differentiation`
- `offline-train-online-infer-deployment`
- `model-versioning-and-rollback`
- `regime-detection-for-strategy-switching`
- `kill-switch-and-drawdown-circuit-breakers`
