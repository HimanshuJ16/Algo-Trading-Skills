# Standards for Deutsche Börse Xetra API Integration

Every value below is traced to a primary source. Where a fact is release-specific
or time-sensitive, that is stated — T7 interface layouts change with each release,
and RTS 11 liquidity bands change annually.

## Engineering standards

| Standard | Requirement | Source |
|---|---|---|
| RTS 11 tick compliance | Order limits MUST be exact multiples of the tick from the RTS 11 Annex for the instrument's **price band and liquidity band**. A price-only rule is not compliant. | RTS 11 Annex; Xetra Circular 024/19 |
| Liquidity band as reference data | The liquidity band (1–6) MUST come from venue reference data, never be inferred from price. | RTS 11 Art. 2; Xetra Circular 024/19 |
| Annual band changeover | New bands apply from the **first Monday of April** following ESMA's publication. Non-compliant resting orders are deleted on Xetra at changeover. | RTS 11 Art. 3(4) as amended by (EU) 2023/960; Xetra Circular 024/19 |
| Decimal price arithmetic | Tick checks MUST use exact decimal arithmetic and MUST reject non-positive prices explicitly. | Engineering requirement (float modulo admits negative prices) |
| ETI price encoding | Prices MUST be encoded as an 8-byte signed integer with 8 implied decimals; a price needing more precision MUST be refused, not rounded. | T7 ETI Cash Message Reference, Data Types |
| ETI field domains | `Side` (54), `TradingCapacity` (1815), `OrderOrigination` (1724) and short-code qualifiers MUST use their documented numeric values. | T7 ETI Cash Message Reference, New Order Single |
| Sequence integrity | `MsgSeqNum` MUST increase by exactly one per request on a session; a locally rejected order MUST NOT consume one. | T7 ETI Cash Message Reference, RequestHeader |
| Template currency | Integrations MUST NOT be built on templates scheduled for decommissioning. | T7 ETI R14.0 Change Log |

## MiFID II RTS 11 — the tick size regime

Commission Delegated Regulation (EU) 2017/588 ("RTS 11") sets the tick size regime
for shares, depositary receipts and ETFs. The Annex is a matrix of **19 price
ranges × 6 liquidity bands**; the liquidity band is set by the average daily number
of transactions (ADNT) in the most relevant market in terms of liquidity.

Liquidity band ADNT boundaries:

| Band | ADNT |
|---|---|
| 1 | 0 ≤ ADNT < 10 |
| 2 | 10 ≤ ADNT < 80 |
| 3 | 80 ≤ ADNT < 600 |
| 4 | 600 ≤ ADNT < 2 000 |
| 5 | 2 000 ≤ ADNT < 9 000 |
| 6 | ADNT ≥ 9 000 |

The Annex table (price range, lower bound inclusive; tick per liquidity band):

| Price range | LB1 | LB2 | LB3 | LB4 | LB5 | LB6 |
|---|---|---|---|---|---|---|
| 0 ≤ p < 0.1 | 0.0005 | 0.0002 | 0.0001 | 0.0001 | 0.0001 | 0.0001 |
| 0.1 ≤ p < 0.2 | 0.001 | 0.0005 | 0.0002 | 0.0001 | 0.0001 | 0.0001 |
| 0.2 ≤ p < 0.5 | 0.002 | 0.001 | 0.0005 | 0.0002 | 0.0001 | 0.0001 |
| 0.5 ≤ p < 1 | 0.005 | 0.002 | 0.001 | 0.0005 | 0.0002 | 0.0001 |
| 1 ≤ p < 2 | 0.01 | 0.005 | 0.002 | 0.001 | 0.0005 | 0.0002 |
| 2 ≤ p < 5 | 0.02 | 0.01 | 0.005 | 0.002 | 0.001 | 0.0005 |
| 5 ≤ p < 10 | 0.05 | 0.02 | 0.01 | 0.005 | 0.002 | 0.001 |
| 10 ≤ p < 20 | 0.1 | 0.05 | 0.02 | 0.01 | 0.005 | 0.002 |
| 20 ≤ p < 50 | 0.2 | 0.1 | 0.05 | 0.02 | 0.01 | 0.005 |
| 50 ≤ p < 100 | 0.5 | 0.2 | 0.1 | 0.05 | 0.02 | 0.01 |
| 100 ≤ p < 200 | 1 | 0.5 | 0.2 | 0.1 | 0.05 | 0.02 |
| 200 ≤ p < 500 | 2 | 1 | 0.5 | 0.2 | 0.1 | 0.05 |
| 500 ≤ p < 1 000 | 5 | 2 | 1 | 0.5 | 0.2 | 0.1 |
| 1 000 ≤ p < 2 000 | 10 | 5 | 2 | 1 | 0.5 | 0.2 |
| 2 000 ≤ p < 5 000 | 20 | 10 | 5 | 2 | 1 | 0.5 |
| 5 000 ≤ p < 10 000 | 50 | 20 | 10 | 5 | 2 | 1 |
| 10 000 ≤ p < 20 000 | 100 | 50 | 20 | 10 | 5 | 2 |
| 20 000 ≤ p < 50 000 | 200 | 100 | 50 | 20 | 10 | 5 |
| 50 000 ≤ p | 500 | 200 | 100 | 50 | 20 | 10 |

