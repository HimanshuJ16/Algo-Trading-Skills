---
name: exchange-multicast-feed-handling
description: Use when building or auditing the sequencing layer of a co-located UDP
  multicast feed handler (CME MDP 3.0, Nasdaq MoldUDP64, Eurex T7 EMDI) - arbitrating
  the redundant A/B lines, re-sequencing out-of-order datagrams, holding a gap for an
  arbitration window before declaring loss, and escalating through the venue's
  retransmission and snapshot recovery tiers.
domain: Venue Integration & Protocols
subdomain: Multicast Line Arbitration & Feed Recovery
tags:
- udp-multicast
- line-arbitration
- cme-mdp3
- moldudp64
- eurex-t7-emdi
- packet-resequencing
- feed-recovery
- co-location
brokers_frameworks:
- CME MDP 3.0
- Nasdaq MoldUDP64
- Eurex T7 EMDI
- Python dataclasses / enum
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when writing or reviewing the **sequencing layer** of a feed handler that
consumes redundant UDP multicast lines from an exchange. It covers the decisions that a
handler gets wrong in ways that either corrupt a book silently or flood a venue with
recovery requests: which copy of a duplicated datagram to keep, how long to wait before
calling a missing packet lost, how many recovery requests one gap may generate, what to
do when recovery comes back incomplete, and how to survive a sequence-number restart.

The recovery model here is a three-tier hierarchy, and the ordering is the venue's, not a
preference:

1. **A/B line arbitration.** CME sends every packet on both Feed A and Feed B and states
   the two feeds "should be used for arbitration"; Eurex replicates onto Service A and
   Service B and advises participants to join both. This tier costs nothing and repairs
   most loss.
2. **Retransmission**, where the venue offers one — and the transports differ. CME uses a
   TCP replay session; Nasdaq MoldUDP64 uses a **UDP unicast** Request Packet to a
   Re-request Server; **Eurex T7 has no retransmission service at all**.
3. **Snapshot resynchronization** — CME Market Recovery (which CME recommends, together
   with Natural Refresh, as the *primary* recovery option), or the Eurex depth-snapshot
   channel.

Every protocol fact in this skill is cited to a primary specification in
`references/standards.md`. Re-verify against the document version your firm is certified
against before relying on it.

## When NOT to Use

- **As a socket layer.** This module owns no sockets and issues no IGMP joins. Binding,
  `IP_ADD_MEMBERSHIP`, `SO_REUSEADDR`, source-specific multicast filters, NIC ring sizing
  and CPU pinning are the caller's, and a datagram's arrival line must be determined by
  the caller (a single socket joined to both groups cannot tell A from B). It is a
  deliberate split: the sequencing decisions are the part that is subtle and testable.
- **As a message decoder.** Payloads are opaque bytes here. Parsing the CME SBE packet
  header, the MoldUDP64 20-byte header and Message Blocks, or Eurex FAST/FIX belongs to
  `binary-protocol-parsing-for-low-latency-feeds`.
- **For per-product gap tracking.** Gaps are tracked in one packet-level sequence space
  per channel. Eurex describes an "optimistic" alternative that decodes to message level
  and recovers only products of interest; that needs a decoder and is out of scope here.
- **As a latency-optimised production handler.** This is a correctness reference in
  Python — dict-based buffer, per-packet allocation, `Enum` comparisons. A colocated
  handler needs preallocated, cache-friendly structures.
- **For in-band recovery feeds.** Eurex T7 MDI and Euronext MDG Lite carry snapshots and
  incrementals on one feed with different sequencing rules. For Euronext Optiq see
  `euronext-optiq-market-data-integration`.

## Prerequisites

- The **A and B multicast group/port pairs** for the channel, and a socket layer that
  tags each datagram with the line it arrived on.
- The **packet sequence number** decoded from the venue's packet header — CME MsgSeqNum
  (uInt32, little-endian), MoldUDP64 Sequence Number (8 bytes, big-endian), Eurex
  PacketSeqNum. Sequence spaces are per channel; one handler instance per channel.
- For MoldUDP64, the header's **Message Count** as well, because that feed numbers
  messages rather than packets.
