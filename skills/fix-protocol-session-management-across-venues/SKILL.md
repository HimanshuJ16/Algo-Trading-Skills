---
name: fix-protocol-session-management-across-venues
description: >-
  Use when building the FIX session layer that decides whether an execution report is
  applied once, twice or never: logon negotiation, inbound sequence discipline, gap
  detection, resend requests and safe sequence reset handling.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: fix-protocol, session-management, sequence-numbers, heartbeat, resend-request, gap-fill, poss-dup, fix-engine
  brokers_frameworks: "FIX 4.2 / 4.4 / 5.0 Session Layer; QuickFIX / QuickFIX-J; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when building or auditing the **session layer** of a FIX order-entry connection — the layer that decides whether an `ExecutionReport` is applied to your positions once, twice, or never. It implements the FIX 4.2/4.4/5.0 session state machine (`DISCONNECTED`, `LOGON_SENT`, `LOGGED_IN`, `RESEND_REQUEST_SENT`, `LOGOUT_SENT`) with the sequence-number rules the specification actually mandates: gap detection and `ResendRequest(35=2)`, `SequenceReset(35=4)` in both Gap Fill and Reset modes, `PossDupFlag(43)` duplicate suppression, `TestRequest(35=1)` liveness, and a two-way `Logout(35=5)` handshake.

Reach for it when a venue is dropping your session on sequence errors, when you suspect duplicate or missing fills after a reconnect, when you need an auditable record of every session-layer decision, or when you are writing the session tests that a venue conformance run will exercise.

The engine performs **no I/O**. It opens no sockets, encodes no wire format, computes no `BodyLength(9)` or `CheckSum(10)`, and sends nothing. It consumes decoded `FixMessage` objects and returns the messages you must transmit.

## When NOT to Use

- **As a complete FIX engine.** There is no tag=value encoder/decoder, no `BodyLength`/`CheckSum`, no TCP handling, no persistence. Use QuickFIX or your venue's certified engine for the transport and codec; use this for the session-layer decisions and as a test harness for them. For wire encoding specifically see `cme-stp-fix-and-ilink2-tag-value-encoding`.
- **For binary session protocols.** CME iLink 3 (FIXP/SBE), Eurex T7 ETI and Nasdaq OUCH/ITCH have their own session layers with different sequencing and recovery semantics. Eurex ETI, for one, logs on at `MsgSeqNum=1` on *every* reconnect and has no sequence recovery at all — none of the rules here transfer.
- **As the venue's specification.** Venues routinely override the session layer: permitted resend ranges, whether `ResetSeqNumFlag(141)` is allowed intraday, heartbeat multipliers, and what happens to working orders on disconnect. Read the venue spec and configure from it; the defaults here are conventions, not compliance.
- **For order-state recovery.** This engine recovers the *message stream*, not your order book. A resynchronised session tells you nothing about which orders the venue still holds. For that see `exchange-gateway-redundancy-and-failover-testing` and reconcile before resuming flow.
- **As a shared object across venues.** One engine instance is one session — one `SenderCompID`/`TargetCompID` pair. Sequence numbers belong to a session, never to a firm.

## Prerequisites

- `SenderCompID(49)`, `TargetCompID(56)` and the venue endpoint, from the venue's onboarding pack.
- A negotiated `HeartBtInt(108)`. The initiator proposes it and the acceptor echoes it back; both sides must use the same value.
- Liveness multipliers taken from the venue spec where it states them. `test_request_multiplier` / `disconnect_multiplier` default to 1.5 and 2.4 — QuickFIX/J's defaults, **not** a FIX requirement (see `references/standards.md`).
- Durable storage for `out_seq_num` and `expected_in_seq_num`. This engine holds them in memory only; if your process restarts and the venue does not reset, you must restore them or the session will fail on the first message.
- A decoder that populates `FixMessage`, including `poss_dup_flag` from Tag 43 and `body_fields` keyed by integer tag.

## Workflow

1. **Logon.**
   - Send `Logon(35=A)` with `HeartBtInt(108)` and `EncryptMethod(98)=0`; state becomes `LOGON_SENT`.
   - **Decision point — the response carries a gap.** If the venue's `Logon` has a `MsgSeqNum` *higher* than expected, process the Logon first (the session comes up), then issue a `ResendRequest`. Do **not** advance the expected sequence to the Logon's own number: that silently discards every message the venue sent while you were away, fills included.
   - `ResetSeqNumFlag(141)=Y` resets both directions to 1. It must be bilaterally agreed beforehand. Resetting mid-session throws away the recovery path for anything unreconciled.
   - Wait for the resend to complete, or send a `TestRequest` and await the answer, before pushing application traffic. Blasting queued orders into a recovering session earns a `ResendRequest` per message.
