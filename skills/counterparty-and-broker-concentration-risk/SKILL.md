---
name: counterparty-and-broker-concentration-risk
description: Quantitative treasury and risk management module for auditing prime broker
  counterparty concentration, enforcing max % NAV limits, credit rating/CDS spread
  bounds, and smart failover order routing.
domain: Risk Management & Treasury
subdomain: Counterparty Risk
tags:
- counterparty-risk
- prime-broker
- concentration-limits
- cds-spread
- smart-order-routing
- hhi
brokers_frameworks:
- Generic Risk Engine
- Python Dataclasses
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in quantitative fund architectures that operate with multiple Prime Brokers (PBs), clearing firms, or crypto exchanges. Concentrating too much cash, margin collateral, or position value at a single counterparty exposes the fund to severe systemic risk (e.g. Lehman Brothers, MF Global, or exchange insolvencies). This module monitors per-broker exposure, incorporates Credit Default Swap (CDS) spread signals, enforces % NAV exposure caps, and returns smart failover routing decisions.

## When NOT to Use

- **You need executed failover, not a routing decision.** `route_order` returns an advisory `RoutingDecision` and never mutates broker state — the order management system must act on it. See `broker-failover-secondary-account-routing` for the execution side.
- **You need venue/endpoint outage handling.** This skill routes around *counterparty concentration and credit distress*; connectivity-loss failover belongs to `smart-order-router-failover-on-venue-outage`.
- **You need replacement-cost or PFE modeling (netting sets, collateral haircuts, ISDA CSA terms).** Exposure here is the simple sum of cash + margin + position value per broker, by design.

## Prerequisites

- Account cash, margin, and position market value balances per prime broker (signed balances allowed: negative cash = debit, negative position value = shorts).
- Total portfolio NAV and broker credit metrics (CDS spread in bps, credit rating).

## Workflow

1. **Broker Inventory Registration**: Register prime brokers (`broker_id`, `max_nav_pct_limit`, `cds_spread_bps`, `credit_rating`). Profiles validate on construction (limits must be fractions in (0, 1], CDS non-negative, balances finite); re-registering the same `broker_id` replaces the profile — that is the balance-update mechanism.
2. **Current Exposure Calculation**:
   - $\text{Exposure}_k = \text{Cash}_k + \text{Margin}_k + \text{PositionValue}_k$ (querying an unknown broker_id raises, never returns a silent 0.0).
   - $\text{NAV Weight}_k = \frac{\text{Exposure}_k}{\text{Portfolio NAV}}$.
3. **Pre-Trade Routing Audit** (`route_order`) — order value $V$ adds to the target broker's exposure while NAV is held constant (the margin-financed / externally-sourced convention — the conservative one):
   - Check if $\frac{\text{Exposure}_k + V}{\text{NAV}} > \text{Max NAV Limit}_k$.
   - Check if broker $\text{CDS Spread} > \text{Max CDS Threshold}$ (credit distress).
   - If portfolio NAV $\le 0$ (empty or net-negative book), concentration is unassessable: the decision returns **blocked** — do not route and do not substitute an invented denominator.
4. **Smart Failover Routing**: If the primary breaches limits or credit signals, re-route to the compliant secondary broker with the **lowest projected NAV weight** (ties broken by broker_id, deterministic).
   - Decision point: if `RoutingDecision.blocked is True` — **route nowhere** (`selected_broker_id` names the original target for audit context only) and escalate to manual review. Every broker is either over its cap or credit-distressed.
   - Decisions are advisory: broker state never mutates; the caller executes.
5. **Broker HHI Reporting**: Compute Herfindahl-Hirschman Index across broker exposures ($HHI = \sum w_k^2$); a warning is logged when HHI exceeds the alert threshold (default 0.35).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Single Broker Reliance**: Maintaining 90% of collateral at a single prime broker despite having active accounts at 3 secondary brokers.
- **Ignoring CDS Spread Spikes**: Failing to monitor real-time broker CDS spreads, continuing to route new collateral to a bank experiencing credit distress. A missing/NaN CDS quote must block routing, not default to "healthy".
- **Executing a Blocked Decision**: when every broker is non-compliant, the decision's `selected_broker_id` still names the original target for audit context. Route on `selected_broker_id` ONLY when `blocked is False` — otherwise the order goes to the distressed broker you were trying to avoid.
- **Inventing a Denominator**: substituting the order value for NAV when NAV is zero/undefined turns an unassessable concentration into a fake number. NAV ≤ 0 means block and review.
- **Excluding Unsettled Trade Cash**: Calculating broker concentration on settled cash only, ignoring open trade receivables.
- **Stale Balances**: exposures are only as current as the registered profiles; route against refreshed balances (re-register to update), not opening-of-day snapshots on volatile days.

## Verification

- Instantiate `CounterpartyConcentrationMonitor` with 3 brokers (`BrokerA` limit 35% NAV, `BrokerB` limit 35% NAV). Set `BrokerA` current exposure to 33% NAV. Submit an order of $50,000 to `BrokerA`. Verify the monitor flags a limit breach and returns a re-route decision to `BrokerB` (`is_rerouted=True`, `blocked=False`).
- Distress every broker (limits or CDS) and verify the decision comes back `blocked=True` with an escalation reason — and that no code path routes on it.
- Verify `route_order` with a NaN order value or an unknown broker raises, and `calculate_total_broker_exposure("typo")` raises rather than returning 0.0.
- Run `python scripts/test_counterparty_monitor.py`.

## Related Skills

- `broker-failover-secondary-account-routing`
- `smart-order-router-failover-on-venue-outage`
---
