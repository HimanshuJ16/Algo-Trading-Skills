# Deep Workflow Reference — exchange-multicast-feed-handling

This file holds the full technical procedure referenced by `SKILL.md`. Every protocol
claim below is cited in `references/standards.md`.

## Scope boundary

The engine in `scripts/multicast_handler.py` is the **sequencing layer only**. It owns no
sockets, issues no IGMP joins and decodes no payloads. The caller supplies, per datagram:
the line it arrived on, the sequence number from the venue's packet header, the opaque
payload, and — on MoldUDP64 — the header's Message Count.

## Full procedure

### 1. Socket layer (caller's responsibility, listed for completeness)

- Join the A and B multicast groups for the channel (`IP_ADD_MEMBERSHIP`, or source-specific
  `IP_ADD_SOURCE_MEMBERSHIP` where the venue publishes a source address). Without the
  group join the NIC never delivers the traffic.
- Tag every datagram with the line it came from. A single socket joined to both groups
  cannot distinguish A from B, so use one socket per line if the arbitration statistics
  matter to you.
- Run one `ExchangeMulticastFeedHandler` per channel. CME sequence numbers are per
  channel; Eurex `PacketSeqNum` is contiguous per SenderCompID per multicast address/port.
  Sharing an instance across channels interleaves unrelated sequence spaces.

### 2. Classification of an arriving datagram

`ingest_packet` places the packet's covered range `[seq .. seq + message_count - 1]`
against the expected sequence and returns one `PacketDisposition`:

| Condition | Disposition | Effect |
|---|---|---|
| Covered range ends more than `sequence_reset_threshold` below expected | `RESET_SUSPECTED` | Not applied. Confirm the venue restart signal, then `reset_sequence()` |
| Covered range ends below expected | `DUPLICATE` | Discarded — the twin line or a network duplicate arrived first |
| Starts below expected but covers it | `PARTIAL_OVERLAP` | Applied; `first_new_message_index` says how many leading messages the caller has already processed |
| Starts at expected | `PROCESSED` | Applied, then the buffer drains contiguously |
| Starts above expected, already buffered | `DUPLICATE` | Discarded; the buffer is not overwritten and no new gap opens |
| Starts above expected, buffer has room | `BUFFERED` | Held; a gap opens or extends |
| Starts above expected, buffer full | `DROPPED_BUFFER_FULL` | Dropped; `requires_resynchronization` latches |

The two dedup rows matter equally. Comparing only against the expected sequence catches
the twin of a packet already processed but not the twin of one still buffered — and the
buffered case is precisely what A/B redundancy produces while a gap is open.

### 3. Gap lifecycle

A handler holds at most one open gap, always anchored at the current expected sequence.

- **Open.** The first out-of-order packet creates `SequenceGap(start=expected,
  end=seq-1, detected_at=now, state=ARBITRATING)`. No recovery request is issued: Eurex
  requires a timed operation that is *cancelled* if the missing packet arrives in
  reasonable time, and CME reserves replay for cases where other options are unavailable.
- **Extend.** Later out-of-order packets raise `end`. `detected_at` is deliberately left
  alone — Eurex states that recovery already pending must not have its timer reset — so a
  burst of later packets cannot postpone escalation, and no second request is emitted.
- **Narrow.** A packet that fills the low end of the gap advances the expected sequence
  and re-anchors `start`, so the eventual request covers only what is still missing.
- **Close.** Once the expected sequence passes `end`, the gap clears. If this happened
  before the window elapsed, no request was ever made — the normal, desired outcome of
  A/B arbitration.
- **Escalate.** Once `now - detected_at >= arbitration_window_s`, the gap transitions to
  `RECOVERY_REQUESTED` and one `RecoveryRequest` is returned, exactly once. Both
  `ingest_packet` and `poll_recovery` can trigger this; `poll_recovery` exists because a
  feed can fall silent right after the loss, and a gap that is only evaluated on ingest
  would then never escalate.

### 4. Sizing the recovery request against venue limits

Before issuing the request, check the range against what the venue will actually serve:

- **CME**: at most 2000 packets per replay, 24-hour window, one Market Data Request per
  login/request/logout cycle, and CME logs you out if no request arrives within 5 seconds
  of logon. Requests are plain-text FIX; responses are SBE.
- **MoldUDP64**: the Re-request Server returns only the messages that completely fit one
  UDP datagram, so a large range needs several sequential requests.
- **Eurex T7**: there is no retransmission service. Skip this tier entirely and go to
  the snapshot channel.

A range larger than the venue will serve is a snapshot-recovery case, not a retry case.

### 5. Reconciling recovery data

`apply_recovery_packets(packets)` sorts by sequence and ingests each one, so recovered
packets are released *before* the queued real-time packets already in the buffer — the
order CME instructs. It returns a `RecoveryResult`:

- `is_gap_closed=True`, `outstanding_gap=None` — the stream is whole again.
- `is_gap_closed=False` with the remaining range — the response was partial. The gap
  narrows and **re-arms** (back to `ARBITRATING` with a fresh `detected_at`) so the
  remainder can be requested again. Partial responses are the normal case for a large
  gap given the caps above, not an error case.

### 6. Snapshot resynchronization

`resynchronize(next_sequence)` sets the expected sequence, discards the re-sequencing
buffer (the snapshot already reflects it), abandons the gap and clears
`requires_resynchronization`. Use it when:

- the buffer overflowed, which means arbitration can no longer repair the stream;
- the gap exceeds what the venue's replay will serve;
- the venue offers no replay at all (Eurex T7);
- CME Market Recovery / Natural Refresh has rebuilt the book — CME's *recommended
  primary* recovery path for MBP UDP-only systems, not a fallback.

`reset_sequence(next_sequence)` is the same operation named for the restart case: a CME
weekly reset or Channel Reset, or a new MoldUDP64 Session. Call it only once the restart
is confirmed from the venue's own in-band signal, because from sequence numbers alone a
restart and a stale replayed packet look identical, and applying the wrong one rewinds a
live book.

### 7. Trading gate

While `requires_resynchronization` is set, downstream book state is untrusted and quoting
must stop. The missing packet may have deleted the level being quoted against, and no
later incremental repairs it. Resume only after a snapshot rebuild, never on a heuristic
that updates "look normal again".

## Production Implementation Reference

- Reference code: `scripts/multicast_handler.py` (`ExchangeMulticastFeedHandler`,
  `MulticastChannel`, `MulticastPacket`, `SequenceGap`, `RecoveryRequest`,
  `PacketDisposition`, `GapState`, `MulticastHandlerResult`, `RecoveryResult`).
- Automated unit tests: `scripts/test_multicast_handler.py`.
