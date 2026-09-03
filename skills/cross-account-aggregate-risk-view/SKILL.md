---
name: cross-account-aggregate-risk-view
description: Quantitative multi-account risk management engine for consolidating positions,
  cash, and margin across sub-accounts/prime brokers, enforcing firm-wide GMV caps,
  and flagging internal long/short offsetting friction.
domain: Risk Management & Operations
subdomain: Multi-Account Risk
tags:
- cross-account
- aggregate-risk
- firm-wide-limits
- sub-accounts
- margin-utilization
- wash-trade-detection
brokers_frameworks:
- Generic Risk Engine
- Python Dataclasses
version: "1.2.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in multi-account or multi-strategy quantitative fund operations (e.g. trading across multiple sub-accounts at Interactive Brokers, Binance, Coinbase, or CME FCMs). Managing risk at individual account silos fails to detect aggregate firm-wide exposure breaches or internal offsetting friction (e.g. Sub-Account 1 buying AAPL while Sub-Account 2 is selling AAPL). This module aggregates holdings into a unified "single-pane-of-glass" risk hub, enforces firm-wide Gross Market Value (GMV) caps, and flags internal offsetting between sub-accounts. It operates on point-in-time snapshots you supply — it does not poll brokers or place orders.

## When NOT to Use

- **You need FX conversion, broker-symbol normalization, or multi-currency NAV.** This engine assumes canonical symbols and USD values already prepared — see `multi-broker-consolidated-position-view` for that layer.
- **You need execution-level wash-trade detection.** Holding offsetting long/short *positions* across accounts is capital friction, not market abuse. Regulatory wash trades attach to *executions* without a change in beneficial ownership (e.g. crossing orders between commonly-owned accounts) — see `wash-trade-and-spoofing-self-detection`.
- **You need to internally cross/net opposing order flow before routing.** That is the execution-side concern of `multi-order-netting-before-routing`; this skill only flags the position-level symptom.
- **You need account-level buying-power or margin-call handling.** Per-account margin actions belong to `broker-account-margin-call-handling` / `margin-utilization-circuit-breaker`; this module enforces the firm-wide aggregate view only.

## Prerequisites

- Real-time position, cash, and margin balances per sub-account (USD-normalized, canonical symbols).
- A valid market price (> 0, finite) for **every held symbol** — valuation fails closed when any price is missing or invalid.
- Broker-reported margin used/limit per account. This engine consolidates those figures; it does **not** compute Reg T, portfolio-margin, or SPAN requirements, so any pre-trade margin projection must be supplied by the caller.

## Workflow

1. **Sub-Account Ingestion**: Ingest sub-account records (`account_id`, `cash_usd`, `margin_used_usd`, `margin_limit_usd`, `positions`). Records validate on construction (balances must be finite, margin used/limits non-negative, quantities finite) — malformed feeds raise instead of partially aggregating. Re-registering an `account_id` replaces its record; that is the balance-update mechanism.
2. **Firm-Wide Valuation & Consolidation**:
   - Net Position per Symbol: $Q_{net}(s) = \sum_a Q_a(s)$.
   - Total Firm NAV: $\text{NAV}_{firm} = \sum_a \text{Cash}_a + \sum_s Q_{net}(s) \cdot P(s)$.
   - Gross Market Value (GMV): $\text{GMV}_{firm} = \sum_s |Q_{net}(s) \cdot P(s)|$ — gross **across symbols**, netted **within each symbol** (offsetting cross-account legs collapse to one economic exposure and are flagged separately in step 3).
3. **Internal Offsetting Audit**:
   - Check if $\exists a_1, a_2$ such that $Q_{a1}(s) > 0$ and $Q_{a2}(s) < 0$.
   - Flag as `INTERNAL_OFFSETTING_FRICTION` (double borrow/commission drag to optimize away via internal netting). This is a capital-efficiency flag — it does not affect compliance and is not itself a wash-trade violation (see When NOT to Use).
