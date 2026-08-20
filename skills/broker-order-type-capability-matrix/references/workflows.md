# Deep Workflow Reference — broker-order-type-capability-matrix

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

### 1. Register broker capability profiles

- `DEFAULT_CAPABILITIES` ships profiles for IBKR, Alpaca, Zerodha and Binance Spot,
  each sourced in `references/standards.md`. **Verify them against your own account's
  entitlements before trading them** — support varies by asset class, product and API
  surface, and the defaults describe a documented API, not your permissions.
- `matrix.register_broker(BrokerCapabilities(...))` adds or overrides a profile.
- The `supports_*` booleans are a *view* of `native_order_types`, not an independent
  switch. A profile where the two disagree raises at construction, so a broker that
  claims `supports_oco=True` without `OrderType.OCO` cannot reach order time.
- `BrokerOrderCapabilityMatrix(custom_matrix={})` gives you a genuinely empty registry
  (every broker then raises), which is the right starting point for an allow-list.

### 2. Plan before dispatch

- Call `matrix.plan_order_execution(...)`. It validates *before* deciding how to
  route, so a malformed order is refused whether or not the broker is native:
  - `quantity` and every price must be positive and finite; `0` is a rejection, not a
    missing value.
  - `BRACKET` needs at least one exit price; `OCO` needs both.
  - Exit prices must sit on the correct side (see *Price geometry* below).
  - `ICEBERG` needs a `price`.
  - Arguments the requested type does not consume are refused by name. A
    `stop_loss_price` quietly dropped from a MARKET order leaves the caller
    believing the position is protected while nothing is watching it.
- The returned `SynthesizedOrderPlan` says whether to route natively or synthesize.

### 3. Execute the plan — the contract

Follow it literally. The two ways to get this wrong both over-execute:

1. If `plan.has_primary_order`, submit `primary_quantity` of `primary_order_type` at
   `primary_price` (`None` means market) **exactly once**, with a client order ID —
   see `order-placement-idempotency`.
2. Register every entry in `plan.emulated_legs` with the EMS.
3. **Do not re-slice the parent quantity.** `primary_quantity` plus the quantity of
   every scheduling leg already equals the requested quantity. A feeder leg's
   `quantity` is what remains *after* the primary order, and its
   `metadata["slice_schedule"]` is the exact remaining slices, summing without
   residue.
4. `primary_order_type is None` (emulated OCO) means **fire nothing now**. Both legs
   are conditional.

### 4. Run the legs in the EMS

- **Price-triggered legs** (`STOP_LOSS`, `TAKE_PROFIT`, `LIMIT_PROFIT`): watch Level 1
  quotes and fire `action` when `trigger_price` is crossed or `limit_price` is
  marketable.
  - `metadata["activate_on"] == "PRIMARY_FILL"` means the leg must **not** be armed
    until the entry actually fills. Arming a bracket's exits against an unfilled entry
    is how a rejected entry becomes an unintended short.
  - `metadata["mutually_exclusive"]` means firing one leg obliges you to cancel its
    sibling — and to treat the cancel as a *request*, not a fact, until the venue
    confirms it.
- **Time-triggered feeders** (`TWAP_FEEDER`): slice *k* fires at
  `k * interval_seconds` from submission. The primary slice is `k = 0`, so the first
  EMS-managed slice fires at `metadata["first_slice_offset_seconds"]` and the last one
  `metadata["effective_span_seconds"]` after submission — one interval before the
  requested window closes, since each slice owns the interval it starts.
  `interval_seconds` is floored to whole seconds; compare
  `metadata["effective_span_seconds"]` against
  `metadata["requested_duration_seconds"]` if the difference matters.
- **Fill-triggered feeders** (`SLICE_FEEDER`): replenish the next slice when the
  resting slice completes (`metadata["replenish_on"] == "SLICE_FILL"`), not on a
  timer. That is what makes it an iceberg rather than a TWAP.

### 5. Round to the venue before submitting

The planner does not know the instrument's quantity step or minimum size, so it does
not round. Pass `min_slice_qty` to have a schedule *rejected* when its slices fall
below the venue floor, and round the schedule to the step with
`minimum-fill-size-and-lot-rounding-logic` before dispatch. Rounding after slicing can
break the exact-sum property — reconcile the final slice against the parent quantity.

### 6. Persist emulated state

`plan.to_dict()` and `leg.to_dict()` render the plan as JSON with Decimals as strings.
Write it down **before** submitting the primary order. An EMS that restarts without its
emulated legs does not fail loudly; it simply never fires the stop loss, and the
position sits unprotected until someone notices.

## Price geometry

`action` means different things to the two multi-leg types, which is the single
easiest thing to get backwards:

| Requested type | `action` is… | Exit leg sides | Long-side geometry |
|---|---|---|---|
| `BRACKET` | the **entry** side | inverted from `action` | BUY entry -> stop below, target above |
| `OCO` | the side of **both exit legs** | same as `action` | SELL exits -> target above, stop below |

So `BRACKET`+`BUY` and `OCO`+`BUY` require *opposite* price orderings: the first is a
long entry with the target above, the second closes a short with the target below.
This mirrors the constraint Binance documents for its native OCO — same side on both
legs, take-profit above the last price for a SELL pair and below it for a BUY pair —
and Alpaca's description of OCO as "two orders with the same side".

Prices that violate the geometry are rejected rather than accepted, because legs
placed on the wrong side of the market are already through their triggers when the EMS
registers them: the "protective" stop executes instantly at a loss.

## Production Implementation Reference

- Reference code: `scripts/capability_matrix.py` — `BrokerOrderCapabilityMatrix`,
  `BrokerCapabilities`, `SynthesizedOrderPlan`, `EmulatedLeg`,
  `EMULATABLE_ORDER_TYPES`.
- Automated unit tests: `scripts/test_capability_matrix.py`.
- Broker evidence and sources: `references/standards.md`.
