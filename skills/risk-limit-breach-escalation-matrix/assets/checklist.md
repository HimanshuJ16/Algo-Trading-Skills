# Pre-Flight Checklist — Risk Limit Breach Escalation Matrix

## Ladder configuration

- [ ] Are multi-tier thresholds configured for every risk limit, with strictly ascending thresholds and non-decreasing severity and action?
- [ ] Have the tier multipliers been calibrated against this book's realised drawdowns, rather than left at the 1.0/1.2/1.5/2.0 house defaults?
- [ ] Is `sustained_breach_seconds` set from an actual view of how long a breach may safely persist, rather than left at the 300 s default?
- [ ] Does every tier route to at least one channel, and do the CRITICAL tiers reach PagerDuty **and** compliance ticketing?
- [ ] Are the acknowledgement deadlines ones the on-call rota can actually meet? (They are house defaults; no regulator sets them.)

## Per-metric wiring

- [ ] Is `direction` set correctly for each metric — `UPPER` for ceilings, `LOWER` for floors?
- [ ] For every `UPPER` metric, does the upstream detector emit a **non-negative magnitude**? (A drawdown emitted as a negative number is the most likely way this control fails silently.)
- [ ] Is `duration_seconds` genuinely measured upstream? A constant `0.0` disables duration escalation entirely.
- [ ] Is `timestamp_iso` emitted with a UTC offset?

## Failure handling

- [ ] Is `process_breach_event()` wrapped in `except EscalationMatrixError`, so a validation failure cannot kill the monitoring loop?
- [ ] Does a rejected input raise an alert of its own? A refused event means the control is blind to that metric, which is not the same as "no breach".
- [ ] Is the enforcement layer idempotent on `event_id`? Replay protection in this engine does not make the order gateway safe.
- [ ] Is a delivery failure on a CRITICAL notification treated as an incident in its own right?

## Latching and de-escalation

- [ ] Is escalation latching enabled (or its absence deliberately justified)?
- [ ] Is there a documented, authorised procedure for calling `reset_incident()`, and is that call itself audited?

## Audit trail

- [ ] Is every escalation decision — including sub-threshold `NONE` decisions — persisted durably?
- [ ] Does the persisted record keep the inputs behind the verdict (`current_value`, `limit_value`, `duration_seconds`, `timestamp_iso`, `matched_threshold`), so the decision can be re-derived at review time?
- [ ] Is the trail drained from memory on a long-running process? The engine never truncates it.

## Regulatory positioning

- [ ] Is there a **pre-trade** control that blocks or rejects breaching orders before entry? (MiFID II RTS 6 Art. 15; SEC Rule 15c3-5(c)(1)(i).) This matrix supplements it and never replaces it.
- [ ] For an EU firm: does the whole path — metric computation, transport, decision, notification — generate the alert within the five seconds RTS 6 Art. 16 requires?
- [ ] For a US broker-dealer: is the enforcement layer under the firm's direct and exclusive control? (Rule 15c3-5(d).)
- [ ] Is the ladder itself covered by the annual effectiveness review and CEO certification? (Rule 15c3-5(e).)
- [ ] Is it documented internally that the thresholds and timings are house defaults, so they are never cited to a regulator as externally mandated values?
