# Standards for Eurex Market Data and Order API

Every value below is traced to a primary source. Where a fact is release-specific
or time-sensitive that is stated — T7 interface layouts change with each release,
and price range tables are exchange parameters that change without a code release.

Primary sources, all published by Eurex Frankfurt AG / Deutsche Börse Group:

| Document | Version used | Date |
|---|---|---|
| T7 Release 14.0 — Functional Reference | Version 3, Final | 30 Oct 2025 |
| T7 Release 14.0 — Enhanced Trading Interface (ETI) Manual | Version 2 | 13 Aug 2025 |
| T7 Release 14.0 — ETI Derivatives Message Reference | Version 2 | 18 Aug 2025 |
| T7 Release 14.0 — Market and Reference Data Interfaces Manual (EMDI/MDI/RDI) | Version 2 | 27 Oct 2025 |
| Eurex Readiness Newsflash — T7 Release 14.1, mandatory new ETI order entry requests | Circular | 2026 |
| Eurex contract specifications — EURO STOXX 50 Index Futures (FESX), Euro-Bund Futures (FGBL) | Product pages | current |
| Eurex Circular 3983656 — FESX price gradation for off-book standardised futures strategies | Circular | eff. 24 Jun 2024 |

T7 Release 14.0 went to production on 10 November 2025.

## Engineering standards

| Standard | Requirement | Source |
|---|---|---|
| Directional reasonability | The Price Reasonability Check MUST be evaluated directionally against the **opposite-side best price**, never symmetrically against the mid. | Functional Reference 6.2.1.1–6.2.1.2 |
| Price range as reference data | The price range MUST be calculated from the instrument's price range table, never from a hard-coded constant. | Functional Reference 6.1.2–6.1.3 |
| Range from the reference | The range MUST be calculated from the reference price, not from the limit price being checked, and MUST NOT be rounded. | Functional Reference 6.1.2, 6.2.1.1 |
| Instrument state | The check applies exclusively in instrument state Continuous. | Functional Reference 6.2.1.1 |
| Minimum price change | Order limits MUST be exact multiples of the contract's on-book minimum price change, checked in exact decimal arithmetic, with non-positive prices rejected explicitly. | Eurex contract specifications; engineering requirement (float modulo admits negative prices) |
| ETI price encoding | Prices MUST be encoded as an 8-byte signed integer with 8 implied decimals; quantities with 4. A value needing more precision MUST be refused, not rounded. | Derivatives Message Reference 3.2 (Data Types) |
| ETI field domains | `Side` (54), `OrdType` (40), `TimeInForce` (59), `TradingCapacity` (1815) and `PriceValidityCheckType` (28710) MUST use their documented derivatives values. | Derivatives Message Reference 6.5 |
| Sequence integrity | `MsgSeqNum` MUST increase by exactly one per request, starting from the Session Logon as 1; a locally rejected order MUST NOT consume one. | ETI Manual 6.6 |
| Session restart | Every reconnection is a new session starting at `MsgSeqNum` 1. There is no sequence recovery. | ETI Manual 6.6 |
| Template currency | Integrations MUST NOT be built on order management requests removed with ETI 14.1. | ETI Manual 4.7.12; T7 R14.1 readiness newsflash |
| Trade finality | Positions MUST be reconciled against Trade Capture Reports (AE), not Execution Reports. | ETI Manual 4.13.1 |

## Price Reasonability Check

### Rejection condition

    Buy  Limit Price > Reference Price + Price Range(Reference Price)
    Sell Limit Price < Reference Price − Price Range(Reference Price)

"The price range is always calculated on the basis of the reference price and not
based on the limit price to be checked" (Functional Reference 6.2.1.1). Performed
exclusively in instrument state Continuous. In Release 14.0 the check is performed
on entry, and a user who wants the limit anyway re-sends the order without it.

For a stop limit order that is not triggered directly on entry or modification,
the reference price is the order's own **stop price** — at entry that is the best
available guess at the market price when it will trigger.

### Reference price determination

