---
name: cme-stp-fix-and-ilink2-tag-value-encoding
description: >-
  Use when a system speaks tag=value FIX to CME Group: an STP FIX 4.4 trade-capture
  session, a replay over archived iLink 2 flow, or a conformance harness. Not for live
  order entry, which moved to iLink 3 binary; see cme-globex-futures-api-integration.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: cme-group, fix-protocol, futures, tag1028, smp, self-match-prevention, seqnum, ilink3
  brokers_frameworks: "CME STP FIX 4.4; CME iLink 2 (decommissioned); CME iLink 3 (SBE/FIXP)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a system speaks **tag=value FIX** to or about CME Group: a CME STP
FIX 4.4 trade-capture session, a replay or simulator over archived iLink 2 flow, a
conformance harness, or a port of legacy CME FIX order-entry code onto iLink 3.

It covers the four things hand-rolled CME FIX code gets wrong:

1. Emitting a "FIX message" with no `BeginString`, `BodyLength`, `SendingTime` or
   `CheckSum` — a string no counterparty can frame, let alone accept.
2. Re-requesting a resend on every out-of-sequence message, turning one gap into a
   resend storm and a disconnect.
3. Putting a value in Tag 8000 that CME does not define (only `O` and `N` exist).
4. Rounding a price to a fixed number of decimals, which mis-prices any instrument
   quoted more finely.

## When NOT to Use

- **For live CME order entry.** CME order entry no longer accepts tag=value FIX. iLink 2
  — the FIX 4.2-based order-entry protocol these tags come from — was decommissioned on
  the Market Segment Gateway on **2021-03-28** and on the Convenience Gateway on
  **2025-04-06**. Live order entry is **iLink 3**: FIX Simple Binary Encoding over the
  FIXP session layer. Nothing this module emits will reach CME Globex. Use
  `to_ilink3_order_fields()` for the field mapping and see
  `cme-globex-futures-api-integration`.
- **As a FIX session engine.** This module frames messages and tracks sequence numbers.
  It does not own a socket, heartbeats, `TestRequest` liveness, logon negotiation,
  resend replay from a message store, or sequence persistence across restarts. For a
  production session, use a maintained engine (QuickFIX, OnixS) and keep this module for
  the CME-specific field semantics. See `fix-protocol-session-management-across-venues`.
- **For iLink 3 sequencing.** FIXP has no `MsgSeqNum(34)`, no `ResendRequest(35=2)` and
  no `SequenceReset(35=4)`. Its recovery is `Retransmit Request` / `NotApplied`, and the
  state machine here does not transfer. See
  `matching-engine-throttle-and-message-gapping-detection`.
- **For CME market data.** MDP 3.0 is SBE multicast, not FIX. See
  `exchange-multicast-feed-handling`.

## Prerequisites

- A session identity for the venue in question: `SenderCompID` / `TargetCompID`, plus an
  operator ID registered under CME Rule 576 (Tag 50 in tag=value FIX; Tag 5392
  `SenderID`, 20 bytes, in iLink 3).
- Self-Match Prevention IDs registered in the CME Firm Administrator Dashboard (FADB).
  An SMP ID must be numeric to survive a port to iLink 3, where Tag 2362 is a `uInt64` —
  a string that is legal in tag=value FIX is not representable there.
- Python 3.9+. Standard library only.

## Workflow

1. **Decide which protocol you are actually on before writing a byte.** If the
   destination is CME order entry, stop: that is iLink 3 SBE, and a tag=value encoder is
   the wrong tool. If it is CME STP, it is FIX 4.4 — set `begin_string="FIX.4.4"`.

2. **Build messages through `build_fix_message()` and send them verbatim.** It emits
   `8=…|9=…|35=…` first and `10=…` last, with SOH delimiters and a UTC `SendingTime(52)`
   at millisecond precision. `BodyLength` and `CheckSum` are computed over exactly those
   bytes: re-serialising the message downstream invalidates both.

3. **Populate the CME order tags, with the values CME defines.**
   `1028=N` for automated flow, `Y` for a human-entered order — required on CME order
   messages since June 2011. `50` carries the Rule 576 operator ID. `7928` is the
   registered SMP ID, and `8000` is either `O` (cancel oldest — the resting order) or
   `N` (cancel newest — the aggressor). Omitting Tag 8000 is legal; CME's default is to
   cancel the resting order. `create_new_order_single()` rejects any other value rather
   than serialising it.

4. **Pass prices as decimal strings or `Decimal`.** They are serialised without rounding
   and without scientific notation. A fixed-precision format is the failure mode here:
   four decimals turns a 1.05125 FX future into 1.0513, a full tick away.

5. **Drive inbound sequencing through `process_inbound_message()` and act on what it
   returns.**
   - A `ResendRequest(35=2)` means a newly detected gap — send it. Messages that arrive
     while that request is outstanding are the peer working through the range; the
     engine deliberately does **not** re-request on each one.
   - A `Logout(35=5)` means the peer sent a sequence number below the expected one
     without `PossDupFlag(43)=Y`. The FIX session layer treats that as unrecoverable:
     send the Logout, then drop the transport connection. Do not keep trading on it.
   - `None` means in sequence, an admissible duplicate, or recovery already in flight —
     three different things. Gate business processing on `last_inbound_accepted`, which
     is true only for an in-sequence message. Applying a duplicate or an out-of-sequence
     `ExecutionReport` replays or reorders fills.

