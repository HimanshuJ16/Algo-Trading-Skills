# Standards — exchange-multicast-feed-handling

## Primary sources

- **Nasdaq**, *MoldUDP64 Protocol Specification*, V 1.00 (version-control table last
  revised 2 Aug 2024)
  ([nasdaqtrader.com](https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/moldudp64.pdf)).
- **CME Group**, *MDP 3.0* client-systems documentation — the *Dissemination*,
  *Recovery Services*, *TCP Recovery*, *SBE Technical Headers* and *Channel Reset* pages
  ([CME Group Client Systems Wiki](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457325847/MDP+3.0+-+Recovery+Services)).
- **Deutsche Börse Group / Eurex**, *T7 Release 14.1 — Market and Reference Data
  Interfaces Manual*, Version 2, chapter 7 "Recovery"
  ([eurex.com](https://www.eurex.com/resource/blob/5055560/c138bd262600e59a7c1018112fdeb252/data/T7_R.14.1_%20EMDI_MDI_RDI_Manual_Version_2.pdf)).

CME revises its wiki continuously and Eurex reissues its manual each T7 release. Re-verify
the rows below against the documents your firm is certified against before relying on them.

## Sequence-space semantics

| Fact | Venue | Source |
|---|---|---|
| Packet header is 12 bytes: `MsgSeqNum` (uInt32) + `SendingTime` (uInt64, ns since Unix epoch). MsgSeqNum is **per channel**, increments by one per packet, maximum 4294967295, and **resets weekly** | CME MDP 3.0 | SBE Technical Headers |
| Market data sequence numbers reset to 1 during the Saturday startup cycle; a Channel Reset is identified by Market Data Incremental Refresh 35=X with 269-MDEntryType=J and 1180-ApplID present | CME MDP 3.0 | Channel Reset |
| Downstream packet header is 20 bytes, big-endian: Session (10 bytes), Sequence Number (8 bytes), Message Count (2 bytes). The Sequence Number is that of the **first message in the packet**; subsequent messages are implicitly numbered sequentially — so the sequence space counts **messages, not packets** | Nasdaq MoldUDP64 | Header; Sequence Number |
| Message Count 0 denotes a heartbeat carrying the next expected sequence number; 0xFFFF denotes end of session, sent in place of heartbeats while re-requests are still accepted | Nasdaq MoldUDP64 | Message Count; Heartbeats; End of Session |
| `PacketSeqNum` is contiguous for packets sharing a `SenderCompID` **per multicast address/port combination**; duplicate A/B packets are identified by comparing the first 10 bytes (EMDI) or 8 bytes (MDI/RDI) of the datagram, i.e. SenderCompID + PacketSeqNum | Eurex T7 | §7.1 |

## Line redundancy (recovery tier 1)

| Fact | Venue | Source |
|---|---|---|
| "All packets are sent through both UDP Feed A and UDP Feed B"; "UDP Feed A and UDP Feed B should be used for arbitration"; CME strongly recommends client systems process both A and B incremental feeds, which "provide the first level of protection against missed market data messages" | CME MDP 3.0 | Dissemination; Recovery Services |
| Feeds are replicated onto Service A and Service B on different multicast addresses; participants are advised to join both. Ideally the receiver processes both simultaneously, takes the copy that arrives first and discards the second | Eurex T7 | §7.2 |
| Multicast does not guarantee ordering; packets may be delayed, reordered or duplicated at network level, and receiving applications must handle all of these | Eurex T7 | §7 intro, §7.3 |

## Declaring loss (the arbitration window)

| Fact | Venue | Source |
|---|---|---|
| On an out-of-sequence packet: release the in-order packet, recognise the missing one, and "start an appropriate timed operation to trigger the recovery actions if the out-of-sequence message fails to arrive in a reasonable time"; if it arrives in time, release it in order and **cancel the timed recovery action** | Eurex T7 | §7.3 |
| "All lost packets start life as 'delayed' packets… The communications layer of the receiving application is responsible for deciding when to declare a network packet as lost." Later out-of-sequence packets are held for the same reason and "timer-based recovery actions are already pending for this product, **so do not reset the timer**" | Eurex T7 | §7.4 |
| The maximum expected recovery interval for a feed is published as `MDRecoveryTimeInterval` (tag 2565) in the T7 RDI Product snapshot | Eurex T7 | §7.2 |

The window length itself is a per-venue, per-network measurement. No source in this file
prescribes a numeric default, and this skill does not invent one — the constructor
requires the caller to supply it.

## Retransmission (recovery tier 2) — venue-specific transport

| Fact | Venue | Source |
|---|---|---|
| TCP replay lets a client request a replay of packets already published on the UDP incremental channel, identified by start and end packet sequence numbers. **Maximum 2000 packets per request**, a **24-hour** availability limit, and **one Market Data Request per session** — multiple requests need separate login/request/logout cycles. CME logs the client out if no request arrives within 5 seconds of logon. Requests are plain-text FIX; responses are SBE | CME MDP 3.0 | TCP Recovery; TCP Market Data Replay Request |
| TCP replay is "intended for small-scale data recovery" and "should only be used if other options are unavailable"; it is explicitly not a performance-based solution | CME MDP 3.0 | TCP Recovery |
| Client systems should queue real-time data until all missed data is recovered, and **the recovered data should then be applied prior to queued data** | CME MDP 3.0 | TCP Recovery |
| Retransmission is requested with a Request Packet sent to a **Re-request Server over UDP unicast** (Session, first Sequence Number, Requested Message Count). The response is a standard Downstream Packet unicast back to the requester, readable on the same socket as the multicast stream | Nasdaq MoldUDP64 | Request Packet; Overview |
| If the requested messages exceed one UDP packet's payload, **only the messages that completely fit are returned**; further requests are needed for the remainder | Nasdaq MoldUDP64 | Requested Message Count |
| **No retransmission service exists.** "Recovery actions are possible on a packet level by using the respective other service (A or B). In case a packet is lost on both services (A and B) clients can create a new current order book by using snapshot information" | Eurex T7 | §7 intro, §7.4 |

> **Unit caveat on the CME 2000 limit.** CME's own pages phrase this cap as "2000
> packets" (TCP Recovery) and as "2000 messages per Market Data Request" (replay-request
> material). One MDP 3.0 packet can carry several messages, so the two readings are not
> equivalent. Confirm the unit against the page version you certify against before sizing
> requests close to the limit; this skill sizes conservatively in packets.

## Snapshot resynchronization (recovery tier 3)

| Fact | Venue | Source |
|---|---|---|
| Market Recovery is a snapshot loop for MBP and MBO recovering the most recent market state per instrument per channel; Feed A carries it with Feed B as backup. Natural Refresh is available for MBP but "is not guaranteed and should not be considered a definitive substitute for recovering lost data" | CME MDP 3.0 | Recovery Services; MBP and MBOFD Market Recovery |
| "CME Group recommends Market Recovery in conjunction with Natural Refresh as a primary recovery option" for MBP UDP-only systems. Client systems must certify for Market Recovery before deployment | CME MDP 3.0 | Recovery Services |
| Recovery compares the Market Recovery snapshot's 369-LastMsgSeqNumProcessed against the incremental feed's packet sequence number, tie-broken on 60-TransactTime | CME MDP 3.0 | MBP and MBOFD Market Recovery |
| EMDI delivers snapshots and incrementals on **separate channels** (out-of-band), linked by `LastMsgSeqNumProcessed`; MDI delivers both on one feed (in-band). If a gap cannot be filled from the other service, the receiver initiates snapshot recovery, rebuilding the book as at start of day | Eurex T7 | §3.3, §7.4.1 |
