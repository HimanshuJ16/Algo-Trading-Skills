---
name: minimum-fill-size-and-lot-rounding-logic
description: >-
  Use at the last step before dispatch when the quantity was computed rather than typed,
  rounding to a per-security board lot or crypto step size with exact decimal arithmetic
  and reporting the overshoot.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: execution-algorithms
  tags: execution-algorithms, lot-rounding, board-lot, odd-lot, step-size, min-notional, fix-tag-110
  brokers_frameworks: "FIX 4.2 / 4.4 (Tag 110 MinQty, Tag 1089 MatchIncrement, Tag 561 RoundLot, Tag 562 MinTradeVol); Nasdaq Equity 4 Rule 4703 (Minimum Quantity Order Attribute); SEC Regulation NMS tiered round lot (effective 3 Nov 2025); HKEX Securities Market (per-security board lots, odd lot handling); Japan Exchange Group / TSE (100-share trading unit); SGX-ST (price-tiered board lots from 5 Oct 2026); Binance Spot API (LOT_SIZE and NOTIONAL filters)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill at the last step before an order is handed to a dispatch layer, whenever the quantity was *computed* rather than typed — a participation schedule's child size, a risk-model target position, a notional-to-quantity conversion, a currency-converted allocation. Those quantities land on arbitrary values (275 shares, 0.2949 BTC) and every venue constrains what it will accept along four independent axes:

- **Step / board lot** — the increment the quantity must be a multiple of (FIX Tag 561 `RoundLot`).
- **Minimum quantity** — the smallest order the venue accepts for the instrument (FIX Tag 562 `MinTradeVol`; Binance `LOT_SIZE.minQty`).
- **Minimum notional** — a floor on `price × quantity` that rounding *down* can breach even when the quantity is legal (Binance `NOTIONAL`/`MIN_NOTIONAL`).
- **Odd-lot handling** — whether a sub-lot quantity is executable on the main book at all.

The skill also answers a question that is frequently confused with the above: *should this order carry a minimum-execution constraint?* FIX Tag 110 `MinQty` is "minimum quantity of an order to be executed" and Tag 1089 `MatchIncrement` requires every execution's cumulative quantity to be a multiple of a value. Both are **instructions the sender adds to its own order**, not venue reference data, and attaching them changes how the venue handles the order.

## When NOT to Use

- **To enforce position, exposure, or capital limits.** `CEIL` rounding routes *more* than the strategy asked for; `MinimumFillSizeAndLotRoundingEngine` reports the overshoot as `quantity_delta` and a `ROUNDED_UP_ABOVE_REQUEST` warning, but a rounder must never be the thing that decides a position is too large. That belongs to the risk layer — see `leverage-limit-enforcement-across-instruments` and `concentration-risk-single-name-limits`.
- **As a source of lot sizes.** The engine consumes reference data; it does not fetch or validate it. Board lots are per security and they change: US round lots became price-tiered on 3 November 2025 and are reassigned semiannually, and SGX moves to price-tiered board lots on 5 October 2026. Source them per symbol from `reference-data-golden-source-designation` and pass the provenance through `lot_size_source`/`lot_size_as_of`.
- **As the tick-size / price rounder.** This skill rounds quantity only. Price increments are a separate constraint — see `exchange-tick-size-regime-tracking`.
- **To decide a slicing schedule.** It sizes one order. A parent order's slice count and timing belong to `execution-algo-twap-vwap-slicing` or `participation-of-volume-pov-execution`; this skill is what each of their child quantities passes through.
- **As a dispatch or reconciliation layer.** It returns a report. It submits nothing and knows nothing about fills.

## Prerequisites

- Per **symbol** (not per venue): `lot_size` and `min_order_quantity`, optionally `max_order_quantity`, `min_notional`. Both required fields have no default — a hard-coded 100 is a stale-reference-data bug, not a safe fallback.
- `rounding_mode`: `'FLOOR'`, `'CEIL'`, or `'ROUND_HALF_UP'`.
- `allow_odd_lots`: whether the venue will actually execute a sub-lot quantity for this instrument on the main book.
- Raw order: `order_id`, `symbol`, `side` (`BUY`/`SELL`), `raw_quantity`, optionally `limit_price` (required for the notional check), `available_liquidity_depth`, and the optional `min_execution_quantity` (Tag 110) / `match_increment` (Tag 1089).
- Quantities should be supplied as `str`, `int`, or `Decimal`. `float` is accepted and converted via `Decimal(str(value))`, which recovers the intended decimal literal but cannot recover precision already lost upstream.

## Workflow

