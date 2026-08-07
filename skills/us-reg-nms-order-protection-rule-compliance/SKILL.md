---
name: us-reg-nms-order-protection-rule-compliance
description: "Institutional regulatory compliance skill for US SEC Regulation NMS Rule 611 (Order Protection Rule), verifying executions against Protected NBBO, detecting trade-through violations, and validating statutory exemptions (ISO, Self-Help, VWAP/Benchmark, Flickering Quotes)."
domain: US Regulatory Compliance & Market Structure
subdomain: SEC Regulation NMS (Rule 611 Order Protection)
tags:
- sec-reg-nms
- rule-611
- trade-through
- protected-nbbo
- iso-orders
- self-help
- market-structure
- finra-compliance
brokers_frameworks:
- sec-reg-nms
- finra-cats
- quickfix
version: "1.1.0"
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when auditing trade execution quality, building smart order routers (SORs), or preparing SEC / FINRA CAT (Consolidated Audit Trail) compliance reports for US equity trading centers (Exchanges, ATSs, Dark Pools, Market Makers, Broker-Dealers) operating under **SEC Regulation NMS Rule 611**.

This skill provides institutional mechanisms to:
- Evaluate executions against automated **Protected National Best Bid and Offer (NBBO)**.
- Detect illegal **Trade-Through Violations** (buying above NBO or selling below NBB).
- Validate statutory SEC Rule 611 exemptions: **Intermarket Sweep Orders (ISO)** (Rule 611(b)(5/6)), **Self-Help Outages** (Rule 611(b)(1)), **Benchmark / VWAP Orders** (Rule 611(b)(7)), and **Flickering Quotes** (Rule 611(b)(8)).
- Declare and revoke venue **Self-Help Exemption** statuses during venue latency (> 1 sec) or system outages.

## Prerequisites

- Python 3.9+
- Direct SIP or proprietary direct depth-of-book feed feeds for US equity exchanges (NASDAQ, NYSE, Cboe, IEX, MEMX).
- Microsecond execution timestamps and order routing tags (`FIX Tag 269` / `18=f`).

## Workflow

1. **Ingest Automated Protected Quotes**: Collect `ProtectedQuote` records from registered exchanges containing `nbb_price`, `nbo_price`, `is_automated`, and microsecond timestamps.
2. **Manage Self-Help Exemption Declarations**: If a venue experiences > 1 sec latency or system outage, invoke `declare_self_help(venue_id)`. The engine automatically excludes that venue's quotes from Protected NBBO calculations.
3. **Submit Executions for Audit**: Pass `ExecutionRecord` (price, quantity, side, ISO tag, VWAP tag) to `evaluate_execution()`.
4. **Evaluate Trade-Through Compliance**: The engine computes Protected NBBO and checks for trade-through violations. If exempt via ISO, VWAP, or Flickering Quotes, it logs the statutory exemption reason.
5. **Archive Audit Records**: Store `Rule611AuditResult` records for FINRA / SEC CAT regulatory examination defense.

## Common Pitfalls

- **Confusing Manual (Non-Automated) Quotes with Protected Quotes**: Rule 611 ONLY protects automated quotes (`is_automated = True`). Trading through a manual quote is NOT a Rule 611 violation.
- **Failing to Route Simultaneous ISO Orders**: Tagging an order as an ISO (Rule 611(b)(5)) relieves the receiving venue of trade-through responsibility, but obligates the routing broker to simultaneously route ISO orders to ALL protected venues displaying superior prices.
- **Delayed Self-Help Declarations**: Executing trades through a lagging venue before declaring Self-Help creates illegal trade-through records. Self-Help must be formally declared in system logs.
- **Ignoring Flickering Quotes (1-Second Window)**: A trade executed at a price inferior to a newly arrived quote updated within 1.0 second is exempt under Rule 611(b)(8).

## Verification

Run the test suite to validate compliant fills, trade-through detection, ISO exemptions, Self-Help declarations, flickering quote exemptions, and audit report generation:

```bash
python -m unittest discover -s skills/us-reg-nms-order-protection-rule-compliance/scripts
```

## Related Skills

- `us-reg-sho-short-sale-locate-requirements`
- `wash-trade-and-spoofing-self-detection`
- `tick-size-pilot-program-impact-assessment`
- `uk-fca-algorithmic-trading-systems-controls`

