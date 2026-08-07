---
name: data-pipeline-schema-contract-testing
description: Quantitative data quality engine for enforcing schema contracts (field
  types, nullability, value bounds, and schema drift) on incoming market data feeds
  and feature stores.
domain: Data Management Global
subdomain: Data Quality & Schema Governance
tags:
- schema-contract
- data-quality
- pydantic
- schema-drift
- dead-letter-queue
- null-constraint
- type-validation
brokers_frameworks:
- Great Expectations
- Pydantic
- Python Dataclasses
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill at the ingestion edges of quantitative market data pipelines, feature stores, and execution algorithms. Market data vendors frequently introduce silent schema changes (renaming `ask_sz` to `ask_volume`, sending string prices instead of floats, or spiking null values during market volatility). This module validates incoming records against explicit `SchemaContract` definitions, quarantining corrupt payloads to a Dead Letter Queue (DLQ) before they corrupt feature stores or trading models.

## Prerequisites

- Field contract specifications: `expected_type` (`float`, `int`, `str`, `datetime`), `is_nullable`, `min_value`, `max_value`.
- Batch nullability limit: `max_allowed_null_pct` (e.g. $\le 0.5\%$).

## Workflow

1. **Contract Specification Setup**:
   - Define field specifications and nullability constraints for market data entities (`TickRecord`, `OrderBookSnapshot`, `OHLCVBar`).
2. **Batch & Record Parsing**:
   - Inspect incoming dictionary/JSON records.
   - Audit required field presence $\implies$ Catch missing fields.
   - Audit data types $\implies$ Catch type mutations.
   - Audit numeric range bounds $\implies$ Catch impossible outliers ($P \le 0$ or $V < 0$).
3. **Quarantine Routing (DLQ)**:
   - Separate compliant records from non-compliant records.
   - Direct invalid payloads to Dead Letter Queue (DLQ) for alerting.
4. **Audit Report Generation**: Output structured `SchemaContractValidationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Silent Schema Drift Ingestion**: Allowing un-validated vendor JSON payloads to enter Pandas DataFrames, turning float columns into `object` types and crashing backtests.
- **Ignoring Null Value Spikes**: Allowing high percentages of null bid/ask values during fast markets, triggering zero-division errors in feature engineering.
- **Dropping Bad Data Without Dead Letter Logging**: Silently discarding invalid records without logging them to a DLQ, masking upstream vendor feed degradations.

## Verification

- Instantiate `DataSchemaContractVerifier`. Define `TickContract` (`price`: float > 0, `volume`: int >= 0, `symbol`: str, non-nullable). Input 100 valid ticks + 2 corrupt ticks (one missing `price`, one string volume `"five"`). Verify engine validates 100 records, routes 2 records to DLQ, and generates a schema audit report.
- Run `python scripts/test_data_pipeline_schema_contract_testing.py`.

## Related Skills

- `data-quality-monitoring-dashboard`
- `cross-vendor-timestamp-precision-reconciliation`
---