1. **Round the quantity with exact decimal arithmetic.**
   - $Q_{\text{lots}} = \text{round}_{\text{mode}}\!\left(Q_{\text{raw}} / \text{lot\_size}\right)$, then $Q_{\text{rounded}} = Q_{\text{lots}} \times \text{lot\_size}$, all in `Decimal`.
   - **Decision point — never do this in binary floating point.** `math.floor(0.29 / 0.01) * 0.01` evaluates to `0.28` and `0.29 % 0.01` evaluates to `0.009999999999999974`, so a float implementation both under-sizes the order and misreports an exact multiple as an odd lot. Crypto step sizes are where this bites; the venue rule is literally `quantity % stepSize == 0`.
   - **Decision point — pick the rounding direction deliberately.** `FLOOR` under-fills the target and is the safe default for entries; `CEIL` over-fills it and can breach a position limit; `ROUND_HALF_UP` is half-away-from-zero. Python's built-in `round()` is banker's rounding — it turns 250 into 200 while turning 350 into 400 at a lot size of 100 — so it is not a valid nearest-lot rounder and the old `'ROUND_NEAREST'` mode now raises.

2. **Apply the odd-lot policy to the *quantity*, not just to the log line.**
   - If `allow_odd_lots` is false, route $Q_{\text{rounded}}$ and report `ODD_LOT_ADJUSTED_TO_ROUND_LOT`. If it is true and the raw quantity is not a lot multiple, route $Q_{\text{raw}}$ unchanged and report `ODD_LOT_PRESERVED`.
   - **Decision point — an odd lot is not universally rejected, and not universally fine.** US equity venues accept and execute odd lots; what the round lot governs there is quote dissemination and sizing. HKEX does not auto-match odd lots on the main book — a quantity below one board lot goes through the separate odd-lot operation, at a worse price. Set this flag per venue and per instrument, never globally.

3. **Clear the venue's quantity and notional floors.**
   - Reject below `min_order_quantity` (`ORDER_REJECTED_BELOW_MIN_QTY`), above `max_order_quantity` (`ORDER_REJECTED_ABOVE_MAX_QTY`), and below `min_notional` (`ORDER_REJECTED_BELOW_MIN_NOTIONAL`).
   - **Decision point — check the notional *after* rounding, not before.** A quantity that satisfied the notional floor at its raw size can fall under it once floored to the step, and the venue rejects on the value it receives.
   - **Decision point — a rejection is not a retry.** Re-sending the same quantity produces the same rejection. Either raise the target above the floor or drop the child order; if it is the tail of a slicing schedule, merge it into the previous slice.

4. **Decide whether to attach the FIX minimum-execution constraint — and only then populate Tag 110 / Tag 1089.**
   - **Decision point — Tag 110 is not the venue minimum.** Copying `min_order_quantity` into `MinQty` on every order is the defining bug of this area. Under Nasdaq Equity 4 Rule 4703(e), an order with a Minimum Quantity attribute **may not be displayed**, and if it is also marked Display the system accepts it but forces a Time-in-Force of IOC. An unrequested Tag 110 therefore turns an order meant to rest lit into a hidden order, or into an IOC. The engine leaves both tags `None` unless the caller asks.
   - **Decision point — size the constraint in round lots.** Nasdaq requires a minimum quantity entered via FIX to be one round lot or a multiple thereof and rounds a mixed-lot condition *down* to the nearest round lot, so a `MinQty` of 150 against a 100-share lot silently becomes 100 (`MIN_EXECUTION_QTY_NOT_LOT_MULTIPLE`). A `MinQty` above the order quantity can never execute and raises.

5. **Read the report as a status plus a list of warnings.**
   - `status` is the single terminal outcome; advisory findings accumulate in `warnings` so a depth or overshoot finding is never overwritten by a later rounding outcome.
   - `MIN_QTY_DEPTH_UNSATISFIED` compares observed depth against `min_execution_quantity` when one is set, and against the routed quantity otherwise. Leave `available_liquidity_depth` as `None` when it was not measured — a fabricated default makes this check pass silently.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Rounding step sizes in binary floating point.** `math.floor(0.29 / 0.01) * 0.01 == 0.28` silently drops 3.4% of the order, and `0.29 % 0.01 != 0` reports an exact multiple as an odd lot. Every quantity comparison and remainder in this path must be `Decimal`.
