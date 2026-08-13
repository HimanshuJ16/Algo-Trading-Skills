# Standards for ASX Connectivity

## Protocol Tiers

| Protocol | Latency Tier | Primary Use Case | Required Topology |
|---|---|---|---|
| **FIX 5.0 SP2** | Milliseconds | Standard algo execution, Drop Copy, Reporting. | ASX Net or ALC |
| **OUCH** | Microseconds | High-Frequency Trading (HFT) order entry. | ALC Co-Location |
| **ITCH** | Microseconds | Market by Order (MBO) full depth data. | ALC Co-Location |

## ASX Trade Session Schedule (Sydney wall-clock time)

The schedule is published in Sydney local time. The wall-clock boundaries are
identical under **AEST (UTC+10)** and **AEDT (UTC+11)** — daylight saving shifts the
UTC offset, not the local session times. ASX randomises the OSPA start within a
15-second window and the CSPA start within a 30-second window; the values below are
the nominal (non-randomised) boundaries used by `AsxSessionSchedule`. Always treat
the ASX Trade system message as authoritative for the exact transition.

| Phase | Nominal Time (Sydney) | Matching | Order Entry |
|---|---|---|---|
| Pre-open | 07:00 - 09:59 | No | Accepted (queued) |
| Opening Single Price Auction (OSPA) | 09:59 - 09:59:45 | At auction | Restricted |
| Normal Trading | 09:59:45 - 16:00 | Continuous | Accepted |
| Pre-CSPA | 16:00 - 16:10 | No | Accepted |
| Closing Single Price Auction (CSPA) | 16:10 - 16:11 | At auction | Restricted |
| Post Close / Adjust / Closed | 16:11 - 07:00 (next day) | No | Not accepted |

During AEDT, the ETFs OOO, QAG, and QCB open one hour later than the standard
schedule. The bulk of volume is transacted in Normal Trading (09:59:45 - 16:00).

## FIX 5.0 SP2 Session Details (ASX Trade FIX Order Entry)

- **Session lifetime**: restricted to a single ASX Trade trading day. The session
  is NOT ended by connectivity loss or a Logout — only by an ASX Trade system
  restart or an explicit `ResetSeqNumFlag (141=Y)` logon.
- **Sequence numbers (`MsgSeqNum 34`)**: set to 1 at session start; reset only on
  ASX Trade system restart or `ResetSeqNumFlag (141=Y)`. Mandatory on every message.
- **Heartbeat (`HeartBtInt 108`)**: negotiated at Logon. Recommended **30s**;
  maximum supported **60s**; a value below **10s** triggers an immediate Logout.
- **Logon/Logout**: disconnection without an exchange of Logout (5) messages is an
  abnormal condition and must be flagged for recovery.
- **Mandatory Logon fields**: `TargetSubID (57)` and `MsgSeqNum (34)` are validated
  by ASX. `TargetCompID = ASXTRADE`.
- **Session-level message types**: Logon (A), Heartbeat (0), Test Request (1),
  ResendRequest (2), Reject (3), SequenceReset (4), Logout (5).

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

- ASX — Cash market trading hours: https://www.asx.com.au/markets/market-resources/trading-hours-calendar/cash-market-trading-hours
- ASX Trade FIX Order Entry Message Specification (asxonline.com)
- ASIC Market Integrity Rules (Securities Markets) 2017: https://www.legislation.gov.au/current/F2017L01474
- ASIC RG 241 — Electronic Trading

## Category

`global-market-integration`
