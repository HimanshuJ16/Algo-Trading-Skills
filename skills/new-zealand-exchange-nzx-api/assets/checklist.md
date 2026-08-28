# Checklist for NZX Main Board Order Entry

## Session Identity
- [ ] `BeginString`, `SenderCompID`, `TargetCompID` (and `TargetSubID` if issued) are taken from the FIX specification NZX gave this firm — not guessed, not copied from another venue's skill or sample.
- [ ] `MsgSeqNum (34)` comes from the FIX engine that owns the session, via `seq_num=` or a `seq_num_provider`.
- [ ] Logon/Logout, heartbeating, sequence persistence and ResendRequest handling live in the FIX engine, not in this module.
- [ ] `field_delimiter` is SOH on every path that reaches the wire; `|` is used only in logs and tests.

## Price Steps
- [ ] Every instrument is classified `EQUITY`, `FUND` or `DEBT_YIELD_QUOTED` before an order is priced.
- [ ] Listed funds use the $0.001 step at **every** price level — the band schedule is not applied to them.
- [ ] Equity bands are correct: up to $0.19 → $0.001; $0.20–$1.995 → $0.005; above $2.00 → $0.01.
- [ ] `NZXTickSchedule` is reconciled against the current NZX notice on a defined cadence (Participant Rule 11.9.1 lets NZX respecify steps at any time).
- [ ] Yield-quoted NZDX debt is routed to a yield-aware adapter, not through this engine.

## Order Validation
- [ ] An unrecognised `side`, `order_type` or `time_in_force` is rejected, never coerced into a working alternative.
- [ ] `quantity` is a positive whole number; floats, zero, negatives and booleans are rejected.
- [ ] Non-positive prices are rejected.
- [ ] `Price (44)` is emitted for LIMIT orders only and omitted on MARKET orders.
- [ ] `Symbol (55)` carries the bare NZX ticker (`FPH`), not vendor symbology (`FPH.NZ`).
- [ ] `ClOrdID` and `Symbol` are screened for SOH, `=`, `|` and non-ASCII (FIX field injection).

## Message Framing
- [ ] Every message carries `BeginString (8)`, `BodyLength (9)`, `MsgSeqNum (34)`, `SendingTime (52)` and `CheckSum (10)`.
- [ ] `SendingTime (52)` and `TransactTime (60)` are FIX `UTCTimestamp` (`YYYYMMDD-HH:MM:SS.sss`, UTC) — not epoch milliseconds.
- [ ] `BodyLength` and `CheckSum` are independently recomputed from the payload bytes in test.
- [ ] `Currency (15) = NZD` is present.

## Session Timing
- [ ] Order entry is gated on Pre-Open / Normal / Pre-Close; Adjust and Enquiry are blocked for new orders.
- [ ] All phase checks use Auckland wall-clock via `zoneinfo.ZoneInfo("Pacific/Auckland")`; UTC conversion happens once at the scheduler boundary, never inside trading logic.
- [ ] Production hosts ship IANA tzdata (the UTC+12 fallback misclassifies NZDT by an hour and logs a warning).
- [ ] A NZ trading-holiday calendar gates the schedule — `NZXSessionSchedule` is time-of-day only.
- [ ] Auction-sensitive logic waits for the exchange session-state message; nothing assumes a hard 10:00:00 or 17:00:00 boundary (both are randomised ±30s).

## Order Lifecycle
- [ ] `OrderCancelRequest` uses a NEW `ClOrdID (11)` distinct from `OrigClOrdID (41)`.
- [ ] A cancel request is treated as `PENDING_CANCEL`; fills are still applied until an ExecutionReport confirms `OrdStatus=4`.
- [ ] Position state is driven from `CumQty (14)` / `LeavesQty (151)`, never by accumulating `LastQty (32)`.
- [ ] `PossDupFlag (43)=Y` resends are detected and not double-counted.
- [ ] A Reject (35=3) or OrderCancelReject (35=9) is never decoded as an ExecutionReport.
- [ ] CheckSum verification is enabled on inbound parsing; a mismatch is treated as corrupt, not acted on.

## Audit & Verification
- [ ] Local rejects (`fix_msg_type == ""`, empty payload) are persisted and are distinguishable in post-trade review from exchange rejections.
- [ ] `rejection_reason`, `normalized_price` and `tick_size` are captured on every reject.
- [ ] Run test suite: `python scripts/test_new_zealand_exchange_nzx_api.py`.

## Sign-off
- Connectivity Engineer: ___________________________
- Date: ___________________________
