# Standards — nyse-arca-integrated-feed-handling

## Primary sources

Every claim on this page is transcribed from one of these two documents. Nothing here is
a house convention presented as an exchange rule.

| Short name | Document | Version / date | URL |
|---|---|---|---|
| **IF v2.5** | NYSE Pillar Integrated Feed — Client Specification (covers NYSE, NYSE American, **NYSE Arca**, NYSE Chicago, NYSE National) | 2.5, 16 May 2022 | https://www.nyse.com/publicdocs/nyse/data/NYSE_Pillar_Integrated_Feed_Client_Specification_v2.5.pdf |
| **Common v2.4k** | NYSE Pillar Equities Common Client Specification | 2.4k, 25 July 2024 | https://www.nyse.com/publicdocs/nyse/data/Pillar_Equities_Common_Client_Specification_v2.4k.pdf |

Historical documents cited only to demonstrate that layouts change:

| Short name | Document | Version / date | URL |
|---|---|---|---|
| **Arca v1.16b** | XDP Integrated Feed Client Specification — NYSE Arca Integrated, Pillar Architecture | 1.16b, 28 July 2016 | https://www.nyse.com/publicdocs/nyse/data/NYSE_Arca_XDP_Integrated_Feed_Client_Specification.pdf |
| **IF v2.4a** | Pillar Integrated Feed — Client Specification | 2.4a, 4 March 2022 | https://www.nyse.com/publicdocs/nyse/data/Integrated_Feed_Client_Specification_v2.4a.pdf |

`SPEC_VERSION` in `scripts/nyse_arca_integrated_feed_handling.py` records the pin.
**Re-verify against the revision your venue actually publishes before going live.**

## Wire-format rules (mandatory, exchange-defined)

| Rule | Source | Notes |
|---|---|---|
| All binary fields are **little-endian**. | Common v2.4k §3 | "Binary fields are published in Little-Endian ordering". |
| Fields align on 1-byte boundaries; there are no padding/filler fields. | Common v2.4k §3 | Use `<` in `struct`, never native alignment. |
| Packet header is **16 bytes**: `PktSize`(2) `DeliveryFlag`(1) `NumberMsgs`(1) `SeqNum`(4) `SendTime`(4) `SendTimeNS`(4). | Common v2.4k §2.1.1 | `PktSize` **includes** the 16-byte header. |
| Maximum packet length is **1400 bytes**; a message never straddles a packet boundary. | Common v2.4k §2.1, §3 | No cross-datagram reassembly is needed. |
| Message header is **4 bytes**: `MsgSize`(2) `MsgType`(2). `MsgSize` includes the header. | Common v2.4k §3.1 | |
| Clients **must not hard-code message sizes**; use `MsgSize` to find the next message. | Common v2.4k §3.1.1 | Verbatim: "clients should never hard code msg sizes in feed handlers. Instead, the feed handler should use the Msg Size field to determine where the next message in a packet begins." |
| "The length of a message as actually published may differ from the length of the message structure defined in the client specifications." | Common v2.4k §3 | This is why the decoder reads a prefix and advances by `MsgSize`. |
| Price fields are **signed** binary integers; Pillar Equities does not publish negative prices. | Common v2.4k §3.5 | v2.3c of the Common spec described integers as unsigned by default; v2.4k states prices are signed. Decode as signed. |
| `OrderID` and `TradeID` are 8-byte unsigned little-endian integers, valid for the trading day only. | Common v2.4k §3.6 | On the Integrated Feed, Msg 103's `TradeID` field is 4 bytes (IF v2.5 §5). |
| ASCII string fields are left-aligned and null-padded. | Common v2.4k §3 | Strip trailing `\x00` before comparing. |

## Price scaling — the rule that is most often got wrong

**`price = Numerator / 10^PriceScaleCode`** (Common v2.4k §3.5), where `PriceScaleCode` is
published **per symbol** in the Symbol Index Mapping message (Msg Type 3, offset 24).

There is **no feed-wide divisor**. Common v2.4k §3.5.1 documents live symbols at three
different scale codes:

| PriceScaleCode | Maximum price (per Common v2.4k §3.5.1) | Effective divisor |
|---|---|---|
| 3 | $999,999.999 (NYSE Floor Broker Systems: $999,999.99) | 1,000 |
| 4 | $214,748.364 (NYSE Floor Broker Systems: $9,999.99) | 10,000 |
| 6 | $2,147.48 | 1,000,000 |

The spec's own worked example: "a price of \$27.56 is represented as a published price
field of 2756 and a PriceScaleCode of 2."

The `default_price_scale_code = 4` in this engine is a **fallback chosen by this skill**,
used only when a symbol's code is unknown. NYSE publishes no default. Every use is counted
in `prices_scaled_with_fallback` and logged — a non-zero count means the Msg Type 3 spin
was not ingested and some prices may be wrong by a factor of 10 or 100.

