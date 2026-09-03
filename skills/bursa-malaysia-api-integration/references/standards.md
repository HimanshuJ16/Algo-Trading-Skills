# Protocol & Symbology Standards — bursa-malaysia-api-integration

Everything below is taken from Bursa Malaysia's own published BTS2 documentation, cited
per section. Where a value is *not* documented in those sources it is marked as such
rather than guessed — the failure mode of an invented wire value is a rejected order or,
worse, an accepted one that means something else.

**Currency of these documents.** The Order Management specification cited here is
v1.15 (10 October 2019), the newest published at the time of writing; the Market Data
specification has moved on to v1.19 (2024), so check Bursa's Documents and Guides page
for a newer Order Management revision before relying on any enumeration below. Bursa
also lists a **Bursa Trade Securities 3 (BTS3)** programme — treat BTS2 as the current
but not permanent platform.

## 1. Session layer

| Item | Value | Note |
|---|---|---|
| BeginString (8) | `FIXT.1.1` | The **transport** version. Not `FIX.5.0SP1`. |
| DefaultApplVerID (1137) | `8` = FIX50SP1 | Required on Logon; the only valid value. |
| ApplVerID (1128) | `8` | Optional, message-level equivalent. |
| EncryptMethod (98) | Always unencrypted | BTS2 FIX supports neither password nor message encryption; use hardware encryption if required. |
| HeartBtInt (108) | Accepted range **10–60** | Out-of-range values are **not rejected**: the server replies with the last valid value, or 60 on the first logon of the day. |
| Username (553) | Required, max 30 chars | Also becomes the operator identifier for the session. |
| Password (554) | Required, max 12 chars (plain text) | Rotate via `NewPassword(925)` at logon, or manually. |
| SenderCompID (49) | Required, max 30 chars | Identifies the initiating firm. |
| TargetCompID (56) | Required | The CompID Bursa assigns. Bursa's published certification log shows `56=XSTRMO`. |
| SenderSubID (50) | Optional | Present when the session operator acts "on behalf of" another user; echoed back as TargetSubID(57). |

**Logon failures lock the account.** "If the session initiator fails to authenticate
with the BTS2 system within a defined number of attempts [default is 3 times], the
account will be locked and all subsequent logon attempts will be rejected. To unlock the
account requires marketplace operations to reset the account and assign a new password."

**Sessions map one-to-one to X-stream.** "FIX sessions are mapped directly one-to-one to
BTS2 native TCP/IP sessions", and a user may not be logged on via both the FIX and the
native protocol at the same time.