**Application date.** Art. 3(4) originally required venues to apply new bands "from
1 April following that publication". Delegated Regulation (EU) 2023/960 replaced
that paragraph: venues now apply them "from the first Monday of April following
that publication", so the changeover falls on a weekend boundary.

**Xetra specifics.** Xetra (XETR) and Börse Frankfurt (XFRA) "will strictly apply
the minimum tick size requirements to orders and quotes in shares and depository
receipts as per the Commission Delegated Regulation (EU) 2017/588 and the
corresponding Annex (RTS 11)". At the annual changeover on Xetra, "orders whose
limits are not compliant with the tick size of the new liquidity band, will be
deleted", with deletion reason "Invalid Limit Price" or "Invalid Stop Limit Price".
Two carve-outs are worth knowing: liquidity band 11 (WM tick size table S) applies
to instruments whose home market is outside the EU and not Switzerland, and ETFs
whose underlyings are exclusively shares themselves subject to the tick size regime
are assigned to liquidity band 6. *(Xetra Circular 024/19, 28 March 2019; the
mechanism recurs annually — check the current year's circular for that year's
bands.)*

## T7 ETI — verified message facts

**Request header (24 bytes, little endian).** Identical in the Release 5.0 and
Release 14.0 Cash Message References:

| Tag | Field | Bytes | Offset | Type |
|---|---|---|---|---|
| 9 | BodyLen | 4 | 0 | unsigned int — "Number of bytes for the message, including this field" |
| 28500 | TemplateID | 2 | 4 | unsigned int |
| 25028 | NetworkMsgID | 8 | 6 | Fixed String, not used |
| 25017 / 39020 | Pad2 | 2 | 14 | Fixed String, not used |
| 34 | MsgSeqNum | 4 | 16 | unsigned int |
| 50 | SenderSubID | 4 | 20 | unsigned int — User ID |

There is **no session identifier and no sending timestamp** in the request header.
`PartyIDSessionID` is a *body* field of the Connection Gateway Request, and inbound
requests carry no clock value.

**Data types.** Integers are little endian. `PriceType` is "Price in integer format
including 8 decimals", an 8-byte signed integer. `Qty` widened from a 4-byte to an
8-byte integer between Release 5.0 and Release 14.0 — a concrete reminder that body
offsets and widths are release-specific.

**Template IDs.**

| Message | TemplateID |
|---|---|
| New Order Single | 10100 |
| New Order Single (short layout) | 10125 |
| New Order Single or Multi Leg | 10138 |
| New Order Single or Multi Leg (short layout) | 10139 |

The R14.0 change log states: "Deutsche Boerse aims to decommission the following
requests with the ETI version 14.1 in mid-2026: New Order Single (10100), New Order
Single (short layout) (10125), Replace Order Single (10106), Replace Order Single
(short layout) (10126)", and names 10138/10139/10140/10141 as the requests that
"can be used instead". T7 Release 14.1 has a published production start of
18 May 2026. *Confirm current status against the release notes for your target
release before relying on any of these templates.*

**Order field domains** (New Order Single / New Order Single or Multi Leg):

