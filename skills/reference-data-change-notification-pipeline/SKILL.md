---
name: reference-data-change-notification-pipeline
description: >-
  Reference data change detection and notification pipeline comparing instrument snapshots to detect field-level mutations (symbol changes, lot size updates, exchange migrations) and routing alerts to downstream consumers.
domain: Data Management Global
subdomain: Reference Data Governance & Change Detection
tags: ["reference-data", "change-detection", "notification-pipeline", "instrument-master", "corporate-actions", "symbol-change"]
brokers_frameworks: ["ISO 10383 MIC Codes", "ISIN/CUSIP/SEDOL Standards", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when maintaining instrument master databases that feed trading strategies, risk engines, and order management systems. Reference data fields (symbol, exchange, lot size, tick size, currency, status) change due to corporate actions, exchange migrations, or regulatory updates. Undetected changes cause silent trading errors (wrong lot sizes, stale symbols, incorrect exchange routing). This engine compares before/after instrument snapshots, detects field-level mutations, classifies change severity, and generates structured notification alerts for downstream consumers.

## Prerequisites

- Instrument snapshot pairs (`instrument_id`, before and after field values).
- Config options (`critical_fields`: fields whose changes trigger high-severity alerts, default `['symbol', 'exchange', 'status', 'currency']`).

## Workflow

1. **Snapshot Comparison**:
   - Compare each field in the "before" snapshot against the "after" snapshot.
2. **Change Detection & Classification**:
   - Identify added, modified, and removed fields.
   - Classify severity: `CRITICAL` if field is in critical_fields list, `INFO` otherwise.
3. **Notification Generation**:
   - Generate structured `ChangeNotification` per detected mutation with old/new values.
4. **Audit Report**: Output structured `ReferenceDataChangeReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Missing Symbol Rename Detection**: Failing to detect ticker symbol changes (e.g. FB → META), causing order routing to stale symbols.
- **Ignoring Lot Size Changes**: Trading with outdated lot sizes after exchange updates, causing order rejections.
- **No Downstream Propagation**: Detecting changes but failing to notify strategy engines and risk systems.

## Verification

- Instantiate `ReferenceDataChangeNotificationPipelineEngine`. Compare snapshot where symbol changed from "FB" to "META" $\implies$ verify `CRITICAL` severity change detected. Compare snapshot with only lot_size change $\implies$ verify `INFO` severity.
- Run `python scripts/test_reference_data_change_notification_pipeline.py`.

## Related Skills

- `reference-data-symbol-mapping-across-vendors`
- `instrument-universe-change-detection-and-alerting`
---
