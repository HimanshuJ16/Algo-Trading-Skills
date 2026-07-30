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
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in quantitative fund architectures that operate with multiple Prime Brokers (PBs), clearing firms, or crypto exchanges. Concentrating too much cash, margin collateral, or position value at a single counterparty exposes the fund to severe systemic risk (e.g. Lehman Brothers, MF Global, or exchange insolvencies). This module monitors per-broker exposure, incorporates Credit Default Swap (CDS) spread signals, enforces % NAV exposure caps, and dynamically re-routes orders to secondary brokers.

## Prerequisites

- Account cash, margin, and position market value balances per prime broker.
- Total portfolio NAV and broker credit metrics (CDS spread in bps, credit rating).

## Workflow

1. **Broker Inventory Registration**: Register prime brokers (`broker_id`, `max_nav_pct_limit`, `cds_spread_bps`, `credit_rating`).
2. **Current Exposure Calculation**:
   - $\text{Exposure}_k = \text{Cash}_k + \text{Margin}_k + \text{PositionValue}_k$.
   - $\text{NAV Weight}_k = \frac{\text{Exposure}_k}{\text{Portfolio NAV}}$.
3. **Pre-Trade Routing Audit**:
   - For proposed order with trade value $V$:
   - Check if $\frac{\text{Exposure}_k + V}{\text{NAV}} > \text{Max NAV Limit}_k$.
   - Check if broker $\text{CDS Spread} > \text{Max CDS Threshold}$ (credit distress).
4. **Smart Failover Routing**: If primary broker breaches limits or credit signals, re-route order to the next available compliant broker.
5. **Broker HHI Reporting**: Compute Herfindahl-Hirschman Index across broker exposures ($HHI = \sum w_k^2$).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Single Broker Reliance**: Maintaining 90% of collateral at a single prime broker despite having active accounts at 3 secondary brokers.
- **Ignoring CDS Spread Spikes**: Failing to monitor real-time broker CDS spreads, continuing to route new collateral to a bank experiencing credit distress.
- **Excluding Unsettled Trade Cash**: Calculating broker concentration on settled cash only, ignoring open trade receivables.

## Verification

- Instantiate `CounterpartyConcentrationMonitor` with 3 brokers (`BrokerA` limit 35% NAV, `BrokerB` limit 35% NAV). Set `BrokerA` current exposure to 33% NAV. Submit an order of $50,000 to `BrokerA`. Verify the monitor flags a limit breach and automatically re-routes to `BrokerB`.
- Run `python scripts/test_counterparty_monitor.py`.

## Related Skills

- `broker-failover-secondary-account-routing`
- `smart-order-router-failover-on-venue-outage`
---
