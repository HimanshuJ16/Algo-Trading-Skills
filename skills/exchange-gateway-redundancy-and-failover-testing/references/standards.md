# Standards — exchange-gateway-redundancy-and-failover-testing

## Protocol facts (verified against primary/vendor specifications)

| Fact | Source |
|---|---|
| `PossDupFlag(43)`: "Indicates possible retransmission of message **with this sequence number**." Session-layer retransmission — the original `MsgSeqNum` is reused. | [FIX 4.4 dictionary, tag 43](https://www.onixs.biz/fix-dictionary/4.4/tagNum_43.html) |
| `PossResend(97)`: "Indicates that message **may contain information that has been sent under another sequence number**." This is the failover case: the same business content re-sent over a different session. | [FIX 4.4 dictionary, tag 97](https://www.onixs.biz/fix-dictionary/4.4/tagNum_97.html); [FIXimate, FIX.Latest](https://fiximate.fixtrading.org/en/FIX.Latest/msg65.html) |
| Messages retransmitted with `PossDupFlag=Y` carry `OrigSendingTime(122)`, the original transmission time, and are validated against the expected `MsgSeqNum`; a mismatch is a session-level reject. | FIX 4.4 session-level rules, as reproduced in venue specifications (e.g. [Aquis FIX 4.2 Technical Specification](https://aqx-web-prod-s3-public-read.s3.eu-west-2.amazonaws.com/Production_Aquis_FIX_4_2_Technical_Specification_v4_7_9b60959d2e.pdf)) |
| `OrderMassStatusRequest(35=AF)` "requests the status for orders matching criteria specified within the request using `MassStatusReqType(585)`"; responses are `ExecutionReport(35=8)` with `ExecType(150)=I` (Order Status). This is the FIX-native way to learn what the venue actually holds after a session loss. | [FIXimate OrderMassStatusRequest](https://fiximate.fixtrading.org/en/FIX.Latest/msg65.html) |
| Handling of PossDup is dictated by session logic; handling of PossResend is dictated by **business** logic (typically ClOrdID-based duplicate rejection). A PossResend flag is therefore a hint to the counterparty, never a substitute for reconciling first. | [FIX Meisters, "PossDupFlag or PossResend?"](http://fixmeisters.blogspot.com/2006/04/possdupflag-or-possresend-this.html) (secondary; consistent with the tag definitions above) |

## Venue-specific behaviour — Eurex T7 ETI

Source: Deutsche Börse, **T7 Release 13.1 Enhanced Trading Interface (ETI) Manual, Version 1, 14 Feb 2025**
([PDF](https://www.eurex.com/resource/blob/4305946/dcadfeef8842b1a84b0e9afa439802e1/data/T7_R.13.1_Enhanced_Trading_Interface_-_Manual_Version_1.pdf)).

| Fact | Section |
|---|---|
| "ETI does not include any mechanism for automatic failover. Participant applications can implement a failover mechanism of their choice that supports their requirements." | §3.7 |
| On network or gateway failure active sessions are disconnected; there is no automatic session failover. The application must open a TCP/IP connection to any available gateway and send a Session Logon. | §5.4 |
| "quotes and non-persistent orders (both, lean and standard ones) are automatically deleted in such cases" — they are not restated, so they must be re-entered as new orders. | §5.4, §4.7.11 |
| `MsgSeqNum(34)` increments per message "starting with the Session Logon message as sequence number 1"; gaps, duplicates or unexpected numbers are rejected with a sequence error **and the session is disconnected**. | §6.6 |
| "There is no recovery mechanism for message sequence numbers in ETI. All participant connections (including a reconnection after a disconnection) are considered 'new,' and all Session Logon requests are expected to contain the message sequence number 1." | §6.6 |
| "Order status inquiries are not supported by the ETI. Participants must maintain the state of orders based on the Execution Report (8) messages." After a market reset the venue pushes an order book restatement of all active orders, bracketed by Trading Session Event messages. | §4.7.11 |
| Restatement messages are recoverable: the owning session can request a retransmission (by `ApplMsgID(28704)`, per partition) if it was not logged on at the time. | §4.7.11, §6.7.1, §6.10.1 |
| All ETI order/quote responses are **preliminary**; execution information must be confirmed against the legally binding Trade Notification (`TradeCaptureReport(AE)`). An Execution Report with no matching Trade Capture Report must be discarded. | §6.10.2 |
| ETI application messages follow FIX 5.0 SP2 *semantics* with a proprietary binary encoding and user-defined fields — FIX tag-level assumptions do not transfer unchanged. | §4.1 |

## Venue-specific behaviour — CME iLink 3 fault tolerance

Source: CME Group Client Systems Wiki, *Fault Tolerance*
([page](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457671413/Fault+Tolerance)).

- Fault-tolerant clients Negotiate and Establish with the designated **primary** market segment gateway and also Establish with the **backup** — both sessions exist concurrently as a redundant group.
- Inbound and outbound sequence state for a UUID must be kept consistent across the primary and backup processes.
- If the exchange receives nothing from the primary session within **2 × `KeepAliveInterval`**, it designates the primary failed and disconnects **both** sessions.
- Consequence for this skill: where the venue owns the failover mechanism, the client's job is to satisfy it, not to invent an independent promotion policy. The `2 ×` rule is the basis for `DEFAULT_HEARTBEAT_TIMEOUT_MULTIPLIER`.

## Regulatory touchpoints

**EU / UK — mandatory for investment firms engaged in algorithmic trading.**
Commission Delegated Regulation (EU) 2017/589 (**RTS 6**), Article 14 "Business continuity arrangements"
([FCA Handbook, assimilated version](https://handbook.fca.org.uk/technical-standards/provision/s119c1039s371p1566)):

- Art. 14(1): business continuity arrangements "appropriate to the nature, scale and complexity" of the business.
- Art. 14(2)(c): "procedures for relocating the trading system to a back-up site and operating the trading system from that site".
- Art. 14(2)(g): "alternative arrangements for the investment firm to manage outstanding orders and positions" — the in-flight order problem this skill exists to solve.
- Art. 14(3): the trading system must be able to be shut down "without creating disorderly trading conditions".
- Art. 14(4): "review and test its business continuity arrangements on an **annual basis**" — the drill this harness supports.

**US — applies to broker-dealers with market access.**
SEC Rule 17 CFR 240.15c3-5(c)(1)(ii) requires controls reasonably designed to prevent erroneous orders "by rejecting orders that exceed appropriate price or size parameters, on an order-by-order basis or over a short period of time, **or that indicate duplicative orders**". A failover procedure that resends unreconciled in-flight orders manufactures exactly the condition the rule requires the firm to reject. Note the scope: this obligation attaches to the broker-dealer providing market access, not to every trading firm.

**Not applicable by default:** Regulation SCI (17 CFR 242.1000 et seq.) imposes business-continuity and disaster-recovery obligations on *SCI entities* — exchanges, clearing agencies, plan processors and ATSs above the volume thresholds — not on ordinary members or buy-side firms. Likewise RTS 7 (Regulation (EU) 2017/584) Article 15 addresses **trading venues**, not participants. Do not cite either as authority for a member firm's gateway failover design.

## Configuration defaults (calibrate before use — these are not standards)

No regulator or venue publishes a mandatory recovery-time objective for a *member's* order gateway. Any RTO figure in this repository is a configuration choice, and the number that matters is the one your own drills measure end to end.

| Parameter | Default | What it actually does |
|---|---|---|
| `max_heartbeat_delay_ms` | none — **required** | Heartbeat-loss threshold. Derive it from the negotiated `HeartBtInt`/`KeepAliveInterval` with `heartbeat_timeout_from_interval()`. A bare millisecond constant is meaningless without the interval it is measured against. |
| `DEFAULT_HEARTBEAT_TIMEOUT_MULTIPLIER` | `2.0` | From CME's 2 × `KeepAliveInterval` failure designation. 1.5 × `HeartBtInt` is also common in FIX session implementations; follow the venue. |
| `max_latency_rtt_ms` | `None` (disabled) | RTT SLA. Disabled by default: a latency breach leaves the session able to transmit, making it the trigger most likely to cause split-brain, and it always requires a fence. |
| `min_consecutive_latency_breaches` | `3` | Consecutive breaching samples before latency counts as a trigger. A single RTT spike is noise. |
| `venue_profile` | `GENERIC_FIX_PROFILE` | A generic FIX default, **not** a venue specification. Check it against the venue's own manual; Eurex T7 ETI differs on every field. |

## Known limitations

- **No I/O.** The engine decides and records. It cannot observe a socket, log on, cancel, or resend; every health value is caller-supplied and a stale sample produces a confident, wrong decision.
- **`decision_latency_ms` is not an RTO.** It excludes TCP connect, logon, order book restatement and reconciliation — in practice the dominant terms.
- **Fencing is asserted, not verified.** `fence_confirmed=True` is the caller's word. The engine cannot prove the failing session is unable to transmit.
- **Two nodes, one venue, warm standby.** N-way gateway pools, cross-venue rerouting and network-level failover are out of scope, and a standby that has never logged on cannot be pre-flighted from health samples — establish it before auditing.
- **Reconciliation is modelled, not performed.** Verdicts are supplied by the caller from the venue's own responses.

## Category

`Venue Integration & Protocols`
