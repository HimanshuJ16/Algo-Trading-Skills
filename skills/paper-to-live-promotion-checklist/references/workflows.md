# Deep Workflow Reference — paper-to-live-promotion-checklist

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

### 0. Decide whether `slippage_alignment` can mean anything here

Do this **before** running the gate, because it determines how much weight the result
carries.

Paper trading does not transact. Whatever `avg_slippage` you record is an output of the
fill simulator, not of the market. Ask:

- Does the paper fill path use *observed* market data — touched quotes, depth, queue
  position, measured venue latency — or does it apply a cost model?
- If a cost model: is it the *same* model the backtest used?

If both paper and backtest slippage come from one shared model, `slippage_alignment` is
tautologically satisfied and evidences nothing about live execution. Record that in the
sign-off note, treat the check as a plumbing assertion, and get your execution-realism
evidence from `demo-account-realism-gap-assessment` and `execution-realistic-simulation`
instead. This is the most common way a fully green promotion gate certifies nothing.

### 1. Assemble and validate the package

`PaperToLivePromotionGate.evaluate_gate(paper_stats, backtest_stats)` requires:

| Payload | Required keys | Type / range |
|---|---|---|
| `paper_stats` | `days_run` | non-negative `int` (calendar or trading days — your convention) |
| | `trades_count` | non-negative `int` |
| | `avg_slippage` | finite `float`, caller-defined units; may be negative (flagged) |
| | `signal_accuracy` | `float` in `[0, 1]` |
| | `risk_controls_triggered` | non-negative `int` |
| | `reauth_cycles_survived` | non-negative `int` |
| | `accuracy_sample_size` *(optional)* | non-negative `int`; defaults to `trades_count` |
| `backtest_stats` | `modeled_slippage` | finite `float` **> 0**, same units as `avg_slippage` |
| | `walk_forward_accuracy` | `float` in `[0, 1]` |

Anything missing, non-finite, negative where a count is expected, `bool` where an `int` is
expected, or outside `[0, 1]` where a proportion is expected raises `ValueError`. That is
deliberate: a corrupt input must never be reported as a failed criterion, because the two
route to different teams.

### 2. Evaluate the six criteria

| Check | Rule | Default | Semantics |
|---|---|---|---|
| `min_paper_duration` | `days_run >= min_days` | 20 | inclusive floor |
| `min_trades_count` | `trades_count >= min_trades_count` | 30 | inclusive floor |
| `slippage_alignment` | `abs(paper - modelled) / modelled <= tol` | 0.15 | **relative**, two-sided |
| `accuracy_alignment` | `abs(paper - walk_forward) <= tol` | 0.10 | **absolute percentage points**, two-sided |
| `risk_controls_exercised` | `risk_controls_triggered > 0` | ≥ 1 | count of firings only |
| `auth_reauth_survived` | `reauth_cycles_survived >= 1` | ≥ 1 | |

All six are conjunctive. `report.failed_checks` diagnoses *why*; 5/6 is exactly as rejected
as 0/6. There is no waiver path and no override flag, deliberately.

Two traps worth restating:

- **The two tolerances share a suffix and not a meaning.** `slippage_tolerance_pct` is a
  ratio of the modelled value; `accuracy_tolerance_pct` is an additive band in accuracy
  units. The naming is a 1.x artefact kept for API compatibility.
- **`slippage_alignment` is two-sided.** Paper slippage far *below* the model fails. That
  is intended: an optimistic fill simulator is a worse problem than a pessimistic one,
  because it flatters the strategy in exactly the dimension live trading will punish.

### 3. Read the sampling-noise advisory before reading `accuracy_alignment`

`report.advisories` carries a note whenever the configured accuracy tolerance is narrower
than the two-sided 95% binomial half-width at the supplied sample size:

```
half_width = 1.96 * sqrt(p * (1 - p) / n)      # p = walk_forward_accuracy, n = sample size
```

At the shipped defaults (`p = 0.56`, `n = 30`) that is:

```
SE         = sqrt(0.56 * 0.44 / 30)  = 0.0906   ->  9.06 pp
half_width = 1.96 * 0.0906           = 0.1776   -> 17.76 pp
```

The 10pp band sits well inside 17.76pp. Consequences, both real:

- A strategy whose true accuracy **equals** the walk-forward value fails
  `accuracy_alignment` about **27%** of the time (exact binomial, `p = 0.56`, `n = 30`).
- A strategy whose accuracy has genuinely degraded by 10pp is not statistically
  distinguishable from one that has not.

So at n=30 this check is a coin-weighted formality in both directions. Options, in
preference order: raise `min_trades_count` until the half-width is inside your tolerance
(≈100 observations for a 10pp band at p≈0.56); supply `accuracy_sample_size` if accuracy is
measured per-bar over far more observations than there are trades; or widen the tolerance
and record that you did, so `policy_weakened` names it. The advisory annotates the report
and never changes `approved`.

### 4. Verify the operational criteria are real

The engine takes `risk_controls_triggered` and `reauth_cycles_survived` as given. The human
work behind those two integers:

- **Risk controls** — for each control in scope (position limit, drawdown limit,
  correlation-cluster limit, kill switch), confirm from the paper-trading logs that it fired
  and that the **observed response matched the intended design**. A control that fired and
  responded wrongly increments the same counter as one that worked. Engineer the trigger
  deliberately if the market did not supply one — see
  `position-limit-breach-simulation-fire-drills` and `risk-control-unit-testing-framework`.
