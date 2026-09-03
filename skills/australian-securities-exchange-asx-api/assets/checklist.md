# Checklist for ASX Integration

## Topology & Protocol
- [ ] Confirm `AsxProtocol.OUCH` and `AsxProtocol.ITCH` are strictly blocked if `is_alc_colocated` is False.
- [ ] Confirm FIX 5.0 SP2 is used for standard routing; OUCH is reserved for HFT inside the ALC.
- [ ] Confirm a non-CDE host whose name begins with `test` triggers a warning (no silent promotion of test hosts to prod).
- [ ] Confirm `host`/`comp_id` are non-empty and `port` is in [1, 65535] — invalid connection parameters fail at construction, not at first socket use.

## FIX Session Configuration
- [ ] `heartbeat_interval_seconds` is in [10, 60]; the ASX-recommended 30 is used unless deliberately tuned (exactly 10 is ambiguous in the spec — avoid it).
- [ ] `CompID`, `TargetCompID = ASXTRADE`, and `TargetSubID (57)` are provisioned by ASX, and `TargetSubID` matches the target environment: `TESTB` (CDE), `TESTC` (CDE+), `PROD`.
- [ ] Reconnect logic treats a silent socket close during logon (invalid CompID/IP/header) as a config failure — it does not block waiting for a Logout that ASX will never send.

## Session Schedule & Timezone
- [ ] The phase table is date-stamped and matches the current ASX cash market trading hours page (post-Service-Release-15, effective 23 June 2025 — no staggered 10:00-10:09 open, Post Close present).
- [ ] New-order traffic is gated on `AsxSessionSchedule.is_order_entry_window(...)`; unwinds and kill-switch cancellations are gated on `is_amend_cancel_window(...)` so ADJUST does not block them.
- [ ] Post Close (16:11:00-16:21:30) is handled as a trading session, and orders sent in it carry the CSPA price — anything else is rejected by ASX.
- [ ] All schedule checks pass a **tz-aware** datetime; Sydney conversion (`zoneinfo.ZoneInfo("Australia/Sydney")`) happens once at the scheduler boundary, never inside trading logic. No code path feeds `datetime.now()` from a non-Sydney host into the schedule.
- [ ] Production hosts ship IANA tzdata; the fixed UTC+10 fallback warning is alerted on, not ignored (it is wrong by an hour during AEDT).
- [ ] OSPA (15s) and CSPA (30s) randomised starts are handled — code waits for the ASX Trade phase message, not the nominal boundary.
- [ ] Non-trading days, halts, instrument suspensions, and the AEDT-delayed ETFs (OOO, QAG, QCB) are gated by a separate calendar/instrument-state check — the schedule models a normal trading day only.

## Sequence Numbers & Reconnect Recovery
- [ ] Outbound `MsgSeqNum (34)` is allocated via `AsxSequenceTracker.next()` (monotonic from 1).
- [ ] Inbound numbers are classified with `classify_inbound(...)`, and each branch is wired to the correct action: `GAP` -> ResendRequest (2); `POSS_DUP` -> discard, no session error; `TOO_LOW` -> Logout (5) with `SessionStatus 1409=9` and terminate.
- [ ] The logon recovery policy is pre-decided and documented: `141=N` + ResendRequest (recovers GTC/GTD and Cancel-on-Disconnect reports) vs. a clean `141=Y` logon.
- [ ] If `141=Y` is ever used, an independent reconciliation (drop copy or order enquiry) runs before order flow resumes — a 141=Y session cannot enumerate its own live GTC/GTD orders.
- [ ] Orders using Cancel on Disconnect (`ExecInst 18 = "o"`) are reconciled after every reconnect via ResendRequest; the local ledger is never assumed to still match the exchange.
- [ ] `ClOrdID` is unique across the full lifetime of GTC/GTD orders (include the date).

## Regulatory & Pre-Trade Controls
- [ ] ASIC Market Integrity Rules (Securities Markets) 2017 compliance posture documented.
- [ ] Automated filters (Part 5.6 / RG 241) are live with direct control over filter parameters; the algo cannot bypass them.
- [ ] Centre Point block-trade minimum thresholds are validated per product (Rule 6.1.2 pre-trade transparency).

## Environment & Verification
- [ ] Verify connection endpoints point to the CDE prior to production release.
- [ ] For FIX, `disconnect()` exchanges Logout (5) messages; an unexchanged logout is flagged for recovery.
- [ ] Run test suite: `python -m unittest discover -s skills/australian-securities-exchange-asx-api/scripts`.

## Monitoring
- [ ] Heartbeat liveness and Test Request round-trip latency are monitored.
- [ ] Unexpected state transitions (CONNECTED -> FAILED, logout-without-Logout-exchange) alert on-call.
- [ ] Phase-transition timestamps are logged so schedule/timezone bugs surface in post-trade review.

## Sign-off
- Connectivity Engineer: ___________________________
- Date: ___________________________