6. **Persist both sequence counters.** They live in memory here. A process restart that
   resets them to 1 mid-session desynchronises the session; the reset policy (and when
   `ResetSeqNumFlag(141)=Y` is appropriate) is venue-specific — take it from the venue's
   session spec, not from this module's defaults.

7. **When porting to iLink 3, run `to_ilink3_order_fields()` first.** It fails loudly on
   the two fields that do not survive a naive port: a non-numeric SMP ID (Tag 2362 is a
   `uInt64`) and an operator ID longer than the 20-byte `SenderID` field.

> Full procedure: see `references/workflows.md`.
> Protocol status, tags, values and sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating a delimited tag list as a FIX message.** Without `BeginString`, `BodyLength`
  and `CheckSum` there is nothing for the peer to frame on, and a `|`-delimited string is
  a log rendering, not the wire format. The wire delimiter is SOH (`\x01`).
- **Re-requesting a resend on every out-of-sequence message.** During recovery the peer
  is replaying the range you asked for, and those messages are legitimately ahead of your
  expectation. Issuing a fresh `ResendRequest` for each one floods the session and ends in
  a disconnect — the opposite of recovery.
- **Ignoring a too-low sequence number.** Logging it and moving on silently discards
  everything the peer sends afterwards, including `ExecutionReport`s: the position on the
  book stops matching the position in the process. The session layer requires a Logout and
  a disconnect, except for `SequenceReset` with `GapFillFlag(123)=N`, which is honoured
  whatever its own sequence number.
- **Inventing Tag 8000 values.** CME defines exactly `O` (CancelOldest) and `N`
  (CancelNewest). `R`, `B`, `CANCEL_RESTING` and similar inventions are rejected by the
  venue — and `O` means "cancel the *oldest*, i.e. resting, order", not "cancel outgoing".
- **Assuming a missing Tag 1028 produces a session-level reject.** CME answers an invalid
  or missing `ManualOrderIndicator` with a **Business Reject** (`35=j`,
  `BusinessRejectReason(380)=100`), not `35=3`. Code that only inspects `35=3` will treat
  the order as live.
- **Rounding prices to a fixed precision.** `f"{price:.4f}"` is silently wrong for every
  instrument quoted below four decimals, and a float price carries binary representation
  artifacts into the wire format.
- **Reusing an iLink 2 SMP ID on iLink 3.** Tag 7928 was an alphanumeric string; Tag 2362
  is a `uInt64`. `SMP_888` has no iLink 3 representation, and the operator ID moves from
  Tag 50 to Tag 5392 at the same time.
- **Defaulting the account.** A hard-coded placeholder account routes real risk to
  whatever that string happens to resolve to. Tag 1 is required here for that reason.
- **Consuming a sequence number for a message that is never sent.** The counter advances
  when the message is built, so messages must be transmitted in build order and a build
  that is discarded leaves a permanent hole the peer will ask you to fill.

## Verification

- Confirm the message starts `8=FIX.4.x<SOH>9=<n><SOH>35=…` and ends `10=<nnn><SOH>`, and
  that `9=` equals the byte count between the end of the `9=` field and the start of
  `10=`, derived independently rather than from the module's own arithmetic.
- Confirm the checksum equals the sum of every preceding byte modulo 256, zero-padded to
  three digits.
- Confirm a price of `1.05125` serialises unrounded, `Decimal("0.000005")` does not become
  `5E-6`, and `NaN`/`Infinity` raise.
- Confirm `smp_instruction="R"` raises, `"O"` and `"N"` are accepted, and `None` omits Tag
  8000 while leaving Tag 7928 in place.
- Confirm a value containing SOH or `=` is rejected rather than forging a field.
- Confirm one gap yields exactly one `ResendRequest`, that sequences 6, 7 and 8 arriving
  during recovery yield none, and that recovery clears once the range is filled.
- Confirm a too-low sequence number without `PossDupFlag` returns a `Logout(35=5)` and
  marks the session terminated, while the same message with `43=Y` is discarded quietly.
- Confirm `last_inbound_accepted` is true only for the in-sequence message — not for the
  duplicate and not for the message that arrived mid-recovery, both of which also return
  `None`.
- Confirm `to_ilink3_order_fields()` maps `50→5392`, `7928→2362` (as an integer) and
  `1028` to `0`/`1`, and raises on a non-numeric SMP ID.
- Run `python -m unittest discover -s skills/cme-stp-fix-and-ilink2-tag-value-encoding/scripts` and confirm a 100% pass rate.
- Against a venue test session only: send one built message unmodified through the real
  transport and confirm it is accepted. A framing bug that unit tests cannot see is one
  where the transport rewrites the bytes.

## Related Skills

- `cme-globex-futures-api-integration`
- `fix-protocol-session-management-across-venues`
- `exchange-self-match-prevention-configuration`
- `matching-engine-throttle-and-message-gapping-detection`
- `order-placement-idempotency`
