# Workflows for Cboe Complex Order Integration

## End-to-End Multileg Execution Lifecycle

```
[Strategy Generator]
        │
        ▼
[1. Pre-Trade Risk Validation (SEC Rule 15c3-5)]
   ├── Max order value / aggregate exposure check
   ├── Leg count bounds (2 <= N <= 16; <= 100 only for C1 floor-routed / FLEX)
   ├── Reg SHO locate for a short equity leg
   └── Conforming stock-option ratio (smallest option leg vs stock leg, <= 8:1)
        │
        ▼
[2. Ratio Normalization]
   ├── Compute GCD(r_1 ... r_n)
   ├── Reduce leg ratios: r_i' = r_i / GCD
   ├── Scale package quantity: Qty' = Qty * GCD
   └── Re-check Qty' <= 999,999 AND (C2/EDGX) max(r') <= 3 * min(r')
        │
        ▼
[3. Pricing & Routing Designation]
   ├── Long-form net price: positive = debit, negative = credit, 0 = even
   ├── Whole pennies for option-only spreads (4 dp only with a stock leg / FLEX)
   └── RoutingInst (9303): [B|P|D] + [S = expose via COA | L = no COA]
        │
        ▼
[4. FIX Serialization (MsgType=AB, long form)]
   ├── Envelope: 35=AB, 1, 11, 60, 167=MLEG, 38, 40, 44, 9303, 47, 59
   └── Repeating group: 555=N, then per leg 654, 600, 608, 611, 612, 623, 624, 564
        │
        ▼
[5. Venue Matching (COA -> COB, or straight to COB when 9303 2nd char = L)]
        │
        ▼
[6. Execution Report Ingestion (MsgType=8)]
   ├── Package fill: 442=3, 167=MLEG -> LastPx (31), LastShares (32), CumQty (14)
   ├── Leg fills:    442=2, 167=OPT/EQ -> LegRefID (654), LastPx (31), LastShares (32)
   └── Reconcile: leg_qty == package_qty * reduced_leg_ratio, per LegRefID
```

---

## Detailed Step-by-Step Procedures

### Phase 1: Strategy Formulation & Pre-Trade Risk Filtering
1. **Define the package**: bull call spread, calendar, iron condor, buy-write, etc.
2. **Choose the request form.** If you are pricing against an already-listed COB
   strategy symbol, that is a *short form* request: `Symbol (55)` carries the COB
   strategy symbol, `Side (54)` carries the package direction, and **the price
   sign inverts on Sell orders**. Otherwise use the *long form* and describe the
   legs. Mixing the two — sending a root underlying in `Symbol (55)` alongside a
   leg group — matches neither form.
3. **Apply SEC Rule 15c3-5 filters**: aggregate gross exposure against firm
   limits; leg count within the venue's ceiling; reject any 1-leg order with an
   instruction to route via `MsgType=D`.
4. **Confirm venue capability**: an equity leg is supported on **C1 and EDGX
   only**, and at most one per order.

### Phase 2: Ratio Normalization
1. Extract the target contract quantities per leg, $q_1 \dots q_k$.
2. Compute $\gcd = \gcd(q_1, \dots, q_k)$.
3. Reduce: $\text{LegRatioQty}_i = q_i / \gcd$.
4. Scale the package: $\text{OrderQty} = \text{TargetPackages} \times \gcd$.
5. **Re-validate after scaling**, not before:
   - `OrderQty (38)` must remain $\le 999{,}999$. GCD scaling is a multiplication,
     so a request that looked in-bounds can exit the accepted range.
   - On **C2 and EDGX**, the *reduced* ratios must satisfy
     $\max(r') \le 3 \times \min(r')$.
6. *Example*: 50 packages of Buy 10 / Sell 20 SPX calls. $\gcd(10,20)=10$;
   reduced ratios $1:2$; `OrderQty (38)` $= 50 \times 10 = 500$. Result: 500
   bought, 1,000 sold — exposure preserved exactly.

