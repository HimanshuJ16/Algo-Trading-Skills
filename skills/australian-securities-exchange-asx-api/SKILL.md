---
name: australian-securities-exchange-asx-api
description: >-
  Use when building direct market access to the Australian Securities Exchange over FIX
  5.0 SP2, OUCH or ITCH; covers ASX Customer Development Environment configuration, ALC
  topology rules and AEST/AEDT session-schedule awareness.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: asx, australia, fix, ouch, itch, market-connectivity
  brokers_frameworks: direct-market-access
  version: "1.3.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when building direct market access (DMA) connectivity to the Australian Securities Exchange (ASX). This engine provides the foundational configuration, topology validation, session-schedule awareness, and FIX session-state primitives required to interface with the ASX Customer Development Environment (CDE) or Production via the Australian Liquidity Centre (ALC).

It supports routing configurations for:
- **FIX 5.0 SP2**: Standard institutional order routing and drop-copy. ASX Trade FIX sessions are bounded by a single trading day, with `HeartBtInt (108)` recommended at 30s (maximum 60s; below 10s triggers an immediate Logout).
- **OUCH**: Ultra-low latency binary order entry for high-frequency strategies.
- **ITCH**: Ultra-low latency binary multicast Market-by-Order full-depth data.

The session schedule reflects ASX **Service Release 15** (effective 23 June 2025), which replaced the staggered alphabetical opening rotation with a single exchange-wide auction and added the **Post Close** trading session.

## When NOT to Use

- **Latency arbitrage over FIX**: FIX 5.0 SP2 is millisecond-tier and is the wrong transport for microsecond arbitrage; route that flow via OUCH from an ALC cross-connect instead.
- **Remote OUCH/ITCH**: OUCH is provisioned on an ALC cross-connect and ITCH is a multicast feed from the ASX Trade platform. Attempting either over ASX Net Global or any remote internet gateway is an anti-pattern and is rejected by this engine's topology validation.
- **Full FIX message serialisation**: This skill owns configuration, topology, schedule, and session-state decisions only. Building/parsing FIX tags belongs to `fix-protocol-session-management-across-venues`; binary frame parsing belongs to `binary-protocol-parsing-for-low-latency-feeds`.
- **Trading-day and instrument-state gating**: `AsxSessionSchedule` models the intraday phases of a *normal* cash-market trading day. It does not know about ASX non-trading days, trading halts, instrument suspensions, ASX 24 derivatives hours, or the three ETFs that open an hour late during AEDT. Gate those separately — see `global-exchange-holiday-calendar-handling`.

## Prerequisites

- Python 3.10+ (uses `zoneinfo` for AEDT/AEST conversion; production hosts must ship IANA tzdata — without it the module falls back to a fixed UTC+10 and logs a warning, and that fallback is wrong by one hour throughout AEDT).
- Exchange-assigned `CompID` and cross-connect details (ASX Trade FIX Order Entry uses `TargetCompID = ASXTRADE`; `TargetSubID (57)` carries the environment — `TESTB` for CDE, `TESTC` for CDE+, `PROD` for production — and `MsgSeqNum (34)` is mandatory on every message).
- For OUCH/ITCH, trading infrastructure co-located in the Australian Liquidity Centre (ALC).
- A pre-decided **session-recovery policy** for sequence-number gaps on reconnect (`ResetSeqNumFlag 141=N` + ResendRequest vs. a clean `141=Y` logon) — this choice determines whether you can still see your own live GTC/GTD orders. See Common Pitfalls.
- ASIC Market Integrity Rules (Securities Markets) 2017 compliance posture in place: automated filters and direct control over filter parameters (Part 5.6; RG 241).

## Workflow