- **Reauth** — confirm a real or forced token expiry was survived without dropped orders or
  a stuck session (`token-lifecycle-live-probing`).
- **Supervision** — confirm at least one restart was handled without state corruption
  (`systemd-supervision-for-trading-bots`).
- **Idempotency** — confirm a simulated submit-timeout/retry did not produce a duplicate
  order (`order-placement-idempotency`).

### 5. Record the sign-off

```python
report = gate.evaluate_gate(paper_stats, backtest_stats)
if report.approved:
    report.record_sign_off(
        reviewer_id="risk.officer@example.com",
        initial_live_sizing_pct=0.10,          # YOUR decision, not the engine's
        rollback_drawdown_pct=0.05,            # the rollback trigger you commit to
        decided_at="2026-08-27T09:30:00+00:00",
    )
assert report.is_authorised
```

- `record_sign_off` refuses a rejected report, a blank reviewer, a sizing outside `(0, 1]`
  and a blank timestamp.
- `decided_at` is caller-supplied rather than read from the system clock so that a report
  regenerated from stored inputs reproduces byte-for-byte.
- `initial_live_sizing_pct` is **recorded, never recommended**. The ramp schedule belongs to
  `incremental-capital-deployment-for-new-strategies`. A sizing of `1.0` is accepted and
  adds a "no reduced-size window" advisory, because full-size day one is a decision that
  should be visible in the record.
- Persist the whole report, including `policy_applied` and `policy_weakened`. A stored
  `PROMOTION APPROVED` without its thresholds is not an audit record: a gate configured with
  `min_days=0, min_trades_count=0` emits the identical string.

### 6. Review the initial live window

`check_rollback_trigger(live_stats, paper_baseline)` — both payloads need
`max_drawdown_pct` (a **non-negative magnitude**; 8% is `0.08`) and `avg_slippage`.

```
drawdown threshold = max(rollback_drawdown_floor, rollback_drawdown_multiple * paper_dd)
slippage threshold = max(rollback_slippage_floor, rollback_slippage_multiple * paper_slip)
```

Defaults: multiples 2.0, drawdown floor 0.05, slippage floor 0.005. **Both floors are house
heuristics and are instrument-dependent** — 50 bps of slippage is noise in a small cap and
enormous in a front-month index future. Override them per strategy; whatever you set is
recorded in `policy_applied`.

Both rules are evaluated, so a simultaneous breach reports both reasons. Each reason names
the threshold **actually applied** and which rule bound it (`absolute floor` vs
`2x paper baseline`), because the floor frequently overrides the multiple and quoting the
multiple in that case puts a number in the audit trail that was never used.

This is a **review** trigger, not a kill switch. It returns a decision; it stops nothing.
Real-time protection is `kill-switch-and-drawdown-circuit-breakers`, which must remain
structurally independent of this module.

### 7. Treat a material change as a new deployment

Per the ESMA supervisory briefing (Feb 2026) and SEBI §7.1.9.5, retest and re-gate when
decision logic, execution behaviour (order types, slicing, routing), scope (new instruments,
venues or asset classes), risk-control thresholds or kill-switch logic, external
dependencies (data feeds, third-party providers) or ML components change — and watch for a
series of small recalibrations accumulating into a material change unnoticed. This engine is
stateless and has no re-promotion tracking; that bookkeeping is yours.

## Known Failure Modes

- **Tautological slippage check.** Paper fills and the backtest share one cost model, so
  `slippage_alignment` passes by construction and the promotion certifies nothing about
  live execution.
- **Free-execution backtest.** A `modeled_slippage` of zero. In 1.x this *passed*
  `slippage_alignment` unconditionally; it now raises.
- **Silently defaulted metrics.** A `paper_stats` dict missing `reauth_cycles_survived`
  produced the audit line "Survived 0 cycles" — a fabricated observation indistinguishable
  from a real one.
- **Sign-flipped drawdown.** An 8% drawdown reported as `-0.08` suppressed every rollback
  check; the worse the loss, the quieter the trigger.
- **Informal "feels ready" promotion.** Flipping to live capital with no quantitative
  evaluation of the paper period, leaving `reviewer_id` blank.
- **Unexercised risk controls.** Promoting a bot whose kill switch has never fired in this
  deployment — integrated, not proven.
- **Separate paper-mode codebase.** Paper trading on a simplified code path, reintroducing
  train/serve skew at go-live.
- **Full capital on day one.** Skipping the reduced-size window and discovering live
  execution quirks at full size.
- **Accuracy theatre.** Reading a ±10pp band over 30 trades as evidence of parity.
- **Reaching for the deprecated helper.** `evaluate_promotion_gate` checks 4 of 6 criteria
  and treats its single `tolerance` argument relatively for slippage and absolutely for
  accuracy.

## Production Implementation Reference

- Reference code: `scripts/promotion_gate.py` — `PaperToLivePromotionGate`,
  `PromotionDecisionReport`, `GateCheckResult`, and the deprecated `evaluate_promotion_gate`.
- Automated unit tests: `scripts/test_promotion_gate.py`
  (`python -m unittest discover -s skills/paper-to-live-promotion-checklist/scripts`).
- Regulatory citations and what is *not* claimed: `references/standards.md`.
