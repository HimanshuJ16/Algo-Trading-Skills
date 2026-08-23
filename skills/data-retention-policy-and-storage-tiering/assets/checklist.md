# Pre-Flight Checklist

## Dataset description
- [ ] Is every dataset classified by `data_type`, age (days), size (GB) and current tier?
- [ ] Is `age_days` derived from the **newest** record in the dataset, not the oldest?
- [ ] Is `regulatory_retention_years` sourced from a compliance determination for that
      specific record type, rather than a blanket period applied to everything?
- [ ] Are `object_count` and `days_in_current_tier` supplied where known?

## Retention safety
- [ ] Is `purge_expired_records` left at `False` unless a human has approved deletion?
- [ ] Are books-and-records types (`TRADE_AUDIT_LOG`, `ORDER_MEMORANDUM`,
      `COMMUNICATIONS`) covered by `regulated_data_types` so they are never auto-purged?
- [ ] Has every `retention_expired=True` dataset been reviewed by a human before any
      deletion is executed?
- [ ] Are records inside the two-year "easily accessible" window (SEC Rule 17a-4(a),
      (b)(1)) held only in millisecond-retrieval classes, never Deep Archive?
- [ ] Does the electronic recordkeeping system satisfy either WORM or the 17a-4(f)
      audit-trail alternative for the records being tiered?

## Cost realism
- [ ] Has `pricing_map` been set to the actual region and negotiated rates rather than
      the illustrative us-east-1 defaults?
- [ ] Have `notes` been read on every recommendation, not just the savings figure?
- [ ] Are small objects (< 128 KB average) compacted before transition? They will not
      transition by default and are billed at a 128 KB minimum in IA/Glacier IR.
- [ ] Has per-object Glacier metadata (32 KB archive-rate + 8 KB Standard-rate) been
      accounted for on high-object-count fleets?
- [ ] Is the minimum storage duration of the **source** class satisfied (30 d IA,
      90 d Glacier IR, 180 d Deep Archive) before transitioning out?
- [ ] Does the transition pay back within the dataset's remaining retention life?

## Audit
- [ ] Is the full `DataRetentionAuditReport` persisted, including `notes` and
      `retention_expired`, as the record of why each dataset moved or stayed?
