---
name: counterparty-and-broker-concentration-risk
description: >-
  Use when cash, collateral and positions sit at several prime brokers or exchanges and
  no one number says how much is at any single one; enforces NAV exposure caps and CDS
  spread bounds, and returns an advisory routing decision.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: risk-management
  tags: counterparty-risk, prime-broker, concentration-limits, cds-spread, smart-order-routing, hhi
  brokers_frameworks: "Generic Risk Engine; Python Dataclasses"
  version: "1.2.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill in quantitative fund architectures that operate with multiple Prime Brokers (PBs), clearing firms, or crypto exchanges. Concentrating too much cash, margin collateral, or position value at a single counterparty exposes the fund to severe systemic risk (e.g. Lehman Brothers, MF Global, or exchange insolvencies). This module monitors per-broker exposure, incorporates Credit Default Swap (CDS) spread signals, enforces % NAV exposure caps, and returns smart failover routing decisions.

## When NOT to Use

- **You need executed failover, not a routing decision.** `route_order` returns an advisory `RoutingDecision` and never mutates broker state — the order management system must act on it. See `broker-failover-secondary-account-routing` for the execution side.
- **You need venue/endpoint outage handling.** This skill routes around *counterparty concentration and credit distress*; connectivity-loss failover belongs to `smart-order-router-failover-on-venue-outage`.
- **You need replacement-cost or PFE modeling (netting sets, collateral haircuts, ISDA CSA terms).** Exposure here is the simple sum of cash + margin + position value per broker, measured on magnitude, by design. It is an engineering control for custody/PB balance concentration, not a regulatory large-exposure measure.

## Prerequisites

- Account cash, margin, and position market value balances per prime broker (signed balances allowed: negative cash = debit, negative position value = shorts).
- A CDS spread in bps per broker, plus the CDS threshold above which that broker is treated as distressed. (Credit ratings are *not* an input — the module scores credit distress from the CDS spread alone.)

## Workflow

1. **Broker Inventory Registration**: Register prime brokers (`broker_id`, `name`, `max_nav_pct_limit`, `cds_spread_bps`, `max_cds_bps_threshold`, and the three balance fields). Profiles validate on construction (limits must be fractions in (0, 1] — `0.0` is rejected, not read as "block everything"; CDS non-negative; balances finite); re-registering the same `broker_id` replaces the profile — that is the balance-update mechanism.
2. **Current Exposure Calculation**:
   - $\text{Exposure}_k = \text{Cash}_k + \text{Margin}_k + \text{PositionValue}_k$, signed (querying an unknown broker_id raises, never returns a silent 0.0).
   - Concentration is measured on **magnitude**: $\text{NAV Weight}_k = \frac{|\text{Exposure}_k|}{\text{Portfolio NAV}}$. A net-debit or net-short balance at a broker is exposure *to* that broker, not spare capacity under its cap. NAV itself stays the signed sum.
3. **Pre-Trade Routing Audit** (`route_order`) — order value $V$ adds to the target broker's exposure while NAV is held constant (the margin-financed / externally-sourced convention — the conservative one):
   - Check if $\frac{|\text{Exposure}_k + V|}{\text{NAV}} > \text{Max NAV Limit}_k$ — the order nets against the existing signed balance first, then the projected balance is measured on magnitude.
   - Check if broker $\text{CDS Spread} > \text{Max CDS Threshold}$ (credit distress).
   - If portfolio NAV $\le 0$ (empty or net-negative book), concentration is unassessable: the decision returns **blocked** — do not route and do not substitute an invented denominator.
4. **Smart Failover Routing**: If the primary breaches limits or credit signals, re-route to the compliant secondary broker with the **lowest projected NAV weight** (ties broken by broker_id, deterministic).
   - Decision point: if `RoutingDecision.blocked is True` — **route nowhere** (`selected_broker_id` names the original target for audit context only) and escalate to manual review. Every broker is either over its cap or credit-distressed.
   - Decisions are advisory: broker state never mutates; the caller executes.
5. **Broker HHI Reporting**: Compute the Herfindahl-Hirschman Index across broker exposure magnitudes, $HHI = \sum w_k^2$ with $w_k = |\text{Exposure}_k| / \sum_j |\text{Exposure}_j|$ — bounded in $[1/n, 1]$ even when brokers carry negative balances, and identical to exposure/NAV weights when all balances are positive. A warning is logged when HHI exceeds the alert threshold (default 0.35). If every broker is flat the index is undefined and `compute_hhi` **raises** rather than returning 0.0.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Single Broker Reliance**: Maintaining 90% of collateral at a single prime broker despite having active accounts at 3 secondary brokers.
- **Ignoring CDS Spread Spikes**: Failing to monitor real-time broker CDS spreads, continuing to route new collateral to a bank experiencing credit distress. A missing/NaN CDS quote must block routing, not default to "healthy".
- **Executing a Blocked Decision**: when every broker is non-compliant, the decision's `selected_broker_id` still names the original target for audit context. Route on `selected_broker_id` ONLY when `blocked is False` — otherwise the order goes to the distressed broker you were trying to avoid.
- **Inventing a Denominator**: substituting the order value for NAV when NAV is zero/undefined turns an unassessable concentration into a fake number. NAV ≤ 0 means block and review.
- **Reading a Negative Balance as Headroom**: a broker you owe $100k nets to −$100k. On signed weights that scores as *less* concentrated than a flat account, so it passes every cap and sorts first in the failover search — the fund keeps routing into the counterparty it is most in debt to. Measure caps and HHI on magnitude.
- **Treating an Undefined HHI as Diversified**: 0.0 is the index value for perfect diversification, so returning it when the denominator is undefined makes a downstream `if hhi > threshold` check pass silently. `compute_hhi` raises instead — handle the exception, don't paper over it with a default.
- **Excluding Unsettled Trade Cash**: Calculating broker concentration on settled cash only, ignoring open trade receivables.
- **Stale Balances**: exposures are only as current as the registered profiles; route against refreshed balances (re-register to update), not opening-of-day snapshots on volatile days.

## Verification

- Instantiate `CounterpartyConcentrationMonitor` with 3 brokers (`BrokerA` limit 35% NAV, `BrokerB` limit 35% NAV). Set `BrokerA` current exposure to 33% NAV. Submit an order of $50,000 to `BrokerA`. Verify the monitor flags a limit breach and returns a re-route decision to `BrokerB` (`is_rerouted=True`, `blocked=False`).
- Distress every broker (limits or CDS) and verify the decision comes back `blocked=True` with an escalation reason — and that no code path routes on it.
- Verify `route_order` with a NaN order value or an unknown broker raises, and `calculate_total_broker_exposure("typo")` raises rather than returning 0.0.
- Register a broker with a −$100,000 net balance alongside a +$200,000 one and verify it is never selected as the failover destination, that `projected_nav_pct` is reported positive, and that `compute_hhi()` stays within $[1/n, 1]$.
- Run `python -m unittest discover -s skills/counterparty-and-broker-concentration-risk/scripts`.

## Related Skills

- `broker-failover-secondary-account-routing`
- `smart-order-router-failover-on-venue-outage`
---
