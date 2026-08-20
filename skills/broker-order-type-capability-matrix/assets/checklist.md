# Pre-Flight / Sign-off Checklist — broker-order-type-capability-matrix

Use this before considering the skill's implementation complete.

## Capability registry

- [ ] **Profiles verified, not inherited.** Every broker profile in use has been
      checked against that broker's current API documentation *for the asset class and
      account you actually trade* — not accepted from `DEFAULT_CAPABILITIES` as-is.
- [ ] **Bounded "native" support recorded.** Where a native type carries limits
      (Binance TWAP: Algo endpoints only, duration and notional bands; IBKR TWAP/VWAP:
      documented for US equities; Zerodha iceberg: 2–50 legs), the limit is written
      down and enforced by the caller — the profile only records that the type exists.
- [ ] **Flags consistent with order types.** No profile sets a `supports_*` boolean
      that contradicts `native_order_types` (construction raises if it does).
- [ ] **Allow-list registries start empty.** Anything building its own registry passes
      an explicit `custom_matrix`, and has confirmed that `{}` resolves no brokers.

## Planning

- [ ] **Validation runs on both paths.** A malformed bracket is rejected when the
      broker supports it natively, not only when it is emulated.
- [ ] **Price geometry understood per type.** Callers know `action` is the entry side
      for `BRACKET` and the exit side for `OCO`, and that the two require opposite
      price orderings for the same `action` value.
- [ ] **Non-emulatable types handled.** Requests for VWAP / PEGGED / TRAILING_STOP /
      MOC at a broker lacking them are caught and routed to a real alternative, not
      retried or silently downgraded.
- [ ] **Venue floors supplied.** `min_slice_qty` is passed for every sliced order type,
      and the schedule is rounded to the instrument's quantity step before dispatch.

## Execution

- [ ] **Quantity conservation asserted end-to-end.** An integration test submits a
      sliced plan against a simulator and confirms the total executed quantity equals
      the requested quantity — not requested + one slice.
- [ ] **`primary_order_type is None` handled.** The dispatcher fires nothing for an
      emulated OCO instead of dereferencing the field.
- [ ] **Idempotent primary submission.** The primary order carries a client order ID
      and a timed-out submission is reconciled, never blindly retried.
- [ ] **Sibling cancellation is confirmed, not assumed.** When one leg of a mutually
      exclusive pair triggers, the cancel of the other is verified against the venue
      before the position is treated as flat.
- [ ] **Bracket exits armed only on primary fill.** Legs carrying
      `activate_on = PRIMARY_FILL` are not registered against an unfilled entry.

## State

- [ ] **Emulated legs persisted before submission.** `plan.to_dict()` (or equivalent)
      is durably written *before* the primary order goes out.
- [ ] **Restart drill run.** The EMS has been killed mid-order in a test environment
      and demonstrated to reload and re-arm every emulated leg.

## Testing

- [ ] **Unit suite green.**
      `python -m unittest discover -s skills/broker-order-type-capability-matrix/scripts`

## Sign-off

- Quant Engineer: ___________________________
- Code Reviewer: ___________________________
- Date: ___________________________
