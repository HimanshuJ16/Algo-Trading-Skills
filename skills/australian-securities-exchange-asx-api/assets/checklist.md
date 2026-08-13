# Checklist for ASX Integration

## Topology & Protocol
- [ ] Confirm `AsxProtocol.OUCH` and `AsxProtocol.ITCH` are strictly blocked if `is_alc_colocated` is False.
- [ ] Confirm FIX 5.0 SP2 is used for standard routing; OUCH is reserved for HFT inside the ALC.
- [ ] Confirm a non-CDE host whose name begins with `test` triggers a warning (no silent promotion of test hosts to prod).

## FIX Session Configuration
- [ ] `heartbeat_interval_seconds` is in [10, 60]; ASX-recommended value 30 is used unless deliberately tuned.
- [ ] `CompID`, `TargetCompID = ASXTRADE`, and `TargetSubID (57)` are provisioned by ASX.
- [ ] Session-recovery policy is pre-decided: ResendRequest (2) vs. clean `ResetSeqNumFlag (141=Y)` logon.

## Session Schedule & Timezone
- [ ] Order-entry traffic is gated on `AsxSessionSchedule.is_order_entry_window(...)`; CLOSED is blocked or warned.
- [ ] All schedule checks use Sydney wall-clock time (AEST UTC+10 / AEDT UTC+11) via `zoneinfo.ZoneInfo("Australia/Sydney")`; UTC conversions happen once at the scheduler boundary, never inside trading logic.
- [ ] OSPA (15s) and CSPA (30s) randomised starts are handled — code waits for the ASX Trade phase message, not the nominal boundary.

## Sequence Numbers
- [ ] Outbound `MsgSeqNum (34)` is allocated via `AsxSequenceTracker.next()` (monotonic from 1).
- [ ] On reconnect, inbound gaps are detected via `AsxSequenceTracker.detect_inbound_gap(...)` and resolved (ResendRequest or clean ResetSeqNumFlag logon) before order traffic resumes.

## Regulatory & Pre-Trade Controls
- [ ] ASIC Market Integrity Rules (Securities Markets) 2017 compliance posture documented.
- [ ] Automated filters (Part 5.6 / RG 241) are live with direct control over filter parameters; the algo cannot bypass them.
- [ ] Centre Point block-trade minimum thresholds are validated per product (Rule 6.1.2 pre-trade transparency).

## Environment & Verification
- [ ] Verify connection endpoints point to the CDE prior to production release.
- [ ] For FIX, `disconnect()` exchanges Logout (5) messages; an unexchanged logout is flagged for recovery.
- [ ] Run test suite: `python scripts/test_australian_securities_exchange_asx_api.py`.

## Monitoring
- [ ] Heartbeat liveness and Test Request round-trip latency are monitored.
- [ ] Unexpected state transitions (CONNECTED -> FAILED, logout-without-Logout-exchange) alert on-call.
- [ ] Phase-transition timestamps are logged so schedule/timeline bugs surface in post-trade review.

## Sign-off
- Connectivity Engineer: ___________________________
- Date: ___________________________
