# Standards for Moscow Exchange (MOEX) Integration

All facts below were verified on **2026-08-26** against the primary sources
cited. Sanctions designations, listed instruments, lot sizes, price steps and
price limits all change; re-derive them rather than trusting this copy.

## Engineering standards

| Requirement | Standard |
|---|---|
| Sanctions gate | An order path into MOEX MUST NOT be built until a dated screening result, naming the regimes screened against, has been attached. The gate MUST be evaluated before every other check, on every order path, so that no later rejection can mask it. Absence, revocation or expiry of an attestation MUST fail closed, with no message fields emitted. |
| Quantity units | FIX Tag 38 `OrderQty` MUST carry **lots**, not shares or units. Lot size MUST come from reference data for the exact Symbol + Board pair. A quantity that is not a whole number of lots MUST raise, not round. |
| Price step | A price MUST be an exact multiple of the instrument's `MINSTEP`. Positivity MUST be checked separately: a negative price is an exact multiple of any step. An off-step price SHOULD be rejected rather than silently moved; where it is aligned, alignment MUST be away from the market (BUY down, SELL up). |
| Price control | A limit order MUST NOT be dispatched with no price control at all. Where the board publishes `LOWLIMIT`/`HIGHLIMIT`, those absolute bounds govern. A percentage band is the caller's own risk policy and MUST be labelled as such, never as a MOEX rule. |
| Price format | Tag 44 MUST be rendered at the instrument's `DECIMALS` and MUST NOT exceed 10 characters including the decimal point. |
| Board identification | The board MUST be carried in Tag 336 `TradingSessionID` inside the Tag 386 `NoTradingSessions` group, which MUST contain exactly one element with 386 immediately followed by 336. There is no `BoardID` field. |
| Venue identification | `MISX` is the ISO 10383 MIC for reference data and reporting. It MUST NOT be sent as `SecurityExchange` (Tag 207) — neither appears in the MOEX FIX 4.4 interface specification. |
| Client order ID | `ClOrdID` MUST be caller-generated, unique per order, at most 20 characters, and MUST NOT begin with `#`. It MUST NOT be derived from the order's own field values. |
| Interface scope | An ASTS MFIX `NewOrderSingle` MUST NOT be built for a SPECTRA board. |
| Field encoding | No emitted string field may contain a FIX delimiter (SOH or `=`); such a value would split the message into fields the caller never wrote. |
| Arithmetic | Price and step comparisons MUST use `Decimal`. Floats MUST be routed through `str` on conversion. |
| Determinism | Screening staleness MUST be evaluated against a caller-supplied date, not a clock read inside the module. |

## Sanctions status — primary source

**OFAC Specially Designated Nationals and Blocked Persons List**. Search it at
<https://sanctionssearch.ofac.treas.gov/>; the machine-readable list is
published under <https://sanctionslistservice.ofac.treas.gov/>. Record-level
identifiers are deliberately not reproduced here — they go stale, nothing in
`scripts/` consumes them, and a copied identifier invites treating this file as
a screening result. It is not one: re-derive the position from the list itself.

Three entities central to a MOEX order path are designated:

| Entity | Role in a MOEX order path |
|---|---|
| Moscow Exchange | the venue |
| National Clearing Center | MOEX's central counterparty — the clearing leg |
| National Settlement Depository | the central securities depository — settlement |

The designations were made on **12 June 2024** under E.O. 14024, and the entries
carry a secondary-sanctions risk remark referencing Section 11 of that order,
alternatively the Ukraine-/Russia-Related Sanctions Regulations at 31 CFR
589.201 and/or 589.209. OFAC issued General License 99 (wind-down of
transactions involving MOEX, NCC or NSD) and General License 100 (divestment /
currency conversion) at designation; as amended, both authorised activity only
through **12:01 a.m. EDT on 13 August 2024** and have expired.

Because the central counterparty is itself designated, the clearing leg of an
exchange trade is inside the block rather than adjacent to it. That is why the
gate in `scripts/` sits in front of message construction and not beside it.

MOEX suspended trading in instruments settling in US dollars and euros from
**13 June 2024**, the day after designation.

Other jurisdictions maintain separate measures on Russian financial
infrastructure — the EU blocked NSD in June 2022, and the UK imposed an asset
freeze on NSD in June 2024 — on their own timelines and with their own scope.
Screen against the lists that bind your entity; the position above is the OFAC
one only and is not a multi-jurisdiction clearance.

## MOEX order entry — primary source

**Moscow Exchange public FIX 4.4 interface specification (MFIX Transactional)**,
<https://ftp.moex.com/pub/FIX/ASTS/docs/backup/public_fix44_interface_in_eng_v4_6_1.pdf>.
Retrieved 2026-08-26.

Scope, stated in section 1:

> "This Interface specification is valid for Moscow Exchange FX and Securities
> (Main and T+2) markets only. For other markets, please refer to the
> www.moex.com web site."

