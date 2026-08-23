# Pre-Flight Checklist

- [ ] Are macro events (FOMC, CPI, NFP, expiry days) registered with pre/post buffers anchored to the release instant **in the release's own timezone**, not a fixed UTC time?
- [ ] Are multi-part events covered — e.g. the FOMC press conference 30 minutes after the 2:00 p.m. ET statement — by a second window or a longer post-buffer?
- [ ] Is `set_calendar_as_of` called on every calendar refresh, and is `max_calendar_staleness_sec` set to the refresh SLA so a stale calendar fails closed?
- [ ] Has the calendar been re-pulled after any known schedule disruption (agency reschedules and cancellations are real: BLS, 2025 lapse in appropriations)?
- [ ] Are daily session windows timezone-aware (IANA zone + local `HH:MM`), so they track daylight-saving transitions?
- [ ] Are early closes and holidays supplied through `session_overrides` from a real exchange calendar (NYSE closes at 1:00 p.m. ET on several dates a year)?
- [ ] Is every environment the pipeline deploys to registered as either production or exempt, so a typo is denied rather than exempted?
- [ ] Do buffers reflect measured rollback time, rather than the shipped 60/15-minute defaults?
- [ ] Does break-glass require two **distinct named** approvers plus a justification — not two booleans one person can set?
- [ ] Are approver identities resolved from authenticated IAM claims before the request is built, never taken from an unverified payload?
- [ ] Are break-glass approvals logged and persisted with both identities, satisfying the change record required of EU/EEA firms (RTS 6 Art. 11: when, who, who approved, nature)?
- [ ] Does the CI/CD job actually **fail** on `is_approved == False`, and does it match on status constants rather than prose?
- [ ] Does the blocked-deployment message surface `freeze_ends_epoch_sec` and all `active_freeze_labels`, so the operator knows when the freeze truly lifts?
- [ ] Are all reports — approvals, denials, overrides — archived to the change-record audit store?
- [ ] Is break-glass frequency reviewed, so routine use is treated as a signal to change the windows or the release process?
- [ ] Is it understood that this gate prevents new risk only, and that running risk needs a kill switch instead?
