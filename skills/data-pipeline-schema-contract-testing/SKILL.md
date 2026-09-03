---
name: data-pipeline-schema-contract-testing
description: >-
  Use at the ingestion edge, to enforce field types, nullability and value bounds
  against a declared contract and catch silent vendor schema drift such as a renamed
  field or a price arriving as a string.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: data-management-global
  tags: schema-contract, data-quality, pydantic, schema-drift, dead-letter-queue, null-constraint, type-validation
  brokers_frameworks: "Great Expectations; Pydantic; Python Dataclasses"
  version: "1.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill at the ingestion edges of quantitative market data pipelines, feature stores, and execution algorithms. Market data vendors frequently introduce silent schema changes (renaming `ask_sz` to `ask_volume`, sending string prices instead of floats, or spiking null values during market volatility). This module validates incoming records against explicit `SchemaContract` definitions, quarantining corrupt payloads to a Dead Letter Queue (DLQ) before they corrupt feature stores or trading models.

## When NOT to Use

- **As a statistical or distributional data-quality monitor.** This engine answers "does this record match the declared structure?", not "does this value look wrong relative to recent history?". Stale-but-well-formed quotes, frozen prices, latency degradation, and duplicate ticks all pass a schema contract cleanly — use `data-quality-monitoring-dashboard` for those dimensions.
- **As a cross-vendor reconciler.** Validating vendor A and vendor B against the same contract does not establish that they agree; see `multi-source-price-reconciliation-tie-breaking` and `cross-vendor-timestamp-precision-reconciliation`.
- **As the sole gate on a live order path.** Schema validation is an ingestion-edge control, not a pre-trade risk control. It cannot substitute for the limit checks required under SEC Rule 15c3-5 or MiFID II RTS 6.
- **For deeply nested or self-describing payloads.** `FieldSpec` addresses flat, top-level keys only. Nested objects, arrays of sub-records, and union-typed fields need a full schema library (Pydantic, JSON Schema, Avro) instead.

## Prerequisites

- Field contract specifications (`FieldSpec`): `expected_type` (`float`, `int`, `str`, `bool`, `datetime`), `is_nullable`, `min_value`, `max_value` (both inclusive bounds), `allow_non_finite`.
- Batch nullability limit (`SchemaContract.max_allowed_null_pct`): expressed **in percent on a 0-100 scale**, applied per nullable field. Defaults to `1.0` (= 1%). This is a tunable operational policy, not an externally mandated figure.
- Drift policy (`SchemaContract.forbid_unknown_fields`): `False` (default) reports undeclared fields as drift alerts; `True` quarantines records that carry them.

## Workflow

1. **Contract Specification Setup**:
   - Define field specifications and nullability constraints for market data entities (`TickRecord`, `OrderBookSnapshot`, `OHLCVBar`).
   - `DataSchemaContractVerifier.__init__` rejects malformed contracts (no fields, duplicate field names, `min_value > max_value`, `max_allowed_null_pct` outside 0-100) with `ValueError`, so a misconfigured contract fails at setup rather than silently passing every record.
2. **Batch & Record Parsing**:
   - Inspect incoming dictionary/JSON records. A payload that is not a mapping is quarantined; it never aborts the batch loop.
   - Audit required field presence $\implies$ Catch missing fields.
   - Audit data types $\implies$ Catch type mutations. `int` widens to `float`, but `bool` never satisfies a numeric field.
   - Audit finiteness $\implies$ Reject `NaN`/`±Inf` before bounds are evaluated (unless the field sets `allow_non_finite`), since non-finite values silently pass every comparison.
   - Audit numeric range bounds $\implies$ Catch impossible outliers ($P \le 0$ or $V < 0$).
   - All violations in a record are collected, not just the first: `QuarantinedRecord.violations` lists each one, and `violation_reason` joins them.
3. **Quarantine Routing (DLQ)**:
   - Separate compliant records from non-compliant records.
   - Direct invalid payloads to Dead Letter Queue (DLQ) for alerting. `raw_payload` is a shallow snapshot taken at validation time, so later mutation of the caller's record cannot rewrite DLQ evidence.
4. **Batch-Level Null Ceiling**: Compute the null rate per nullable field across the records that survive per-record validation — i.e. the records that actually reach the pipeline — and flag any field exceeding `max_allowed_null_pct`. The ceiling is inclusive: a rate exactly equal to the limit is not a breach.
5. **Schema Drift Detection**: Collect every undeclared field observed across the batch into `observed_unknown_fields` and raise a drift alert. A vendor renaming `ask_sz` to `ask_volume` surfaces as both a missing declared field and a new undeclared one.
6. **Audit Report Generation**: Output structured `SchemaContractValidationReport`. `is_batch_valid` is `True` only when zero records were quarantined **and** no null ceiling was breached.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Silent Schema Drift Ingestion**: Allowing un-validated vendor JSON payloads to enter Pandas DataFrames, turning float columns into `object` types and crashing backtests.
- **Ignoring Null Value Spikes**: Allowing high percentages of null bid/ask values during fast markets, triggering zero-division errors in feature engineering.
- **Dropping Bad Data Without Dead Letter Logging**: Silently discarding invalid records without logging them to a DLQ, masking upstream vendor feed degradations.
- **Treating NaN as an In-Range Number**: `NaN` fails every comparison, so `min_value`/`max_value` bounds accept it silently. A range check alone will pass a `NaN` price straight into the feature store — finiteness must be asserted before bounds are evaluated.
- **Letting `bool` Satisfy a Numeric Contract**: `bool` is a subclass of `int` in Python, so a naive `isinstance(val, int)` accepts JSON `true`/`false` as a volume. Reject `bool` explicitly unless the contract declares it.
- **Aborting a Batch on One Malformed Payload**: Letting a `None` or non-mapping record raise out of the validation loop discards the whole batch, including the records that were fine. Quarantine it as a record-level violation instead.
- **Assuming the DLQ Payload Is Immutable**: Storing a reference to the caller's dict lets downstream mutation silently rewrite the quarantined evidence, destroying the forensic trail the DLQ exists to preserve.

## Verification

- Instantiate `DataSchemaContractVerifier`. Define `TickContract` (`price`: float > 0, `volume`: int >= 0, `symbol`: str, non-nullable). Input 100 valid ticks + 2 corrupt ticks (one missing `price`, one string volume `"five"`). Verify engine validates 100 records, routes 2 records to DLQ, and generates a schema audit report.
- Confirm a `NaN` price and a boolean volume are both quarantined, not accepted.
- Confirm a batch whose nullable field exceeds `max_allowed_null_pct` returns `is_batch_valid == False` with a populated `null_breach_fields`, even when no individual record was quarantined.
- Run `python -m unittest discover -s skills/data-pipeline-schema-contract-testing/scripts`.

## Related Skills

- `data-quality-monitoring-dashboard`
- `cross-vendor-timestamp-precision-reconciliation`
---
