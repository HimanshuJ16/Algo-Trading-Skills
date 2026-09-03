# Standards for ASX Connectivity

## Protocol Tiers

| Protocol | Latency Tier | Primary Use Case | Delivery / Topology |
|---|---|---|---|
| **FIX 5.0 SP2** | Milliseconds | Standard algo execution, Drop Copy, Reporting. | TCP over ASX Net or an ALC cross connect. |
| **OUCH** | Microseconds | High-Frequency Trading (HFT) order entry. | Enabled by ASX on the customer's ALC cross connect. |
| **ITCH** | Microseconds | Market by Order (MBO) full depth data. | Multicast direct from the ASX Trade platform. |

ASX documents OUCH as being enabled on a customer's existing ALC cross connect, and
ITCH as a multicast feed delivered directly from the ASX Trade platform. Multicast
does not traverse an ordinary remote/internet path, and neither binary protocol is
documented as a remote-delivery option. `AsxIntegrationEngine` therefore treats ALC
co-location as **mandatory** for OUCH and ITCH and raises `ValueError` otherwise.
Read that as this skill's deployment policy grounded in the delivery mechanism, not
as a quoted prohibition from an ASX rulebook — confirm your own entitlement and
delivery path with ASX Customer Technical Services.

## ASX Trade Session Schedule (Sydney wall-clock time)

**As at 2026-09-03**, reflecting ASX **Service Release 15**, effective **23 June
2025**. SR15 removed the staggered alphabetical opening rotation (which had run
since 1987, opening five code-range groups between 10:00 and 10:09) and replaced it
with one exchange-wide Opening Single Price Auction, and it introduced the **Post
Close** trading session. Any ASX document, vendor guide, or blog post predating
23 June 2025 shows the old staggered open and no Post Close phase — do not copy a
schedule from one.

The schedule is published in Sydney local time. The wall-clock boundaries are
identical under **AEST (UTC+10)** and **AEDT (UTC+11)** — daylight saving shifts the
UTC offset, not the local session times. ASX randomises the OSPA start within a
15-second window and the CSPA start within a 30-second window; the values below are
the nominal (non-randomised) boundaries used by `AsxSessionSchedule`. Always treat
the ASX Trade system message as authoritative for the exact transition.

| Phase | Nominal Time (Sydney) | Matching | New Orders | Amend / Cancel |
|---|---|---|---|---|
| Pre-open | 07:00:00 - 09:59:00 | No | Accepted (queued) | Yes |
| Opening Single Price Auction (OSPA) | 09:59:00 - 09:59:45 | At auction | Restricted | Restricted |
| Normal Trading | 09:59:45 - 16:00:00 | Continuous | Accepted | Yes |
| Pre-CSPA | 16:00:00 - 16:10:00 | No | Accepted | Yes |
| Closing Single Price Auction (CSPA) | 16:10:00 - 16:11:00 | At auction | Restricted | Restricted |
| Post Close | 16:11:00 - 16:21:30 | At the CSPA price | Accepted **at the CSPA price only** | Yes |
| Adjust / Adjust ON | 16:21:30 - 18:50:00 | No | **Not accepted** | Yes |
| Purge Orders / System Maintenance / Close | 18:50:00 - 07:00 (next day) | No | Not accepted | No |

ASX describes Post Close as: "Brokers enter new orders and amend existing orders at
the CSPA price. New orders not adhering to the CSPA price will be rejected. ASX
matches orders at the CSPA price." Adjust is described as: "Brokers may 'tidy up'
their orders by cancelling unwanted orders, amending orders, etc. New orders cannot
be entered and ASX Trade does not execute trades." Adjust ON continues the same
permitted activities.

`AsxSessionSchedule` collapses Adjust and Adjust ON into a single `ADJUST` phase
because their order-handling semantics are identical, and collapses Purge Orders,
System Maintenance and Close into `CLOSED`. It models a **normal cash-market trading
day only**: it does not know about non-trading days, trading halts, ASX 24
derivatives hours, or instrument-level suspensions. Gate on the exchange calendar
and instrument state separately.

During AEDT, the ETFs **OOO**, **QAG** and **QCB** move into Open one hour later
than the standard schedule (the issuer documents 11:00-16:00 rather than 10:00-16:00
Australian Eastern Time). `AsxSessionSchedule` does not special-case them.

## FIX 5.0 SP2 Session Details (ASX Trade FIX Order Entry)

Sourced from the *ASX Trade FIX Order Entry Specification*, **Version 1.4.1,
updated January 2026** (FIXT.1.1 session layer, FIX 5.0 SP2 application layer).

- **Session lifetime**: "The FIX session lifetime is restricted to one trading day;
  session lifetime is not ended at connectivity loss or logouts."
- **Sequence numbers (`MsgSeqNum 34`)**: "FIX sequence numbers are reset on ASX Trade
  system restart." Mandatory on every message.
- **Heartbeat (`HeartBtInt 108`)**: negotiated at Logon. "The system allows heartbeat
  intervals greater than 10 seconds. The recommended heartbeat interval is 30 seconds
  and maximum supported is 60 seconds. A heartbeat interval set lower than 10 seconds
  will result in a Logout response." Note the spec words the floor two ways
  ("greater than 10" vs. "lower than 10 … will result in a Logout"), so **exactly 10s
  is ambiguous**; 30s is the only value ASX recommends. Logout carries
  `SessionStatus (1409)` = **101** (Heartbeat Interval too low) or **104** (too high).
- **Logon/Logout**: "Disconnection without the exchange of logout messages should be
  interpreted as an abnormal condition." Separately, if "the username, SenderCompID,
  TargetCompID, IP address or fields in the Header such as Begin String and MsgType
  are invalid, the session is immediately terminated and no Logout message is sent" —
  reconnect logic must not block waiting for a Logout that will never arrive.