Source: [BTS2 FIX Specification — Order Management v1.15](https://www.bursamalaysia.com/sites/5d809dcf39fba22790cad230/assets/5dc0f2975b711a16da1e86ef/BTS2-FIX-Specification-Order-Management-v1-15.pdf),
sections 1–2 and Appendix A.

## 2. FIX connection types and broker codes

`FIXTRADER` and `FIXNEGDEAL` are **connection types**, not TargetCompID values. Each is
issued with broker codes in a distinct format.

Broker Code (6 digits) = Firm Code (3 digits) + Branch Code (3 digits). The **4th digit**
— the first of the branch code — declares what the order is:

| Connection type | Boards | Branch digit | Example |
|---|---|---|---|
| `FIXTRADER` | Normal, Odd-Lot, Buy-In | `9` (ordinary orders) | `068901`, `033902` |
| `FIXTRADER` | Normal (market-maker orders) | `1` | `068101`, `033102` |
| `FIXNEGDEAL` | Direct Business Transactions, Off-Market | `2` | `068201`, `033202` |

"When negotiating DBT with counterparty, ensure Broker Codes are provided in the right
format by both parties of the trade."

**These formats are Production-only.** "The broker code formats provided in the previous
slides are ONLY APPLICABLE to BTS2 Production platform. These format are not applicable
in the BTS2 Certification (UAT) platform. However, in the BTS2 Certification platform,
FIXTRADER and FIXNEGDEAL connections are issued along with respective broker codes."
This is why `BursaConfig` scopes the branch-digit rule to `Environment.PRODUCTION`.

Source: [BTS2 Technical Guide #4 — Production FIX Connections & Broker Codes v1.1](https://www.bursamalaysia.com/sites/5bb54be15f36ca0af339077a/assets/5bc04a425f36ca5cab8bf75a/BTS2_Technical_Guide_4-FIX_Connection_Broker_Code_v1.1.pdf).

## 3. Symbology

BTS2 does **not** identify instruments by name ticker.

| Tag | Field | Value |
|---|---|---|
| 48 | SecurityID | Marketplace-assigned order-book identifier, e.g. `"1818"`, `"1818WA"`, `"1082"` |
| 22 | SecurityIDSource | `99` — Marketplace assigned identifier (the only valid value) |
| 762 | SecuritySubType | Board: `NM` Normal, `OD` Odd-lot, `BI` Buy-In. Equivalent to MarketSegmentID(1300). |
| 1 | Account | The **9-digit CDS account**, left-padded with `0`, e.g. `"000181818"` |

**No SecuritySubType value for Direct Business Transactions is documented** in the Order
Management specification, whose enumeration for tag 762 is limited to `NM`/`OD`/`BI`.
BTS2 handles privately negotiated trades through Trade Capture Reporting (MsgType=AE,
`MatchType` 1/2 "privately negotiated trade", `TrdType` 22), which this skill does not
model. The engine therefore refuses order entry on a FIXNEGDEAL connection rather than
inventing a board code.

## 4. Order entry enumerations

**OrdType (40)** — `1` Market · `2` Limit · `3` Stop/Stop Loss · `4` Stop Limit ·
`Z` Market at Best (Bursa-defined).

Price rules follow from these definitions: Market "The Price (44) field is not used";
Stop "The Price (44) field is not specified, but the TriggerPrice (1102) is"; Stop Limit
"Specifies both the Price (44) and the TriggerPrice (1102) field."

**Side (54)** — `1` Buy · `2` Sell · `5` Regulated Short Sell (RSS) · `6` Proprietary Day
Trading (PDT) · `I` Intraday Short Sell (IDSS) · `V` Permitted Short Sell (PSS).

The short-sell values are distinct order-entry declarations, not annotations. Bursa also
publishes instrument-level attributes bearing on them — a Short Sell Indicator and a
"Maximum RSS traded percentage authorized for the trading day" — so an instrument's
eligibility and remaining capacity are reference-data questions, not client-side ones.

**TimeInForce (59)** — `0` Day · `1` GTC · `2` At the Opening (Session) · `3` IOC ·
`4` FoK · `6` Good till Date · `7` At the Close (Session). Absence of the field means
Day. Value `S` (Session) is **outbound only** — the marketplace sets it in response to
`59=2` or `59=7` and it is "not allow for inbond".

**OrderCapacity (528)** — `A` Agency · `P` Principal · `M` Market Maker ·
`R` Riskless Principal.

**OrderRestrictions (529)** — **required**, max 5 characters, multiple values separated
by a space: `9` ASEAN Link · `E` Algorithmic · `I` Internet · `M` DMA · `R` Broker
Assisted (the last three are Bursa extensions). `E` is what declares an order
algorithm-generated.

**ExecInst (18)** — only `G` (All or None) is allowed on order entry, and "whenever
using this value to specify an All or None order, the Minimum Quantity field must be
equal to the total quantity". `o` (Withdraw on log off) also appears in the
enumerations. This skill does not model AoN; if you send it, carry MinQty(110) yourself.

## 5. Order identification and lifecycle

**ClOrdID (11) is String(20), and BTS2 does not police it.** "BTS2 will not check for
uniqueness of ClOrdId(11) on New Order Single, Order Cancel/Replace Request and Order
Cancel Request messages. Firms submitting order transactions via FIX interface must
ensure unique ClOrdId(11) is entered on these transactions. When an action (order
modification or order cancellation) is requested on a ClOrderId that happens to be
duplicated, only the last order identified by ClOrderId is affected."

Orders are identified either by ClOrdID via `OrigClOrdID(41)`, or by BTS2's own
`OrderID(37)` (String(18)) with `OrigClOrdID` set to `"NONE"`. **OrderID can be
renumbered after an order amendment**, with the previous value returned in
`SecondaryOrderID(198)`.

**Cancellation is a request.** "The order cancel request message requests the
cancellation of all of the remaining quantity of an existing order... The request will
only be accepted if the order can successfully be withdrawn from the Exchange without
executing. A cancel request is assigned a ClOrdID and is treated as a separate entity...
The ClOrdID assigned to the cancel request must be unique amongst the ClOrdID assigned
to regular orders and replacement orders."

The answer is either an ExecutionReport with ExecType=Canceled, or an **Order Cancel
Reject (MsgType=9)**, whose `CxlRejReason(102)` is documented as returning only `99`
(Other) in practice — "Refer to 'text' (58) for exact reason for rejection".

**Modification (MsgType=G) replaces the whole order state.** "Fields not set in the
Cancel Replace will be reset. To keep the original value, the same field must be set
with the same value." A new ClOrdID is required, and "any change to the price of an
order, or increasing quantities will result in the order losing its priority in the
market." Only OrderQty, Price, OrdType, TimeInForce, ExpireDate, ExecInst, TriggerPrice,
OrderRestrictions, Text and DisplayQty may change.

**Unsolicited reports are normal.** Orders may be cancelled without any request from you
and arrive "as unsolicited Execution Reports"; a supervisor may cancel orders; orders
entered via FIX can be altered or cancelled through the native protocol, in which case
"no ClOrdID will be set on those Execution Reports"; and "the system will generate cancel
messages (Execution Report – IOC/FoK Order Cancel) for every IOC and FoK order."

**OrdStatus (39)** — `0` New · `1` Partially filled · `2` Filled · `4` Cancelled ·
`5` Replaced · `8` Rejected · `9` Suspended · `C` Expired, plus BTS2-defined `U`
Unplaced, `X` (trigger in the book, not yet activated) and `Z` Private Order. Note there
is **no Pending Cancel OrdStatus**; `ExecType(150)=6` is Pending Cancel, and the
`PENDING_CANCEL` state in this skill's engine is a deliberate local model of that window.

**ExecType (150)** — `0` New · `3` Done for day · `4` Cancelled · `5` Replaced ·
`6` Pending Cancel · `7` Stopped · `8` Rejected · `9` Suspended · `C` Expired ·
`F` Trade · `G` Trade Correct · `H` Trade Cancel · `I` Order Status · `U` Unplaced.

**ExecRestatementReason (378)** — `0` GT Corporate actions · `3` Expired carried-forward
GT orders outside threshold · `6` Order expired due to Dynamic Limit · `99` Other.

## 6. Execution report fields that carry the fill

| Tag | Field | Meaning |
|---|---|---|
| 17 | ExecID | **Required**, unique per execution message (`0` for ExecType=I Order Status). The only defence against a resend double-counting a fill. |
| 32 | LastQty | Quantity of *this* fill |
| 31 | LastPx | Price of *this* fill |
| 14 | CumQty | Total matched quantity |
| 6 | AvgPx | Exchange-calculated average price for all fills on the order during the day |
| 151 | LeavesQty | Open for further execution; `OrderQty − CumQty` while active |
| 880 | TradeMatchID | The TRS number |
| 1057 | AggressorIndicator | Whether the order initiator was the aggressor (continuous trading only) |

Bursa's published certification log gives an independently verifiable arithmetic check:
5,000 shares of SecurityID `1082` filled 2,000 @ 6.10 then 3,000 @ 6.20 reports
`6=6.1600000`. The skill's test suite asserts exactly this.

Source: [BTS2 FIX Certification Test Logs](https://www.bursamalaysia.com/sites/5d809dcf39fba22790cad230/assets/6864aeabcd34aa68d433d8b3/BTS2_FIX_Certification_Test_Logs.pdf).

## 7. Certification and connectivity

Access to the **BTS2 FIX CERT** environment requires submitting the **BTS2-A1 form** to
Bursa's IT Infrastructure team, which returns an RFC1918 address range, a sample VPN
router configuration and a pre-shared key for a site-to-site IPsec tunnel (IKEv2,
AES-GCM-256). Requests must be submitted at least five working days ahead; the
environment runs weekdays 08:30–17:00 and each participant gets up to three months of
access at a time. Bursa notes the facility "should not be misused as a stress test
facility".

Sources: [BTS2 FIX Certification Environment — Site to Site VPN Connection Guide v1.8](https://www.bursamalaysia.com/sites/5d809dcf39fba22790cad230/assets/6865f056cd34aadd76b03020/FIX_CERT_Connectivity_v1.8-final.pdf),
[BTS2 On-Boarding](https://www.bursamalaysia.com/trade/our_products_services/bts2_on_boarding/overview).

Production connectivity is a separate commercial arrangement — Bursa publishes
**Bursa Connectivity Services** and **Co-Location Services** for it. Confirm which
applies to your firm with Bursa; this skill does not model transport.

## Regulatory note — scope and limits

Bursa Malaysia is the market operator; the **Securities Commission Malaysia** is the
statutory regulator. Participating Organisation status, Direct Market Access
arrangements, short-selling eligibility (RSS/IDSS/PSS) and algorithmic-trading controls
are governed by the Rules of Bursa Malaysia Securities and SC requirements, **not** by
the FIX specification. Nothing in this skill establishes compliance with them: the
protocol fields above tell you how to *declare* a short sale or an algorithmic order,
not whether you are permitted to place one. Verify entitlements with Bursa and your
compliance function before routing.
