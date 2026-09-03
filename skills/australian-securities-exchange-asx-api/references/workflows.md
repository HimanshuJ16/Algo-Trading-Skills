# Workflows for ASX API Connectivity

1. **Environment Setup**: Determine if the deployment targets the CDE (Customer
   Development Environment) or Production. Set `is_cde_environment` accordingly and
   verify endpoints; a host whose name begins with `test` while `is_cde_environment`
   is False produces a warning. Note ASX runs **two** test environments, selected by
   `TargetSubID (57)`: `TESTB` (CDE) and `TESTC` (CDE+); production is `PROD`. The
   boolean flag does not distinguish CDE from CDE+, so carry the actual `TargetSubID`
   in your FIX engine configuration and assert it at logon.

2. **Topology Verification**: If the strategy is high-frequency market making,
   provision servers within the ALC (Australian Liquidity Centre) and select
   `AsxProtocol.OUCH` or `ITCH`. If it is a slower VWAP/TWAP execution algorithm, a
   standard ASX Net Global connection with `AsxProtocol.FIX_5_0_SP2` is sufficient.
   OUCH/ITCH with `is_alc_colocated=False` is rejected at construction — ITCH is a
   multicast feed and OUCH is provisioned on an ALC cross connect, so neither has a
   documented remote delivery path.

3. **Engine Initialization**: Instantiate `AsxIntegrationEngine` with the correct
   `AsxProtocol`. `host`, `comp_id` and `port` are validated (non-empty, non-empty,
   and in [1, 65535] respectively). For FIX, set `heartbeat_interval_seconds` in
   [10, 60]; use **30**, the only value ASX recommends — the spec's floor is worded
   both as "greater than 10 seconds" and "lower than 10 seconds will result in a
   Logout", so exactly 10 is ambiguous. The engine exposes an `AsxSequenceTracker`
   only for FIX.

4. **Session-Schedule Check**: Before sending order-entry traffic, call
   `AsxSessionSchedule.phase_at(dt)` with a **tz-aware** datetime (the schedule
   converts it to Sydney local time; a naive datetime is read as already-Sydney,
   which on a UTC host is a 10-11 hour error). New orders are accepted in PRE_OPEN,
   OPENING_AUCTION, NORMAL, PRE_CSPA, CLOSING_AUCTION, and POST_CLOSE — but in
   POST_CLOSE only **at the CSPA price**; anything else is rejected by ASX. New
   orders are NOT accepted in ADJUST or CLOSED.

   Use `is_amend_cancel_window(dt)` rather than `is_order_entry_window(dt)` when the
   question is "can I still pull my resting orders?". ADJUST (16:21:30-18:50 Sydney)
   accepts no new orders and executes no trades, but does allow cancels and amends;
   an unwind or kill-switch gated on the order-entry predicate alone will wrongly
   believe it is powerless during that window.

   Remember the OSPA (15s) and CSPA (30s) starts are randomised — wait for the ASX
   Trade phase message rather than assuming the nominal boundary. The schedule
   models a normal trading day only: it knows nothing about non-trading days,
   halts, or instrument suspensions, which must be gated separately.

5. **Session Logon**: Call `connect()` to transition DISCONNECTED -> CONNECTING ->
   CONNECTED. The engine logs the current market phase and warns if the phase accepts
   no new orders. For FIX, the logon carries `HeartBtInt (108)`, `TargetSubID (57)`,
   `TargetCompID = ASXTRADE`, `Username (553)`/`Password (554)`, and
   `MsgSeqNum (34) = 1`.

   If the username, `SenderCompID`, `TargetCompID`, IP address, or header fields such
   as `BeginString`/`MsgType` are invalid, ASX **terminates the session immediately
   and sends no Logout message**. Reconnect logic must treat a silent socket close
   during logon as a configuration failure and back off, not wait for a Logout.

6. **Heartbeating**: Maintain the connection via Heartbeat (0) at the negotiated
   `HeartBtInt`; respond to Test Request (1) with a Heartbeat. An interval below 10s
   triggers an ASX Logout with `SessionStatus (1409) = 101`; above 60s yields 104.

