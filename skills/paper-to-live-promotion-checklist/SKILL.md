---
name: paper-to-live-promotion-checklist
description: >-
  Use as the final gate before a strategy routes real orders with real capital, scoring
  six conjunctive criteria including paper duration, trade count and slippage alignment.
  Every input is an observation you supply, not a verification.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: deployment-ops
  tags: deployment-ops, paper-trading, promotion-gate, go-live-readiness, strategy-governance, rollback-planning, human-sign-off
  brokers_frameworks: "MiFID II RTS 6 (EU) 2017/589; SEBI Master Circular for Stock Exchanges (Ch. 2); Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this as the final gate before any strategy or model routes real orders with real capital: after backtest validation, after `new-strategy-onboarding-checklist`, and before enabling live order placement. A strategy passing backtest validation is necessary but not sufficient — backtests cannot capture execution realities, broker-specific quirks, or the operational pressure of live capital, so a defined paper-trading period with explicit pass/fail criteria is a separate gate, not a formality.

The engine enforces **six conjunctive criteria**, all of which must pass:

1. **`min_paper_duration`** — the paper period ran at least `min_days` (default 20).
2. **`min_trades_count`** — it produced at least `min_trades_count` trades (default 30).
3. **`slippage_alignment`** — realised paper slippage is within a **relative** tolerance of modelled backtest slippage (default 15%).
4. **`accuracy_alignment`** — paper signal accuracy is within an **absolute** tolerance of walk-forward accuracy (default 10 percentage points).
5. **`risk_controls_exercised`** — at least one risk control actually fired during the period.
6. **`auth_reauth_survived`** — at least one broker token expiry/reauth cycle was survived.

The value it adds is not arithmetic — six comparisons need no engine. It is that the six claims are captured **together**, against **thresholds recorded in the report itself**, plus a sign-off that is a discrete step, in an artefact you can hand a reviewer six months later and reproduce.

## When NOT to Use

- **As verification of anything.** Every input is an **observation supplied by the caller**. `risk_controls_triggered=2` means somebody typed 2. The engine never opens the paper-trading logs, re-runs the backtest, queries the broker, or checks whether each control's *response* matched its design. Supply each figure from whoever owns that system.
- **As the deployment authorisation.** `approved=True` is the machine verdict alone; `is_authorised` additionally requires a recorded sign-off. For an EU or UK investment firm, MiFID II RTS 6 Article 5 requires that a person designated by the firm's senior management authorise the deployment or substantial update of an algorithmic trading system. This report is an input to that human decision, never a replacement for it.
- **As a pre-trade control or a kill switch.** The gate cancels no order and caps no position. `check_rollback_trigger` is a *review* trigger for the initial live window, evaluated on whatever cadence you choose. Real-time protection is `kill-switch-and-drawdown-circuit-breakers`, which must stay structurally independent of strategy logic.
- **When `slippage_alignment` would be tautological.** Paper fills are *simulated*. If the paper fill simulator shares its slippage model with the backtest — the usual case when both live in one codebase — this check compares a model to itself and passes while proving nothing about live execution. See Common Pitfalls.
- **For sizing the initial live allocation or the capital ramp.** This gate answers "may this begin?". How much and on what ramp is `incremental-capital-deployment-for-new-strategies`.
- **For steady-state divergence monitoring after the initial live window.** That is `backtest-vs-live-performance-divergence-tracking`.
- **As a substitute for exchange conformance testing.** SEBI-regulated brokers must test in the exchange's simulated environment before putting new or changed software in use and participate monthly thereafter; RTS 6 Articles 6–7 require conformance testing in an environment separated from production. Those are venue obligations. This gate is an internal *strategy-performance* control that sits alongside them, not instead of them.

## Prerequisites

