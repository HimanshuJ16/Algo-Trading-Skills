# Workflows for Minimum Fill Size & Lot Rounding

The full procedure behind `SKILL.md`. Each step names the failure it prevents.

## 0. Resolve reference data before any arithmetic

1. Look up `lot_size` and `min_order_quantity` **for the symbol**, not for the venue. HKEX board lots differ across securities on the same board; US round lots differ across price bands and are reassigned semiannually; SGX-ST board lots become price-tiered on 5 October 2026.
2. Record `lot_size_source` and `lot_size_as_of`. The engine emits `LOT_SIZE_REFERENCE_DATA_UNSOURCED` when neither is set — that warning is the only thing standing between a cached 100 and a wrong quantity after a re-tiering.
3. Where a venue re-tiers on a schedule, refresh before the first order of the session, not on a cache TTL that can straddle the change. SGX purges resting orders in an affected book before a new board lot size takes effect, so a stale lot size is not merely a rounding error — the order it produced may no longer exist.

**Prevents:** sizing today's order with last quarter's lot.

## 1. Round with exact decimal arithmetic

1. Convert every quantity to a decimal type at the boundary. Reject NaN and Infinity explicitly: `float('nan') <= 0` is `False`, so a naive positivity guard passes NaN straight through to `math.floor`, which then raises from deep inside the arithmetic rather than at the input.
2. Compute the lot count, round it in the chosen direction, and multiply back:
   - `FLOOR`: $Q = \lfloor Q_{\text{raw}} / L \rfloor \times L$
   - `CEIL`: $Q = \lceil Q_{\text{raw}} / L \rceil \times L$
   - `ROUND_HALF_UP`: $Q = \text{round}_{\text{half-up}}(Q_{\text{raw}} / L) \times L$
3. Compute `quantity_delta = Q - Q_raw` and surface it. A negative delta is under-fill; a positive delta is added exposure.

**Prevents:** the float step-size defect (`math.floor(0.29 / 0.01) * 0.01 == 0.28`), the banker's-rounding tie asymmetry (250 → 200 while 350 → 400), and a silent overshoot on `CEIL`.

## 2. Apply the odd-lot policy to the quantity

1. `is_odd_lot_request` = the **raw** quantity is not a lot multiple. `routes_odd_lot` = the **outgoing** quantity is not a lot multiple. They are different questions and both belong in the report.
2. With `allow_odd_lots` false, route the rounded quantity (`ODD_LOT_ADJUSTED_TO_ROUND_LOT`). With it true, route the raw quantity unchanged (`ODD_LOT_PRESERVED`).
3. Set the flag from venue behaviour: US equity venues execute odd lots; HKEX handles a sub-board-lot quantity through a separate odd-lot operation rather than main-board auto-matching.

**Prevents:** a flag that changes only the log line, leaving the caller believing an odd lot was routed when a rounded quantity went out — and the position ending up smaller than the strategy sized.

## 3. Clear the venue floors and ceilings, in order

1. `Q < min_order_quantity` → `ORDER_REJECTED_BELOW_MIN_QTY`, quantity zeroed.
2. `Q > max_order_quantity` (where the venue publishes one) → `ORDER_REJECTED_ABOVE_MAX_QTY`. Split the parent rather than clamping silently.
3. `Q × price < min_notional` → `ORDER_REJECTED_BELOW_MIN_NOTIONAL`. Evaluate this **after** rounding: a raw quantity can clear the notional floor and the floored quantity can fall under it.
4. With no limit price (a market order), report the notional check as not performed (`MIN_NOTIONAL_UNCHECKED_NO_PRICE`) rather than passing it.
5. Zero the quantity on every rejection so a caller that reads `rounded_quantity` without checking `is_compliant` cannot route a rejected size.

**Prevents:** a venue-side reject on a value the client never computed, and a clamped order that quietly differs from what the strategy asked for.

## 4. Decide the FIX execution constraint deliberately

1. Default both Tag 110 and Tag 1089 to absent. They are optional instructions, not compliance fields.
2. Attach Tag 110 only when the order genuinely must not fill in small pieces — a block that becomes uneconomic below a size, or a leg whose partial fill would leave an unhedged residual.
3. Accept the order-handling consequences before attaching it. Under Nasdaq Equity 4 Rule 4703(e) a Minimum Quantity order may not be displayed, and if it also carries a Display instruction the system accepts it but forces IOC. A "board lot compliance" field applied by habit therefore converts a resting lit order into a hidden order or an IOC.
4. Size the constraint in whole round lots. Nasdaq requires a FIX-entered minimum quantity to be one round lot or a multiple thereof and rounds a mixed lot condition down, so a `MinQty` of 150 against a 100-share lot becomes 100 at the venue while your records still say 150.
5. Reject a `MinQty` or `MatchIncrement` above the routed quantity outright — no execution could satisfy it.
6. Remember that a partially executed Minimum Quantity order has its minimum reduced to the shares remaining; do not model the constraint as invariant across the order's life.

**Prevents:** the single most common defect in this area — copying the venue's minimum order size into Tag 110 on every order.

## 5. Assess fill likelihood without inventing data

1. Compare measured depth against `min_execution_quantity` when one is set, and against the routed quantity otherwise. A plain order can partially fill against thin depth; a `MinQty` order cannot.
2. Leave depth unset when it was not measured. A default depth makes the check pass and the report look clean.
3. Emit `MIN_QTY_DEPTH_UNSATISFIED` as a warning, not a terminal status — depth is a snapshot, and the correct response is usually to reconsider the constraint, not to cancel the order.

**Prevents:** a block order resting behind an unsatisfiable minimum, and false reassurance from a fabricated depth default.

## 6. Emit the audit report

1. One terminal `status`; every advisory finding in `warnings`. Never let a later step overwrite an earlier finding.
2. Carry `raw_quantity`, `rounded_quantity`, `quantity_delta`, `lot_size`, `venue_min_order_quantity`, the FIX tags actually populated, and `notional`, so a post-trade investigation can reconstruct why the routed quantity differs from the strategy's target.
3. Hand the result to the dispatch layer, which owns idempotency (`order-placement-idempotency`) and to the risk layer, which owns exposure limits. This engine sizes; it does not decide whether the position is allowed.