## Timestamps

| Rule | Source |
|---|---|
| Times are UTC nanoseconds since the Unix epoch, carried as two 4-byte fields (seconds, nanoseconds-within-second). | Common v2.4k §3.2 |
| High-volume feeds — **Integrated and BBO** — publish only the nanoseconds portion in each message. The seconds portion arrives in a **Source Time Reference message (Msg Type 2)** once a second, per matching-engine partition. | Common v2.4k §3.2, §4.2 |
| Msg Type 106 (Add Order Refresh) is an exception: it carries a full `SourceTime` seconds field of its own. | IF v2.5 §8 |

A full timestamp for Msg Types 100–104 is therefore unavailable until a Msg Type 2 has
been seen on that channel. The engine reports `source_time_ns = None` rather than
fabricating one.

## Sequence numbers and delivery flags

| Rule | Source |
|---|---|
| Sequence numbers increase monotonically **per channel** and "can be used to detect publication gaps". They are not carried per message: the packet header holds the sequence number of the **first** message plus `NumberMsgs`. | Common v2.4k §3.3 |
| A heartbeat is a packet with `DeliveryFlag = 1` and `NumberMsgs = 0`, and **does not increment the next expected sequence number**. | Common v2.4k §2.2 |
| Many message types also carry a per-symbol `SymbolSeqNum`; a client tracking few symbols may gap-detect on that instead and request a per-symbol refresh. | Common v2.4k §3.4 |

`DeliveryFlag` valid values (Common v2.4k §2.1.1):

| Value | Meaning | Effect on gap detection in this engine |
|---|---|---|
| 1 | Heartbeat | Ignored; does not advance the sequence. |
| 10 | Failover | Numbering restarts at 1; reset expected sequence, no gap logged. |
| 11 | Original Message | The real-time stream — gap detection applies here. |
| 12 | Sequence Number Reset Message | Numbering restarts at 1; no gap logged. |
| 13 | Only one packet in retransmission sequence | Excluded (own numbering). |
| 15 | Part of a retransmission sequence | Excluded. |
| 17 | Only one packet in Refresh sequence | Excluded. |
| 18 | Start of Refresh sequence | Excluded. |
| 19 | Part of a Refresh sequence | Excluded. |
| 20 | End of Refresh sequence | Excluded. |
| 21 | Message Unavailable | Book marked unrecoverable; refresh required. |

## Startup, failover and refresh

At startup or recovery, each channel publishes (Common v2.4k §9.1): multicast priming
(heartbeats or Sequence-Reset messages, sequence set to 1), then a Sequence Number Reset
(Msg Type 1) in its own packet with `DeliveryFlag = 12`, then a full spin of Symbol Index
Mapping (3), Symbol Clear (32) and Security Status (34) per symbol.

During publisher failover all non-heartbeat packet headers carry `DeliveryFlag = 10`,
returning to 11 once every symbol has been refreshed (Common v2.4k §9.2).

**Symbol Clear (Msg Type 32)** is mandatory to act on: "The client should react to receipt
of a Symbol Clear message by clearing all state information for the specified symbol in
anticipation of receiving a full state refresh" (Common v2.4k §4.4).

## Order-book message layouts (IF v2.5)

Offsets are relative to the start of the message, i.e. offset 0 is the `MsgSize` field.

| MsgType | Name | MsgSize | Field layout |
|---|---|---|---|
| 100 | Add Order (§2) | 39 | `SourceTimeNS`@4(4) `SymbolIndex`@8(4) `SymbolSeqNum`@12(4) `OrderID`@16(8) `Price`@24(4,signed) `Volume`@28(4) `Side`@32(1) `FirmID`@33(5) `Reserved1`@38(1) |
| 101 | Modify Order (§3) | 35 | `SourceTimeNS`@4 `SymbolIndex`@8 `SymbolSeqNum`@12 `OrderID`@16(8) `Price`@24 `Volume`@28 `PositionChange`@32(1) `Side`@33(1) `Reserved2`@34(1) |
| 102 | Delete Order (§4) | 25 | `SourceTimeNS`@4 `SymbolIndex`@8 `SymbolSeqNum`@12 `OrderID`@16(8) `Reserved1`@24(1) |
| 103 | Order Execution (§5) | 42 | `SourceTimeNS`@4 `SymbolIndex`@8 `SymbolSeqNum`@12 `OrderID`@16(8) **`TradeID`@24(4)** `Price`@28 `Volume`@32 `PrintableFlag`@36(1) `Reserved1`@37(1) `TradeCond1..4`@38–41 |
| 104 | Replace Order (§6) | 42 | `SourceTimeNS`@4 `SymbolIndex`@8 `SymbolSeqNum`@12 `OrderID`@16(8) `NewOrderID`@24(8) `Price`@32 `Volume`@36 `Side`@40(1) `Reserved2`@41(1) |
| 106 | Add Order Refresh (§8) | 43 | `SourceTime`@4(4) `SourceTimeNS`@8(4) `SymbolIndex`@12 `SymbolSeqNum`@16 `OrderID`@20(8) `Price`@28 `Volume`@32 `Side`@36(1) `FirmID`@37(5) `Reserved1`@42(1) |