4. **Pre-Trade Firm-Wide Limit Audit** (`evaluate_pre_trade_order`):
   - For a proposed order in Sub-Account $A$: recompute projected $\text{GMV}_{firm}$ on the exact post-trade book (the traded symbol is valued at the live order price, not a stale feed).
   - Margin projection is opt-in: pass `additional_margin_usd` (the incremental margin the order consumes in that account, negative to model margin released; the projected account figure is floored at 0). **Omit it and margin utilization is not projected** — the margin cap then only fires on a breach already present in the registered balances.
   - Reject if projected $\text{GMV}_{firm} > \text{Max GMV Limit}$ or utilization > cap. There is **no automatic downsizing** — the gate is approve/reject; sizing the downsized order is the caller's job.
   - Fail-closed paths: unknown `account_id` → `(False, reason)`; non-finite quantity or non-positive price → raises `ValueError`; any held symbol without a valid price → violation, so **all** orders are blocked until the feed is repaired.
   - Decision point: risk-reducing orders are evaluated on their projected net position and can be approved even while the firm is currently over a cap — do not freeze de-risking during a breach.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Siloed Account Limit Checks**: Passing a trade in Sub-Account 3 because Sub-Account 3 has available capital, even though the firm-wide single-name limit in NVDA is already 100% full.
- **Ignoring Internal Offsetting Friction**: Paying double borrow and commission fees to hold long 10,000 shares in Account A and short 10,000 shares in Account B.
- **Uncoordinated Margin Utilization**: Allowing aggregate margin utilization across accounts to exceed 80% without a centralized liquidity buffer.
- **Assuming the Pre-Trade Gate Projects Margin**: `evaluate_pre_trade_order` defaults `additional_margin_usd=0.0`, so by default it gates GMV only and reports margin against *current* balances. An order that would blow through the aggregate utilization cap is approved unless the caller passes the order's margin requirement.
- **Fail-Open Pricing**: Valuing unpriced positions at $0.00 lets the GMV check pass while real exposure is unknown. Missing, zero, negative, or NaN prices must block approval (this engine now enforces that; never relax it back to a `.get(symbol, 0.0)` default).
- **Silent NaN Propagation**: A NaN position or price makes every limit comparison evaluate False, so a corrupt feed reports itself "compliant". Reject non-finite inputs at ingestion.
- **Mislabeling Offsetting Positions as Wash Trades**: A regulatory wash sale under Exchange Act §9(a)(1) (15 U.S.C. §78i(a)(1)) is an *execution* involving no change in beneficial ownership entered **for the purpose of creating a false or misleading appearance of active trading** (CEA §4c(a) is the futures analogue); merely holding offsetting positions is not one. Conversely, actually *crossing* orders between commonly-owned sub-accounts CAN implicate those rules — run `wash-trade-and-spoofing-self-detection`.
- **Check-Then-Trade Races**: Two concurrent orders can each pass the pre-trade check against the same cap and jointly breach it. Serialize check-then-place sequences at the caller when orders can arrive from multiple threads; the aggregator's internal lock protects only its own registry snapshots.

## Verification

- Instantiate `CrossAccountRiskAggregator`. Register Sub-Account 1 (Long 1,000 AAPL @ $150) and Sub-Account 2 (Short 400 AAPL @ $150). Verify Net Position = +600 AAPL, Gross Market Value = $190,000 (with NVDA 500 @ $200), Firm NAV = $540,000, margin utilization = 20%, and AAPL flagged for internal offsetting.
- Submit a small, GMV-compliant order in Sub-Account 1 with `additional_margin_usd=250_000` and verify rejection on the aggregate margin utilization cap ($80k + $250k over $400k capacity = 82.5%); verify a $240,000 draw, landing exactly on the 80.0% cap, is approved; verify the same order with the argument omitted is approved (margin not projected by default).
- Submit an order in Sub-Account 1 breaching the firm-wide GMV limit ($1M) and verify pre-trade rejection; submit a sell-down on an already-breached firm and verify approval; verify an unknown account is rejected.
- Omit the AAPL price and verify the report turns non-compliant (`AAPL` in `unvalued_symbols`) and blocks pre-trade approval — never a silent $0.00 valuation.
- Verify malformed records (NaN quantity, string cash, zero GMV cap) raise `ValueError` at construction.
- Run `python -m unittest discover -s skills/cross-account-aggregate-risk-view/scripts`.

## Related Skills

- `counterparty-and-broker-concentration-risk`
- `multi-broker-consolidated-position-view`
- `multi-order-netting-before-routing`
- `wash-trade-and-spoofing-self-detection`
---
