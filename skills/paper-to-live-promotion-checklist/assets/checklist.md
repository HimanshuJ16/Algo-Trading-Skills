# Pre-Flight / Sign-off Checklist — paper-to-live-promotion-checklist

Work top to bottom. The first section decides whether the gate's result is worth anything;
run it before the gate, not after.

## 0. Is the comparison meaningful?

- [ ] **Paper code path is the live code path.** Same signal generation, same order logic,
      same risk checks; only the final broker submission is redirected. Not a separate
      "paper mode" implementation.
- [ ] **Paper fill path is independent of the backtest cost model.** If both slippage
      numbers come from one shared model, `slippage_alignment` is tautological — record
      that fact and do not treat a pass as execution evidence.
- [ ] **Units and conventions agreed and written down:** `days_run` calendar vs trading;
      `avg_slippage` and `modeled_slippage` in the same units; accuracies as proportions in
      `[0, 1]`; `max_drawdown_pct` as a non-negative magnitude.
- [ ] **The paper window covered more than a calm stretch** — at least one meaningfully
      volatile session, and the range of conditions the backtest was validated against.
      Nothing in the engine measures this; assert it here or not at all.

## 1. Gate evaluation

- [ ] **Every required key supplied.** `paper_stats`: `days_run`, `trades_count`,
      `avg_slippage`, `signal_accuracy`, `risk_controls_triggered`,
      `reauth_cycles_survived`. `backtest_stats`: `modeled_slippage` (> 0),
      `walk_forward_accuracy`. No key is defaulted — a missing one raises.
- [ ] **`evaluate_gate()` returns `approved=True` with `failed_checks == []`** across all
      six criteria.
- [ ] **`policy_applied` reviewed.** The thresholds actually used are recorded in the
      report, not assumed from the defaults.
- [ ] **`policy_weakened` reviewed and accepted.** Any threshold set more permissively than
      the shipped default is named there. An empty list means the strict defaults applied.
- [ ] **`advisories` read.** In particular the sampling-noise advisory: at 30 trades the
      10pp accuracy band sits inside a ±17.8pp 95% interval, so a pass is not evidence of
      parity. Raise `min_trades_count`, supply `accuracy_sample_size`, or widen the
      tolerance and record it.

## 2. Operational evidence behind the two counters

- [ ] **Each risk control fired at least once during the paper period** — naturally or via
      an engineered trigger — **and its observed response matched its intended design.**
      The engine counts firings only.
- [ ] **At least one broker token expiry / reauth cycle survived** with no dropped orders
      or stuck session.
- [ ] **At least one process restart handled** without state corruption.
- [ ] **Order idempotency exercised** against a simulated submit-timeout/retry with no
      duplicate order.

## 3. Sign-off (the discrete step)

- [ ] **`record_sign_off()` called** with a real `reviewer_id`, an
      `initial_live_sizing_pct` in `(0, 1]`, a committed `rollback_drawdown_pct`, and a
      caller-supplied `decided_at`.
- [ ] **`report.is_authorised` is True.** `approved` alone authorises nothing.
- [ ] **The initial live sizing is the reviewer's decision**, taken with
      `incremental-capital-deployment-for-new-strategies` — the engine records it and does
      not recommend one. A sizing of `1.0` is accepted but flagged as "no reduced-size
      window"; confirm that is deliberate.
- [ ] **The whole report persisted**, including `policy_applied`, `policy_weakened`,
      `advisories`, `failed_checks` and the sign-off fields. A stored `PROMOTION APPROVED`
      without its thresholds is not an audit record.
- [ ] **Where in scope:** the sign-off is evidence of, not a substitute for, the MiFID II
      RTS 6 Article 5 authorisation by a person designated by senior management, and it does
      not discharge SEBI exchange conformance / simulated-environment obligations. See
      `references/standards.md`.

## 4. Initial live window and rollback

- [ ] **Rollback thresholds set per strategy**, not left at the instrument-agnostic house
      floors (drawdown 5%, slippage 0.005). Whatever is set appears in `policy_applied`.
- [ ] **`check_rollback_trigger()` scheduled** on a shorter cadence than steady-state
      review, with a written response — who is paged, what gets flattened, who decides to
      return to paper.
- [ ] **Understood as a review trigger, not a kill switch.** Real-time protection is
      `kill-switch-and-drawdown-circuit-breakers`, structurally independent of this module.
- [ ] **Re-gating rule agreed:** a material change — decision logic, execution behaviour,
      scope, risk-control thresholds, kill-switch logic, data feeds, ML retraining — is a
      new deployment requiring a new paper period and a new gate, including a series of
      small recalibrations that accumulate into one.

## 5. Automated verification

- [ ] `python -m unittest discover -s skills/paper-to-live-promotion-checklist/scripts`
      passes 100%.
- [ ] The deprecated `evaluate_promotion_gate` is **not** used in production code — it
      checks 4 of the 6 criteria and applies its single `tolerance` relatively for slippage
      and absolutely for accuracy.

## Sign-off

- Strategy / version: ___________________________
- Reviewed by (`reviewer_id`): ___________________________
- Date (`decided_at`): ___________________________
- Initial live sizing (`initial_live_sizing_pct`): ___________________________
- Rollback trigger committed (`rollback_drawdown_pct`): ___________________________
- Is `slippage_alignment` independent of the backtest cost model? (Y/N): _______________
- Thresholds weakened from defaults, if any: ___________________________
