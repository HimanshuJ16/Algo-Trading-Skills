# Workflows for Incremental Capital Deployment

The engine resolves each evaluation in a fixed order, and **each branch is terminal**.
Order matters: emergency outranks maintenance, and maintenance outranks promotion, so a
strategy can never be promoted in the same evaluation in which it breached a limit.

## 0. Input Validation (before any gate is evaluated)

Reject rather than compare. Every gate is a threshold test, so an unusable input does
not produce a wrong answer loudly — it produces a *safe-looking* answer silently.

- **Non-finite values** (`NaN`, `±Inf`) on drawdown, Sharpe, slippage, or capital.
  `float('nan') >= 12.0` is `False`, so a `NaN` drawdown skips the emergency demotion
  and the strategy retains its full allocation.
- **Negative-signed drawdown.** The convention is a positive magnitude in percent. A
  caller passing `-14.5` for a 14.5% drawdown passes every `<= limit` promotion gate
  *and* fails the `>= limit` emergency gate, so the strategy is promoted to 100%
  capital at the exact moment it should be deactivated.
- **`current_tier` outside $\{0,1,2,3\}$**, negative `days_in_tier`, negative
  `execution_errors_in_tier`, non-positive slippage ratio, negative
  `target_full_capital_usd` (which would otherwise produce a negative allocation), and
  drawdown above 100%.

## 1. Emergency Drawdown & Health Audit

- Audit realized drawdown against the emergency limit (12%). Comparison is **inclusive**:
  exactly 12.0% deactivates.
- On breach: demote to Tier 0, allocation $0, `promotion_status =
  DEMOTED_DRAWDOWN_BREACH`, `tier_name = EMERGENCY_DEACTIVATED`, logged at CRITICAL.
- This outranks a perfect record on every other metric. It applies from Tier 0 too — a
  paper strategy that blew up is frozen rather than promoted.
- The entitlement drops to $0; **acting on that is the caller's job**. This is not a
  kill switch (see `kill-switch-and-drawdown-circuit-breakers`).

## 2. Maintenance Audit (does the strategy still deserve its current tier?)

Entry gates are entry conditions only. Without a maintenance check a strategy that
cleared the Tier 3 gate keeps 100% of capital until it falls off the 12% cliff.

- Limits: Max DD $> 8.0\%$ (Tier 1), $> 10.0\%$ (Tiers 2-3); Slippage $> 2.0\times$ at
  any tier. Comparison is **exclusive**: exactly at the limit retains.
- On breach: step down **exactly one tier** (`DEMOTED_MAINTENANCE_BREACH`), logged at
  WARNING. Tier 3 -> Tier 2 -> Tier 1 -> Tier 0, one evaluation at a time.
- **Sharpe is deliberately excluded.** Drawdown and slippage are facts about fills that
  already happened; a 30-day Sharpe has a standard error near 2.90 (Lo 2002), so
  demoting on it would thrash capital between tiers on estimation noise.
- Maintenance limits are looser than the entry gate above them (enter Tier 2 at
  DD $\le 5\%$, leave Tier 1 at DD $> 8\%$). This hysteresis band is what stops
  oscillation across a boundary the strategy just cleared. Configuring a maintenance
  limit at or above the emergency limit is rejected at construction — the emergency
  branch would resolve first and the step-down would be dead code.
- Set `enable_maintenance_demotion=False` only for a pure ramp-up simulation where
  de-risking is handled entirely elsewhere.

## 3. Promotion Gate Evaluation

At most one tier per evaluation, so a long track record cannot skip a rung.

| Transition | Days | Sharpe | Max DD | Slippage | Exec errors |
|---|---|---|---|---|---|
| 0 -> 1 (Seed, 10%) | $\ge 14$ | — | $\le 5.0\%$ | — | $0$ |
| 1 -> 2 (Scale, 50%) | $\ge 30$ | $\ge 1.0$ | $\le 5.0\%$ | $\le 1.5\times$ | — |
| 2 -> 3 (Full, 100%) | $\ge 60$ | $\ge 1.2$ | $\le 8.0\%$ | $\le 1.5\times$ | — |

- All gate comparisons are **inclusive** at the threshold.
- Tier 0 -> 1 gates paper drawdown and execution crashes but **not** paper Sharpe: it is
  the first commitment of real capital, and a paper Sharpe is not evidence of live edge.
- Tier 2 -> 3 gates slippage because it is the largest single capital increase in the
  ladder; a strategy whose live execution has degraded should not receive the biggest
  step-up available.
- When promotion is blocked, `failed_gates` names every failing condition
  (`min_days_in_tier: 15 < 30`), which is what makes a retention decision auditable
  rather than opaque.

## 4. Sharpe Confidence Annotation

- `sharpe_standard_error` = $\sqrt{(q + \text{SR}_{\text{ann}}^2/2)/T}$ over
  `days_in_tier` daily observations (Lo 2002; see `references/standards.md`).
- `sharpe_gate_conclusive` is True only if the realized Sharpe exceeds its threshold by
  more than $1.96$ standard errors. **It will normally be False** — that is the honest
  signal, not a defect. Treat a passing Sharpe gate as "not obviously broken".

## 5. Capital Allocation Calculation

- Assign tier allocation percentage (0%, 10%, 50%, 100%) and compute
  $\text{Allocated USD} = \text{Target Full USD} \times \text{Tier Allocation Pct}$,
  rounded to cents.
- This is an **entitlement**, not an instruction to trade. A separate pre-trade control
  must enforce it (SEC Rule 15c3-5(c)(1)(i) for US broker-dealers).

## 6. Audit Report Generation & State Persistence

- Output the structured `IncrementalDeploymentReport`.
- **Persist `next_days_in_tier`** — 0 on any tier change, unchanged on retention.
  Carrying the old counter across a promotion lets a 70-day *paper* record satisfy the
  30-day *live* gate, promoting a strategy two tiers in two evaluations with zero live
  days at Tier 1.
- Timestamp, approve and record each tier change. Per ESMA's supervisory briefing
  (¶30-31, non-binding), a series of small changes can accumulate into an untested
  material change, so each rung of the ramp needs its own record rather than one
  record for "the rollout".
- In an EU/UK regulated firm, obtain authorisation from the person designated by senior
  management before applying the new allocation (RTS 6 Art. 5).
