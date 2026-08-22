# Cboe Complex Order Book (COB) & Protocol Standards

All FIX field semantics below are taken from the **Cboe Titanium U.S. Options FIX
Specification** (New Order Multileg Message Fields; Execution Report Message
Fields) and the **Cboe Titanium U.S. Options Complex Book Process**. Where a
claim is not directly supported by a Cboe or SEC document it is marked
*unverified* rather than stated as fact.

## Primary Sources

| Source | Publisher | URL |
|---|---|---|
| Cboe Titanium U.S. Options FIX Specification — New Order Multileg Message Fields | Cboe Global Markets | https://www.cboe.com/document/tech-spec/content/technical-specifications/cboe-titanium-u.s.-options-fix-specification/fix-messages/order-protocol---member-to-cboe/new-order-multileg-message-fields |
| Cboe Titanium U.S. Options FIX Specification — Execution Report Message Fields | Cboe Global Markets | https://www.cboe.com/document/tech-spec/content/technical-specifications/cboe-titanium-u.s.-options-fix-specification/fix-messages/order-protocol---cboe-to-member/execution-report-message-fields |
| Cboe Titanium U.S. Options Complex Book Process | Cboe Global Markets | https://www.cboe.com/document/tech-spec/document/technical-specifications/cboe-titanium-u.s.-options-complex-book-process |
| Cboe U.S. Options — Complex Orders (product page, stock-option ratio) | Cboe Global Markets | https://www.cboe.com/us/options/trading/complex_orders/ |
| Cboe Exchange, Inc. Rule Book (Rule 5.33 Complex Orders) | Cboe Global Markets | https://cdn.cboe.com/resources/regulation/rule_book/C1_Exchange_Rule_Book.pdf |
| SEC Rule 15c3-5 (Market Access Rule) | U.S. SEC | https://www.sec.gov/rules/final/2010/34-63241.pdf |

## Exchange Rules & Engineering Requirements

| Dimension | Standard | Engineering Requirement |
|---|---|---|
| **Governing Rule** | Cboe Rule 5.33 (Complex Orders) | A complex order is an order for two or more different option series in the same underlying, sent as a single order and guaranteed to execute within a net price and ratio. |
| **Leg Boundaries** | FIX Spec, NoLegs (555) | "Minimum of 2, maximum of 16 total legs, including 1 equity leg." Up to **100** legs on non-FLEX *floor-routed* orders (C1 only) and on FLEX (up to 99 option legs plus 1 equity leg; FLEX DAC up to 98 option legs plus 1 equity leg). Single-leg orders must use `MsgType=D`. |
| **Ratio Normalization** | FIX Spec, LegRatioQty (623) | "All legs must be reduced (i.e., 2:2 must be sent as 1:1) in order to be accepted by the system." Reduce by GCD and scale `OrderQty (38)` by the same GCD. Accepted ratio values are 1–999,999. |
| **Ratio Spread Cap (C2/EDGX)** | FIX Spec, LegRatioQty (623) | "C2 and EDGX Only: In addition, when reduced, the ratio between the smallest and largest leg must be no more than 1:3." No such cap is documented for C1 or BZX. |
| **Order Quantity Ceiling** | FIX Spec, OrderQty (38) | "Number of contracts for order, 1 to 999,999." GCD scaling can push a valid request past this ceiling — check *after* normalization. |
| **Stock-Option Conformance** | Cboe Rule 5.33 / Cboe complex orders page | Maximum conforming ratio is **8 option contracts to 100 shares**, evaluated using the **smallest option leg**, not the aggregate of all option legs. Non-conforming stock-option orders receive different priority and auction handling. |
| **Equity Leg Availability** | FIX Spec / Complex Book Process | Complex orders may include an equity leg on **C1 and EDGX only**, and at most one. The equity leg is identified by `LegCFICode (608) = E`; short sales are marked `LegSide (624) = 5/6` (long form) or `EquityLegShortSell (22624) = 5/6` (short form). |
| **Net Price Sign Convention** | FIX Spec, Price (44) | **Long form** (legs supplied): positive = debit, negative = credit, 0 = even. **Short form** (priced against a listed COB strategy symbol): the sign is read against `Side (54)` — on a **Sell** order positive = **credit** and negative = **debit**. Do not apply the long-form reading to short-form requests. |
| **Net Price Precision** | FIX Spec, Price (44) | "Price must be in whole pennies for option-only spreads. Can be up to 4 decimal places for spreads with stock legs and FLEX instruments." Accepted range ±$999,999,999.90. |
| **Net Price Increments** | Complex Book Process | Most complex orders trade in $0.01 increments. **SPX/SPXW is an exception**: $0.05 for non-box/roll spreads, $0.01 for boxes and rolls. The blanket claim that all classes trade in pennies is incorrect. Class-level increments are not enforced by the engine — confirm per class before pricing. |
| **COA Exposure** | FIX Spec, RoutingInst (9303) | COA participation is controlled by the **second character of RoutingInst (9303)**: `L` = do not expose via COA, `S` = expose via COA. Cboe defaults the second character to `S` for non-IOC orders and `L` for IOC orders. `RoutingInst = PS` (Post Only + COA eligible) is **not supported**. |
| **COA Duration** | Complex Book Process | "COAs are exposed for 100ms (the Response Time Interval)", followed by a processing window. Treat the duration as exchange-configurable, not a constant. |
| **AIM** | Cboe Rules / FIX Spec | AIM is a **paired auction** entered via `New Order Cross Multileg` (C1 and EDGX only), not an attribute of a New Order Multileg order. There is no ExecInst value that turns a standard complex order into an AIM order. |
| **Clearing & Settlement** | FIX Spec, LegPositionEffect (564); OCC rules | `564` is required per leg (`O` Open / `C` Close / `N` None). Orders with `OrderCapacity (47)` of `M` or `N` are not required to specify it. |
| **Pre-Trade Risk Controls** | SEC Rule 15c3-5 | Enforce pre-submission checks: max order size, aggregate dollar exposure, credit limits, Regulation SHO locates for short equity legs, and price reasonability. Cboe additionally offers `DrillThruProtection (6253)` for SNBBO trade-through tolerance. |
| **Audit Trail** | FINRA/SEC Consolidated Audit Trail (Rule 613) | Retain the submitted `ClOrdId (11)` and `TransactTime (60)` for every submission, modification and execution. CAT clock-synchronization and timestamp-granularity obligations apply to the *reporting* firm, not to this message construction. |