- **Session identifiers**: `TargetCompID = ASXTRADE`. `TargetSubID (57)` is sent on
  all messages and carries the environment: **`TESTC`** (CDE+), **`TESTB`** (CDE), or
  **`PROD`**. `SenderCompID` and the IP/port are supplied by ASX; `SenderSubID` is the
  client environment, agreed with ASX. Note there are **two** test environments —
  the engine's boolean `is_cde_environment` does not distinguish CDE from CDE+.
- **`ResetSeqNumFlag (141)`**: "If Tag 141=Y set on Logon, the session will NOT be
  able to retrieve GTC and GTD orders using a ResendRequest." To recover them, log on
  with **141=N** and send a ResendRequest (2) for the missing range; restated orders
  arrive as `ExecType = Restated (150=D)` with
  `ExecRestatementReason = GT renewal/restatement (378=1)`.
- **Cancel on Disconnect**: orders sent with `ExecInst (18)` = `o` are cancelled if
  the FIX session that sent them disconnects. "The Execution reports for the Orders
  that are cancelled due to Cancel on Disconnect, are not automatically sent when the
  session is re-connected" — you must issue a ResendRequest (2) to learn which of your
  orders died.
- **Password complexity**: minimum 8 characters, with at least one alpha, one numeric
  and one special character.
- **Character encoding**: US-ASCII. Extended ASCII in an inbound FIX message is
  rejected as an invalid Checksum.
- **Session-level message types**: Logon (A), Heartbeat (0), Test Request (1),
  ResendRequest (2), Reject (3), SequenceReset (4), Logout (5).

## Inbound Sequence-Number Recovery (FIX session layer)

The three anomalies below need different actions; conflating them corrupts the
session. `AsxSequenceTracker.classify_inbound` returns which case applies.

| Condition | FIX-correct action |
|---|---|
| `MsgSeqNum` == expected | Process normally. |
| `MsgSeqNum` > expected | Gap. Issue **ResendRequest (2)** for the missing range before resuming order traffic. |
| `MsgSeqNum` < expected **with** `PossDupFlag (43) = Y` | Legitimate retransmission. Discard if already processed — not a session error. |
| `MsgSeqNum` < expected **without** `PossDupFlag = Y` | Unrecoverable. Send **Logout (5)** with `SessionStatus (1409) = 9` and terminate the connection. **Not** a ResendRequest. |

## Centre Point (Anonymous Matching)

ASX Centre Point is ASX's anonymous matching platform. Order-entry fields of note:
- `MinQty (110)` — required for Centre Point and Sweep Block Orders.
- `PegPriceType (1094)` — required for Best limit orders and Centre Point Limit orders.
- Block trades have a minimum threshold (e.g. $200,000 for Tier 3 Equity Market
  Products); misconfiguration here previously led to an ASIC infringement notice
  (Rule 6.1.2 pre-trade transparency). Validate the threshold per product.

## Regulatory References

- **ASIC Market Integrity Rules (Securities Markets) 2017** (F2017L01474) — the
  governing ruleset for market operators and trading participants.
- **Rule 6.1.2** — Pre-Trade Information must be made available continuously and in
  real time, with limited exceptions (block trades, large portfolio trades, price
  improvement trades).
- **Part 5.6 — Automated Order Processing (AOP)**: trading participants must
  maintain automated filters and retain direct control over filter parameters.
- **ASIC RG 241 (Electronic Trading)** — guidance on AOP compliance, automated
  filters, trading management arrangements, and unauthorised-access prevention.
- **ASX Operating Rules, Appendix 4013** — authoritative trading-hours reference.

## Sources

- ASX — Cash market trading hours (phase table, verified 2026-09-03):
  https://www.asx.com.au/markets/market-resources/trading-hours-calendar/cash-market-trading-hours
- ASX — Changes to equity market structure: opening and closing the market
  (SR15, effective 23 June 2025):
  https://www.asx.com.au/blog/listed-at-asx/changes-to-equity-market-structure
- ASX — Removal of Staggered Opening Rotation (April 2024 consultation):
  https://www.asx.com.au/content/dam/asx/markets/trade-our-cash-market/equity-markets-working-groups/asx-removal-of-staggered-opening-rotation-april-2024.pdf
- ASX Trade FIX Order Entry Specification, Version 1.4.1, January 2026:
  https://asxonline.com/content/dam/asxonline/public/documents/asx-trade-refresh-manuals/asx-trade-fix-order-entry-specification.pdf
- ASX Trade ITCH Message Specification (Market by Order full depth, multicast):
  https://www.asxonline.com/content/dam/asxonline/public/documents/asx-trade-refresh-manuals/asx-trade-itch-message-specification.pdf
- ASX Trade OUCH Message Specification:
  https://www.asxonline.com/content/dam/asxonline/public/documents/asx-trade-refresh-manuals/asx-trade-ouch-message-specification.pdf
- ASX — Australian Liquidity Centre: https://www.asx.com.au/alc
- FIX Trading Community — FIX Session Layer (MsgSeqNum too low handling):
  https://www.fixtrading.org/standards/fix-session-layer-online/
- ASIC Market Integrity Rules (Securities Markets) 2017:
  https://www.legislation.gov.au/current/F2017L01474
- ASIC RG 241 — Electronic Trading
- BetaShares — A Guide to Trading BetaShares Commodity ETFs (OOO/QAG/QCB AEDT hours):
  https://www.betashares.com.au/wp-content/uploads/2016/12/A-Guide-to-Trading-BetaShares-Commodity-ETFs.pdf

## Category

`global-market-integration`