2. **Liveness.**
   - Send `Heartbeat(35=0)` when the outbound link has been idle for `HeartBtInt`.
   - Send `TestRequest(35=1)` when nothing has been *received* for `test_request_multiplier × HeartBtInt`, and keep **one** outstanding — re-issuing per poll is the spin-lock failure mode and forces nothing extra.
   - Answer an inbound `TestRequest` with a `Heartbeat` echoing its `TestReqID(112)`. An unanswered TestRequest is read as a dead session and gets you disconnected.
   - Declare the session lost past `disconnect_multiplier × HeartBtInt`. **A silent session is not an empty order book** — reconcile working orders before assuming anything.
3. **Inbound sequence discipline** — the order of these checks is the substance of the skill:
   - **`SequenceReset`-Reset (`GapFillFlag(123)` absent or `N`) is evaluated first.** Its own `MsgSeqNum` is ignored by specification and it must never provoke a `ResendRequest`. Gap-checking it first produces a reset/resend loop that never converges.
   - **`PossDupFlag(43)=Y` below the expected sequence ⟹ discard, and leave the expected sequence untouched.** This is a retransmission you already applied.
   - **`MsgSeqNum` above expected ⟹ `ResendRequest(BeginSeqNo=expected, EndSeqNo=0)`**, state `RESEND_REQUEST_SENT`, and hold the triggering message. `EndSeqNo=0` means infinity and is the form FIX strongly recommends. While a request is outstanding, do **not** issue another for each new message — the open-ended range already covers them.
   - **`MsgSeqNum` below expected *without* `PossDupFlag` ⟹ unrecoverable.** Send `Logout` with `Text(58)` naming the expected and received values, and terminate. Do not attempt to continue.
   - **A `SequenceReset` of either mode may only *increase* the expected sequence number.** A `NewSeqNo(36)` at or below it is rejected — honouring it would replay execution reports already applied to positions.
4. **Serving an inbound `ResendRequest`.**
   - Replay stored application messages under their **original** `MsgSeqNum` with `PossDupFlag(43)=Y` and `OrigSendingTime(122)`. Retransmission must not consume new outbound sequence numbers.
   - Do not replay administrative messages; collapse contiguous runs of them into a `SequenceReset`-GapFill.
   - **Decision point — the range has aged out of your buffer.** Gap-filling it is spec-legal but means real application messages were skipped. Treat it as a reconciliation trigger, not a clean recovery.
5. **Graceful logout.** Send `Logout(35=5)`, wait for the counterparty's `Logout`, then go `DISCONNECTED`. Dropping the socket immediately forfeits the confirmation that both sides agree on the final sequence numbers — which is exactly what you need at next logon.
6. **Audit.** Every inbound message yields a `FixSessionAuditReport`. `report.responses` is the authoritative ordered list of messages to transmit; the tuple's second element is `responses[0]` and is complete for every case except an inbound `ResendRequest`, which can require several.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Letting a `PossDup` retransmission rewind the expected sequence number.** Processing a duplicate as though it were new — setting `expected = received + 1` — rewinds the session, so every message after it is applied a second time. That is a duplicate-fill generator, and duplicative orders are precisely what SEC Rule 15c3-5(c)(1)(ii) requires a broker-dealer's controls to reject. Below-expected plus `PossDupFlag=Y` means *drop it and change nothing*.
- **Honouring a `SequenceReset` that lowers `NewSeqNo`.** The specification permits only increases; a decrease "should be rejected and treated as a serious error". A buggy or hostile counterparty that can rewind your inbound sequence can make you re-apply every fill in the range.
- **Gap-checking a `SequenceReset`-Reset.** Reset mode carries an arbitrary `MsgSeqNum` that must be ignored. Running gap detection first answers it with a `ResendRequest`, which the venue answers with another reset — a loop that ends in a disconnect.
- **Issuing a `ResendRequest` for every message that arrives ahead of the gap.** With `EndSeqNo=0` the first request already covers everything that follows. Repeating it per message is a resend storm, and on venues that meter message rates it is also an order-to-trade-ratio problem.
- **Advancing past a gap in the `Logon` response.** The Logon's sequence number is not permission to skip. Accept the Logon, then recover the range.
- **Not answering an inbound `TestRequest`.** The `Heartbeat` must echo `TestReqID(112)`. A bare heartbeat, or none, reads as a dead session.
- **Treating a heartbeat timeout as a cancelled order book.** Cancel-on-disconnect is a per-venue, per-session configuration, not a default. Verify which regime applies before assuming the venue dropped your working orders.
- **Formatting `SendingTime(52)` as ISO-8601.** FIX requires `YYYYMMDD-HH:MM:SS[.sss]` — hyphen, no `T`, no `Z`. `20260730T10:00:00Z` is not valid FIX and venue parsers reject it.
- **Sharing sequence state between venues.** Sequence numbers belong to a `SenderCompID`/`TargetCompID` pair. Copying one session's counter into another desynchronises both.
- **Spin-locking heartbeat timers.** Poll a monotonic clock from a timer or event loop; do not burn a core comparing timestamps.
- **Retaining every outbound message forever.** An unbounded resend buffer is an out-of-memory failure on a long-lived session. Bound it, and treat a resend request that reaches past the bound as a reconciliation event.