7. **Sequence-Number Management and Reconnect Recovery**: Allocate outbound sequence
   numbers via `AsxSequenceTracker.next()`. On reconnect, classify each inbound
   `MsgSeqNum (34)` with `AsxSequenceTracker.classify_inbound(last_seen, received,
   poss_dup)` and branch on the result — the three anomalies need different actions
   and conflating them corrupts the session:

   - `IN_SEQUENCE` — process normally.
   - `GAP` (received > expected) — issue **ResendRequest (2)** for the missing range
     before resuming order traffic.
   - `POSS_DUP` (received < expected with `PossDupFlag (43) = Y`) — a legitimate
     retransmission answering your own ResendRequest. Discard if already processed.
     It is **not** a session error, and treating it as one aborts your own recovery.
   - `TOO_LOW` (received < expected without `PossDupFlag`) — unrecoverable per the
     FIX session layer. Send **Logout (5)** with `SessionStatus (1409) = 9` and
     terminate the connection. Do **not** issue a ResendRequest.

   `detect_inbound_gap(last_seen, received)` remains as a boolean tripwire but cannot
   express which action applies and flags a legitimate `PossDupFlag = Y` replay as a
   gap; prefer `classify_inbound` for any recovery decision.

8. **Choosing the Logon Recovery Policy (decide BEFORE go-live)**: The choice between
   a `141=N` recovering logon and a `141=Y` clean logon is not a matter of taste — it
   determines whether you can still see your own live orders.

   - **`ResetSeqNumFlag (141=N)` + ResendRequest (2)** — the recovering path. This is
     how you retrieve active GTC and GTD orders after the morning logon: ASX restates
     them as `ExecType = Restated (150=D)` with
     `ExecRestatementReason = GT renewal/restatement (378=1)`. It is also how you
     learn which orders were killed by **Cancel on Disconnect** — orders sent with
     `ExecInst (18) = "o"` are cancelled when their FIX session drops, and ASX does
     **not** re-send those Execution Reports automatically on reconnect.
   - **`ResetSeqNumFlag (141=Y)`** — the clean path. Per the ASX spec, a 141=Y session
     **cannot retrieve GTC and GTD orders via a ResendRequest**. You will have live
     resting orders in the market that this session cannot enumerate. Only choose it
     when you can reconcile positions and working orders from an independent source
     (drop copy, or a broker/exchange order enquiry) before re-enabling order flow —
     and treat `AsxSequenceTracker.reset()` as an operation that must be paired with
     that reconciliation.

   Because `ClOrdID` must be unique across an order's entire lifetime, and GTC/GTD
   orders outlive the session, include the date in the `ClOrdID`.

9. **Timezone Handling (Recovery)**: All schedule decisions must use Sydney local
   wall-clock. Convert any UTC instant to Sydney time once, at the boundary of the
   scheduler, using `zoneinfo.ZoneInfo("Australia/Sydney")`. Do not spread UTC
   conversions across trading logic. During the AEST->AEDT transition the local
   session times do not change, but naive UTC-offset arithmetic will. Production
   hosts must ship IANA tzdata: without it the module falls back to a fixed UTC+10
   and logs a warning, and that fallback is wrong by one hour throughout AEDT. See
   the `daylight-saving-time-transition-handling` skill.

10. **Logout / Disconnect**: Call `disconnect()` to mark the session DISCONNECTED.
    For FIX, ASX requires an exchange of Logout (5) messages; a disconnect without
    that exchange is an abnormal condition. Flag it for the recovery workflow above,
    and remember that the FIX session lifetime is bounded by the trading day and is
    not ended by connectivity loss or a Logout.

11. **CDE -> Production Promotion**: Flip `is_cde_environment` to False, switch
    `TargetSubID (57)` from `TESTB`/`TESTC` to `PROD`, point endpoints at production,
    re-run the test suite, and confirm ASIC AOP filters and pre-trade controls are
    live before enabling order flow.

12. **Schedule Currency Review**: The phase table is dated (see
    `references/standards.md`) and reflects ASX Service Release 15, effective
    23 June 2025. Re-verify it against the ASX cash market trading hours page after
    any ASX service release; a stale schedule silently mis-gates order entry rather
    than failing loudly.
