---
name: data-vendor-contractual-usage-restriction-tracking
description: >-
  Quantitative market data compliance engine for tracking vendor license restrictions (Bloomberg, Refinitiv, ICE), non-display trading entitlements, and blocking illegal external data redistribution.
domain: Data Management Global
subdomain: Vendor Data Governance
tags: ["vendor-compliance", "data-licensing", "non-display-trading", "bloomberg-bpipe", "refinitiv-dacs", "redistribution-audit", "entitlement-tracking"]
brokers_frameworks: ["Refinitiv DACS", "Bloomberg EMRS", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in quantitative trading firms, broker adapters, and market data distribution hubs to enforce vendor contractual licensing restrictions (Bloomberg B-PIPE, Refinitiv ELEKTRON, S&P Capital IQ). Exchanges and market data vendors impose strict licensing boundaries distinguishing **Internal Research**, **Non-Display Automated Trading**, and **External Redistribution**. Unauthorized external redistribution or non-display trading without explicit licensing triggers severe financial audit penalties from exchanges.

## Prerequisites

- Vendor contract definitions (`vendor_name`, `license_tier`, `allowed_use_cases`, `is_redistribution_allowed`, `is_non_display_allowed`, `max_concurrent_entitlements`).
- Access request payload (`requested_by_system`, `use_case_type`, `is_external_redistribution`, `active_user_count`).

## Workflow

1. **Vendor License Entitlement Audit**:
   - Verify `use_case_type` against contract `allowed_use_cases`.
2. **Non-Display & Redistribution Interception**:
   - If `is_external_redistribution` is True and contract `is_redistribution_allowed` is False $\implies$ Issue `REDISTRIBUTION_LICENSING_VIOLATION`.
   - If `use_case_type == "NON_DISPLAY_TRADING"` and `is_non_display_allowed` is False $\implies$ Issue `NON_DISPLAY_LICENSING_VIOLATION`.
3. **Concurrency Headroom Audit**:
   - If `active_user_count > max_concurrent_entitlements` $\implies$ Issue `CONCURRENCY_CAP_EXCEEDED`.
4. **Audit Report Generation**: Output structured `VendorUsageAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Publishing Internal Benchmarks Externally**: Exposing raw or derived Bloomberg/Refinitiv quotes on client-facing websites without an External Redistribution License.
- **Un-licensed Algorithmic Trading**: Deploying automated HFT bots on desktop-only terminal subscriptions without non-display trading enterprise licenses.
- **Ignoring Concurrent Seat Caps**: Exceeding concurrent terminal or API user entitlement caps during market volatility spikes.

## Verification

- Instantiate `VendorUsageRestrictionEngine`. Register Bloomberg B-PIPE contract (Non-display=True, Redistribution=False, Seat cap=10). Submit a request for internal HFT bot execution. Verify request is `APPROVED`. Submit a request to publish raw quotes on an external portal. Verify engine flags `REDISTRIBUTION_LICENSING_VIOLATION` and returns `DENIED`.
- Run `python scripts/test_vendor_usage_tracking.py`.

## Related Skills

- `real-time-vs-delayed-data-entitlement-handling`
- `market-data-cost-optimization-tiered-subscriptions`
---
