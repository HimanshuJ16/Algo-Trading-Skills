---
name: b3-brazil-exchange-api-integration
description: >-
  Use when configuring B3 PUMA Trading System connectivity and choosing between the
  legacy FIX/FAST stack and Binary SBE; enforces the SBE gap-recovery precondition that
  separates them, and manages connection lifecycle state around a vendor SDK.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: b3, brazil, puma-trading-system, fix-fast, umdf, sbe, binary-order-entry, direct-market-access, low-latency
  brokers_frameworks: "B3 PUMA Trading System; B3 UMDF (Unified Market Data Feed); B3 Binary Order Entry (FIXP/SBE); FIX 4.4"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## Purpose

Validates B3-specific connectivity configuration and manages connection state for algorithmic trading systems connecting to the B3 PUMA Trading System. Its central job is to make one architectural asymmetry impossible to get wrong: B3's legacy FIX/FAST UMDF publishes TCP-based recovery services, while the newer Binary UMDF (SBE) feed does not — so an SBE consumer must implement its own gap recovery or it will lose market data silently.

## Scope — what this skill does NOT do

**This module performs no network I/O.** It opens no sockets, sends no FIX/FIXP messages, and contacts nothing. `connect()` drives a state machine and delegates to an injected `connector` callable; with no connector supplied it logs a warning and reports success without touching the network.

A successful `connect()` is therefore **not** evidence of reachability, valid credentials, IP whitelisting, or entitlement. Earlier versions of this skill presented `if engine.connect(): print("Connected to B3")` as a connectivity example, which was misleading — it would print success on a machine with no network at all. Inject a real connector (OnixS, B2BITS, or your own socket layer) for actual sessions.

## When to Use

- Validating B3 DMA configuration before handing it to a real session layer
- Choosing between the legacy FIX/FAST stack and the Binary SBE stack
- Enforcing the SBE gap-recovery precondition in CI or at application startup
- Managing connection lifecycle state around a vendor SDK

## When NOT to Use

- **As a connectivity or credential test** — it performs no I/O (see Scope above)
- **As a FIX/FIXP engine or SBE decoder** — use `binary-protocol-parsing-for-low-latency-feeds` and a real session library
- Connecting to B3 via broker-provided APIs (use broker-specific adapters)
- Systems that do not need direct exchange connectivity

## Prerequisites

- Python 3.9+
- Network connectivity to B3 colocation or a certified B3 network provider
- B3-assigned SenderCompID/TargetCompID from B3 Membership Services
- Valid B3 credentials and IP whitelisting for order entry and market data
- For Binary SBE: an application-level gap recovery implementation (sequence tracking + snapshot recovery), which is **outside this skill**

## Protocol Selection

| | `LEGACY_FIX_FAST` | `MODERN_BINARY_SBE` |
|---|---|---|
| Order entry | FIX 4.4 | Binary Order Entry (FIXP + SBE) |
| Market data | UMDF FIX/FAST | Binary UMDF (SBE) |
| Book model | MBP / Top of Book available | **MBO only** — no MBP, no TOB |
| Gap recovery | TCP Replayer, TCP Historical Replayer, Snapshot Recovery | **No TCP recovery channel**; sequence tracking + snapshot |
| `enable_application_gap_recovery` | Optional | **Must be True** (enforced) |

**Legacy is not a neutral long-term choice.** B3 has been reducing FIX order entry gateways in phases — Derivatives in Q4 2025, Equities 10 April – 15 May 2026 — reassigning IPs, ports, and TargetCompIDs, with login failures for participants who miss their cutoff. Confirm current gateway status with B3 before starting new legacy work. See `references/standards.md` for sources and dating.

## Workflow

1. **Create `B3ConnectionConfig`.** Validation runs in `__post_init__` and raises `B3ConfigurationError` (a `ValueError` subclass):
   - `comp_id` — matched against a conservative `^[A-Za-z0-9_]{1,12}$` whitelist. This is *this skill's* defensive default, not a published B3 grammar; override via `comp_id_pattern` once you confirm the real limits with B3.
   - `password` — excluded from `repr` so it cannot leak into logs or tracebacks.
   - `order_entry_ip` — must be a dotted-quad string and must **not** be multicast (order entry is point-to-point TCP).
   - `market_data_multicast_ip` — must be a genuine multicast address unless you pass `require_multicast_market_data=False` for an isolated test rig.
   - `protocol_suite` — must be a `B3ProtocolSuite` member, not a string.