**Standard procedure.** The reference price is the best available price on the
side opposite the order: best sell price for a buy, best buy price for a sell. It
applies only when both best prices are available *and* the difference between them
is less than or equal to the price range being applied. Where no best buy price
exists, the instrument's smallest allowed limit price substitutes for it — relevant
for instruments priced near zero, such as out-of-the-money option series. "Best
price" means the best price as published in the market data feed, which for
synthetically traded products may be a synthetic price.

**Non-standard procedure.** Used when the spread condition fails. `TP` is the
alternative reference price: the last trade price or a theoretical price depending
on the product, or failing those the previous day's settlement price. `BBP` is the
best buy price and `BSP` the best sell price.

| BBP | BSP | Condition | Reference for a buy | Reference for a sell |
|---|---|---|---|---|
| Yes | Yes | BBP ≤ TP ≤ BSP | TP | TP |
| Yes | Yes | TP < BBP < BSP | BSP | BBP |
| Yes | Yes | BBP < BSP < TP | BSP | BBP |
| No | Yes | TP ≤ BSP | BSP | TP |
| No | Yes | BSP < TP | BSP | BSP |
| Yes | No | BBP ≤ TP | TP | BBP |
| Yes | No | TP < BBP | BBP | BBP |
| No | No | n/a | TP | TP |

If neither procedure applies, no check is done and `PriceValidityCheckType`
decides the outcome.

### Price range calculation

    Price Range(Reference Price) = APR + |Reference Price| × PPR / 100

`APR` (Absolute Price Range Parameter) and `PPR` (Percent Price Range Parameter)
come from the row of the standard price range table whose interval contains the
reference price. Tables are defined for positive prices; a negative reference
price is matched on its absolute value. Under fast or stressed market conditions:

    Price Range Fast = Price Range × (1 + FastMarketPercentage / 100)

A calculated price range is never rounded but applied with its exact value.

**Where the parameters live.** Standard price range tables per product are
published by the T7 RDI in the product snapshot message (group message
`PriceRangeRules`); the identifier of the table for a specific instrument is in the
instrument snapshot message. `FastMarketPercentage` is an RDI product-snapshot
field, also carried in the Trading Parameters File within the Products and
Instruments Files on the Eurex website. Extended price range tables are in the same
files. There is **no published universal band** for any Eurex product; a fixed "50
index points" figure has no source.

### Related validations, not to be conflated

- **Extended Price Range Validation** — a cruder backstop T7 applies when the Price
  Reasonability Check was *not* performed, referenced to the best price on the
  other side, with its own table (and a separate table for untriggered stop limit
  orders).
- **Market Order Matching Range** — what bounds market orders, which have no limit
  price to check.
- **Maximum Quote Spread Validation** — the buy/sell spread limit for double-sided
  quotes where they are mandatory.

### `PriceValidityCheckType` (tag 28710)

| Value | Meaning |
|---|---|
| 0 | None |
| 1 | Optional — derivatives markets only |
| 2 | Mandatory |

Optional and Mandatory differ only in what happens when neither best prices with a
reasonable spread nor an alternative reference price are available: Optional
accepts the order without a price validation, Mandatory rejects it.

## Contract specifications

| | FESX | FGBL |
|---|---|---|
| Product | EURO STOXX 50 Index Futures | Euro-Bund Futures |
| Quotation | index points | percent of par |
| Contract value | EUR 10 per index point | EUR 100,000 nominal |
| Minimum price change (on-book) | 1 index point = EUR 10 | 0.01 percent = EUR 10 |
| Value of one full point | EUR 10 | EUR 1,000 |
| Settlement | cash | physical delivery |
| Contract months | twelve nearest quarterly months (Mar/Jun/Sep/Dec) | three nearest quarterly months |

The minimum price changes above are the **on-book** figures. Off-book trading in
standardised futures strategies uses a finer gradation: for FESX it moved from
0.25 to 0.01 index points effective 24 June 2024 (Eurex Circular 3983656).

