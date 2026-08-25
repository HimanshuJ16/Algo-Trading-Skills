# Standards — euronext-optiq-market-data-integration

## Primary source

Euronext, **"Euronext Cash and Derivatives — Optiq MDG Messages: Interface Specification"**,
version **6.362.3**, 9 Feb 2026, related SBE template version **362**
([Euronext Connect, IT documentation](https://connect.euronext.com/sites/default/files/it-documentation/optiq-mdg-messages-interface-specification-euronext-cash-and-derivatives-markets-external-v63623.pdf)).

Every wire fact in this skill is cited to a section of that document. Euronext revises it
several times a year; re-verify the sections below against the version your firm is
certified against before relying on them. The SBE template XML — not this document and
not this skill — is authoritative for message *block* layouts.

## Wire format facts (verified against the primary source)

| Fact | Section |
|---|---|
| Market Data Packet Header is **16 bytes**: Packet Time (uint64, ns since 1970-01-01 UTC), Packet Sequence Number (uint32), Packet Flags (uint16), Channel ID (uint16) | 4.2 |
| Packet Flags: bit 0 body compressed; bits 1–3 MDG restart counter; bits 4–6 PSN high-order bits (35-bit effective PSN); bit 7 Start Of Snapshot present; bit 8 End Of Snapshot present; bit 9 Health Status / Start Of Day / End Of Day present | 4.2 |
| Each message is Frame (uint16, total message length including this header) + 8-byte SBE header (Block Length, Template ID, Schema ID, Schema Version, each uint16), then the block, then any repeating sections | 4.3, 6.5 |
| A repeating-section header is 2 bytes: first byte the section length, second byte the occurrence count; both 0..254 | 4.3 |
| Packet body length must equal the sum of the Frame fields, else the packet is corrupted | 4.2 |
| Maximum message length 1384 bytes (1400-byte packet maximum minus the 16-byte header) | 4.3 |
| Compression is LZ4 in block mode with no headers, body only; a compressed channel may still carry uncompressed packets; maximum extracted packet size 8192 bytes | 3.4 |
| Prices: `price = integer / 10^(Price/Index Level Decimals)`, decimals sourced per instrument from Standing Data (1007). Quantity, ratio and amount fields have their own decimal fields; Issue Price and Strike Price have dedicated decimal fields | 5.4 |
| Null values: price `-2^63`; unsigned 64-bit `2^64-1`; unsigned 32-bit `2^32-1`; unsigned 16-bit `2^16-1`; unsigned 8-bit `2^8-1` | 8 |

## Sequencing and recovery

| Fact | Section |
|---|---|
| **Gap detection uses the Packet Sequence Number**, which is per channel and increments by 1 from 1 at each MDG start | 3.6, 4.2, 5.3.1 |
| The Market Data Sequence Number is managed per aggregator; on one channel it increments unevenly, and snapshot messages may share MDSN `0`. The specification states explicitly that gap detection must use the PSN | 5.3.2, 3.7 |
| Line A/B arbitration: a packet lost on one line is recovered from the other; lost on both, resynchronize from the snapshot channel | 3.6 |
| UDP packets can arrive out of order and can be delivered twice; handlers must reorder and de-duplicate | 3.6 |
| MDG failover is identified by the PSN restarting at 1 while Packet Flags bits 1–3 increment (these 3 bits wrap). A book retransmission and a trade retransmission follow, flagged with Rebroadcast Indicator = 1 | 3.7 |
| Trade retransmission is bracketed by Technical Notification (1106) with type 10 (start) and 11 (end); trades previously received inside the retransmission window must be discarded | 3.8 |

## Message templates referenced by this skill

| Template ID | Message | Section |
|---|---|---|
| 1001 | Market Update — aggregated limits, BBO, collars, short trades | 7.3.1 |
| 1002 | Order Update — market-by-order, cash central order book | 7.3.2 |
| 1003 | Price Update | 7.3.4 |
| 1004 | Full Trade Information | 7.3.5 |
| 1005 | Market Status Change | 7.3.9 |
| 1006 | Timetable | 7.2.1 |
| 1007 | Standing Data — source of Price/Index Level Decimals | 7.2.2 |
| 1101 / 1102 / 1103 | Start Of Day / End Of Day / Health Status | 7.1.1 – 7.1.3 |
| 1106 | Technical Notification — retransmission start/end | 7.1.4 |
| 2101 / 2102 | Start Of Snapshot / End Of Snapshot | 7.4.3 / 7.4.4 |

Note: template 1002 is **Order Update**, not a full-book message. Market-by-limit books
are built from 1001; 1002 is the order-by-order feed for cash instruments.

## Book maintenance semantics

| Fact | Section |
|---|---|
| For aggregated-limit books, a limit deletion arrives as an update with **quantity 0** at the price to delete | 6.12 |
| Market Data Update Type 254 (Clear Book) instructs the client to clear the entire book for a Symbol Index; quantity 0 and price null | 7.3.1 |
| When a book side becomes empty, the BBO is sent with quantity 0 and a **null** price | 6.10, 7.3.1 |
| Market and Market-to-Limit orders are published with a null price and the client order's quantity | 7.3.1 |
| **Clients must not build the book from both BBO and depth limits** — doing so can make the book appear crossed | 6.12 |
| A limit may show volume with a Number Of Orders of 0 when it is contributed entirely by implied prices | 6.11 |

## Trading state enumerations

Book State (Market Status Change 1005, uint8, null `255`), spec section 8:

| Value | State | Matching behaviour |
|---|---|---|
| 1 | Inaccessible | No market access |
| 2 | Closed | Available to Market Operations; cancel (cash) / modify and cancel (derivatives) only |
| 3 | Call | Orders collected, **no matching**; BBO and indicative matching price broadcast |
| 4 | Uncrossing | Uncrossing algorithm matches crossed orders |
| 5 | Continuous | Incoming orders matched on arrival |
| 6 | Halted | — |
| 7 | Continuous Uncrossing | Warrants and Certificates only |
| 8 | Suspended | — |
| 9 | Reserved | e.g. price outside dynamic collars |

Order Entry Qualifier (1005 and Timetable 1006, uint8, null `255`), spec section 8:
`0` Order Entry/Cancel/Modify Disabled · `1` Enabled · `2` Cancel and Modify Only
(derivatives only) · `3` Cancel Only.

Status Reason (spec section 8) explains a Book State change — `0` Scheduled, `4` Collars
Breach, `7` Automatic Reopening, `15` Action by Market Operations, `21` Due to Underlying,
among others. Instrument State carries the instrument-level reservation/suspension detail.

Because Book State is enumerated by the venue, the enum values in
`scripts/euronext_optiq_market_data_integration.py` are pinned to the table above; the set
has changed between specification versions (v3.0.0 published a value `10`, Random
Uncrossing Period, that v6.362.3 does not), which is why an unrecognised value raises
rather than being coerced to a default.

## What this skill does *not* assert

- **No latency SLA for reacting to a halt.** Euronext publishes no maximum client reaction
  time for a Market Status Change, and none is implied here. How fast a quoting engine must
  pull quotes is a firm risk-control parameter, set and evidenced by the firm.
- **No claim that quoting must stop during a Call phase.** Order entry is generally enabled
  during Call; whether to quote into an auction is a strategy decision. This library's
  default (`is_quoting_allowed` requires Continuous) is conservative, and the report exposes
  `is_continuous_trading` and `is_order_entry_allowed` separately so a caller can decide.
- **No regulatory requirement is asserted from the protocol.** EU firms engaged in
  algorithmic trading are subject to MiFID II Article 17 and its systems-and-controls RTS,
  and market-making quoting obligations sit in venue market-making agreements under
  RTS 8 (Delegated Regulation (EU) 2017/578). Those obligations are outside the scope of a
  feed handler and were not verified in detail for this skill — confirm applicability with
  compliance rather than inferring it from the market data feed.

## Category

`real-time-architecture` — see top-level `mappings/` directory.
