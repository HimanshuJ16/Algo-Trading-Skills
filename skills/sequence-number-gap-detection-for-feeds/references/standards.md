# Standards — sequence-number-gap-detection-for-feeds

## Primary sources

- **Nasdaq**, *MoldUDP64 Protocol Specification*, V 1.00 (version-control table last revised
  2 Aug 2024)
  ([nasdaqtrader.com](https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/moldudp64.pdf)).
- **CME Group**, *MDP 3.0* client-systems documentation — the *Recovery Services*,
  *TCP Recovery*, *Incremental Feed Arbitration* and *SBE Technical Headers* pages
  ([CME Group Client Systems Wiki](https://www.cmegroup.com/confluence/display/EPICSANDBOX/MDP+3.0+-+Recovery+Services)).
- **Binance**, *How To Manage A Local Order Book Correctly* and *Diff. Book Depth Streams*
  ([developers.binance.com](https://developers.binance.com/docs/derivatives/coin-margined-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly)).

CME revises its wiki continuously and Binance publishes changelog-driven updates to its
API docs. Re-verify every row below against the document version your firm is certified
against before relying on it.

## Sequence-space semantics

| Fact | Venue | Source |
|---|---|---|
| The downstream packet header is 20 bytes, big-endian: Session (10), Sequence Number (8), Message Count (2). The Sequence Number is that of the **first message in the packet**; "any messages following the first message are implicitly numbered sequentially" — so the space counts **messages, not packets** | Nasdaq MoldUDP64 | Downstream Packet → Header, Sequence Number |
| A Session is "a sequence of one or more messages"; once terminated no more messages are sent on it. A restarting receiver is configured with "a session and sequence number of the next expected message" | Nasdaq MoldUDP64 | Terms → Session; Receiver Example |
| The packet header carries a per-channel packet sequence number that increments per packet and is **reset weekly** | CME MDP 3.0 | SBE Technical Headers; Recovery Services |
| A depth event carries `U` (first update ID in event) and `u` (final update ID in event); the continuity rule is that "each new event's `pu` should be equal to the previous event's `u`" | Binance futures | How To Manage A Local Order Book Correctly |

**Implication for this engine.** MoldUDP64 and CME MDP 3.0 sequence one space per session
or channel, covering every instrument carried on it. `FeedFrame.stream` is that space.
Keying per symbol against such a feed makes every message a gap. Binance sequences per
symbol stream, but by *range* — pass `last_sequence_id`.

## Detecting the gap

| Fact | Venue | Source |
|---|---|---|
| A receiver compares each packet's sequence number against its next expected; on mismatch it sends a Request Packet, waits for a new packet, and re-checks | Nasdaq MoldUDP64 | Receiver Example, steps 4–5 |
| Heartbeats are sent periodically, "typically once per second", "so receivers can sense packet loss even during times of low traffic", and **contain the next expected Sequence Number**. A heartbeat is a packet with Message Count 0 | Nasdaq MoldUDP64 | Heartbeats; Message Count |
| End of Session is signalled by Message Count 0xFFFF, sent in place of heartbeats and also carrying the next expected sequence number; re-requests may still be made while it persists — "the last chance to ensure that all messages have been received" | Nasdaq MoldUDP64 | End of Session |
| If a sequence gap is detected, "it should be assumed that all books maintained in the client system may no longer have the correct, latest state maintained by CME Group" | CME MDP 3.0 | Recovery Services |
| A gap on the incremental feed means the packet was lost on **both** Feed A and Feed B, and the client must initiate recovery | CME MDP 3.0 | Incremental Feed Arbitration |

**Implication for this engine.** `observe_heartbeat` exists because loss at the tail of a
stream is invisible to frame-driven detection: with no later frame, nothing exposes the
missing sequences. The End of Session marker is the deadline for closing any gap on a
MoldUDP64 session — a stream still `DIRTY_SYNC_PENDING` when it passes is unrecoverable.

## Recovery — and why one round is not enough

| Fact | Venue | Source |
|---|---|---|
| Retransmission is requested with a Request Packet (Session, first Sequence Number, Requested Message Count) sent to a Re-request Server; the response is a standard Downstream Packet unicast back to the requester and readable on the same socket as the multicast stream | Nasdaq MoldUDP64 | Request Packet; Overview |
| "If the total size of the requested messages exceeds the maximum payload size of one UDP packet, only the number of messages that completely fit will be returned. **Additional retransmission requests must be made for the subsequent messages** if they are still desired" | Nasdaq MoldUDP64 | Requested Message Count |
| TCP replay recovers specific missed packets by packet sequence number; CME's client documentation states a maximum of 2000 packets per request | CME MDP 3.0 | TCP Recovery |
| Market Recovery is a snapshot loop recovering the most recent market state per instrument per channel; client systems must process one full iteration starting at sequence number 1 for full recovery | CME MDP 3.0 | Recovery Services |
| On a `pu`/`u` continuity break the client must "initialize the process from step 3" — re-fetch the REST depth snapshot and restart. There is no retransmission service | Binance | How To Manage A Local Order Book Correctly, step 6 |
| The first processed event after a snapshot must satisfy `U <= lastUpdateId AND u >= lastUpdateId`; events whose `u` is at or below `lastUpdateId` are dropped | Binance | How To Manage A Local Order Book Correctly, steps 4–5 |

> **Unit caveat on the CME 2000 limit.** CME's pages phrase this cap in packets on the TCP
> Recovery page and in messages elsewhere, and one MDP 3.0 packet can carry several
> messages. The CME wiki pages did not render for direct retrieval during this skill's
> last review; the figure above is reported from CME's own documentation but was not
> re-read line by line. Confirm the number *and its unit* against the page version you
> certify against before sizing requests near the limit. This engine hard-codes no venue
> limit — it reports `missing_ranges` and leaves request sizing to the caller.

**Implication for this engine.** `ReconciliationResult.is_synced` exists because a single
backfill round closing the gap is the exception, not the rule. `remaining_ranges` is what
you re-request; when it exceeds what the venue will replay, escalate to `resynchronize`
against a snapshot instead.

## Sequence-state model

| State | Condition | Trading authorization | Exit |
|---|---|---|---|
| `SYNCED` | Every sequence below `expected` delivered, nothing outstanding | **ENABLED** | — |
| `DIRTY_SYNC_PENDING` | A gap is open and no backfill has been applied to it | DISABLED | Backfill or snapshot |
| `RECOVERING` | A backfill round was applied and the gap is still open | DISABLED | Further backfill, or snapshot |
| `RESET_REQUIRED` | Buffer overflowed, or a suspected publisher restart | DISABLED (latched) | `resynchronize` only |

An unknown stream is not authorized: no frame has yet established that local state
corresponds to anything the publisher sent.

## Thresholds this skill does *not* invent

- **`max_buffer_size`** is an engineering guard, not a venue constant. No source above
  prescribes one. Size it from the stream's message rate times the longest recovery you
  intend to survive.
- **`sequence_reset_threshold`** distinguishes a restart from a stale echo. CME resets
  weekly and on a Channel Reset and MoldUDP64 opens a new Session, but neither publishes a
  numeric threshold at which a backward jump becomes "a restart". The default (1,000,000)
  is a deliberately conservative engineering default; the restart itself must be confirmed
  from the venue's in-band signal before calling `resynchronize`.
- **Arbitration windows** are out of scope here entirely — see
  `exchange-multicast-feed-handling`, which measures how long a gap should be held before
  it is declared lost.