1. **Protocol Selection**: Configure `AsxConnectionConfig` with the required `AsxProtocol` (FIX, OUCH, or ITCH) and, for FIX, a `heartbeat_interval_seconds` in [10, 60] — use 30, the only value ASX recommends.
2. **Configuration & ALC Topology Validation**: The engine validates on construction. `host` and `comp_id` must be non-empty and `port` must be in [1, 65535]. OUCH and ITCH are rejected if `is_alc_colocated` is False, because neither binary protocol has a documented remote delivery path. A non-CDE host whose name begins with `test` produces a warning.
3. **Session-Schedule Awareness**: Before sending order-entry traffic, determine the ASX Trade market phase with `AsxSessionSchedule.phase_at(...)`, passing a **tz-aware** datetime. New orders are accepted in PRE_OPEN, OPENING_AUCTION, NORMAL, PRE_CSPA, CLOSING_AUCTION, and POST_CLOSE — but in POST_CLOSE only at the CSPA price, and never in ADJUST or CLOSED. ASX randomises the OSPA start within 15s and the CSPA start within 30s — treat the ASX Trade system message as authoritative for the exact transition, not this table.
4. **Distinguish "can I send?" from "can I cancel?"**: Gate new orders on `is_order_entry_window(...)`, but gate unwinds and kill-switch cancellations on `is_amend_cancel_window(...)`. ADJUST (16:21:30-18:50 Sydney) accepts no new orders and executes no trades, yet still permits cancels and amends.
5. **Session Initialization**: `connect()` transitions DISCONNECTED -> CONNECTING -> CONNECTED, logs the current market phase, and warns when the phase accepts no new orders (typically a scheduler/timezone bug). On FIX, the engine exposes an `AsxSequenceTracker` for outbound `MsgSeqNum (34)`.
6. **Heartbeating**: Maintain the FIX session via Heartbeat (0) at the negotiated `HeartBtInt`; respond to Test Request (1) with a Heartbeat. A rejected interval returns `SessionStatus (1409)` 101 (too low) or 104 (too high).
7. **Sequence-Number Classification**: Track outbound sequence numbers monotonically. On reconnect, classify each inbound number with `AsxSequenceTracker.classify_inbound(last_seen, received, poss_dup)` and branch: `GAP` -> ResendRequest (2); `POSS_DUP` -> discard, not an error; `TOO_LOW` -> Logout (5) with `SessionStatus 1409=9` and terminate. Do not use one boolean for all three — the correct responses are opposite.
8. **Logout / Disconnect**: `disconnect()` marks the session DISCONNECTED. For FIX, ASX requires an exchange of Logout (5) messages — a disconnect without that exchange is an abnormal condition and must be flagged for recovery.
9. **CDE Testing**: All endpoints must point to the ASX CDE prior to production deployment; flip `is_cde_environment`, switch `TargetSubID` to `PROD`, and re-validate.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Session schedule table, regulatory references, and FIX session details: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Copying a pre-June-2025 ASX schedule**: ASX Service Release 15 (effective 23 June 2025) removed the staggered alphabetical opening rotation that had run since 1987 — five code-range groups opening between 10:00 and 10:09 — and replaced it with one exchange-wide auction at 09:59. It also added the Post Close session. Any ASX document, vendor guide, or blog post older than that date will put your opening auction ~10 minutes late and stagger it by ticker. Date-stamp your schedule table and re-verify it after every ASX service release.
- **Treating Post Close as CLOSED**: 16:11:00-16:21:30 Sydney is a real trading session in which ASX accepts new and amended orders and matches them at the CSPA price. Code that treats everything after the closing auction as CLOSED silently forgoes that liquidity. Note the exchange rejects any Post Close order *not* at the CSPA price, so pass the auction price through rather than your own limit.
- **Confusing "no new orders" with "no order management"**: During Adjust and Adjust ON (16:21:30-18:50 Sydney) ASX accepts no new orders and executes no trades, but participants may still cancel and amend. A risk unwind or kill switch gated on an order-entry predicate will wrongly conclude it cannot pull its resting orders for two and a half hours.
- **Using the host clock for schedule decisions**: `AsxSessionSchedule` reads a naive datetime as *already* Sydney wall-clock. Passing `datetime.now()` from a UTC-configured host — the default on most cloud instances — misclassifies the phase by 10-11 hours. Always pass a tz-aware datetime; `AsxIntegrationEngine.market_phase()` defaults to tz-aware UTC for exactly this reason.
- **`ResetSeqNumFlag (141=Y)` losing your live orders**: Per the ASX FIX spec, a 141=Y logon session **cannot retrieve GTC and GTD orders via a ResendRequest**. Taking the "clean session" path on reconnect therefore leaves resting orders live in the market that your session can no longer enumerate — a position you cannot see is a position you cannot risk-manage. Recover with a `141=N` logon plus ResendRequest (restated orders arrive as `ExecType=Restated 150=D`, `ExecRestatementReason=378=1`), or reconcile from drop copy before re-enabling order flow.
- **Assuming your orders survived the disconnect**: Orders sent with `ExecInst (18) = "o"` are cancelled by ASX when their FIX session drops, and those Execution Reports are **not** re-sent automatically on reconnect. You must issue a ResendRequest (2) to discover which of your orders died — otherwise your local ledger shows working orders the exchange has already killed.
- **Responding to a too-low sequence number with a ResendRequest**: A `MsgSeqNum (34)` *below* expected without `PossDupFlag (43) = Y` is unrecoverable under the FIX session layer — send Logout (5) with `SessionStatus (1409) = 9` and terminate. Conversely, a number below expected *with* `PossDupFlag = Y` is a legitimate replay answering your own ResendRequest and must not be treated as a session error. A single boolean "is there a gap?" cannot distinguish these, and picking the wrong branch either aborts your own recovery or leaves a desynchronised session running.
- **Waiting for a Logout that never comes**: If the username, `SenderCompID`, `TargetCompID`, IP address, or header fields such as `BeginString`/`MsgType` are invalid, ASX terminates the session immediately and sends **no** Logout message. Reconnect logic must treat a silent close during logon as a configuration failure and back off, not block waiting for a graceful teardown.
- **Using FIX for HFT**: FIX 5.0 SP2 is excellent for standard execution algorithms (VWAP, TWAP), but using it for latency arbitrage on the ASX is a pitfall. Market-making bots must be routed via OUCH from an ALC cross-connect.
- **Remote OUCH**: Attempting to route OUCH binary messages over a standard internet gateway (ASX Net Global) instead of an ALC cross-connect. The engine rejects this; do not bypass the check.
- **Timezone Errors (AEST vs AEDT)**: Scheduling orders using UTC or a fixed offset instead of Sydney wall-clock time. ASX publishes the schedule in local time; daylight saving shifts the UTC offset (UTC+10 -> UTC+11), not the local session times. Convert any UTC instant to Sydney time once, at the boundary of your scheduler, and never inside trading logic. See `daylight-saving-time-transition-handling`.
- **Invalid HeartBtInt**: `HeartBtInt (108)` below 10 seconds triggers an immediate ASX Logout; above 60s is rejected. The ASX spec words the floor both as "greater than 10 seconds" and "lower than 10 seconds will result in a Logout", so exactly 10s is ambiguous — use the recommended 30s.
- **Disconnect Without Logout Exchange**: For FIX, tearing down the TCP socket without exchanging Logout (5) messages is an abnormal condition; ASX and your drop-copy will treat it as a session fault. Always exchange Logout before disconnect.
- **Missing ASIC AOP Filters**: Part 5.6 of the ASIC Market Integrity Rules and RG 241 require automated filters with direct control over filter parameters for Automated Order Processing. Ensure pre-trade price/size/fat-finger filters are in place and that the algo cannot bypass them.
- **Treating the Schedule as Exact**: The OSPA and CSPA starts are randomised (15s and 30s windows). Code that assumes a hard 09:59:00 opening auction start will intermittently race the exchange. Wait for the ASX Trade phase message.

