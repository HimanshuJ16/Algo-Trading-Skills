---
name: australian-securities-exchange-asx-api
description: Institutional API integration adapter for the Australian Securities Exchange
  (ASX), supporting FIX 5.0 SP2, OUCH, and ITCH protocols with AEST/AEDT session-schedule
  awareness, ALC topology enforcement, and FIX session-state primitives.
domain: global-market-integration
subdomain: exchanges
tags:
- asx
- australia
- fix
- ouch
- itch
- market-connectivity
brokers_frameworks:
- direct-market-access
version: "1.2.0"
author: System
license: MIT
---

## When to Use

Use this skill when building direct market access (DMA) connectivity to the Australian Securities Exchange (ASX). This engine provides the foundational configuration, topology validation, session-schedule awareness, and FIX session-state primitives required to interface with the ASX Customer Development Environment (CDE) or Production via the Australian Liquidity Centre (ALC).

It supports routing configurations for:
- **FIX 5.0 SP2**: Standard institutional order routing and drop-copy. ASX Trade FIX sessions are bounded by a single trading day, with `HeartBtInt (108)` recommended at 30s (must be 10-60s; <10s triggers an immediate Logout).
- **OUCH**: Ultra-low latency binary order entry for high-frequency strategies.
- **ITCH**: Ultra-low latency binary multicast Market-by-Order full-depth data.

## When NOT to Use

- **Latency arbitrage over FIX**: FIX 5.0 SP2 is millisecond-tier and is the wrong transport for microsecond arbitrage; route that flow via OUCH from an ALC cross-connect instead.
- **Remote OUCH/ITCH**: OUCH and ITCH are designed for ALC co-location. Attempting either over ASX Net Global or any remote internet gateway is an anti-pattern and is rejected by this engine's topology validation.
- **Full FIX message serialisation**: This skill owns configuration, topology, schedule, and session-state decisions only. Building/parsing FIX tags belongs to `fix-protocol-session-management-across-venues`; binary frame parsing belongs to `binary-protocol-parsing-for-low-latency-feeds`.

## Prerequisites

- Python 3.9+ (uses `zoneinfo` for AEDT/AEST conversion; production hosts must ship IANA tzdata).
- Exchange-assigned `CompID` and cross-connect details (ASX Trade FIX Order Entry uses `TargetCompID = ASXTRADE`; `TargetSubID (57)` and `MsgSeqNum (34)` are mandatory on Logon).
- For OUCH/ITCH, trading infrastructure co-located in the Australian Liquidity Centre (ALC).
- A pre-decided **session-recovery policy** for sequence-number gaps on reconnect (ResendRequest vs. clean ResetSeqNumFlag logon) — see Common Pitfalls.
- ASIC Market Integrity Rules (Securities Markets) 2017 compliance posture in place: automated filters and direct control over filter parameters (Part 5.6; RG 241).

## Workflow

