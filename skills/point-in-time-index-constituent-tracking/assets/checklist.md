# Pre-Flight Checklist

## Event log

- [ ] Are historical index constituent additions **and** deletions both logged, including
      names removed after bankruptcy, merger, or delisting?
- [ ] Are all `effective_date` values zero-padded ISO-8601 (`YYYY-MM-DD`) or `datetime.date`?
- [ ] Is the vendor's deletion-date convention confirmed (not assumed) — half-open first
      non-member day, or inclusive last day of membership reconciled by adding one session?
- [ ] Is a stable `security_id` (CUSIP / SEDOL / PERMNO / FIGI) attached to every event, so
      reused tickers do not collapse into one membership timeline?
- [ ] Is `effective_date` genuinely the effective date, not the announcement date and not
      the vendor's load date?
- [ ] Was the event log sourced from historical membership records rather than
      reverse-engineered from today's constituent list?

## Resolution

- [ ] Is the point-in-time universe evaluated as `add_date <= T < del_date` (half-open), so
      an addition effective on `T` is a member and a deletion effective on `T` is not?
- [ ] Is `report.status` checked for `INDEX_NOT_FOUND` before the universe is used, so a
      misspelled index name cannot pass as an empty index?
- [ ] Is `report.data_quality_warnings` empty, or every warning triaged?
- [ ] Does the resolved universe reproduce identically when the event log is ingested in a
      different order?

## Survivorship-bias audit

- [ ] Is the ghost audit executed with `current_static_universe` supplied?
- [ ] Is `survivorship_bias_ghost_count is None` handled as "not audited" rather than zero?
- [ ] Is a zero ghost count on a historical date investigated rather than accepted?
- [ ] Are delisted and bankrupt names given terminal settlement values downstream, not just
      included in the universe?
