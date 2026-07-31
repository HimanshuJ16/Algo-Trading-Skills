---
name: real-time-vs-delayed-data-entitlement-handling
description: >-
  Market data entitlement engine enforcing exchange licensing rules, non-pro vs professional subscriber access control, and blocking live order execution on delayed data streams.
domain: Data Management Global
subdomain: Market Data Entitlement & Licensing Compliance
tags: ["entitlement-handling", "real-time-data", "delayed-data", "exchange-licensing", "market-data-compliance", "pro-vs-nonpro"]
brokers_frameworks: ["Exchange Market Data Licensing Agreements (NYSE/NASDAQ/CME)", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when handling real-time vs 15-minute delayed market data streams across institutional trading applications and retail research dashboards. Exchange licensing agreements (NYSE, NASDAQ, CME, LSE) enforce strict commercial policies: real-time data is fee-liable, whereas 15-minute delayed data is low-cost or free. This engine validates user entitlement tiers (`REAL_TIME` vs `DELAYED`), enforces subscriber classification (`PROFESSIONAL` vs `NON_PROFESSIONAL`), and strictly blocks live order execution when operating on delayed data streams.

## Prerequisites

- User entitlement profile (`user_id`, `subscriber_type`: `'PROFESSIONAL'`/`'NON_PROFESSIONAL'`, `subscribed_exchanges`, `entitlement_tier`: `'REAL_TIME'`/`'DELAYED'`).
- Market data request spec (`symbol`, `exchange`, `is_trading_execution_request`).

## Workflow

1. **Exchange Subscription Verification**:
   - Check if target exchange is in user's `subscribed_exchanges` list.
2. **Trading Execution Compliance Check**:
   - If `is_trading_execution_request` is True and `entitlement_tier == 'DELAYED'` $\implies$ block order placement (`LIVE_TRADING_BLOCKED_DELAYED_DATA`).
3. **Data Stream Processing & Tagging**:
   - For `REAL_TIME` tier $\implies$ serve live stream with $\text{Delay} = 0\text{ min}$.
   - For `DELAYED` tier $\implies$ serve delayed stream with $\text{Delay} = 15\text{ min}$ and prominent delay tag.
4. **Audit Report Generation**: Output structured `EntitlementAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Trading on Delayed Quotes**: Allowing automated trading algorithms to execute orders using 15-minute delayed quotes, leading to severe execution slippage and exchange audit violations.
- **Misclassifying Subscriber Status**: Treating professional institutional traders as non-professionals to reduce monthly exchange fees, triggering compliance penalties.
- **Missing Delay Tags**: Displaying delayed prices on user interfaces without prominent "15-Min Delayed" labels.

## Verification

- Instantiate `RealTimeVsDelayedEntitlementEngine`. Test live trading request with `DELAYED` entitlement on NASDAQ $\implies$ verify `LIVE_TRADING_BLOCKED_DELAYED_DATA` status. Test data request with `REAL_TIME` entitlement $\implies$ verify `REALTIME_STREAM_ENTITLED` status and 0-minute delay.
- Run `python scripts/test_real_time_vs_delayed_data_entitlement_handling.py`.

## Related Skills

- `market-data-entitlement-and-licensing-per-venue`
- `market-data-cost-optimization-tiered-subscriptions`
---
