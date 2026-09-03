# Standards for Matching Engine Throttle and Message Gapping Detection

## Scope note

The venue figures below are **release-, market- and session-type-specific** and change.
Confirm the current values for your own session against the venue's documentation before
wiring a number into code. Where a figure is this skill's own operating convention rather
than a venue or regulatory requirement, it is labelled as such.

`max_allowed_mps = 500.0` in `scripts/` is a **placeholder default and not a
venue-published figure**. No venue on this page publishes a single "500 msgs/sec per iLink
session" application limit. Take the real number from your session's contracted limit.

## What venues actually do above a message-rate limit

None of these venues queue your overflow. Each sheds load by rejecting, then disconnects.

| Venue | Documented behaviour | Source |
|---|---|---|
| CME Globex iLink | Messaging Controls are enforced at the **iLink session** level. Messages are "monitored by the number of messages sent over a pre-defined time interval", and "the time interval begins with the first message processed" — a fixed interval that resets, not a rolling average. Above a Reject threshold subsequent messages are rejected; above the larger Terminate threshold the session is terminated. Thresholds differ between Convenience Gateway and MSGW sessions. | [CME Messaging Controls](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457317540/Messaging+Controls) |
| CME Globex iLink — **administrative** messages | A separate counter. An iLink session exceeding an average of **100 administrative MPS over a three-second window** has subsequent administrative messages rejected until the rate falls back. CME **automatically closes the ports** of a session exceeding **200 administrative MPS over a three-second window**, or exceeding **5 invalid Negotiate/Establish messages in 60 seconds**. | [CME Messaging Controls](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457317540/Messaging+Controls) |
| Nasdaq INET Nordic — OUCH/FIX order entry ports | Per-port throttling limits. The limit was reduced from 20,000 msg/s in 2020: notice 23/20 announced 5,000, and notice 29/20 revised the new limit to **10,000** msg/s before it took effect. Separately, warrant and certificate order books carry a **50 updates/second/connection** control, above which messages may be rejected. | [Notice 23/20](https://view.news.eu.nasdaq.com/view?id=b3b2b4e44dd9245f5d087631d4f28e3a1&lang=en), [Notice 29/20](https://view.news.eu.nasdaq.com/view?id=b10ed1ab12326d346423e55f175075733&lang=en), [Notice 26/20](https://view.news.eu.nasdaq.com/view?id=b3ae68d27bea3fb8577f1ac9f24786967&lang=en) |

**Implication.** Two things follow for the counter in `scripts/`. First, the counting
**interval** is a venue parameter, so `window_seconds` must be set from the venue's own
interval — a burst that breaches a 100 ms counter sits well inside a 1 s average. Second,
counters are **per message class**: CME's administrative counter is independent of
application messaging, so a mixed log must be split and audited once per class.

## Sequence numbering and gap recovery, by protocol

| Protocol | Sequence semantics | Gap recovery | Source |
|---|---|---|---|
| FIX session layer (4.2 / 4.4 / FIXT.1.1) | `MsgSeqNum(34)`, per session, first message numbered 1. Retransmitted messages carry `PossDupFlag(43)=Y`. | `ResendRequest(35=2)` for the missing range; `SequenceReset(35=4)` with `GapFillFlag(123)` for administrative fill. | [FIX Session Layer](https://www.fixtrading.org/standards/fixsession/), [ResendRequest](https://fiximate.fixtrading.org/legacy/en/FIX.4.4/body_5150.html) |
| CME iLink 3 (FIXP) | Binary FIXP session layer with per-UUID sequence numbers. | Customer sends a **Retransmit Request** (`UUID`, `FromSeqNo`, `Count`) on detecting an inbound gap. The exchange does **not** send a Retransmit Request for its own inbound gaps — it sends `NotApplied`, and the customer responds with a `Sequence` message carrying the `NextSeqNo` it will resume from. | [CME Session Layer — Transferring](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/714604582/Session+Layer+-+Transferring) |
| Nasdaq SoupBinTCP (carries OUCH) | Sequence numbers are **implicit**: only the Login Accepted packet states one, and both sides count locally from there. The first sequenced message of a session is always 1. | An in-session gap is not observable — TCP delivers in order. Recovery is **reconnect** with a `Requested Sequence Number` in the Login Request. | [SoupBinTCP 3.00](https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/soupbintcp.pdf) |
| Nasdaq MoldUDP64 (carries ITCH) | Each downstream packet header carries a 10-byte session id, an 8-byte sequence number and a 2-byte message count. The sequence number is that of the **first message in the packet**; the rest follow at sn+1, sn+2. `MessageCount = 0` is a heartbeat carrying the next expected sequence number; `0xFFFF` marks End of Session. | Listener detects the gap and sends a Request Packet to a re-request server; the response is a standard Downstream Packet unicast back. | [MoldUDP64 1.00](https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/moldudp64.pdf) |

### Rules this skill enforces

| Rule | Basis |
|---|---|
| A sequence number **below** the expected one, without a possible-duplicate marker, is a protocol violation — not noise to discard. FIX requires `Logout(35=5)` with `SessionStatus(1409)=9` ("received MsgSeqNum too low") and termination of the transport connection, the sole exception being `SequenceReset(35=4)` with `GapFillFlag(123)=N`. | [FIX Session Layer](https://www.fixtrading.org/standards/fixsession/) |
| A sequence **reset** cannot be inferred from the data. It is signalled by the session layer (FIX `Logon(35=A)` with `ResetSeqNumFlag(141)=Y`, a new FIXP UUID, a new SoupBinTCP session id) and must be supplied explicitly via `reset_session_sequence`. | Protocol semantics above; inferring one would silently discard the evidence of a regression. |
| The expected counter is **not** advanced past a gap on detection. The venue still owes those messages and retransmits them under their original numbers. | Retransmission semantics of FIX `ResendRequest`, FIXP Retransmit Request and MoldUDP64 re-request. |
| The inbound batch is processed in **arrival order**, never sorted by sequence number. Sorting lets a later arrival fill a hole that was open when an earlier one landed, so a genuine loss reads as contiguous. | Look-ahead avoidance; FIX processes the stream in arrival order. |
| One retransmit request may not exceed the venue's cap. CME iLink 3 and Drop Copy 4.0 cap a Resend/Retransmit Request at **2500 messages**; a Drop Copy request above it draws a Session Level Reject. A larger gap must be recovered with **several sequential requests**. | [Processing Message Gaps of More than 2500 Messages](https://www.cmegroup.com/tools-information/webhelp/autocert-ilink-3/Content/Processing2500Messages.html), [Drop Copy 4.0](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/665321494) |
| A gap larger than the cap must not be re-requested as one oversized request in a loop. CME's AutoCert+ suite exists specifically to verify a client system "does not enter an infinite resend loop" on a gap above the 2500 limit. | As above |
| Rate limits and sequence numbers are **per session**. Records for another session are excluded, never pooled. | Both CME Messaging Controls and FIX `MsgSeqNum` are defined at session scope. |

### Detector parameters

None of these are set by a regulator or exchange except where noted. They are engineering
defaults to be calibrated against your own session.

| Parameter | Default | Basis | Description |
|---|---|---|---|
| `max_allowed_mps` | 500.0 | **Placeholder** | Not a venue-published figure. Set from your session's contracted limit. |
| `warning_threshold_pct` | 80.0 | Convention | Percentage of the limit at which to warn. Must be in (0, 100]. Chosen to leave headroom for a burst, not derived from any venue rule. |
| `window_seconds` | 1.0 | **Placeholder** | Must match the venue's counting interval — CME's administrative counter uses three seconds, for instance. |
| `max_retransmit_request_count` | 2500 | **Venue-documented (CME)** | Cap on the Count of one Retransmit/Resend Request. Confirm for your venue; it is not universal. |
| `max_buffered_ahead` | 10,000 | Convention | Out-of-order sequence numbers held while a gap is open before the audit escalates to a session resync. A memory bound and a non-convergence signal, not a venue rule. |

Threshold comparisons are **non-strict lower bounds** evaluated on the exact rate: a rate
landing on the limit takes the more conservative branch. This is deliberate — the cost of
one over-cautious slowdown is far below the cost of a terminated session.

The throttle verdict is taken from the **peak** window in the supplied log rather than the
trailing one. This is a fail-safe choice: under-reporting a breach costs the session,
over-reporting costs one slowed batch. It also makes the verdict a pure function of the
data, so a replayed capture or a clock step cannot turn a breach into a healthy verdict.

## Status precedence

`status` collapses two independent verdicts into one label for logging and is lossy by
construction. `is_throttled`, `has_sequence_gap` and `has_sequence_regression` are
independent and must all be honoured.

| Severity | Status | Rationale |
|---|---|---|
| 0 | `MATCHING_ENGINE_NORMAL` / `SEQUENCE_CONTIGUOUS` | Nothing to do. |
| 1 | `THROTTLE_WARNING_SLOW_DOWN` | Advisory; keep trading, reduce rate. |
| 2 | `MESSAGE_SEQUENCE_GAP_DETECTED` | Recoverable inside the session by retransmission. |
| 3 | `EXCHANGE_RATE_LIMIT_THROTTLED` | Outranks a gap: continuing to submit risks termination, which would lose the very link the retransmission arrives on. |
| 4 | `SEQUENCE_REGRESSION_SESSION_UNRECOVERABLE` | The session layer requires logout and termination; local order state may already be wrong. |

## Regulatory context

Jurisdiction: **EU**, and **UK** as assimilated law. Applies to investment firms engaged in
algorithmic trading. It does not universalise to other jurisdictions, and nothing in the
protocol sections above is a regulatory requirement.

| Requirement | Source | Bearing on this skill |
|---|---|---|
| "maximum messages limits, which prevent sending an excessive number of messages to order books pertaining to the submission, modification or cancellation of an order" | Commission Delegated Regulation (EU) 2017/589 (RTS 6), Article 15(1)(d) — pre-trade controls on order entry | **Mandatory.** This is the hard pre-trade counter against a known limit that the outbound half of this skill implements. Article 15 also requires that all orders sent to a venue be included in the pre-trade limit calculation immediately — so the counter must see the whole outbound stream, not a sample. |
| "Real-time alerts shall be generated within five seconds after the relevant event." | RTS 6, Article 16(5) — real-time monitoring | Bounds how long a throttle breach or sequence gap may sit unreported. An audit sweep interval longer than five seconds cannot meet it. |

Source: [EUR-Lex CELEX:32017R0589](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589);
[FCA Handbook, RTS 6 Article 16](https://handbook.fca.org.uk/technical-standards/provision/s119c1039s371p1568).

## Sources

- CME Group Client Systems Wiki — *Messaging Controls*: https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457317540/Messaging+Controls
- CME Group Client Systems Wiki — *Session Layer - Transferring* (Retransmit Request, NotApplied, Sequence): https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/714604582/Session+Layer+-+Transferring
- CME Group AutoCert+ iLink 3 — *Processing Message Gaps of More than 2500 Messages*: https://www.cmegroup.com/tools-information/webhelp/autocert-ilink-3/Content/Processing2500Messages.html
- CME Group Client Systems Wiki — *Drop Copy 4.0 Functional Specification* (2500-message Resend Request limit): https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/665321494
- FIX Trading Community — *FIX Session Layer*: https://www.fixtrading.org/standards/fixsession/
- FIX Trading Community FIXimate — *ResendRequest (35=2)*: https://fiximate.fixtrading.org/legacy/en/FIX.4.4/body_5150.html
- Nasdaq — *SoupBinTCP Version 3.00*: https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/soupbintcp.pdf
- Nasdaq — *MoldUDP64 Protocol Specification V 1.00*: https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/moldudp64.pdf
- Nasdaq — *OUCH 5.0 Order Entry Specification*: https://www.nasdaqtrader.com/content/technicalsupport/specifications/TradingProducts/OUCH5.0.pdf
- Nasdaq INET Nordic — *Changes of throttling limits for FIX and OUCH order entry ports* (23/20): https://view.news.eu.nasdaq.com/view?id=b3b2b4e44dd9245f5d087631d4f28e3a1&lang=en
- Nasdaq INET Nordic — *Update: Changes of throttling limits* (29/20, revising the limit to 10,000 msg/s): https://view.news.eu.nasdaq.com/view?id=b10ed1ab12326d346423e55f175075733&lang=en
- Nasdaq INET Nordic — *OUCH Port: New messaging rate control for Warrants and Certificates order books* (26/20): https://view.news.eu.nasdaq.com/view?id=b3ae68d27bea3fb8577f1ac9f24786967&lang=en
- EUR-Lex — *Commission Delegated Regulation (EU) 2017/589 (RTS 6)*: https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589