1. **Protocol Selection**: Configure `AsxConnectionConfig` with the required `AsxProtocol` (FIX, OUCH, or ITCH) and, for FIX, a `heartbeat_interval_seconds` in [10, 60] (ASX recommends 30).
2. **ALC Topology Validation**: The engine validates the topology on construction. OUCH and ITCH are rejected if `is_alc_colocated` is False, because remote binary routing is an anti-pattern. A non-CDE host whose name begins with `test` produces a warning.
3. **Session-Schedule Awareness**: Before sending order-entry traffic, determine the ASX Trade market phase with `AsxSessionSchedule.phase_at(...)` using Sydney wall-clock time (AEST UTC+10 / AEDT UTC+11). Order entry is accepted in PRE_OPEN, OPENING_AUCTION, NORMAL, PRE_CSPA, and CLOSING_AUCTION, but NOT in CLOSED. ASX randomises the OSPA start within 15s and the CSPA start within 30s — treat the ASX Trade system message as authoritative for the exact transition, not this table.
4. **Session Initialization**: `connect()` transitions DISCONNECTED -> CONNECTING -> CONNECTED and logs the current market phase. Connecting an order-entry protocol during CLOSED triggers a warning (typically a scheduler/timezone bug). On FIX, the engine exposes an `AsxSequenceTracker` for outbound `MsgSeqNum (34)`.
5. **Heartbeating & Sequence Numbers**: Maintain the FIX session via Heartbeat (0) at the negotiated `HeartBtInt`; respond to Test Request (1) with a Heartbeat. Track outbound sequence numbers monotonically; on reconnect, detect inbound gaps with `AsxSequenceTracker.detect_inbound_gap(...)` and resolve them via ResendRequest (2) before resuming order traffic.
6. **Logout / Disconnect**: `disconnect()` marks the session DISCONNECTED. For FIX, ASX requires an exchange of Logout (5) messages — a disconnect without that exchange is an abnormal condition and must be flagged for recovery.
7. **CDE Testing**: All endpoints must point to the ASX CDE prior to production deployment; flip `is_cde_environment` and re-validate.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Session schedule table, regulatory references, and FIX session details: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using FIX for HFT**: FIX 5.0 SP2 is excellent for standard execution algorithms (VWAP, TWAP), but using it for latency arbitrage on the ASX is a pitfall. Market-making bots must be routed via OUCH from an ALC cross-connect.
- **Remote OUCH**: Attempting to route OUCH binary messages over a standard internet gateway (ASX Net Global) instead of an ALC cross-connect. The engine rejects this; do not bypass the check.
- **Timezone Errors (AEST vs AEDT)**: Submitting or scheduling orders using UTC or a fixed offset instead of Sydney wall-clock time. ASX publishes the schedule in local time; daylight saving shifts the UTC offset (UTC+10 -> UTC+11), not the local session times. Convert any UTC instant to Sydney time once, at the boundary of your scheduler, and never inside trading logic. See `daylight-saving-time-transition-handling`.
- **Invalid HeartBtInt**: Setting FIX `HeartBtInt (108)` below 10 seconds triggers an immediate ASX Logout; above 60s is rejected. The recommended value is 30s.
- **Silent Sequence-Number Gaps on Reconnect**: After a reconnect, a gap between the exchange's expected `MsgSeqNum (34)` and your last sent number indicates lost or replayed messages. Never silently skip — issue a ResendRequest (2), or start a clean session with `ResetSeqNumFlag (141=Y)` and accept that prior messages cannot be recovered.
- **Disconnect Without Logout Exchange**: For FIX, tearing down the TCP socket without exchanging Logout (5) messages is an abnormal condition; ASX and your drop-copy will treat it as a session fault. Always exchange Logout before disconnect.
- **Missing ASIC AOP Filters**: Part 5.6 of the ASIC Market Integrity Rules and RG 241 require automated filters with direct control over filter parameters for Automated Order Processing. Ensure pre-trade price/size/fat-finger filters are in place and that the algo cannot bypass them.
- **Treating the Schedule as Exact**: The OSPA and CSPA starts are randomised (15s and 30s windows). Code that assumes a hard 09:59:00 opening auction start will intermittently race the exchange. Wait for the ASX Trade phase message.

## Verification

- Instantiate `AsxIntegrationEngine` with FIX and assert it transitions DISCONNECTED -> CONNECTED on `connect()`, and back to DISCONNECTED on `disconnect()`.
- Assert OUCH and ITCH with `is_alc_colocated=False` raise `ValueError` on construction.
- Assert FIX with `heartbeat_interval_seconds` of 9 and 61 raises `ValueError`; 10 and 60 are accepted.
- Assert `AsxSessionSchedule.phase_at(...)` returns PRE_OPEN (08:30), OPENING_AUCTION (09:59:20), NORMAL (12:00), PRE_CSPA (16:05), CLOSING_AUCTION (16:10:30), and CLOSED (17:00) for Sydney wall-clock times.
- Assert `AsxSequenceTracker` is monotonic (1, 2, 3) and that `detect_inbound_gap` flags a forward gap, a replay, and an out-of-order number.
- Run `python scripts/test_australian_securities_exchange_asx_api.py`.

## Related Skills

- `fix-protocol-session-management-across-venues`
- `binary-protocol-parsing-for-low-latency-feeds`
- `daylight-saving-time-transition-handling`
- `sequence-number-gap-detection-for-feeds`
- `new-zealand-exchange-nzx-api`