Control messages (Common v2.4k):

| MsgType | Name | MsgSize | Field layout |
|---|---|---|---|
| 1 | Sequence Number Reset (§4.1) | 14 | `SourceTime`@4 `SourceTimeNS`@8 `ProductID`@12(1) `ChannelID`@13(1) |
| 2 | Source Time Reference (§4.2) | 16 | `ID`@4(4) `SymbolSeqNum`@8(4, reserved) `SourceTime`@12(4) |
| 3 | Symbol Index Mapping (§4.3) | 44 | `SymbolIndex`@4(4) `Symbol`@8(11, null-terminated ASCII) `Reserved`@19(1) `MarketID`@20(2) `SystemID`@22(1) `ExchangeCode`@23(1) **`PriceScaleCode`@24(1)** … |
| 32 | Symbol Clear (§4.4) | 20 | `SourceTime`@4 `SourceTimeNS`@8 `SymbolIndex`@12(4) `NextSourceSeqNum`@16(4) |

`MarketID = 3` is NYSE Arca Equities; `ExchangeCode = 'P'` is NYSE Arca (Common v2.4k §4.3).

## Book-state semantics defined by the spec

| Behaviour | Source |
|---|---|
| Modify Order price/volume "represent the new values after modification" — absolute, not deltas. | IF v2.5 §3 |
| `PositionChange`: 0 = kept position in book, 1 = lost position. If the price changed, the order **always** loses position. | IF v2.5 §3 |
| Execution: "If the Volume field equals the number of shares previously remaining in the order, then the order has been fully executed and should be removed from the book." | IF v2.5 §5 |
| Execution: "If the Price field is different from the price of the order, any remaining shares keep their original price." | IF v2.5 §5 |
| Replace: "The sitting order must be removed from the book and replaced with the new order", inheriting symbol, side and attribution. | IF v2.5 §6 |
| **A replaced order gets no Delete message** — "If the order is replaced, a delete order message will not be published, rather a Replace Order message." | IF v2.5 §4 |
| Delete is published when an order leaves the book for any reason **except full execution**. | IF v2.5 §4 |
| At session transitions, orders for the current/previous session are explicitly deleted. But when a security closes for the day a Security Status 'X' is sent and unexecuted orders are cancelled **without** explicit Delete messages. | IF v2.5 §4 |
| An `OrderID` may legitimately reappear in a second Add Order if a previously displayed order routed away and returned unexecuted with no residual. | IF v2.5 §2 |
| `PrintableFlag` is 0 for auction trades so auction volume is not double-counted. | IF v2.5 §5 |

## Layout drift — evidence that pinning the version matters

| MsgType | Arca v1.16b (2016) | Pillar IF v2.5 (2022) |
|---|---|---|
| 100 Add Order | MsgSize **31**; `OrderID` **4 bytes** @16; `Price`@20; `Volume`@24; `Side`@28; `OrderIDGTCIndicator`@29; `TradeSession`@30 | MsgSize **39**; `OrderID` **8 bytes** @16; `Price`@24; `Volume`@28; `Side`@32; `FirmID`@33 |
| 103 Execution | MsgSize **34**; `Price`@20; `Volume`@24; `OrderIDGTCIndicator`@28; `ReasonCode`@29; `TradeID`@30 | MsgSize **42**; `TradeID`@24; `Price`@28; `Volume`@32; `PrintableFlag`@36 |

And within Pillar itself: Msg Type 101 kept MsgSize 35 from **IF v2.4a** to **IF v2.5**,
but offset 33 changed from `Reserved 1` to `Side`. **Message length does not identify a
layout.**

## Regulatory position

Nothing on this page is a regulatory requirement. These are exchange technical
specifications governing a proprietary market data product. No SEC, FINRA or Reg NMS rule
prescribes XDP field offsets, price scaling, or gap-detection behaviour.

The adjacent obligations a firm consuming this feed is likely subject to — market data
entitlement and display rules, Reg NMS order protection, and pre-trade risk controls under
SEC Rule 15c3-5 — are out of scope here and are covered by
`market-data-entitlement-and-licensing-per-venue`, `us-reg-nms-order-protection-rule-compliance`
and `sec-rule-15c3-5-risk-controls-us`. Do not cite this document in support of a
compliance claim.