---

## FIX Protocol Tag Reference (MsgType=AB New Order Multileg)

Cboe supports two request forms. The **short form** prices a package against an
already-listed COB strategy symbol and supplies `Symbol (55)` + `Side (54)` and no
legs. The **long form** describes the legs explicitly and does not require
`Symbol (55)` or `Side (54)`. The helper script emits the long form only.

### Order Envelope Tags (subset relevant to complex order entry)

| Tag | Field Name | Required | Description / Values |
|---|---|---|---|
| `35` | `MsgType` | Yes | `AB` = New Order Multileg |
| `1` | `Account` | No | Up to 16 characters, ASCII 33–126 |
| `11` | `ClOrdId` | Yes | 20 characters or less, ASCII 33–126, excluding `,` `;` `\|`. A leading `~` is reserved by Cboe and is rejected. Uniqueness is enforced only among live orders. |
| `60` | `TransactTime` | Yes | UTC `YYYYMMDD-HH:MM:SS.sss` |
| `167` | `SecurityType` | Yes | `MLEG` = Multileg |
| `54` | `Side` | Yes* | `1` = Buy, `2` = Sell. *Required only for short form requests.* |
| `55` | `Symbol` | Yes* | Cboe **Complex Order Book strategy symbol** (case sensitive) — not the underlying root. *Required only for short form requests.* |
| `555` | `NoLegs` | Yes | 2–16 legs (up to 100 for C1 floor-routed / FLEX) |
| `18` | `ExecInst` | No | Single value only. The **only** documented value is `G` = All or None (AON); requires DAY and COA-eligible, C1 and EDGX only. |
| `38` | `OrderQty` | Yes | 1 to 999,999 contracts, post-GCD normalization |
| `40` | `OrdType` | Yes | `1` = Market, `2` = Limit, `4` = Stop Limit (effective TBD). Market and stop orders are not supported during GTH or Curb sessions. |
| `44` | `Price` | Yes | Net price of the strategy; see sign convention and precision above |
| `9303` | `RoutingInst` | No | 1st char: `B` Book Only (default) / `P` Post Only / `D` Complex Book Only (requires DAY or IOC and `OrderCapacity = M`). 2nd char: `L` no COA / `S` expose via COA. `PS` unsupported. |
| `47` | `Rule80A` (OrderCapacity) | Yes | `C` Customer, `F` Firm, `M` Market Maker, `U` Professional Customer, `N` Away Market Maker, `B` Broker-Dealer, `J` Joint Back Office, `L` Non-TPH Affiliate (C1/C2), `D` Non-TPH Broker-Dealer (FLEX, C1) |
| `59` | `TimeInForce` | No | `0` DAY (default), `1` GTC, `2` At The Open, `3` IOC, `6` GTD. **FOK is not a documented value for this message.** |
| `126` | `ExpireTime` | No | Required when `TimeInForce = 6` (GTD) |
| `6253` | `DrillThruProtection` | No | Amount willing to trade through the SNBBO; `0` requests full protection |
| `7928` | `PreventMatch` | No | Cboe Match Trade Prevention, 3 characters |

