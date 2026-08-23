# Pre-Flight Checklist

- [ ] Is each event's **ex-date convention declared by the feed**, never inferred from event type or value?
- [ ] Are sub-25% distributions (`PRE_RECORD`) sequenced `declaration <= ex <= record <= payment`, with `ex == record` accepted for T+1 markets?
- [ ] Are distributions of 25% or more — forward splits, large special dividends, spin-offs — sequenced `declaration <= record <= payment <= ex` per FINRA Rule 11140(b)(2), rather than rejected as out-of-order?
- [ ] Are invalid events rejected at construction (unknown event type, out-of-order dates, non-positive or non-finite value)?
- [ ] Are duplicate `event_id` re-broadcasts deduplicated instead of double-counted?
- [ ] Is a re-broadcast whose payload *differs* escalated as an amendment (ISO 15022 MT 564 `REPL`) rather than dropped as a routine duplicate?
- [ ] Is upcoming corporate action risk query active within a forward window (sized for weekends/holidays, since the window is calendar days)?
- [ ] Is dividend entitlement receivable calculated from the position held at the close preceding the ex-date, for the latest dividend past its record date?
- [ ] Is a special dividend sharing the regular dividend's record date accrued separately, rather than resolved to a single "latest" event?
- [ ] Is reconciliation symmetric — events missing from *either* feed are flagged, along with symbol, event-type, ex-date-convention, ex/record/payment date and value mismatches?
- [ ] Are vendor feed discrepancies flagged and escalated before ex-date, rather than silently resolved toward one vendor?