- A backtested strategy that has already passed `lookahead-bias-elimination`, `walk-forward-validation-setup`, and `execution-realistic-simulation` checks.
- Paper-trading infrastructure that runs the **exact same code path** as live trading (same signal generation, same order-placement logic, same risk checks) with only the final broker order submission redirected to a simulated fill — not a separate, simplified "paper mode" implementation, which reintroduces the train/serve-skew risk `offline-train-online-infer-deployment` describes for ML models.
- `paper_stats` containing every key in `REQUIRED_PAPER_KEYS`: `days_run`, `trades_count`, `avg_slippage`, `signal_accuracy`, `risk_controls_triggered`, `reauth_cycles_survived`. Optionally `accuracy_sample_size`.
- `backtest_stats` containing `modeled_slippage` (strictly positive) and `walk_forward_accuracy`.
- **Five caller conventions the engine cannot verify:**
  - `days_run` may be calendar or trading days — pick one and keep it. 20 calendar days is roughly 14 trading days.
  - `signal_accuracy` and `walk_forward_accuracy` are proportions in `[0, 1]`. `58` for 58% is rejected as a unit error, not treated as a divergence.
  - `signal_accuracy` may be a per-trade hit rate or a per-bar directional accuracy. When it is not measured over `trades_count`, supply `accuracy_sample_size` so the sampling-noise advisory is computed against the right *n*.
  - Slippage units are caller-defined (fraction of price, bps, currency) — the same units must produce `avg_slippage` and `modeled_slippage`, since the check is a ratio.
  - `max_drawdown_pct` is a **non-negative magnitude**: report an 8% drawdown as `0.08`. `-0.08` is rejected.
- Thresholds you are willing to defend. **The defaults are house heuristics, not standards** — no regulator prescribes a paper-trading duration, a trade count, or a performance tolerance. See `references/standards.md`.

## Workflow

1. **Validate the paper-trading package before trusting any criterion.**
   - Missing keys, non-finite metrics, negative counts, bools-as-counts, accuracies outside `[0, 1]` and a non-positive `modeled_slippage` all raise `ValueError`. The gate does not return a verdict on corrupt input.
   - **Decision point — corrupt input is a data failure, not a strategy failure.** Every criterion is a comparison, and every comparison against `NaN` is `False`, so a corrupt metric used to surface as `PROMOTION REJECTED`: indistinguishable from a genuinely unready strategy and routed to the wrong team.
2. **Run the paper period long enough to be worth measuring, then evaluate duration and trade count.**
   - **Decision point — duration is not coverage.** Twenty quiet days prove the plumbing works, not that the strategy survives stress. The paper window must cover the range of conditions the backtest was validated against and include at least one meaningfully volatile session; nothing in this engine measures that, so state it in the sign-off. Regime coverage of the *backtest* is `multi-year-regime-coverage-requirement`.
   - **Decision point — the two floors are inclusive.** Exactly 20 days and exactly 30 trades pass; 19 and 29 do not.
3. **Compare execution against the backtest's model, and decide first whether the comparison means anything.**
   - **Decision point — is the paper fill path independent of the backtest cost model?** If the same slippage model produces both numbers, `slippage_alignment` is satisfied by construction. It is informative only when paper fills are driven by *observed* market data — touched quotes, queue position, real venue latency. If they are not, record that in the sign-off rather than reading the green tick as evidence, and consult `demo-account-realism-gap-assessment`.
   - **Decision point — a divergence is a model defect, not bad luck.** Paper slippage materially off the model means the backtest's execution model (or, for ML signals, train/serve parity) needs revisiting before promotion.
   - The check is **two-sided and relative**: paper slippage far *below* the model fails too, because that is usually an optimistic simulator rather than an edge.
4. **Compare signal accuracy — and read the sampling-noise advisory before reading the result.**
   - **Decision point — the default band is narrower than its own noise floor.** At the gate's own `min_trades_count` of 30, the binomial standard error on a hit rate near 0.56 is about 9.1pp, so the two-sided 95% band is roughly ±17.8pp. A perfectly aligned strategy fails the 10pp check about 27% of the time at that sample size, and a genuine 10pp degradation is not distinguishable from noise. `evaluate_gate` computes this half-width and emits an advisory whenever the tolerance sits inside it. Raise `min_trades_count`, widen the tolerance, or treat the check as diagnostic — but never read a pass at n=30 as evidence of parity.
   - The tolerance is **absolute percentage points**, unlike the relative slippage tolerance. The shared `_pct` suffix is a 1.x naming artefact retained for compatibility.