This is why the engine refuses to build an ASTS message for `RFUD`.

`New Order - Single (MsgType = 'D')` fields this module emits, with the
specification's own wording:

| Tag | Field | Req'd | Type | Specification text |
|---|---|---|---|---|
| 11 | ClOrdID | Y | String(20) | Caller-assigned unique identifier for **this order**. (The specification's "Unique ID of cancel request as assigned by the institution" wording belongs to the cancel and cancel/replace messages, not to 35=D; do not read it as licence to reuse a cancel's identifier here.) The leading-`#` restriction is stated on those cancel messages: 35=F and 35=G "will be rejected via the Reject message (35=3) if the ClOrdID field (11) of these messages starts with a hash (pound) symbol '#'. You can use '#' symbol in any position of this string, except the first one." An order whose ClOrdID starts with `#` is therefore uncancellable by client order ID. |
| 386 | NoTradingSessions | Y\* | NumInGroup | "1 (one element) … TradingSessionIDs group should contain only one element of group. Note: tags 386 and 336 compose a group and should be placed exactly in the order 386, then 336, and not separated by other tags." |
| 336 | TradingSessionID | Y\* | String(4) | "Identifier for Trading Session which contains MOEX security board (SECBOARD)." |
| 55 | Symbol | Y | String(12) | "Ticker symbol. The MOEX internal instrument identifier, SecCode". The `<Instrument>` note adds: "FIX gateway checks that tags 336 and 55 combination points to existing security. If there is no match, the order is rejected with 'Unknown Security' error message." |
| 1 | Account | Y\* | String(12) | "Account mnemonic as agreed between buy and sell sides … Is used to represent trading account." |
| 453/448/447/452 | Parties | N | — | "PartyID (448) = \<client code\>, PartyIDSource (447) = 'D', PartyRole (452) = '3' – specifies client". "It's recommended to define client code only for broker's client accounts". |
| 54 | Side | Y | char | "'1' (Buy); '2' (Sell)." |
| 60 | TransactTime | Y | UTCTimestamp | "Required by FIX protocol but not processed at MOEX." |
| 38 | OrderQty | Y\* | Qty(10) | "Quantity ordered, expressed in number of lots. **Lot size is different for different Symbol + Board combinations and should be determined from the marketdata feeds.**" The `<OrderQtyData>` block repeats: "Always expressed in lots of security. Please make sure that you get correct lot size values from marketdata streams." |
| 40 | OrdType | Y | char | "'1' (Market); '2' (Limit); 'W' (Weighted-average price) (Used only for Securities market)". |
| 44 | Price | C | Price(9) | "Required for limit OrdTypes. **Must be zero for market orders at MOEX.** … Maximum allowed length of Price field is 10 characters, including decimal point. **Orders with price that does not fit in minimal price steps levels will be rejected.**" |
| 59 | TimeInForce | N | char | "'0' (Day); '3' (Immediate or Cancel); '4' (Fill or Kill)". Absence "is interpreted as DAY". |

**Tag 207 `SecurityExchange` and the string `MISX` do not appear anywhere in
this specification.** The `<Instrument>` block is Symbol (55), Product (460),
CFICode (461) and SecurityType (167) only. The specification states that fields
"absent in MOEX Interface specification are optional and will be ignored by
MOEX", so sending `207=MISX` is at best inert and at worst misleading in an
audit trail that implies it is an exchange requirement.