## T7 ETI request header

24 bytes, little endian, identical across the messages in the derivatives message
reference:

| Offset | Field | Tag | Width | Type | Note |
|---|---|---|---|---|---|
| 0 | `BodyLen` | 9 | 4 | unsigned int | number of bytes for the message, **including this field** |
| 4 | `TemplateID` | 28500 | 2 | unsigned int | message layout identifier |
| 6 | `NetworkMsgID` | 25028 | 8 | Fixed String | not used |
| 14 | `Pad2` | 39020 | 2 | Fixed String | not used |
| 16 | `MsgSeqNum` | 34 | 4 | unsigned int | participant request sequence number |
| 20 | `SenderSubID` | 50 | 4 | unsigned int | T7 User ID |

There is no session identifier and no sending timestamp in the request header.

New Order Single or Multi Leg (10138) in Release 14.0 has a 280-byte fixed part
(last fixed field `Pad2_3` at offset 278, width 2), followed by the `LegOrdGrp`
repeating group of 8-byte records, cardinality 0–144. A simple instrument
(`ProductComplex` 1) has no legs, so `BodyLen` is 280.

Selected body fields, for the domains this skill validates:

| Offset | Field | Tag | Width | Type |
|---|---|---|---|---|
| 24 | `ClOrdID` | 11 | 8 | unsigned int |
| 67 | `ApplSeqIndicator` | 28703 | 1 | unsigned int (0 lean, 1 standard) |
| 68 | `OrdType` | 40 | 1 | unsigned int |
| 69 | `PriceValidityCheckType` | 28710 | 1 | unsigned int |
| 70 | `ValueCheckTypeValue` | 25126 | 1 | unsigned int |
| 187 | `FIXClOrdID` | 30011 | 20 | Fixed String |
| 241 | `TradingCapacity` | 1815 | 1 | unsigned int |
| 242 | `ProductComplex` | 1227 | 1 | unsigned int |
| 244 | `MarketSegmentID` | 1300 | 4 | signed int |
| 248 | `SecurityID` | 48 | 8 | signed int |
| 256 | `OrderQty` | 38 | 8 | Qty |
| 264 | `Price` | 44 | 8 | PriceType |
| 272 | `Side` | 54 | 1 | unsigned int |
| 274 | `TimeInForce` | 59 | 1 | unsigned int |

Offsets are Release 14.0. They change between releases — re-derive them from the
message reference for the release you are certified against.

### ETI data types

| Type | Encoding | No-value |
|---|---|---|
| `PriceType` | 8-byte signed integer, 8 implied decimals | `0x8000000000000000` |
| `Qty` | 8-byte signed integer, 4 implied decimals | `0x8000000000000000` |
| unsigned int | little endian, 1/2/4/8 bytes | all bits set |
| Fixed String | fixed-size character array, space padded | `0x00` at first position |

### Field domains (derivatives)

| Field | Tag | Values |
|---|---|---|
| `Side` | 54 | 1 Buy, 2 Sell |
| `OrdType` | 40 | 1 Market, 2 Limit, 3 Stop, 4 Stop Limit |
| `TimeInForce` | 59 | 0 Day (GFD), 1 GTC, 3 IOC, 4 FOK, 6 GTD |
| `TradingCapacity` | 1815 | 1 Customer (Agency), 5 Principal (Proprietary), 6 Market Maker |
| `ProductComplex` | 1227 | 1 Simple instrument; 5 Futures Spread; 7 Standard Future Strategy; others |
| `ExecutingTraderQualifier` | 25124 | 22 Algo, 24 Human/Natural person |

GTC and GTD are available to standard orders only. The `TradingCapacity` domain
above is the derivatives one; the cash market adds 9 Riskless Principal and 10
Retail Customer (Agency).

## Session layer

- ETI follows FIX 5.0 SP2 semantics with modified headers and trailers. Binary
  values are little endian, fields are fixed-length, and repeating groups sit at
  the end of the message. Messages sent *by* the gateway always have a `BodyLen`
  that is a multiple of 8.