5. **Verify risk controls were actually exercised, naturally or via engineered test conditions.**
   - **Decision point — a count is not a proven control.** A control that never fired in this deployment has been integrated, not proven; but a control that fired and responded wrongly also produces `risk_controls_triggered=1`. The count clears the gate; the human review confirms the responses matched design. See `risk-control-unit-testing-framework` and `position-limit-breach-simulation-fire-drills`.
6. **Verify operational reliability.** Process supervision (`systemd-supervision-for-trading-bots`) survived a restart without state corruption, broker auth (`token-lifecycle-live-probing`) survived a real expiry/reauth cycle, and order idempotency (`order-placement-idempotency`) was exercised against a simulated timeout/retry.
7. **Record the human sign-off as a discrete step** via `record_sign_off(reviewer_id, initial_live_sizing_pct, rollback_drawdown_pct, decided_at)`.
   - **Decision point — `approved` is not `is_authorised`.** The engine refuses to stamp a rejected report and refuses a blank reviewer. `decided_at` is caller-supplied rather than read from the clock so a report regenerated from stored inputs reproduces byte-for-byte.
   - **Decision point — the engine records a sizing, it does not recommend one.** 1.x shipped a `0.25` default that nothing read and that contradicts the 10% seed tier of `incremental-capital-deployment-for-new-strategies`; that skill owns the ramp. A sizing of `1.0` is accepted and flagged, because going live at full target size is a decision, not a default.
   - **Decision point — the verdict is meaningless without `policy_applied`.** A gate configured with `min_days=0, min_trades_count=0` passes anything and emits the identical `PROMOTION APPROVED` string as the strict default. Every report therefore embeds the thresholds applied, and `policy_weakened` names any threshold set more permissively than the shipped default. A stored verdict without its policy snapshot is not an audit record.
8. **Review the initial live window on a shorter cadence than steady state**, calling `check_rollback_trigger(live_stats, paper_baseline)`. It fires at `max(floor, multiple × paper baseline)` for drawdown and slippage, evaluates both rules so simultaneous breaches both appear, and names the threshold actually applied together with which rule bound it.
   - **Decision point — a material change restarts this gate.** ESMA's 2026 supervisory briefing treats altering decision logic, execution behaviour, risk-control thresholds, kill-switch logic, data feeds or ML retraining as a material change requiring retesting, and warns that accumulated small recalibrations can amount to one. A change to a promoted strategy is a new deployment, not a continuation.

> Full procedure: see `references/workflows.md`.
> Standards reference and citations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reading a `slippage_alignment` pass as execution evidence when paper fills and the backtest share a cost model.** The check then compares a model to itself. This is the single most common way a green promotion gate certifies nothing.
- **Trusting a zero-cost backtest.** Before validation existed, `modeled_slippage=0.0` made `slippage_alignment` pass *unconditionally* — the division was guarded by `if bt_slip > 0 else 0.0` — so the free-execution backtest this gate exists to catch was the one input guaranteed to clear it. A non-positive or `NaN` modelled slippage now raises.
- **Letting an absent metric become an observation.** 1.x read every field with `dict.get(key, default)`: an omitted `reauth_cycles_survived` produced the audit line "Survived 0 cycles", and an omitted `walk_forward_accuracy` invented a 0.50 baseline to compare against. Missing keys now raise and name the key.
- **Reporting a drawdown as a negative number.** `-0.08` for an 8% drawdown failed every `>=` comparison in the rollback trigger, so the worse the loss the more certainly the trigger stayed quiet. Magnitudes only.
- **Treating a 10pp accuracy band as a meaningful test at 30 trades.** It sits inside its own sampling noise. Read `report.advisories`.
- **Confusing the two tolerance parameters.** `slippage_tolerance_pct` is relative; `accuracy_tolerance_pct` is absolute percentage points. Same suffix, different meaning.
- **Treating a separately-implemented "paper mode" as equivalent validation.** Differences between the paper and live code paths reintroduce exactly the skew this checklist exists to catch.
- **Promoting because the duration elapsed**, without checking that risk controls were exercised or that performance matched backtest expectations for the conditions actually observed.
- **Skipping a reduced-size initial live period**, removing the chance to catch live-specific issues — real slippage, real latency, real broker quirks — at lower stakes.
- **Treating sign-off as a formality.** A verbal "looks good, ship it" leaves `reviewer_id` blank and `is_authorised` false, and it is not the RTS 6 Article 5 authorisation.
- **Reaching for `evaluate_promotion_gate`.** The deprecated 1.x helper checks only 4 of the 6 criteria — it ignores trade count and reauth entirely — and applies its single `tolerance` argument relatively for slippage and absolutely for accuracy.
- **Not defining a rollback plan in advance**, so a divergence is answered by improvisation under pressure rather than a predefined trigger and a predefined response.

