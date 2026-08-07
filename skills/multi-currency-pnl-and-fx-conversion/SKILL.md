---
name: multi-currency-pnl-and-fx-conversion
description: Use when a bot or backtest holds positions denominated in more than one
  currency, to prevent P&L from different currencies being silently summed as if they
  were the same unit
domain: algorithmic-trading
subdomain: data-management-global
tags:
- data-management-global
brokers_frameworks: []
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a portfolio spans instruments denominated in more than one currency (e.g. a US-listed stock and an Indian-listed stock held in the same conceptual portfolio, or any forex-adjacent multi-asset strategy). The single most common bug in multi-currency systems is silently summing raw P&L figures across currencies as if $100 and ₹100 were interchangeable units — every aggregate figure (total P&L, portfolio value, exposure for a risk check) must have an explicit, consistently-applied currency conversion step before aggregation, or the resulting numbers are meaningless despite looking like valid numbers.

## Prerequisites

- A reliable, timestamped source of FX conversion rates for every currency pair in use (not just spot rate "as of now" — historical point-in-time rates are needed for backtest-accurate conversion)
- An explicit decision on the portfolio's "base currency" (the single currency all aggregate reporting converts into) — this must be a deliberate choice, not implicit from whichever currency happens to dominate the codebase's assumptions

## Workflow

1. Define an explicit base/reporting currency for the portfolio, and tag every position, trade, and P&L figure internally with its native currency — never store a bare numeric P&L value without an accompanying currency field, even when a system currently only trades one currency, since that assumption is exactly what breaks silently when a second currency is added later.
2. Convert to base currency only at the point of aggregation/reporting (position-level P&L stays in native currency for record-keeping and reconciliation against the broker's own native-currency statements), not by converting at trade-entry and then treating the converted figure as if it were the authoritative native-currency P&L — this preserves the ability to reconcile against broker statements, which report in native currency.
3. Use point-in-time FX rates matching each P&L event's actual timestamp for historical/backtest conversion, not a single current-day rate applied uniformly across history — this is the multi-currency-specific instance of the lookahead-bias concern in `lookahead-bias-elimination`, since using today's FX rate to convert a historical trade's P&L is itself a form of using unavailable-at-the-time information.
4. For any risk check that aggregates exposure across currencies (e.g. a portfolio-level exposure limit, or the correlation-cluster exposure check in `correlation-aware-exposure-limits`), ensure the aggregation converts to base currency first — a raw-notional aggregate across currencies without conversion can dramatically understate or overstate true exposure depending on the relative unit sizes of the currencies involved.
5. Separately track FX translation P&L (the gain/loss purely from currency movement on a held foreign-currency position) from the underlying instrument's own price-driven P&L — conflating the two obscures whether a strategy's returns are actually coming from its trading edge or from incidental currency exposure, which matters both for honest performance evaluation and for deciding whether to hedge the currency exposure separately.
6. Handle rounding and precision explicitly per currency — currencies have different standard decimal precision (most quote to 2 decimal places, some to 0, crypto commonly to 8), and applying one currency's rounding convention to another's figures after conversion introduces small but compounding errors over many trades.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Summing raw P&L or position-value figures across currencies without conversion, producing an aggregate number that looks plausible but is not meaningful.
- Converting at trade-entry time and discarding the native-currency figure, making reconciliation against broker statements (which report natively) difficult or impossible.
- Applying a single current FX rate to convert historical P&L across a backtest, introducing a lookahead-bias-adjacent distortion specific to multi-currency systems.
- Aggregating cross-currency exposure for a risk check using raw notional figures without first converting to a common base currency.
- Conflating FX translation P&L with the underlying instrument's price P&L, obscuring the actual source of a strategy's returns.

## Verification

- Confirm every stored P&L/position record includes an explicit currency field, even in a currently-single-currency deployment, verified by checking the data schema rather than just current behavior.
- Reconcile a sample of converted, base-currency P&L figures against the broker's native-currency statement for the same trades, confirming the conversion (using the correct point-in-time rate) produces the expected base-currency figure.
- Confirm a backtest run with historical point-in-time FX rates produces different (and more accurate) results than the same backtest run with a single current-day rate applied throughout, for a period with meaningful FX movement.
- Confirm a portfolio-level exposure check correctly reflects converted, base-currency exposure when tested with a constructed multi-currency position set.

## Related Skills

- `lookahead-bias-elimination`
- `correlation-aware-exposure-limits`
- `forex-broker-integration-oanda-mt5`
