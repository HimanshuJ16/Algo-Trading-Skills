# Workflows for ASX API Connectivity

1. **Environment Setup**: Determine if the deployment targets the CDE (Customer
   Development Environment) or Production. Set `is_cde_environment` accordingly and
   verify endpoints; a host whose name begins with `test` while `is_cde_environment`
   is False produces a warning.

2. **Topology Verification**: If the strategy is high-frequency market making,
   provision servers within the ALC (Australian Liquidity Centre) and select
   `AsxProtocol.OUCH` or `ITCH`. If it is a slower VWAP/TWAP execution algorithm, a
   standard ASX Net Global connection with `AsxProtocol.FIX_5_0_SP2` is sufficient.
   OUCH/ITCH with `is_alc_colocated=False` is rejected at construction.

3. **Engine Initialization**: Instantiate `AsxIntegrationEngine` with the correct
   `AsxProtocol`. For FIX, set `heartbeat_interval_seconds` in [10, 60] (ASX
   recommends 30). The engine exposes an `AsxSequenceTracker` only for FIX.

4. **Session-Schedule Check**: Before sending order-entry traffic, call
   `AsxSessionSchedule.phase_at(dt)` with Sydney wall-clock time (AEST UTC+10 /
   AEDT UTC+11). Order entry is accepted in PRE_OPEN, OPENING_AUCTION, NORMAL,
   PRE_CSPA, and CLOSING_AUCTION; it is NOT accepted in CLOSED. Remember the OSPA
   (15s) and CSPA (30s) starts are randomised — wait for the ASX Trade phase
   message rather than assuming the nominal boundary.

5. **Session Logon**: Call `connect()` to transition DISCONNECTED -> CONNECTING ->
   CONNECTED. The engine logs the current market phase. For FIX, the logon carries
   `HeartBtInt (108)`, `TargetSubID (57)`, and `MsgSeqNum (34) = 1`.

6. **Heartbeating**: Maintain the connection via Heartbeat (0) at the negotiated
   `HeartBtInt`; respond to Test Request (1) with a Heartbeat. An interval below
   10s triggers an ASX Logout.

7. **Sequence-Number Management**: Allocate outbound sequence numbers via
   `AsxSequenceTracker.next()`. On reconnect, detect inbound gaps with
   `AsxSequenceTracker.detect_inbound_gap(last_seen, received)`. A gap must be
   resolved BEFORE resuming order traffic:
   - **ResendRequest (2)** — ask the exchange to replay missing messages, or
   - **Clean session** — logon with `ResetSeqNumFlag (141=Y)` and accept that prior
     messages cannot be recovered.
   Never silently skip a gap; doing so hides lost or replayed order state.

8. **Timezone Handling (Recovery)**: All schedule decisions must use Sydney local
   wall-clock. Convert any UTC instant to Sydney time once, at the boundary of the
   scheduler, using `zoneinfo.ZoneInfo("Australia/Sydney")`. Do not spread UTC
   conversions across trading logic. During the AEST->AEDT transition the local
   session times do not change, but naive UTC-offset arithmetic will. See the
   `daylight-saving-time-transition-handling` skill.

9. **Logout / Disconnect**: Call `disconnect()` to mark the session DISCONNECTED.
   For FIX, ASX requires an exchange of Logout (5) messages; a disconnect without
   that exchange is an abnormal condition. Flag it for the recovery workflow above.

10. **CDE -> Production Promotion**: Flip `is_cde_environment` to False, point
    endpoints at production, re-run the test suite, and confirm ASIC AOP filters
    and pre-trade controls are live before enabling order flow.
