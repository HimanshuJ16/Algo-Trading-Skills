---
name: fix-protocol-session-management-across-venues
description: >-
  Quantitative execution engine for managing multi-venue FIX protocol session state machines, sequence number resynchronization, gap fills, heartbeats, and graceful failovers.
domain: Execution Algorithms
subdomain: FIX Protocol & Venue Connectivity
tags: ["fix-protocol", "session-management", "sequence-numbers", "heartbeat", "resend-request", "gap-fill", "fix-engine"]
brokers_frameworks: ["FIX 4.2 / 4.4 / 5.0 Standard", "QuickFIX Engine", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in FIX engine implementations, Smart Order Routers (SOR), and venue connectivity gateways. The FIX (Financial Information eXchange) protocol maintains session state across multi-venue connections (Nasdaq, NYSE, CME) via bi-directional sequence numbers (`MsgSeqNum` Tag 34). This module implements the complete FIX session state machine (`DISCONNECTED`, `LOGON_SENT`, `LOGGED_IN`, `RESEND_REQUEST_SENT`), handling sequence gap detection, `ResendRequest` (Tag 35=2), `SequenceReset` (Tag 35=4), and heartbeat liveness monitoring.

## Prerequisites

- Target Comp ID, Sender Comp ID, venue endpoint details.
- Configured Heartbeat Interval (`HeartBtInt` Tag 108 = 30s).
- Sequence number storage (`out_seq_num`, `in_seq_num`).

## Workflow

1. **Logon Initiation**:
   - Send `Logon` (Tag 35=A) with `MsgSeqNum` = 1, `HeartBtInt` = 30. Transition state to `LOGON_SENT`.
   - Receive counterparty `Logon` response. Transition state to `LOGGED_IN`.
2. **Heartbeat & Liveness Audit**:
   - Send periodic `Heartbeat` (Tag 35=0) when idle for `HeartBtInt` seconds.
   - If no incoming message received for $1.5 \times \text{HeartBtInt}$, issue `TestRequest` (Tag 35=1).
3. **Sequence Gap Detection & Resend Request**:
   - If incoming `MsgSeqNum` > Expected `in_seq_num` $\implies$ Issue `ResendRequest` (Tag 35=2, Tag 7 `BeginSeqNo`, Tag 16 `EndSeqNo`). Transition state to `RESEND_REQUEST_SENT`.
   - Receive missing messages or `SequenceReset` (Tag 35=4, Tag 123 `GapFillFlag`=Y), incrementing `in_seq_num` to match.
4. **Graceful Logout**:
   - Issue `Logout` (Tag 35=5) $\to$ Receive `Logout` response $\to$ Transition state to `DISCONNECTED`.
5. **Audit Report Generation**: Output structured `FixSessionAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Out-of-Sequence Message Gaps**: Processing out-of-order child order execution reports without triggering a `ResendRequest`, resulting in missed order fills.
- **Uncontrolled Sequence Resets**: Issuing un-gapped `SequenceReset` (Reset mode Tag 123=N) during live trading, wiping out un-reconciled execution reports.
- **Spin-Locking Heartbeat Timers**: Burning 100% CPU loops while checking heartbeat timestamps rather than using non-blocking timers.

## Verification

- Instantiate `FixProtocolSessionManagerEngine`. Initialize session `FIX_NASDAQ_01`. Test Logon transition $\implies$ verifies `LOGGED_IN` state. Simulate incoming gap (`MsgSeqNum` = 5 when expected `in_seq_num` = 3) $\implies$ verifies engine issues `ResendRequest` (BeginSeq=3, EndSeq=0) and updates state to `RESEND_REQUEST_SENT`. Simulate gap-fill $\implies$ verifies resynchronization to `LOGGED_IN`.
- Run `python scripts/test_fix_protocol_session_management_across_venues.py`.

## Related Skills

- `exchange-gateway-redundancy-and-failover-testing`
- `exchange-self-match-prevention-configuration`
---
