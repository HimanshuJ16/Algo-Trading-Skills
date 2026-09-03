---
name: cboe-options-exchange-api-integration
description: >-
  Use when trading multi-leg option strategies on Cboe options exchanges over Titanium
  FIX: New Order Multileg construction, leg ratio normalisation and Complex Order
  Auction participation. Single-leg orders use New Order Single instead.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: cboe, options, complex-order-book, multi-leg, fix-protocol, boe-protocol, coa
  brokers_frameworks: "Generic FIX Engine; Cboe Titanium FIX; Cboe BOEv3"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when integrating directly with Cboe Options Exchanges (Cboe Options Exchange [C1], C2 Options Exchange, Cboe BZX Options, Cboe EDGX Options) over Cboe Titanium FIX to trade multi-leg option strategies. Specifically:
- Construct and serialize **New Order Multileg** (`MsgType=AB`) messages with the `NoLegs (555)` repeating group.
- Eliminate **legging risk** by routing spreads, straddles, strangles, condors, butterflies, collars and ratio spreads to the **Complex Order Book (COB)** as one package that executes within a net price and ratio.
- Control **Complex Order Auction (COA)** exposure through `RoutingInst (9303)`.
- Execute **stock-option combination orders** (buy-writes, collars) on C1 and EDGX, including the Cboe Rule 5.33 conforming-ratio test.
- Reconcile a complex fill from the package report plus the per-leg reports Cboe sends alongside it.

## When NOT to Use

- **Single-Leg Option Orders**: use `MsgType=D` (New Order Single). Cboe requires at least 2 legs on `MsgType=AB`.
- **AIM / paired auctions**: AIM is entered through `New Order Cross Multileg` (C1 and EDGX only), not by tagging a standard complex order. No `ExecInst` value converts an ordinary order into an AIM order.
- **Short-form COB requests**: this skill's helper emits the *long form* only. The short form (pricing against an already-listed COB strategy symbol via `Symbol (55)` + `Side (54)`) has a **side-dependent net-price sign** and needs its own builder.
- **BOEv3 wire encoding**: the helper implements FIX only; take binary layouts from the Cboe BOE v3 specification.
- **Venues Lacking Native Combo Books**: use algorithmic synthetic legging with hedge monitoring instead (see `calendar-spread-and-multi-leg-order-atomicity`).
- **OTC / bilateral options** not listed or cleared through the OCC.

## Prerequisites

- Active Cboe Titanium FIX order-entry session with market access credentials and a valid EFID.
- FIX engine that serializes and parses repeating groups; it supplies `BeginString (8)`, `BodyLength (9)` and `CheckSum (10)`.
- OCC clearing parameters for `LegPositionEffect (564)` and, where applicable, CMTA fields.
- Pre-trade risk controls per SEC Rule 15c3-5.
- For stock-option orders: C1 or EDGX access, plus an equity matching destination (`EquityExDestination (22016)`).

## Workflow