- **Stamping FIX Tag 110 with the venue minimum on every order.** `MinQty` is a client execution constraint, not reference data. On Nasdaq it makes the order non-displayed, and combined with a Display instruction it forces IOC — so a routine "board lot compliance" field quietly changes the order's execution character.
- **Treating the board lot as a per-venue constant.** It is per security and it moves: Reg NMS round lots have been price-tiered (100/40/10/1 shares by price band) since 3 November 2025 and are reassigned semiannually; SGX-ST moves to price-tiered board lots on 5 October 2026; HKEX board lots vary by security across a wide range. A cached lot size is a wrong-quantity bug the moment the venue re-tiers it.
- **Assuming odd lots are rejected everywhere.** They are not rejected on US equity venues. They are not auto-matched on the HKEX main board. Universalising either answer produces either needless rounding or repeated rejects.
- **Letting an `allow_odd_lots` flag change only the log message.** If the flag does not change the routed quantity, the caller believes an odd lot went out when a rounded one did — and the position ends up smaller than the strategy sized.
- **Using `round()` for nearest-lot rounding.** Banker's rounding is asymmetric across ties: 250 → 200 but 350 → 400. In a schedule of same-sized slices that systematically under-fills half the parent.
- **Checking the minimum notional before rounding.** Floor-rounding after the check lets an order through that the venue will reject on value.
- **Retrying a below-minimum rejection unchanged.** The rejection is deterministic; only a different quantity changes it. Retrying burns message-rate budget — see `order-to-trade-ratio-fee-penalty-avoidance`.
- **Letting `CEIL` silently overshoot a position limit.** Rounding up is a real increase in exposure; check `quantity_delta` and route the result through the risk layer rather than trusting that "it is only one lot".
- **Reading `rounded_quantity` without checking `is_compliant`.** A rejected report carries `rounded_quantity == 0`; a caller that only reads the quantity would send a zero-quantity order.

## Verification

- Instantiate `MinimumFillSizeAndLotRoundingEngine`. Size 275 shares with `lot_size=100, min_order_quantity=100, rounding_mode='FLOOR'`: verify `rounded_quantity == Decimal("200")`, `quantity_delta == Decimal("-75")`, `status == 'ODD_LOT_ADJUSTED_TO_ROUND_LOT'`, and that `fix_tag_110_min_qty` and `fix_tag_1089_match_increment` are both `None`.
- Decimal regression: 0.29 at `lot_size=0.01` under `FLOOR` must return `Decimal("0.29")` with `is_odd_lot_request is False` (a float implementation returns 0.28 and reports an odd lot); 0.2949 must return `Decimal("0.29")` with `quantity_delta == Decimal("-0.0049")`.
- Rounding modes: `CEIL` on 275 gives 300 with `ROUNDED_UP_ABOVE_REQUEST`; `ROUND_HALF_UP` gives 300 for 250 and 400 for 350 (banker's rounding would give 200 and 400).
- Odd-lot policy: with `allow_odd_lots=True` and `min_order_quantity=1`, 275 must route as 275 with `status == 'ODD_LOT_PRESERVED'` and `routes_odd_lot is True` — and must still be rejected when the venue minimum is 100 and the quantity is 37.
- Warning independence: depth of 50 against a 200-share routed order must report `status == 'ODD_LOT_ADJUSTED_TO_ROUND_LOT'` *and* `MIN_QTY_DEPTH_UNSATISFIED` in `warnings`; warnings must also survive on a rejected report.
- Notional: 0.00002 BTC at 60,000 with `min_notional=5` must be rejected `ORDER_REJECTED_BELOW_MIN_NOTIONAL` with `notional == Decimal("1.2")`; a market order with no `limit_price` must report `MIN_NOTIONAL_UNCHECKED_NO_PRICE` instead of silently passing.
- Negative checks: `rounding_mode='ROUND_NEAREST'` (the removed banker's-rounding mode), a missing `lot_size`, `side='LONG'`, NaN/Inf/zero/negative/`bool`/unparseable quantities, a config symbol that disagrees with the order symbol, a `min_execution_quantity` or `match_increment` above the routed quantity, and an implausible quantity-to-lot ratio (1E+40 against a 1E-40 step) must each raise `ValueError`/`TypeError` — never a bare `decimal.InvalidOperation` from inside the arithmetic.
- Run `python -m unittest discover -s skills/minimum-fill-size-and-lot-rounding-logic/scripts` and confirm all tests pass.

## Related Skills

- `reference-data-golden-source-designation`
- `exchange-tick-size-regime-tracking`
- `execution-algo-twap-vwap-slicing`
- `iceberg-order-native-broker-support-vs-simulation`
- `auction-only-order-types-for-illiquid-names`
- `order-to-trade-ratio-fee-penalty-avoidance`
- `leverage-limit-enforcement-across-instruments`