### Leg Repeating Group Tags

`LegRefID (654)` is the **required tag to start each repeated group** — a group
that begins with any other field is not a valid FIX repeating group.

| Tag | Field Name | Required | Description / Values |
|---|---|---|---|
| `654` | `LegRefID` | Yes | Client-chosen leg ID. Five alphanumeric or space characters or less. Must be the first field of each group. |
| `600` | `LegSymbol` | Yes* | OSI root symbol (upper case) or Cboe format symbol (case sensitive). *Not required for short form requests.* |
| `608` | `LegCFICode` | Conditional | Required if `600` is an OSI root. `OC` = Option Call, `OP` = Option Put, `E` = Equity (required for equity legs; C1 and EDGX only). |
| `611` | `LegMaturityDate` | Conditional | `YYYYMMDD`. Required if `600` is an OSI root. |
| `612` | `LegStrikePrice` | Conditional | `0` – `999999.999`. Required if `600` is an OSI root. |
| `623` | `LegRatioQty` | Yes* | Reduced integer leg ratio, 1–999,999. *Not required for short form requests.* |
| `624` | `LegSide` | Yes* | `1` Buy, `2` Sell, `5` Sell Short (**stock leg only**), `6` Sell Short Exempt (**stock leg only**). *Not required for short form requests.* |
| `566` | `LegPrice` | No | FLEX orders only (C1) |
| `564` | `LegPositionEffect` | Yes* | `O` Open, `C` Close, `N` None. *Not required when `OrderCapacity (47)` is `M` or `N`.* |
| `22024` | `LegDelta` | No | FLEX DAC orders only (C1) |

**Tags that do NOT exist in this message:** `LegSecurityType (609)`. Leg
instrument type is conveyed by `LegCFICode (608)`, not by a leg `SecurityType`.

---

## Execution Report (MsgType=8) — Complex Fill Reporting

A complex execution does **not** report leg fill prices inside the package
message. Cboe sends:

| Report | `MultilegReportingType (442)` | `SecurityType (167)` | Fill fields |
|---|---|---|---|
| Complex package fill | `3` | `MLEG` | `LastPx (31)`, `LastShares (32)`, `CumQty (14)`, `LeavesQty (151)`, `AvgPx (6)`; the `NoLegs (555)` group echoes the order's leg definitions only |
| Individual leg fill | `2` | `OPT` or `EQ` | `LegRefID (654)`, `LastPx (31)`, `LastShares (32)` at the **top level** of the message |
| Single-leg instrument fill | `1` | `OPT` | Standard single-order fields |

**`LegLastPx (637)` and `LegLastQty (638)` are not part of the Cboe message set.**
Reconciliation must join the `442=2` leg reports to the `442=3` package report by
`LegRefID (654)`, asserting
`leg_filled_quantity == package_filled_quantity × reduced_leg_ratio`.

Note that `AvgPx (6)` is zero when `MultilegReportingType (442) = 2`, and
`CumQty (14)` / `LeavesQty (151)` are not supported on FLEX order restatements
(`150=D`, `378=9`).

---

## BOEv3 (Binary Order Entry)

Cboe also offers BOE (Binary Order Entry) for latency-sensitive participants,
with fixed-length binary headers and bitfield-selected optional fields. The
binary field semantics for complex orders track the FIX fields above but the
exact wire layout must be taken from the *Cboe Titanium U.S. Options Binary Order
Entry Version 3 Specification* — the helper script in this skill implements FIX
only and makes no BOE encoding claims.