### Phase 3: Stock-Option Conformance Validation
1. Mark the equity leg with `LegCFICode (608) = E`; option legs with `OC` / `OP`.
2. Compute the conforming ratio using the **smallest** option leg:
   $$\text{Ratio} = \frac{\min_i(\text{OptionLegRatio}_i \times \text{Multiplier}_i)}{\sum \text{StockLegShares}} \le 8.0$$
   Using the sum of all option legs instead of the smallest leg over-rejects
   legitimate multi-option-leg packages such as collars.
3. A non-conforming order is not necessarily invalid — it receives different
   priority and auction handling. Decide deliberately whether to reject it or to
   submit it knowing the handling differs.
4. For a short equity leg, set `LegSide (624) = 5` (Sell Short) or `6` (Sell
   Short Exempt) and confirm the Regulation SHO locate before submission.

### Phase 4: Pricing & Routing Tagging
1. **Net price (long form)**: positive = debit, negative = credit, `0` = even.
   Under the *short form* the sign is read against `Side (54)`: on a Sell order a
   positive price is a **credit**.
2. **Precision**: whole pennies for option-only spreads; up to 4 decimal places
   only for spreads with a stock leg and for FLEX. Format from `Decimal`, never
   from a binary float — `0.1 + 0.2` serializes as `0.30000000000000004`.
3. **Increments**: most classes trade the net price in $0.01. **SPX/SPXW is an
   exception** at $0.05 for non-box/roll spreads. Verify the increment for the
   class before pricing; it is not enforced by the helper script.
4. **Routing**: `RoutingInst (9303)` second character `S` exposes the order to the
   COA, `L` suppresses it. Leave the field unset to accept the Cboe defaults
   (`S` for non-IOC, `L` for IOC). `PS` is not supported. `ExecInst (18)` plays no
   part in auction selection — its only documented value on this message is `G`
   (All or None).

### Phase 5: FIX Serialization & Dispatch
1. Emit `167=MLEG` and `47` (OrderCapacity) — both are required and are easy to
   forget when adapting a single-order builder.
2. Start every repeated leg group with `LegRefID (654)`; keep the leg IDs to five
   alphanumeric-or-space characters and unique within the order, because they are
   the only key that ties leg fills back to legs.
3. When `LegSymbol (600)` is an OSI root, include `608`, `611` and `612`.
4. Populate `564` per leg unless `OrderCapacity (47)` is `M` or `N`.
5. Use SOH (`0x01`) as the field delimiter on the wire. `BeginString (8)`,
   `BodyLength (9)` and `CheckSum (10)` are the FIX engine's responsibility.

### Phase 6: Execution Report Handling & Reconciliation
1. Route each `35=8` by `MultilegReportingType (442)`:
   - `3` — complex package fill. Take `LastShares (32)`, `LastPx (31)`,
     `CumQty (14)`, `LeavesQty (151)`, `AvgPx (6)`. The `555` group here echoes
     the order's leg definitions; it carries **no fill data**.
   - `2` — individual leg fill. `LegRefID (654)`, `LastPx (31)` and
     `LastShares (32)` are top-level fields of that message.
   - `1` — single-leg instrument fill (not part of a complex trade).
2. Join leg reports to the package by `LegRefID (654)` and assert
   `leg_filled_quantity == package_filled_quantity × reduced_leg_ratio`.
   Aggregate multiple leg reports for the same `LegRefID` before comparing —
   one leg can fill in several prints.
3. Treat a broken invariant as a position-integrity incident: stop submitting
   further orders in that underlying and reconcile against the clearing firm
   before resuming. Do **not** resubmit the package.
4. Only after reconciliation succeeds, update portfolio Greeks and margin.

### Phase 7: Timeout and Ambiguous-State Handling
1. A missing or late Execution Report does **not** mean the order was not
   accepted. Never resubmit the same package with a new `ClOrdId` to "retry".
2. Resolve state by querying the drop copy / order state, or by sending an
   `Order Cancel Request` for the original `ClOrdId` and acting on its outcome.
   Cboe enforces `ClOrdId` uniqueness only among *live* orders, so a reused ID is
   not a reliable duplicate guard once an order is no longer live.
