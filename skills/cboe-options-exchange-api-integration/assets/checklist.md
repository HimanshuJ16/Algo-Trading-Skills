# Pre-Flight Checklist: Cboe Complex Order API Integration

Use before submitting multi-leg option orders (`MsgType=AB`) to Cboe Options
Exchanges (C1, C2, BZX Options, EDGX Options). Field references follow the Cboe
Titanium U.S. Options FIX Specification.

## 1. Request Form
- [ ] Is this a **long form** request (legs in the `NoLegs (555)` group) or a **short form** request (priced against a listed COB strategy symbol)?
- [ ] For long form: are `Symbol (55)` and `Side (54)` omitted rather than filled with the underlying root?
- [ ] For short form: does `Symbol (55)` carry the Cboe COB **strategy** symbol (case sensitive), and has the side-dependent price sign been applied?

## 2. Pre-Trade Risk & Regulatory Compliance (SEC Rule 15c3-5)
- [ ] Has gross strategy dollar exposure been verified against firm pre-trade credit limits?
- [ ] Is the leg count within the venue ceiling (2–16; up to 100 only for C1 non-FLEX floor-routed and FLEX orders)?
- [ ] If the order has only 1 leg, has it been redirected to `MsgType=D` (New Order Single)?
- [ ] If a short equity leg is present, has a Regulation SHO locate been confirmed and marked with `LegSide (624) = 5` or `6`?
- [ ] Has `DrillThruProtection (6253)` been set deliberately (or left at the exchange default) rather than by accident?

## 3. Ratio Normalization & Contract Scaling
- [ ] Has the GCD of all leg ratios been computed and applied so every reduced ratio is in lowest terms?
- [ ] Has `OrderQty (38)` been scaled **up** by the GCD to preserve total contract exposure?
- [ ] **After** scaling, is `OrderQty (38)` still within 1–999,999?
- [ ] Is every reduced `LegRatioQty (623)` within 1–999,999?
- [ ] On **C2 or EDGX**, is the reduced smallest-to-largest leg ratio no wider than 1:3?

## 4. Stock-Option Combination (C1 and EDGX only)
- [ ] Is the equity leg marked `LegCFICode (608) = E`, and is there at most one equity leg?
- [ ] Is the conforming ratio computed from the **smallest option leg** (not the sum of all option legs) and is it ≤ 8:1?
- [ ] If the order is non-conforming, is the different priority and auction handling an accepted, deliberate choice?
- [ ] Is `EquityExDestination (22016)` set if a specific equity matching venue is required?

## 5. Net Pricing
- [ ] Long form: is a **net debit** positive, a **net credit** negative, and an even package `0`?
- [ ] Short form: has the sign been inverted appropriately for a **Sell** order (positive = credit)?
- [ ] Is the price in **whole pennies** for an option-only spread (4 decimals only with a stock leg or FLEX)?
- [ ] Has the class-level increment been checked — $0.01 generally, but **$0.05 for SPX/SPXW** non-box/roll spreads?
- [ ] Is the price rendered from a decimal type rather than a binary float (no `1.5000000000000002`)?
- [ ] Is `|Price| ≤ 999,999,999.90`?
- [ ] For `OrdType (40) = 1` (Market): is market-order eligibility confirmed for the session (not supported during GTH or Curb)?

## 6. Routing, Auction & Time in Force
- [ ] Is COA exposure set through the **second character of `RoutingInst (9303)`** (`S` expose / `L` suppress) — *not* through `ExecInst (18)`?
- [ ] Is the combination `RoutingInst = PS` avoided (Post Only cannot be COA eligible)?
- [ ] If `RoutingInst` 1st character is `D` (Complex Book Only), is `TimeInForce` DAY or IOC **and** `OrderCapacity (47) = M`?
- [ ] Is `TimeInForce (59)` one of `0`, `1`, `2`, `3`, `6` — noting that **FOK is not a documented value** for this message?
- [ ] If `TimeInForce = 6` (GTD), is `ExpireTime (126)` supplied?

## 7. FIX Serialization (MsgType=AB)
- [ ] Is `MsgType (35) = AB`?
- [ ] Is `SecurityType (167) = MLEG` present (required)?
- [ ] Is `Rule80A / OrderCapacity (47)` present (required)?
- [ ] Is `ClOrdId (11)` ≤ 20 chars, ASCII 33–126, free of `,` `;` `|`, and not starting with `~`?
- [ ] Does `NoLegs (555)` equal the exact count of repeated groups?
- [ ] Does **every** leg group start with `LegRefID (654)`, ≤ 5 alphanumeric-or-space characters, unique within the order?
- [ ] Where `LegSymbol (600)` is an OSI root, are `LegCFICode (608)`, `LegMaturityDate (611)` and `LegStrikePrice (612)` all present?
- [ ] Is `LegPositionEffect (564)` populated on every leg unless `OrderCapacity (47)` is `M` or `N`?
- [ ] Is `LegSecurityType (609)` **absent** (it is not a field of this message)?
- [ ] Is `TransactTime (60)` a timezone-aware UTC value formatted `YYYYMMDD-HH:MM:SS.sss`?
- [ ] Is the wire delimiter SOH (`\x01`), with `8`, `9` and `10` supplied by the FIX engine?

## 8. Execution Report Reconciliation (MsgType=8)
- [ ] Does the handler branch on `MultilegReportingType (442)` — `3` package, `2` leg, `1` single-leg?
- [ ] Is it understood that the `555` group on a package report echoes leg **definitions**, not fills, and that `LegLastPx (637)` / `LegLastQty (638)` do **not** exist on Cboe?
- [ ] Are leg fills taken from the top-level `LastPx (31)` / `LastShares (32)` of each `442=2` report and joined by `LegRefID (654)`?
- [ ] Are multiple leg reports for the same `LegRefID` aggregated before comparison?
- [ ] Is `leg_filled_quantity == package_filled_quantity × reduced_leg_ratio` asserted, with a breach escalated as a position-integrity incident rather than retried?

## 9. Ambiguous State
- [ ] On a timeout or missing Execution Report, does the system **query state** rather than resubmit the package?
- [ ] Is it understood that Cboe enforces `ClOrdId` uniqueness only among live orders, so a duplicate ID is not a reliable resubmission guard?
