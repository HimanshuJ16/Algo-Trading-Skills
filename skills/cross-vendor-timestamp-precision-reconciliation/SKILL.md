---
name: cross-vendor-timestamp-precision-reconciliation
description: Quantitative market data reconciliation engine for normalizing multi-vendor
  timestamps (s, ms, us, ns, ISO-8601) to 64-bit nanosecond UTC epoch, detecting clock
  drift, and aligning tick sequences.
domain: Data Management Global
subdomain: Market Data Reconciliation
tags:
- timestamp-reconciliation
- nanoseconds
- utc-epoch
- iso-8601
- databento
- refinitiv
- bloomberg
- mifid-ii-rts25
brokers_frameworks:
- Databento
- Refinitiv ELEKTRON
- Bloomberg B-PIPE
- Python Dataclasses
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when processing market data feeds from multiple vendors (e.g., Bloomberg B-PIPE in float seconds, Refinitiv ELEKTRON in ISO-8601 microsecond UTC strings, Databento in int64 nanoseconds). Ingesting mixed-precision timestamps without temporal reconciliation causes out-of-order tick alignment, phantom latency arbitrage signals, and MiFID II RTS 25 compliance audit failures. This module converts raw vendor timestamps into a unified 64-bit nanosecond UTC epoch ($t_{\text{ns}}$), flags clock drift, and audits precision degradation.

## Prerequisites

- Vendor market data records containing raw timestamp (`timestamp_raw`), vendor ID (`vendor_id`), and precision type (`SECONDS`, `MILLISECONDS`, `MICROSECONDS`, `NANOSECONDS`, `ISO8601_STRING`).
- Maximum allowable clock drift threshold (e.g. $\Delta t_{\text{max}} = 1.0\text{ ms}$).

## Workflow

1. **Precision Normalization Engine**:
   - Parse raw string or numeric timestamp $T_{\text{raw}}$.
   - Convert to 64-bit integer nanoseconds ($t_{\text{ns}}$):
     - `SECONDS`: $t_{\text{ns}} = \text{int}(T \times 10^9)$.
     - `MILLISECONDS`: $t_{\text{ns}} = T \times 10^6$.
     - `MICROSECONDS`: $t_{\text{ns}} = T \times 10^3$.
     - `ISO8601_STRING`: Parse UTC string and convert to nanosecond epoch.
2. **Temporal Alignment & Out-of-Order Detection**:
   - Sort ticks across vendors using $t_{\text{ns}}$.
   - Detect negative time deltas ($\Delta t < 0$) indicating out-of-order tick arrivals.
3. **Precision & Drift Audit**:
   - Check if vendor precision meets target SLA (e.g. MiFID II RTS 25 microsecond/nanosecond SLA).
   - Flag clock drift when vendor timestamps diverge from exchange matching engine sequence numbers.
4. **Audit Report Generation**: Output structured `TimestampReconciliationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Precision Truncation**: Converting nanosecond Databento timestamps to float64 seconds, losing precision due to IEEE 754 floating-point mantissa limitations.
- **Ignoring Timezone Offsets**: Assuming ISO-8601 timestamps without explicit `Z` designators are in local exchange time rather than UTC.
- **Zero-Padding Fake Precision**: Appending 6 zeros to millisecond data to force nanosecond schema formatting without flagging the underlying coarse resolution.

## Verification

- Instantiate `CrossVendorTimestampReconciler`. Submit 3 ticks from Bloomberg (`1700000000.123` s), Refinitiv (`2023-11-14T21:20:00.123456Z`), and Databento (`1700000000123456789` ns). Verify reconciler normalizes all to nanosecond integer UTC epochs, aligns tick sequences correctly, and reports precision tiers.
- Run `python scripts/test_cross_vendor_timestamp_precision_reconciliation.py`.

## Related Skills

- `clock-drift-monitoring-alerting-thresholds`
- `data-pipeline-schema-contract-testing`
---