2. **Construct `B3IntegrationEngine`.** If `protocol_suite` is `MODERN_BINARY_SBE` and `enable_application_gap_recovery` is False, construction fails — the SBE feed has no TCP gap-fill channel to fall back on.
3. **Inject a connector** if you want real connectivity. Without one, `connect()` is a state-machine simulation and says so at WARNING level.
4. **Call `connect()`.** DISCONNECTED/FAILED → CONNECTING → CONNECTED. A connector that raises moves the engine to `FAILED` and raises `B3ConnectionError`; retry with backoff from there, since `connect()` is callable again after a failure.
5. **Implement SBE gap recovery separately.** Track sequence numbers on every message, detect gaps, and recover via the snapshot/refresh streams — **not** via a TCP gap-fill request, which Binary UMDF does not offer.
6. **Call `disconnect()` on shutdown.** Idempotent, and it forces the state to DISCONNECTED even if the injected disconnector raises, so a failing teardown cannot strand the session.

> Full procedure: see `references/workflows.md`.
> Protocol specifics, recovery mechanisms, and sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reading `connect() == True` as connectivity.** With no connector injected this function does nothing but set an enum. It is configuration validation, not a reachability check.
- **Expecting a TCP gap-fill channel on Binary UMDF.** It does not exist. Legacy FIX/FAST offers TCP Replayer and TCP Historical Replayer; the SBE feed gives you sequence numbers and snapshot recovery, and nothing else. Building against a gap-fill request that Binary UMDF does not implement is a design dead end.
- **Assuming Binary UMDF is a drop-in upgrade.** It is MBO-only — no Market-by-Price, no Top of Book. An algorithm consuming aggregated MBP from the legacy feed must rebuild the book from individual order events, not merely re-point at a new multicast group.
- **Treating a data-quality gap as recoverable after the fact.** Once an SBE consumer has missed messages without detecting it, book state is wrong and every downstream signal is wrong with it. Detect at receive time via sequence continuity.
- **Assuming legacy FIX will stay available on the same endpoints.** B3 has been consolidating FIX order entry gateways and reassigning IPs, ports, and TargetCompIDs; sessions that miss a cutoff fail to log in.
- **Logging the config object.** A credential in a dataclass `repr` ends up in logs and crash reporters. `password` is `repr=False` here; keep it that way if you extend the config.
- **Unicast market data addresses in production.** UMDF is multicast; a unicast address means no data arrives. This is now rejected by default rather than merely warned about in prose.
- **Trusting unsourced latency figures.** The precise microsecond ranges commonly quoted for B3 trace to no B3 or vendor publication — measure in your own footprint.

## Verification

Run `python -m unittest discover -s skills/b3-brazil-exchange-api-integration/scripts` — 29 tests, 100% pass rate. The suite covers:

- `MODERN_BINARY_SBE` rejected without `enable_application_gap_recovery`, accepted with it; `LEGACY_FIX_FAST` allowed without it
- CompID whitelist, including the regression that special characters and control characters were accepted whenever the ID also contained an underscore
- IP validation: malformed addresses, integer addresses, multicast enforcement on market data, and multicast rejection on order entry
- State machine: idempotent connect/disconnect, and the `FAILED` state, which was previously unreachable
- Credential redaction from `repr`
- Concurrent connect/disconnect leaving a valid state

## Related Skills

- `binary-protocol-parsing-for-low-latency-feeds` — decoding the SBE market data messages
- `fix-protocol-session-management-across-venues` — full FIX/FIXP session handling
- `order-placement-idempotency` — safe order submission once connected
- `sequence-number-gap-detection-for-feeds` — the gap detection this skill requires but does not implement
- `exchange-multicast-feed-handling` — multicast joins and feed plumbing
- `market-data-cost-optimization-tiered-subscriptions` — B3 market data subscription costs

## References

- B3 PUMA Trading System: https://www.b3.com.br/en_us/solutions/platforms/puma-trading-system/
- B3 FIX/FAST UMDF: https://www.b3.com.br/en_us/solutions/platforms/puma-trading-system/for-developers-and-vendors/fix-fast-umdf/
- FIX Protocol standards (FIXP, SBE): https://www.fixtrading.org/standards/