## Verification

- Instantiate `FixProtocolSessionManagerEngine` with `FixSessionConfig(session_id="FIX_NASDAQ_01", sender_comp_id="FIRM_ALPHA", target_comp_id="NASDAQ")` and an injected `clock` for deterministic liveness tests.
- **Logon:** `initiate_logon()` ⟹ `LOGON_SENT`, `MsgSeqNum=1`. Inbound `Logon(35=A, seq=1)` ⟹ `LOGGED_IN`, `expected_in_seq_num == 2`.
- **Logon with a gap:** inbound `Logon(seq=7)` when expecting 1 ⟹ state `RESEND_REQUEST_SENT`, a `ResendRequest` with `BeginSeqNo=1`/`EndSeqNo=0`, and `expected_in_seq_num` still **1** — not 8.
- **Gap:** inbound `MsgSeqNum=5` when expecting 2 ⟹ `ResendRequest(BeginSeqNo=2, EndSeqNo=0)`, `RESEND_REQUEST_SENT`, `expected_in_seq_num` still 2. A second gap message ⟹ **no** second `ResendRequest`.
- **Gap fill:** `SequenceReset(seq=2, NewSeqNo=6, GapFillFlag=Y)` ⟹ `LOGGED_IN`, `expected_in_seq_num == 6`.
- **Rewind refusal:** `SequenceReset(NewSeqNo=2)` in either mode while expecting 6 ⟹ `MESSAGE_REJECTED`, a `Reject(35=3)`, and `expected_in_seq_num` unchanged at 6.
- **Reset mode:** `SequenceReset(seq=9, NewSeqNo=20, GapFillFlag=N)` while expecting 2 ⟹ **no** `ResendRequest`, `expected_in_seq_num == 20`.
- **Duplicate:** after processing 2-5, a `PossDupFlag=Y` message at seq 3 ⟹ `MESSAGE_DISCARDED`, `expected_in_seq_num` still 6, state still `LOGGED_IN`.
- **Too low:** seq below expected without `PossDupFlag` ⟹ `SESSION_TERMINATED`, a `Logout` whose `Text(58)` names both numbers, and `report.state == engine.state == LOGOUT_SENT`.
- **Guards:** any non-`Logon` message while `DISCONNECTED` ⟹ terminated with sequence numbers untouched. Mismatched CompIDs ⟹ `Logout`, and the message counts as neither session activity nor sequence progress.
- **Liveness:** with `HeartBtInt=30` and an injected clock — `+31s` ⟹ `Heartbeat`; `+46s` ⟹ one `TestRequest` and no duplicate on re-poll; `+73s` ⟹ `is_timed_out()`. `HeartBtInt=0` ⟹ no liveness traffic at all.
- **Resend serving:** an inbound `ResendRequest` ⟹ application messages replayed at their original `MsgSeqNum` with `poss_dup_flag=True` and Tag 122 set, administrative runs collapsed into one GapFill, and `out_seq_num` **unchanged**.
- Run `python -m unittest discover -s skills/fix-protocol-session-management-across-venues/scripts` — 48 tests, 100% pass.

## Related Skills

- `exchange-gateway-redundancy-and-failover-testing`
- `cme-stp-fix-and-ilink2-tag-value-encoding`
- `order-placement-idempotency`
- `sequence-number-gap-detection-for-feeds`
- `smart-order-router-failover-on-venue-outage`
- `exchange-self-match-prevention-configuration`