- `MsgSeqNum` must increment with each message sent by the participant, starting
  with the Session Logon as 1. Unexpected, gapped or duplicate sequence numbers are
  rejected and the session disconnected. There is no recovery mechanism: every
  connection, including a reconnection, is new and logs on at 1.
- Throughput is bounded by a sliding-window transaction limit
  (`ThrottleNoMsgs` / `ThrottleTimeInterval`, delivered in the Logon response) and
  a reject/disconnect limit (`ThrottleRejectNoMsg`). Required heartbeats do not
  count against the transaction limit.
- Quotes and non-persistent orders are automatically cancelled on session
  disconnect and on duplicate session login, with `MassActionReason` 6 "Session
  Loss" or 7 "Duplicate Session Login".
- There is no automatic session failover. On disconnect the application must open a
  new TCP connection and send a Session Logon.

### Template decommissioning (T7 Release 14.1)

Five generic requests introduced in Release 12.0 replaced the separate simple and
complex order layouts; the old ones were marked deprecated in Release 13.1 and
**removed from production on 18 May 2026** with Release 14.1.

| Removed (single leg) | Removed (multi leg) | Replacement |
|---|---|---|
| 10100 New Order Single | 10113 New Order Multi Leg | 10138 New Order Single or Multi Leg |
| 10125 New Order Single (short) | 10129 New Order Multi Leg (short) | 10139 New Order Single or Multi Leg (short) |
| 10106 Replace Order Single | 10114 Replace Order Multi Leg | 10140 Replace Order Single or Multi Leg |
| 10126 Replace Order Single (short) | 10130 Replace Order Multi Leg (short) | 10141 Replace Order Single or Multi Leg (short) |
| 10109 Cancel Order Single | 10123 Cancel Order Multi Leg | 10142 Cancel Order Single or Multi Leg |

The removed and replacement identifiers are as listed in the T7 Release 14.1
readiness newsflash; the row-by-row pairing follows that listing order and the
request names, and should be re-confirmed against the message reference for the
release being certified. Release 14.1 also decommissioned TLS 1.2 support for ETI
LF and FIX LF gateway connections in production on 27 April 2026.

Orders entered via a short layout may only be modified via a short layout. Short
layouts implicitly set Limit order and, for simple instruments, the product
identifier, and restrict `TimeInForce` to GFD, IOC, FOK (cash only) and GTC.

## T7 market data interfaces

| | T7 EMDI | T7 MDI | T7 EOBI |
|---|---|---|---|
| Netting | un-netted: every book change up to the configured depth | netted over `MarketDepthTimeInterval` | un-netted, order-by-order |
| Aggregation | price level | price level | individual orders and quotes |
| Trades | each on-exchange trade individually | statistics plus last trade in the interval | each trade individually |
| Snapshot / incremental | separate channels (out-of-band), linked by `LastMsgSeqNumProcessed` | same channel (in-band), `RefreshIndicator` flags applicability | — |
| Depth | configured depth | fewer price levels than EMDI | full visible book |

All are UDP multicast. EMDI, MDI and RDI carry FIX 5.0 SP2 semantics in FAST
encoding. Every message is published on two identical services, A and B, with
different multicast addresses (live-live), so the first recovery step for a lost
message is the other service; the snapshot feed is the fallback when both are
missing it. Messages on the EMDI depth incremental feed carry their own
`MsgSeqNum` range per product.

Reference data comes from the T7 RDI (product- and instrument-level snapshots and
incrementals) or the T7 RDF start-of-day and intraday files. Every tradable object
is referenced by a unique numeric identifier, which is why reference data is
mandatory for any trading application.

## Trade finality

The Execution Report (8, U8) communicates order events, but the information it
carries is indicative and must be confirmed by a Trade Capture Report (AE) on the
trade broadcast. For complex instruments a Trade Notification is generated per
instrument leg execution. Reconcile positions and P&L against Trade Capture
Reports; public EMDI trade prints do not carry your order's identity.
