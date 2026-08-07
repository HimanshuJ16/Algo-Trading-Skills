---
name: paper-to-live-promotion-checklist
description: Use when deciding whether a strategy that has passed backtesting is ready
  to be promoted from paper trading to live capital, providing gated criteria rather
  than a subjective "feels ready" judgment
domain: algorithmic-trading
subdomain: deployment-ops
tags:
- deployment-ops
brokers_frameworks: []
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this as the final gate before any strategy or model routes real orders with real capital, after backtesting (see the backtesting-methodology category) and before enabling live order placement. A strategy passing backtest validation is necessary but not sufficient — backtests cannot fully capture execution realities, broker-specific quirks, or the psychological/operational pressure of live capital, so a defined paper-trading period with explicit pass/fail criteria is required as a separate gate, not a formality to rush through.

## Prerequisites

- A backtested strategy that has already passed `lookahead-bias-elimination`, `walk-forward-validation-setup`, and `execution-realistic-simulation` checks
- Paper trading infrastructure that runs the exact same code path as live trading would (same signal generation, same order-placement logic, same risk checks) with only the final broker order submission redirected to a simulated/no-op fill — not a separate, simplified "paper mode" implementation, since a separate implementation reintroduces the same train/serve-skew-style risk as `offline-train-online-infer-deployment` describes for ML models

## Workflow

1. Run the strategy in paper trading for a minimum defined duration (not a fixed universal number — long enough to cover the range of market conditions the backtest was validated against, and long enough to include at least one meaningfully volatile session, not just a calm stretch) before considering promotion.
2. Compare paper-trading performance against the backtest's expectations for the equivalent period/conditions — not just "was it profitable" but specifically: did realized slippage match modeled slippage, did fill rates match assumptions, did the signal's paper-trading accuracy match its walk-forward validation accuracy. A significant divergence here means the backtest's execution model (or the model's train/serve parity, for ML-based signals) needs revisiting before promotion, not that paper trading was "just unlucky."
3. Verify all risk controls (see `kill-switch-and-drawdown-circuit-breakers`, `correlation-aware-exposure-limits`) have been exercised at least once during the paper period, either naturally or via deliberately engineered test conditions — a risk control that has never actually triggered during paper trading has not been proven to work in this specific deployment, only in isolated testing.
4. Verify operational reliability during the paper period: process supervision (`systemd-supervision-for-trading-bots`) has handled at least one real or simulated restart without state corruption, broker auth/token handling (`token-lifecycle-live-probing`) has correctly survived at least one natural token expiry/reauth cycle, and order idempotency logic (`order-placement-idempotency`) has been exercised against at least one simulated timeout/retry scenario.
5. Require explicit human sign-off as a discrete step, not an implicit "it's been running fine, let's flip the switch" — document the specific metrics reviewed and the specific decision to promote, including the size at which live trading will begin (starting live trading at a reduced size relative to eventual target size is a reasonable additional gate, distinct from paper trading itself).
6. After promotion, do not consider the gate a one-time event — define a defined initial live-trading review period (shorter, more frequent review than the eventual steady-state cadence) where performance and risk-control behavior are reviewed against paper-trading and backtest expectations, with an explicit rollback plan (return to paper trading) if live behavior diverges meaningfully from what paper trading predicted.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Treating a separately-implemented "paper mode" (rather than the same code path with only order submission redirected) as equivalent validation — differences between the paper and live code paths reintroduce exactly the kind of skew this checklist is meant to catch.
- Promoting to live capital purely because the paper-trading duration elapsed, without checking whether risk controls were actually exercised or whether performance matched backtest expectations for the specific conditions observed.
- Skipping a reduced-size initial live period and going straight to full target position sizing, removing the opportunity to catch live-specific issues (real slippage, real latency, real broker quirks) at lower stakes.
- Treating the human sign-off as a formality rather than a genuine review checkpoint, documented and specific rather than a verbal "looks good, ship it."
- Not defining a rollback plan in advance, so if live performance diverges badly from paper-trading expectations, the response is improvised under pressure rather than following a predefined plan.

## Verification

- Produce a documented comparison report: paper-trading performance vs backtest-predicted performance for the same period/conditions, covering P&L, fill quality/slippage, and signal accuracy, with any material divergence explicitly explained or flagged for further investigation before promotion.
- Confirm a written log exists showing each risk control (position limit, drawdown limit, correlation-cluster limit) was exercised at least once during the paper period, with the observed response matching the intended design.
- Confirm the promotion decision itself is documented (date, reviewer, metrics reviewed, initial live position sizing, and the defined rollback trigger condition) rather than existing only as an informal decision.

## Related Skills

- `execution-realistic-simulation`
- `kill-switch-and-drawdown-circuit-breakers`
- `systemd-supervision-for-trading-bots`
- `order-placement-idempotency`
- `token-lifecycle-live-probing`