1. **Choose the request form.** Long form (legs in the `555` group) or short form (a listed COB strategy symbol in `Symbol (55)` with `Side (54)`). Do not mix them: sending the underlying *root* in `Symbol (55)` alongside a leg group is neither form. The rest of this workflow is the long form.
2. **Define legs.** Each `OptionLeg` needs `LegSymbol (600)`, `LegRatioQty (623)`, `LegSide (624)` and, unless `OrderCapacity (47)` is `M`/`N`, `LegPositionEffect (564)`. When `600` is an OSI root, `LegCFICode (608)`, `LegMaturityDate (611)` and `LegStrikePrice (612)` are required too. Mark the equity leg with `608=E` — there is no `LegSecurityType (609)` in this message.
3. **Normalize ratios (CRITICAL).** Reduce all leg ratios by their GCD and multiply `OrderQty (38)` by the same GCD, so net exposure is unchanged. Cboe rejects unreduced ratios outright.
4. **Re-validate after scaling, not before.** GCD scaling is a multiplication: `OrderQty (38)` must still be ≤ 999,999 afterwards. On **C2 and EDGX** the *reduced* smallest-to-largest leg ratio must also be no wider than 1:3.
5. **Stock-option conformance (Cboe Rule 5.33).** Compute the ratio from the **smallest option leg** against the stock leg and require ≤ 8:1. Using the sum of all option legs over-rejects legitimate collars. A non-conforming order is not invalid — it receives different priority and auction handling, so decide deliberately.
6. **Price the package.** Long form: positive = net debit, negative = net credit, `0` = even. Whole pennies for option-only spreads; up to 4 decimals only with a stock leg or FLEX. Check the class increment separately — SPX/SPXW non-box/roll spreads trade in $0.05, not $0.01.
7. **Set routing.** `RoutingInst (9303)`: first character `B`/`P`/`D`, second character `S` (expose via COA) or `L` (suppress). Leave unset to accept Cboe's defaults (`S` for non-IOC, `L` for IOC). `PS` is rejected. `ExecInst (18)` has exactly one documented value here — `G` (All or None) — and plays no part in auction selection.
8. **Serialize.** `35=AB` with `167=MLEG` and `47` (both required), then `555=N` followed by leg groups that each **start with `LegRefID (654)`**, then `38`, `40`, `44`, `9303`, `47`, `59`.
9. **Reconcile the fill.** Branch on `MultilegReportingType (442)`: `3` is the package fill, `2` is a per-leg fill carrying `LegRefID (654)`, `LastPx (31)` and `LastShares (32)` at the top level. Join by `LegRefID` and assert `leg_qty == package_qty × reduced_ratio`. A breach is a position-integrity incident, not a retry trigger.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Selecting COA through `ExecInst (18)`**: COA exposure lives in the *second character of `RoutingInst (9303)`* (`S`/`L`). `ExecInst` values borrowed from base FIX are not Cboe values — sending `18=A` transmits "No cross", not "COA eligible".
- **Emitting `LegSecurityType (609)`**: it is not a field of Cboe's New Order Multileg. Leg type is `LegCFICode (608)` = `OC` / `OP` / `E`.
- **Starting the leg group on `LegSymbol (600)`**: Cboe documents `LegRefID (654)` as "the required tag to start each repeated group". A group opened on another tag is not a valid repeating group, and `654` is also the only key that maps leg fills back to legs.
- **Expecting `LegLastPx (637)` / `LegLastQty (638)`**: they do not exist in the Cboe message set. A parser that looks for them silently reports every leg as filled at 0.00 for 0 contracts.
- **Applying the debit-positive rule to a short-form Sell order**: under the short form, a positive `Price (44)` on a Sell order is a **credit**. Inverting it crosses the market or is rejected off-market.
- **Validating `OrderQty` before GCD scaling**: 200,000 packages of a 10:20 spread normalizes to 2,000,000 contracts and breaches the documented 999,999 ceiling only *after* normalization.
- **Summing option legs for the 8:1 test**: Cboe's ratio check uses the smallest option leg. Summing rejects conforming collars and buy-writes.
- **Assuming FOK is available**: `TimeInForce (59)` on this message is `0`, `1`, `2`, `3` or `6` — FOK is not documented.
- **Assuming pennies everywhere**: SPX/SPXW non-box/roll complex orders price in $0.05 increments.
- **Serializing prices from binary floats**: format from `Decimal`; `0.1 + 0.2` reaches the wire as `0.30000000000000004`.
- **Retrying on a request timeout**: a lost response does not mean the order was not accepted. Query order state or cancel the original `ClOrdId`; Cboe enforces `ClOrdId` uniqueness only among *live* orders, so it is not a duplicate guard once an order is no longer live.

## Verification

- Run the test suite: `python -m unittest discover -s skills/cboe-options-exchange-api-integration/scripts`.
- Validate structure: `python tools/validate_skills.py --skill cboe-options-exchange-api-integration`.
- Confirm a generated debit spread contains `35=AB`, `167=MLEG`, `47`, `555=2`, and that the text following `555=2` begins with `654=`.
- Confirm `609=` and `18=` never appear in generated output, and that `55=` / `54=` are absent from long-form output.
- Confirm a 10:20 ratio spread reduces to 1:2 with `OrderQty` scaled ×10, and that a scaled quantity above 999,999 is rejected.
- Confirm `reconcile_leg_fills` raises when a `442=2` leg quantity does not equal `package_qty × reduced_ratio`.

## Limitations

- Class-level net price **increments** (e.g. $0.05 for SPX/SPXW) are not enforced — only the whole-penny / 4-decimal precision rule from the FIX specification is.
- FLEX-specific fields (`LegPrice (566)`, `LegDelta (22024)`, `FLEXAuctionDuration (21010)`) and floor routing (`FloorRoutingInst (22303)`) are documented in `references/standards.md` but not implemented.
- Cboe's field table marks `Price (44)` as required on New Order Multileg without stating an exception for `OrdType=1`; the helper omits `44` on market orders. Confirm market complex-order handling with the Cboe Trade Desk before enabling it.

## Related Skills

- `calendar-spread-and-multi-leg-order-atomicity`
- `fix-protocol-session-management-across-venues`
- `multi-leg-strategy-margin-optimization`
- `order-placement-idempotency`