## Verification

- Instantiate `AsxIntegrationEngine` with FIX and assert it transitions DISCONNECTED -> CONNECTED on `connect()`, and back to DISCONNECTED on `disconnect()`.
- Assert OUCH and ITCH with `is_alc_colocated=False` raise `ValueError` on construction.
- Assert FIX with `heartbeat_interval_seconds` of 9 and 61 raises `ValueError`; 10 and 60 are accepted.
- Assert an empty `host` or `comp_id`, and a `port` of 0, -1, or 65536, raise `ValueError`; ports 1 and 65535 are accepted.
- Assert `AsxSessionSchedule.phase_at(...)` returns PRE_OPEN (08:30), OPENING_AUCTION (09:59:20), NORMAL (12:00), PRE_CSPA (16:05), CLOSING_AUCTION (16:10:30), POST_CLOSE (16:15), ADJUST (17:00), and CLOSED (20:00) for Sydney wall-clock times, and that every minute of the day maps to some phase.
- Assert `is_order_entry_window` is True at 16:15 (Post Close) and False at 17:00 (Adjust), while `is_amend_cancel_window` is True at 17:00 and False at 19:30.
- Assert `AsxIntegrationEngine.market_phase()` with no argument resolves the current instant as tz-aware UTC, not as naive host-local wall-clock.
- Assert `AsxSequenceTracker` is monotonic (1, 2, 3) and that `classify_inbound` returns IN_SEQUENCE, GAP, POSS_DUP (with `poss_dup=True`), and TOO_LOW for the corresponding inputs, and raises `ValueError` on a non-positive sequence number.
- Assert `detect_inbound_gap` still flags a forward gap, a replay, and an out-of-order number.
- Run `python -m unittest discover -s skills/australian-securities-exchange-asx-api/scripts`.

## Related Skills

- `fix-protocol-session-management-across-venues`
- `binary-protocol-parsing-for-low-latency-feeds`
- `daylight-saving-time-transition-handling`
- `sequence-number-gap-detection-for-feeds`
- `global-exchange-holiday-calendar-handling`
- `new-zealand-exchange-nzx-api`
