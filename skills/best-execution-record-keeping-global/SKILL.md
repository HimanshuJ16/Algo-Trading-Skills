---
name: best-execution-record-keeping-global
description: Rigorous compliance controls for global best execution and audit-proof
  record keeping in quantitative trading.
domain: regulatory-compliance-global
subdomain: regulatory
tags:
- compliance
- risk
- regulatory
- best-execution
- audit
brokers_frameworks:
- any
version: 2.0.0
author: skill-builder
license: MIT
---

## When to Use
Use this skill when processing trade executions across global markets to ensure compliance with best execution requirements (e.g., MiFID II, SEC Rule 606). It verifies execution quality against benchmarks, guarantees the presence of required regulatory metadata, and enforces immutable record-keeping.

## Prerequisites
- Integrated trade capture system with accurate microsecond timestamping.
- Established benchmark pricing (e.g., VWAP, Arrival Price, TWAP).
- Pre-trade and post-trade compliance infrastructure.

## Workflow
1. **Trade Capture**: Ingest trade orders, executions, and corresponding timestamps.
2. **Benchmark Comparison**: Calculate execution slippage versus the prescribed benchmark.
3. **Regulatory Validation**: Ensure the presence of required regulatory tags (e.g., capacity, algorithmic IDs, LEIs).
4. **Immutable Logging**: Generate cryptographic hashes for records to ensure tamper-evident audit trails.
5. **Reporting**: Flag any compliance breaches (e.g., high slippage, missing tags) and export audit logs.

## Common Pitfalls
- Incomplete timestamps resulting in untrackable execution latency.
- Missing regulatory tags causing severe audit failures.
- Lack of cryptographic hashing allowing post-trade alterations without detection.
- Inaccurate benchmark price calculations.

## Verification
- Comprehensive unit tests asserting both compliant and non-compliant scenarios.
- Verifying cryptographic hashes of records are consistent for identical inputs.
- Stress testing slippage calculations across edge cases.

## Related Skills
- compliance-base
- trade-surveillance