- An **arbitration window** measured for your own network. There is no safe default:
  Eurex publishes the maximum expected recovery interval as `MDRecoveryTimeInterval`
  (tag 2565) in the T7 RDI Product snapshot; for other venues, measure the A-versus-B
  arrival skew on your own cross-connects. The constructor requires it.
- A configured path to whichever recovery tiers your venue offers, plus a snapshot
  consumer — snapshot recovery is the only tier every venue here provides.

## Workflow

1. **Ingest both lines into one handler instance**:
   - Call `ingest_packet(channel, sequence_id, payload, message_count)` for every datagram
     from A and from B. The first copy to arrive is processed; the second is reported
     `DUPLICATE`.
   - **Decision point — dedup must cover packets that are still ahead of the stream.**
     A twin that arrives while its sequence is sitting in the re-sequencing buffer is
     still a duplicate. Comparing only against the expected sequence misses it, and the
     packet then gets buffered twice and re-triggers recovery.

2. **Classify a low sequence number before discarding it**:
   - **Decision point — a large backward jump is a restart, not a duplicate.** CME resets
     MsgSeqNum weekly and restarts it on a Channel Reset (35=X, 269=J, with 1180-ApplID);
     MoldUDP64 opens a new Session. The handler reports `RESET_SUSPECTED` and refuses to
     apply the packet, because from sequence numbers alone a restart and a stale replayed
     packet are indistinguishable. Confirm from the venue's in-band signal, then call
     `reset_sequence()`. A handler that files every low sequence under "duplicate" goes
     deaf for the rest of the week.

3. **Hold an out-of-order gap before declaring loss**:
   - Buffer the early packet, open one gap at `[expected .. seq-1]`, and start the
     arbitration window. Return `BUFFERED`, not a recovery request.
   - **Decision point — every lost packet starts life as a delayed one.** Multicast
     reorders routinely and the twin is normally microseconds behind on the other line.
     Requesting recovery on the first reordered datagram converts ordinary reordering
     into request volume the venue has capped.
   - While the gap stays open, later out-of-order packets **extend its range without
     restarting the timer** — Eurex states that recovery already pending must not have
     its timer reset — and yield no second request.

4. **Escalate exactly once when the window elapses**:
   - `ingest_packet` and `poll_recovery` both age the gap; the crossing call returns one
     `RecoveryRequest`. **Poll on a timer as well as on ingest**: after a loss the feed
     can go quiet, and a handler that only evaluates gaps when packets arrive never
     escalates.
   - **Decision point — size the request against the venue's limits.** CME caps a replay
     at 2000 packets, keeps only 24 hours, and processes one Market Data Request per
     login/logout cycle. MoldUDP64 returns only the messages that fit one datagram, so a
     large range needs several requests. If the range exceeds what the venue will serve,
     go to the snapshot tier instead.

5. **Apply recovered data ahead of queued data**:
   - `apply_recovery_packets()` releases the recovered packets first and then drains the
     buffered real-time packets behind them, which is the order CME instructs.
   - **Decision point — a partial fill does not close the gap.** The handler narrows the
     gap, re-arms the timer, and reports `is_gap_closed=False` with the outstanding range.
     Treating a partial response as success leaves a permanent hole no later update
     repairs.

6. **Fall back to the snapshot when arbitration cannot repair the stream**:
   - Overflowing `max_buffered_packets` latches `requires_resynchronization`. So does any
     gap the venue will not replay. Call `resynchronize(next_sequence)` from the snapshot.
   - **Decision point — stop quoting while that flag is set.** The missing packet may
     have deleted the level you are quoting against, and "resume when updates look normal
     again" resumes against a corrupt book.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Requesting recovery on the first out-of-sequence datagram.** UDP delivers out of
  order as a matter of course. Eurex's guidance is to start a timed operation and cancel
  it if the missing packet arrives in reasonable time; CME reserves TCP replay for
  small-scale recovery and says it should only be used if other options are unavailable.
  An immediate request also races the twin on the other line, which usually arrives first
  and makes the request pointless.
- **Emitting one request per out-of-order packet.** With a gap open at 101 and packets
  103, 104, 105 arriving, a handler that re-derives the range each time issues three
  overlapping requests. One gap gets one request; later packets extend its range.