MFIX Transactional comprises MFIX Trade, FIFO MFIX Trade (which "delivers
incoming trading messages to matching engine strictly in the order as they are
received over network"), MFIX Trade Capture and MFIX Drop Copy, on separate
servers with different access levels.

## Interfaces — which protocol serves which market

| Interface | Markets | Encoding | Source |
|---|---|---|---|
| MFIX Transactional (FIX 4.4) | FX and Securities (Main and T+2) | FIX tag/value | Specification section 1, above |
| TWIME SPECTRA | Derivatives market only | FIX Simple Binary Encoding over FIXP | <https://www.moex.com/msn/en-twime> |
| TWIME ASTS | Equity & Bond Market and FX Market, from 18 March 2024 | binary | <https://www.moex.com/n67575> |
| ASTS Bridge / Plaza II | native APIs | proprietary | <https://www.moex.com/a7939> |

MOEX describes TWIME SPECTRA as "ultra-low latency access to Derivatives market
… designed as the fastest transactional interface to SPECTRA trading system",
and notes "The service supports only trading functionality: order entry/change/
withdrawal, obtaining results of message processing and execution reports" —
market data comes from SIMBA or FAST separately. The March 2024 announcement
launches TWIME ASTS "for the Equity & Bond Market and FX Market", citing
"unification of the protocol with the Derivatives market". They are separate
services, not one protocol.

## Boards and reference data — MOEX ISS

Board registry verified against <https://iss.moex.com/iss/index.json>
(2026-08-26):

| Board | Engine | Market | Board ID | Title |
|---|---|---|---|---|
| `TQBR` | `stock` | `shares` | 129 | "Т+: Акции и ДР - безадрес." (T+ shares and depositary receipts, order-driven) |
| `CETS` | `currency` | `selt` | 21 | "Системные сделки - безадрес." (system trades, order-driven) |
| `RFUD` | `futures` | `forts` | 101 | "Фьючерсы" (futures) |

### Lot size, price step and decimals are per instrument

From `https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json`
(2026-08-26), 506 securities. `LOTSIZE` distribution: **1** (331 securities),
**10** (80), **100** (45), **1,000** (27), **10,000** (19), **100,000** (3),
**1,000,000** (1). The modal value covers about two thirds of the board, so a
default of 1 would silently misprice the other third. Worked examples used in
`scripts/`:

| SECID | LOTSIZE | MINSTEP | DECIMALS | CURRENCYID |
|---|---|---|---|---|
| SBER | 1 | 0.01 | 2 | SUR |
| GAZP | 10 | 0.01 | 2 | SUR |
| ROSN | 1 | 0.05 | 2 | SUR |
| VTBR | 10,000 | 0.005 | 3 | SUR |
| LKOH | 1 | 0.5 | 1 | SUR |
| MGNT | 1 | 0.5 | 1 | SUR |

Note `CURRENCYID` is **`SUR`**, not `RUB`. There is no universal MOEX lot size
and no universal MOEX tick, which is why `MOEXInstrument` supplies no defaults.

From the `CETS` board on the same date: `CNYRUB_TOM` lot 1,000, step 0.0005,
decimals 5; `CNYRUB_TMS` lot 1, step 0.0001, decimals 6; `CNYRUBTODTOM` lot
100,000, step 0.00001, decimals 5.

### Price limits are absolute and per instrument, not a percentage

The equity and FX securities blocks carry **no** price-limit columns. The FORTS
board does, via `LOWLIMIT` and `HIGHLIMIT` on
`https://iss.moex.com/iss/engines/futures/markets/forts/boards/RFUD/securities.json`.
Observed 2026-08-26:

| SECID | PREVSETTLEPRICE | LOWLIMIT | HIGHLIMIT | Implied band |
|---|---|---|---|---|
| `92Q6` (AI92-8.26) | 73,960 | 70,240 | 77,680 | ±5.03% |
| *(illustrative — not a real quote)* contract B | 1,000 | 890 | 1,110 | ±11% |
| *(illustrative — not a real quote)* contract C | 1,000 | 950 | 1,050 | ±5% |

The first row is the observed instrument the tests use; the two illustrative
rows stand for what was seen alongside it — contracts on the same board on the
same day whose implied bands differed by more than a factor of two. **No fixed
percentage reproduces the exchange's bounds**, which is why any percentage band
in this skill is labelled a client-side policy and the published bounds are
consumed as absolute numbers. Re-read the live values rather than reusing any
row above.

### A listed instrument is not necessarily a trading instrument

From the `CETS` board, 2026-08-26. Every row below carries `STATUS = 'A'` and a
current `PREVDATE`:

| SECID | NUMTRADES that session | LAST |
|---|---|---|
| `CNYRUB_TOM` | 94,102 | 12.49 |
| `CNY000000TOD` | 10,716 | 12.453 |
| `USD000UTSTOM` | 58 | 84.15 |
| `USD000000TOD` | 0 | null |
| `EUR_RUB__TOM` | 0 | null |

`USD000000TOD` is still listed and carries a stale price with no trading
activity, consistent with the June 2024 suspension of USD and EUR settlement
instruments. Screen on activity, not on listing status.

## ISO 10383 MIC

From the ISO 20022 MIC list, <https://www.iso20022.org/market-identifier-codes>,
downloaded 2026-08-26:

| MIC | Type | Operating MIC | Description | Status |
|---|---|---|---|---|
| `MISX` | OPRT | `MISX` | MOSCOW EXCHANGE - ALL MARKETS | ACTIVE |
| `RTSX` | SGMT | `MISX` | MOSCOW EXCHANGE - DERIVATIVES MARKET | ACTIVE |

`MISX` is the operating MIC; `RTSX` is its only listed segment. Reporting
regimes that require a segment MIC for derivatives need `RTSX`, not `MISX`.
Neither is a MOEX FIX order-entry field.

## Not verified

- **ISS rate limits and authentication.** The ISS reference at
  <https://iss.moex.com/iss/reference/> documents endpoints but publishes no
  rate limit and no authentication requirement for the public data used here.
  This skill therefore states none. Do not assume the absence of a documented
  limit means the absence of a limit.
- **Securities-market price bands.** MOEX operates price fluctuation limits on
  the Equity & Bond Market under its Trading Rules, but they are not exposed as
  ISS securities-block columns the way the FORTS limits are. This skill does not
  assert a value for them.
- **The full multi-jurisdiction sanctions picture.** Only the OFAC position was
  verified against a primary source.