## Verification

- Instantiate `PaperToLivePromotionGate()` and evaluate the compliant fixture (25 days, 45 trades, slippage 0.00105 vs modelled 0.00100, accuracy 0.58 vs walk-forward 0.56, 2 risk triggers, 3 reauth cycles) $\implies$ `approved` True, six checks, `failed_checks` empty, `policy_weakened` empty, `type(c.passed) is bool` on every check, and `is_authorised` **False** until sign-off.
- Isolation: each of `days_run=10`, `trades_count=5`, `risk_controls_triggered=0`, `reauth_cycles_survived=0`, `signal_accuracy=0.20`, `avg_slippage=0.005` must fail *only* its own check.
- Boundaries are inclusive floors: exactly 20 days, 30 trades, 1 risk trigger and 1 reauth cycle pass; 19, 29, 0 and 0 fail.
- Tolerance semantics: accuracy 0.20 → 0.28 (8pp absolute, 40% relative) must **pass**, pinning the absolute reading; slippage 0.0001 → 0.0002 (0.0001 absolute, 100% relative) must **fail**, pinning the relative reading.
- Regressions that must now **raise** `ValueError`: `modeled_slippage` of `0.0`, a negative, or `NaN`; any missing key in `paper_stats` or `backtest_stats`; `NaN`/`Inf` on any metric; `True` supplied as a count; `signal_accuracy=58`; a negative or whole-number `max_drawdown_pct`.
- Rollback attribution: with a 2% paper baseline, an 8% live drawdown must report an applied threshold of **5.00%** "bound by absolute floor" — not the 4.00% that 1.x quoted; with a 10% baseline, a 21% live drawdown must report 20.00% "bound by 2x paper baseline". A simultaneous drawdown and slippage breach must report both reasons.
- Sampling advisory: at n=30 the report must carry an advisory naming `n=30` and the independently derived 95% half-width `1.96·sqrt(p(1-p)/n)` ≈ 17.8pp; at n=400 it must be absent; the advisory must never change `approved`.
- Sign-off: `record_sign_off` on a rejected report raises and names the failed check; a blank reviewer, a sizing outside `(0, 1]`, or a blank `decided_at` raises; a sizing of `1.0` is accepted and adds a "no reduced-size window" advisory.
- Auditability: a gate configured `min_days=0, min_trades_count=0` must still return `approved` True for an unfit strategy, with both thresholds named in `policy_weakened` and recorded in `policy_applied`.
- Run `python -m unittest discover -s skills/paper-to-live-promotion-checklist/scripts` and confirm a 100% pass rate.

## Related Skills

- `new-strategy-onboarding-checklist`
- `incremental-capital-deployment-for-new-strategies`
- `execution-realistic-simulation`
- `demo-account-realism-gap-assessment`
- `backtest-vs-live-performance-divergence-tracking`
- `kill-switch-and-drawdown-circuit-breakers`
- `risk-control-unit-testing-framework`
- `systemd-supervision-for-trading-bots`
- `order-placement-idempotency`
- `token-lifecycle-live-probing`