- **Deduplicating only against the expected sequence.** That catches the twin of a packet
  already processed, but not the twin of one still buffered — which is exactly the case
  A/B redundancy exists to produce during a gap.
- **Treating a weekly or channel reset as a stream of duplicates.** CME resets MsgSeqNum
  weekly. A handler that silently drops everything below its expected sequence discards
  100% of the following session while logging nothing above debug level.
- **Assuming one sequence number per packet on MoldUDP64.** The header carries the
  sequence of the *first* message plus a Message Count, and the rest are implicitly
  numbered; the next expected sequence is `seq + message_count`. Advancing by one
  manufactures a gap on every multi-message packet.
- **Assuming a TCP historical server exists.** It is venue-specific: CME has TCP replay,
  MoldUDP64 re-requests over UDP unicast, and Eurex T7 has no retransmission service —
  a packet lost on both Eurex services is recoverable only from the snapshot channel.
- **Closing a gap on a partial recovery response.** MoldUDP64 returns only the messages
  that fit one datagram and CME caps a replay at 2000 packets, so an incomplete response
  is the normal case for a large gap, not an error case.
- **Letting the re-sequencing buffer grow without a cap.** A sustained outage otherwise
  turns a feed handler into a memory-exhaustion incident. A full buffer is also the
  signal that arbitration has failed and only a snapshot will restore the book.
- **Timing the arbitration window on the wall clock.** `time.time()` can step backwards
  under NTP correction, which either fires recovery instantly or suspends it. Use a
  monotonic clock.
- **Sharing one handler across channels or instruments.** Sequence spaces are per channel
  (CME per channel, Eurex per SenderCompID per multicast address/port); interleaving two
  of them produces continuous false gaps.

## Verification

- Instantiate `ExchangeMulticastFeedHandler(100, arbitration_window_s=0.010)` with an
  injected clock. Feed seq 100 on A, then 100 on B, then 101 on B; verify `PROCESSED`,
  `DUPLICATE`, `PROCESSED` and `expected_sequence == 102`.
- Feed 100 then 102 on A, advance the clock by half the window, then feed 101 on B;
  verify both 101 and 102 are released in order, the gap closes, and
  `recovery_requests` is still empty — the twin line did its job.
- Feed 100 then 103 on A and verify `BUFFERED`, `missing_range == (101, 102)` and no
  recovery request. Advance the clock by the full window, then feed 104, 105, 106, 107;
  verify exactly **one** `RecoveryRequest` starting at 101 exists.
- Feed 100 then 103, then repeatedly feed further out-of-order packets inside the window;
  verify `pending_gap.detected_at` never moves.
- With a gap open, call `apply_recovery_packets` with only 101 of `[101, 102]`; verify
  `is_gap_closed` is False, `outstanding_gap == (102, 102)`, 103 is still buffered, and
  the gap re-arms so a second request can be issued.
- Start at sequence 4,000,000 and feed sequence 1; verify `RESET_SUSPECTED`, nothing
  processed, and `expected_sequence` unchanged. Then `reset_sequence(1)` and verify the
  same packet is processed.
- MoldUDP64: feed seq 1 with `message_count=3`; verify `expected_sequence == 4`. Feed a
  straddling recovery packet at seq 2 with `message_count=4`; verify `PARTIAL_OVERLAP`
  and `first_new_message_index == 2`.
- With `max_buffered_packets=3`, buffer three packets and feed a fourth; verify
  `DROPPED_BUFFER_FULL` and `requires_resynchronization`, then clear it with
  `resynchronize()`.
- Negative checks: constructing without `arbitration_window_s`, a negative
  `initial_sequence`, a negative `sequence_id`, a boolean sequence number, a non-`bytes`
  payload, and `message_count=0` must each raise.
- Run `python scripts/test_multicast_handler.py` and confirm a 100% pass rate.

## Related Skills

- `sequence-number-gap-detection-for-feeds`
- `binary-protocol-parsing-for-low-latency-feeds`
- `market-data-snapshot-plus-delta-reconciliation`
- `nasdaq-totalview-itch-feed-parsing`
- `euronext-optiq-market-data-integration`
- `cme-globex-futures-api-integration`
- `eurex-market-data-and-order-api`
- `clock-synchronization-ptp-for-trading-hosts`
