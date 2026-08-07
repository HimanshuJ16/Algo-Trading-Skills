---
name: vendor-outage-fallback-data-source-hierarchy
description: "Institutional market data resilience skill for managing multi-vendor fallback hierarchies (Primary, Secondary, Tertiary, Synthetic Cache), detecting feed staleness/disconnections, executing automated failover, enforcing anti-flapping recovery cooling periods, and maintaining quote continuity."
domain: Market Data Infrastructure & Quantitative Execution
subdomain: Feed Resilience & High Availability
tags:
- market-data
- vendor-outage
- fallback-hierarchy
- failover
- staleness-detection
- anti-flapping
- synthetic-cache
- high-availability
brokers_frameworks:
- bloomberg-bpipe
- refinitiv-elektron
- polygon-io
- iex-cloud
- exchange-direct-itch
version: "1.1.0"
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when building high-availability market data ingestion pipelines, connecting to external exchange feeds or data aggregators (e.g. Bloomberg B-PIPE, Refinitiv Elektron, Polygon, Exchange Direct FIX/ITCH), and configuring automated failover hierarchies.

This skill provides institutional mechanisms to:
- Assign explicit **Priority Weights** to market data vendors (Priority 1 = Direct Feed, Priority 2 = Secondary Aggregator, Priority 3 = REST Polling).
- Monitor heartbeat intervals and flag **Stale Data** ($t_{\text{now}} - t_{\text{heartbeat}} > \text{max\_staleness\_seconds}$).
- Execute **Automated Failover** to secondary/tertiary data nodes upon connection loss or error threshold breach.
- Enforce an **Anti-Flapping Recovery Cooling Period** before restoring Primary status during intermittent connection drops.
- Serve **Synthetic Cache Fallback** quotes when all live data providers are offline.

## Prerequisites

- Python 3.9+
- Standard Python libraries (`datetime`, `dataclasses`, `typing`).
- Live market data vendor subscriptions and fallback endpoint URLs.

## Workflow

1. **Register Prioritized Data Sources**: Construct `DataSourceNode` instances for Primary, Secondary, and Tertiary vendors specifying priority numbers and staleness limits, then register with `VendorFallbackHierarchyEngine`.
2. **Track Feed Heartbeats**: Invoke `record_heartbeat(source_id)` on every tick/heartbeat received from active feeds.
3. **Handle Errors & Outages**: Call `record_error(source_id)` upon connection timeouts or socket disconnects to trigger automatic failover evaluation.
4. **Fetch Market Data Ticks**: Execute `fetch_market_data_tick(symbol, fetch_func)` to automatically route quote requests to the highest-priority healthy data source or synthetic cache.
5. **Enforce Recovery Cooling**: The engine maintains Secondary status until the Primary feed demonstrates stability for the duration of the recovery cooling period.

## Common Pitfalls

- **Connection Flapping**: Switching back to Primary immediately upon a single heartbeat causes rapid, erratic switching when a feed is intermittently dropping packets. Mandatory cooling periods prevent flapping.
- **Undetected Stale Freezes**: Market data feeds can freeze on a price without dropping the TCP connection. Staleness timers ($\Delta t > 5.0\text{s}$) are required alongside socket error checks.
- **Unreconciled Price Spreads Across Vendors**: Different vendors may report slightly different bid/ask quotes during failover. Algorithms must tolerate minor cross-vendor spread offsets during failover transitions.
- **Cache Invalidation Failures**: Serving stale synthetic cache ticks during prolonged market outages without flagging `is_synthetic=True` risks executing on obsolete prices.

## Verification

Run the unit test suite to validate primary fetch, stale data failover, error threshold degradation, synthetic cache fallback, and anti-flapping recovery cooling periods:

```bash
python -m unittest discover -s skills/vendor-outage-fallback-data-source-hierarchy/scripts
```

## Related Skills

- `vendor-lock-in-risk-for-proprietary-custody-formats`
- `vendor-specific-adjustment-methodology-reconciliation`
- `tick-to-trade-latency-measurement`
- `zero-downtime-database-schema-migrations`