| Tag | Field | Values |
|---|---|---|
| 54 | Side | 1 Buy, 2 Sell |
| 40 | OrdType | 1 Market, 2 Limit, 3 Stop, 4 Stop Limit |
| 59 | TimeInForce | 0 Day (GFD), 1 GTC, 3 IOC, 4 FOK, 6 GTD |
| 1815 | TradingCapacity | 1 Customer (Agency), 5 Principal (Proprietary), 6 Market Maker, 9 Riskless Principal, 10 Retail Customer (Agency) |
| 1 | Account | 2-char Fixed String; valid characters `1-9`, `A`, `G`, `M`, `P` — books positions, distinct from TradingCapacity |
| 1724 | OrderOrigination | MiFID field; 5 = order received from a direct access customer |
| 23002 | OrderAttributeLiquidityProvision | MiFID field; 1 liquidity provision, 0 none |
| 25123 | ExecutingTrader | MiFID short code (ESMA Field 5, Section A) |
| 25124 | ExecutingTraderQualifier | 22 Algo, 24 Human/Natural person |
| 21222 | PartyIdInvestmentDecisionMakerQualifier | 22 Algo, 24 Human/Natural person |
| 48 | SecurityID | Instrument identifier (numeric) |
| 1300 | MarketSegmentID | Product identifier |

*TradingCapacity values 9 and 10 appear in Release 14.0 but not in Release 5.0.*

**Error and restatement reasons.** Rejections arrive on the FIX Reject (3) message
with the code in `SessionRejectReason` (373) and text in `VarText` (30355). The
dedicated codes documented in the Release 5.0 reference are 100–104, 211–215 and
10000–10011; **there is no documented code "10013 Invalid Price Step"** — an
earlier version of this skill cited one that does not appear in the reference.
Off-tick limits surface through `ExecRestatementReason` (378): **238 "Invalid limit
price"**, **243 "Invalid stop price"**, matching the deletion reasons named in the
Xetra circular.

## A note on RTS 28

An earlier version of this skill described it as validating "MiFID II RTS 28 order
parameters". That was wrong twice over. RTS 28 (Commission Delegated Regulation
(EU) 2017/576) governs the annual publication of a firm's top five execution venues
and execution quality under Art. 27(6) MiFID II — a reporting obligation, with no
bearing on order message contents. That obligation was removed by the MiFID II /
MiFIR review (Directive (EU) 2024/790 amending MiFID II). The regulation actually
governing tick sizes is RTS 11; the order-record-keeping fields carried as short
codes derive from the MiFID II order record keeping regime (RTS 24).

## Sources

- Commission Delegated Regulation (EU) 2017/588 (RTS 11), incl. Annex —
  https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0588
- Commission Delegated Regulation (EU) 2023/960 (amending RTS 11 Art. 3(4)) —
  https://eur-lex.europa.eu/eli/reg_del/2023/960/oj/eng
- Xetra Circular 024/19, *Tick sizes for shares and depositary receipts — Annual review*, 28 March 2019 —
  https://deutsche-boerse.com/resource/blob/1518504/c32d5bf24f33dbc2bc7cd23c65968e0d/data/024_19e.pdf
- Deutsche Börse Group, *T7 Enhanced Trading Interface — Cash Message Reference*, Release 14.0 (Version 2, ETI 14.0-C0002) —
  https://www.eurex.com/resource/blob/4629434/1a6206d4b26e4d3af6c8bbcc93f46575/data/T7_R.14.0_Enhanced_Trading_Interface_-_Cash_Message_Reference_Version%202.pdf
- Deutsche Börse Group, *T7 Enhanced Trading Interface — Cash Message Reference*, Release 5.0 —
  https://www.cashmarket.deutsche-boerse.com/resource/blob/296638/b2f891cb86e4575db31ba0700574d63f/data/T7-Enhanced-Trading-Interface-Cash-Message-Reference.pdf
- Deutsche Börse Xetra, *T7 Release 14.1* (production start 18 May 2026) —
  https://www.cashmarket.deutsche-boerse.com/cash-en/Data-Tech/Initiatives-Releases/release14-1
- Deutsche Börse Xetra, *MiFID II/MiFIR: New Short Code regime* —
  https://www.xetra.com/xetra-en/technology/t7/publications/Cash-Market-Readiness-Newsflash-MiFID-II-MiFIR-New-Short-Code-regime-starting-3-January-2022-2848142
