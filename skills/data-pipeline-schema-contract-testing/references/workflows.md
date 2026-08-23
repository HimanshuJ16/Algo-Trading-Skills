# Workflows for Data Pipeline Schema Contract Testing

1. **Schema Contract Definition**:
   - Register required fields, data types, nullability rules, and inclusive numeric bounds.
   - Set `max_allowed_null_pct` (percent, 0-100) and `forbid_unknown_fields` for the feed.
   - Construction validates the contract itself: empty specs, duplicate field names,
     inverted bounds, or an out-of-range null ceiling raise `ValueError` immediately.
2. **Payload Validation**:
   - Reject non-mapping payloads as record-level violations rather than letting them
     abort the batch.
   - Inspect each payload for field presence, type safety (with `bool` excluded from
     numeric fields), finiteness, and range limits.
   - Accumulate every violation per record, not only the first, so one DLQ entry
     explains all of what is wrong with the payload.
3. **Dead Letter Queue (DLQ) Quarantine**:
   - Route invalid records to DLQ for developer investigation, snapshotting the payload
     so later mutation cannot alter the evidence.
4. **Batch-Level Null Ceiling**:
   - Measure the per-field null rate over the records that pass validation — the ones
     that actually reach downstream consumers — and breach the batch if any nullable
     field exceeds the ceiling.
5. **Schema Drift Detection**:
   - Aggregate undeclared fields seen anywhere in the batch and alert on them. Under
     `forbid_unknown_fields`, quarantine the carrying records as well.
6. **Audit Reporting**:
   - Generate schema compliance metrics, per-field null rates, drift signals, and
     violation summaries. Treat the batch as valid only when nothing was quarantined
     and no null ceiling was breached.
