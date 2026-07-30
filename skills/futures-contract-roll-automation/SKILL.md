---
name: futures-contract-roll-automation
description: >-
  Quantitative execution engine for automating futures contract roll decisions via volume/open interest crossover or days-to-expiration rules, constructing atomic calendar spread orders.
domain: Execution Algorithms
subdomain: Futures Derivatives & Roll Automation
tags: ["futures-roll", "calendar-spread", "volume-crossover", "open-interest", "contango", "backwardation", "derivatives"]
brokers_frameworks: ["CME Group", "Interactive Brokers", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in CTA momentum strategies, futures execution algorithms, and multi-asset derivative trading systems. Holding futures contracts through expiration exposes funds to physical delivery obligations or illiquid cash settlement. This module automates the contract roll process, evaluating **volume/open interest crossover** ($V_{\text{next}} > V_{\text{front}}$) and **days-to-expiration (DBE)** thresholds, constructing atomic **calendar spread orders** to roll positions with zero directional market risk.

## Prerequisites

- Front-month contract details (`symbol_front`, `volume_front`, `open_interest_front`, `price_front`, `days_to_expiration`).
- Next-month contract details (`symbol_next`, `volume_next`, `open_interest_next`, `price_next`).
- Roll rules configuration (`min_days_to_expiration = 5`, `trigger_on_volume_crossover = True`).

## Workflow

1. **Roll Trigger Evaluation**:
   - Audit Volume Crossover: Trigger roll if $V_{\text{next}} > V_{\text{front}}$.
   - Audit Expiration Hurdle: Trigger roll if $\text{DBE} \le \text{min\_days\_to\_expiration}$.
2. **Calendar Spread Basis Calculation**:
   - Compute spread price differential: $\text{Spread Price} = P_{\text{next}} - P_{\text{front}}$.
   - Classify market structure: `CONTANGO` ($P_{\text{next}} > P_{\text{front}}$) vs `BACKWARDATION` ($P_{\text{next}} < P_{\text{front}}$).
3. **Atomic Calendar Spread Construction**:
   - For LONG position: Generate spread order `SELL front_month` / `BUY next_month`.
   - For SHORT position: Generate spread order `BUY front_month` / `SELL next_month`.
4. **Audit Report Generation**: Output structured `FuturesRollAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Legging Out of Roll Orders**: Executing two separate leg limit orders instead of an atomic exchange calendar spread order (`SP`), incurring directional market slippage.
- **Waiting Until Expiration Day**: Delaying rolls until Last Trading Day when volume dries up, suffering extreme bid-ask spreads.
- **Ignoring Contango Roll Yield Drag**: Failing to model the roll yield cost ($\text{Spread} \times \text{Position Size}$) in strategy PnL accounting.

## Verification

- Instantiate `FuturesContractRollEngine`. Input ESH6 (Front, Vol=50k, DBE=3) vs ESM6 (Next, Vol=120k). Test Volume Crossover $\implies$ verify engine detects $120\text{k} > 50\text{k}$ crossover, triggers `ROLL_SIGNAL_ACTIVE`, and constructs atomic calendar spread `SELL ESH6 / BUY ESM6`.
- Run `python scripts/test_futures_contract_roll_automation.py`.

## Related Skills

- `synthetic-continuous-futures-contract-construction`
- `calendar-spread-and-multi-leg-order-atomicity`
---
