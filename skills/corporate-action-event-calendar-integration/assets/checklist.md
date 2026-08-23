# Pre-Flight Checklist

- [ ] Are declaration, ex-date, record date, and payment date parsed correctly and in sequence (`declaration <= ex <= record <= payment`, with `ex == record` accepted for T+1 markets)?
- [ ] Are invalid events rejected at construction (unknown event type, out-of-order dates, non-positive or non-finite value)?
- [ ] Are duplicate `event_id` re-broadcasts deduplicated instead of double-counted?
- [ ] Is upcoming corporate action risk query active within a forward window (sized for weekends/holidays, since the window is calendar days)?
- [ ] Is dividend entitlement receivable calculated from the position held at the close preceding the ex-date, for the latest dividend past its record date?
- [ ] Is reconciliation symmetric — events missing from *either* feed are flagged, along with ex/record/payment date and value mismatches?
- [ ] Are vendor feed discrepancies flagged and escalated before ex-date, rather than silently resolved toward one vendor?
