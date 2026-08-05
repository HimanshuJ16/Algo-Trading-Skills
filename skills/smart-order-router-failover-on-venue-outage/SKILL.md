---
name: smart-order-router-failover-on-venue-outage
description: >-
  Production-grade Smart Order Router (SOR) venue outage failover engine implementing real-time health monitoring, consecutive error threshold circuit breakers, best execution price selection, and in-flight order recovery.
domain: Execution & Smart Order Routing
subdomain: Venue Outage Failover & Resiliency
tags: ["smart-order-router", "sor-failover", "venue-outage", "circuit-breaker", "best-execution", "order-routing"]
brokers_frameworks: ["Smart Order Router Architecture", "FIX Transport Circuit Breaker", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing resilient Smart Order Routers (SOR) or execution algorithms routing parent orders across fragmented liquid exchanges (NASDAQ, NYSE, Cboe BATS, EDGX). Exchange outages, network disconnects, or FIX gateway drops occur without warning. Routing live orders to a dead or degraded venue causes lost fills, stuck in-flight orders, and execution latency spikes. This engine monitors venue health, trips circuit breakers upon error thresholds, excludes dead venues from route selection, and automatically fails over to backup venues.

## Prerequisites

- Trading venue definitions (`TradingVenue`: `venue_id`, `venue_name`, `state`, `consecutive_error_count`, `max_error_threshold`, `bid_price`, `ask_price`, `available_qty`, `latency_ms`).

## Workflow

1. **Venue Health & Error Monitoring**:
   - Report FIX timeouts or API 5xx errors (`report_venue_error()`). If errors $\ge$ threshold (e.g. 3), trip circuit breaker (`VenueHealthState.CIRCUIT_BROKEN_OUTAGE`).
2. **Best Execution Route Selection**:
   - Filter available venues: exclude `CIRCUIT_BROKEN_OUTAGE` venues.
   - Select best price venue (lowest ask for buy orders, highest bid for sell orders).
3. **Automatic Outage Failover**:
   - If preferred venue is in outage state, log failover event and route to next best healthy backup venue.
4. **Execution Output**: Output structured `SORRoutingResult`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Routing Orders to Outage Venues**: Continuing to send orders to an exchange after multiple consecutive FIX disconnects or timeouts.
- **Cascading Failover Latency**: Failing to maintain pre-warmed backup venue FIX connections, causing latency spikes when failover occurs.
- **Unreconciled In-Flight Orders**: Rerouting residual order quantity without confirming whether in-flight orders at the failed venue were filled or cancelled.

## Verification

- Instantiate `SmartOrderRouterFailoverEngine`. Route buy order across NASDAQ ($100.05 ask), NYSE ($100.10 ask), and BATS ($100.08 ask) $\implies$ verify routed to NASDAQ. Report 3 errors on NASDAQ $\implies$ verify `CIRCUIT_BROKEN_OUTAGE` tripped. Route buy order with preferred venue NASDAQ $\implies$ verify automatic failover to BATS ($100.08 ask). Trip circuit breakers on all venues $\implies$ verify `RuntimeError` raised.
- Run `python scripts/test_smart_order_router_failover_on_venue_outage.py`.

## Related Skills

- `smart-order-router-failover-on-venue-outage`
- `execution-algorithm-kill-switch-integration`
---
