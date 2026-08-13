# Workflows for B3 Exchange API Connectivity

Protocol facts here are sourced and dated in `references/standards.md`.
This module validates configuration and manages state; it performs **no network
I/O**. Everything below that involves real packets assumes you have injected a
connector or are using a separate session library.

## Phase 1 — Planning and provisioning

1. **Choose the protocol suite.**
   - `MODERN_BINARY_SBE` — Binary Order Entry (FIXP + SBE) with Binary UMDF.
     Lower encode/decode overhead, MBO order-level data.
   - `LEGACY_FIX_FAST` — FIX 4.4 order entry with UMDF FIX/FAST. MBP and Top of
     Book available; TCP-based recovery services available.

   Two constraints usually decide this before latency does:
   - Binary UMDF is **MBO only** — no MBP, no Top of Book. If your strategy
     consumes aggregated price levels, moving to SBE means rebuilding the book
     from individual order events, not re-pointing a socket.
   - B3 has been **reducing FIX order entry gateways** (Derivatives Q4 2025,
     Equities 10 April – 15 May 2026), reassigning IPs, ports, and TargetCompIDs.
     Confirm current gateway availability with B3 before committing to legacy.

2. **Network infrastructure.**
   - Colocation or a certified network provider; arrange cross-connects.
   - Obtain approved endpoints for order entry (TCP unicast) and market data
     (UDP multicast).
   - Firewall: TCP for order entry, UDP for market data; verify multicast
     routing and IGMP support.

3. **Credentials.**
   - Obtain SenderCompID/TargetCompID from B3 Membership Services, and confirm
     the permitted CompID format while you are there — this skill's
     `^[A-Za-z0-9_]{1,12}$` whitelist is a defensive default, not a quoted B3
     grammar. Override `comp_id_pattern` if B3's real rule differs.
   - Confirm IP whitelisting for both order entry and market data.

## Phase 2 — Configuration

```python
from b3_brazil_exchange_api_integration import (
    B3ConnectionConfig, B3IntegrationEngine, B3ProtocolSuite,
    B3ConfigurationError, B3ConnectionError,
)

config = B3ConnectionConfig(
    comp_id="YOUR_COMPID",
    password=os.environ["B3_SESSION_PASSWORD"],   # never hard-code
    order_entry_ip="10.0.1.100",                  # unicast; multicast is rejected
    market_data_multicast_ip="239.1.1.100",       # must be multicast by default
    protocol_suite=B3ProtocolSuite.MODERN_BINARY_SBE,
    enable_application_gap_recovery=True,         # mandatory for SBE
)
```

Setting `enable_application_gap_recovery` from the protocol suite
(`suite is MODERN_BINARY_SBE`) satisfies the check mechanically but defeats its
purpose — the flag is an assertion that you have *built* gap recovery, not a
formality. Set it True only when the recovery path in Phase 4 actually exists.

## Phase 3 — Engine and connection

```python
engine = B3IntegrationEngine(config, connector=my_session_layer.connect)

try:
    engine.connect()
except B3ConnectionError:
    # engine.state is now FAILED; retry with exponential backoff and a cap
    ...
```

Without a `connector`, `connect()` returns True having done nothing and logs a
warning saying so. Do not build a health check on it.

State transitions: `DISCONNECTED`/`FAILED` → `CONNECTING` → `CONNECTED`, and any
state → `DISCONNECTED` via `disconnect()`. `connect()` is callable again after a
failure, so backoff-retry loops work directly against the same engine.

## Phase 4 — Market data and gap recovery

### Legacy FIX/FAST UMDF

Recovery services published by B3 alongside the incremental stream:

- **TCP Replayer** — request messages already sent on the incremental stream
  during the day. Intended for small gaps.
- **TCP Historical Replayer** — higher-response-time feed that can query
  incremental messages back to sequence number 1.
- **Snapshot Recovery stream** — for late joiners and large losses.

### Binary UMDF (SBE)

There is **no TCP recovery channel for gap filling** on the binary feed. Recovery
is:

1. Track the sequence number on every received message.
2. Detect a gap the moment expected ≠ received — not on a timer, and not by
   noticing the book looks wrong later.
3. Recover from the **snapshot/refresh** stream, resynchronising via
   RptSeq/LastRptSeq. Handle Channel Reset and EmptyBook events.
4. Buffer incrementals arriving during recovery and replay them once
   synchronised.

Do **not** design a TCP gap-fill request for the binary feed. An earlier revision
of this skill instructed exactly that; the facility does not exist.

## Phase 5 — Operations

- **Health monitoring:** watch for unexpected state transitions, session-level
  sequence gaps in both directions, and heartbeat timeouts (FIX `HeartBtInt` or
  the FIXP equivalent).
- **Retry policy:** exponential backoff with a maximum attempt count. Never
  retry a session logon in a tight loop against an exchange gateway.
- **Audit:** log connection lifecycle events with timestamps. Log the config
  object freely — `password` is excluded from its `repr` — but never log the
  password field directly.
- **Shutdown:** call `disconnect()`. It forces DISCONNECTED even if the injected
  disconnector raises, so a failing teardown cannot strand the engine.

## Testing

```
python -m unittest discover -s scripts
```

Pre-deployment, verify against B3's certification environment:

- Binary EntryPoint: FIXP Negotiate/Establish handshake completes.
- Legacy: standard FIX logon succeeds.
- Multicast joins succeed and messages decode against the official schemas.
- Induced packet loss is detected by sequence tracking and recovered via the
  correct mechanism for the feed in use.
- Invalid credentials and unreachable endpoints surface as `FAILED`, not as a
  silent success.

## Failure modes seen in this skill's own history

- **Simulation mistaken for connectivity** — `connect()` returning True with no
  I/O, presented in examples as proof of a live session.
- **Recovery designed against a nonexistent channel** — building a TCP gap-fill
  request for Binary UMDF.
- **Validator that did not validate** — a CompID check that only inspected
  characters when the ID contained no underscore, so `A_!@#$%` passed.
- **Credential in `repr`** — the config dataclass printing the session password.
- **Unsourced latency figures** — precise microsecond ranges with no origin,
  removed rather than propagated.
