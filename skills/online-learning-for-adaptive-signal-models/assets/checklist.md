# Pre-Flight Checklist — Online Learning for Adaptive Signal Models

## Label horizon

- [ ] Is `label_ready_time` the timestamp at which the target becomes **observable** (close of bar $t+h$), not the timestamp at which you computed it?
- [ ] Are `label_ready_time` and `now` both passed to `update()`? (Passing one alone raises; passing neither disables the check entirely.)
- [ ] Are feature vectors held in a `LabelHorizonBuffer` between prediction and update, rather than updated inline?
- [ ] Is `pending_count` monitored? A queue that only grows means the outcome feed has stalled.

## Rule and parameters

- [ ] Is the update rule chosen from the problem — tracking a moving coefficient (`"rls"`), unscaled features (`"nlms"`), stationary and z-scored (`"lms"`) — rather than by default?
- [ ] For `"lms"`: has $\eta\lVert x\rVert^2$ been measured on **this instrument's** features, and does it stay well below 2?
- [ ] Is `unstable_step_count` zero, or understood if not?
- [ ] For `"rls"`: was $\lambda$ derived from $T_0 = 1/(1-\lambda)$ and the horizon being tracked, rather than picked for looking reasonable?
- [ ] For `"rls"`: is `rls_max_covariance_trace` comfortably above the initial trace $n \cdot c$? (At or below it, the constructor refuses.)
- [ ] Has `max_weight_norm` been set from your own feature scaling and exposure limits, rather than left at the placeholder 10.0?
- [ ] If `l2_penalty` is non-zero under `"rls"`, is it understood that it is ignored and only logged?

## Data integrity

- [ ] Are non-finite ticks rejected upstream as well, so the model raising is the second line of defence and not the first?
- [ ] Is `OnlineLearningError` caught and routed somewhere a human sees it, rather than swallowed in the tick loop?
- [ ] Is the target centred, or is a constant feature appended? (The model has no intercept.)
- [ ] Are the feature components on comparable scales?

## Drift

- [ ] Are Page-Hinkley `delta` and `threshold` set from an **observed** baseline error scale, not copied from a library example?
- [ ] Has the detector been confirmed to fire on a replayed historical regime shift? (A detector that never fires reads as reassurance.)
- [ ] Is the drift response defined — `reset_covariance_on_drift` for RLS, escalation otherwise — before the first signal, not after?
- [ ] Is the halting decision routed through `kill-switch-and-drawdown-circuit-breakers` rather than implemented here?
- [ ] Is the *cause* classified (`concept-drift-vs-staleness-differentiation`) before remediating?

## Operations

- [ ] Is `to_state()` checkpointed on a schedule **and** at shutdown?
- [ ] Does startup load the checkpoint, and does it alert rather than proceed silently when there is none? (Zero weights predict exactly 0.0, which a sizer reads as "no edge".)
- [ ] Are checkpoints versioned so "which weights traded at 14:32" is answerable? (See `model-versioning-and-rollback`.)
- [ ] Is the model updated from a single thread?

## Reading the audit

- [ ] Is `sufficient_samples` true before `is_converged` is read at all?
- [ ] Is `is_converged` treated as a smoke alarm rather than a statistical claim? (No significance test, no confidence bound.)
- [ ] Is a falling MAE being distinguished from a profitable signal? (MAE is blind to sign accuracy and payoff asymmetry.)
- [ ] Are `clipped_update_count`, `unstable_step_count`, `covariance_frozen_count` and `drift_detection_count` reviewed alongside it?

## Automated testing

- [ ] Run `python -m unittest discover -s skills/online-learning-for-adaptive-signal-models/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
