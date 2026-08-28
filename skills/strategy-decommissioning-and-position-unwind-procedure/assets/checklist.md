# Pre-Flight / Sign-off Checklist — strategy-decommissioning-and-position-unwind-procedure

## Before initiation
- [ ] Strategy positions reconciled against the broker of record; the loaded inventory is the whole book.
- [ ] Every **working** order belonging to the strategy enumerated (resting limits, stops, GTCs, venue-side algo parents).
- [ ] ADV per symbol is current, not carried over from a calmer regime.
- [ ] `market_price` and `avg_daily_volume` are positive for every position; `max_adv_slice_pct` is inside `(0, 100]`.
- [ ] `lot_size` matches the instrument's real minimum increment.
- [ ] No leg of a hedged structure is being decommissioned in isolation.
- [ ] Decommissioning reason written down and passed to `initiate_decommissioning()` — it is the audit record.

## Entry block
- [ ] `assert_entry_allowed()` is wired into the strategy's entry path, not only into the engine's state.
- [ ] Exit and unwind order paths deliberately do **not** call the entry gate.
- [ ] `new_entries_allowed` verified `False` after initiation.

## Working orders
- [ ] All working order ids registered via `working_order_ids` at initiation.
- [ ] Each cancellation confirmed on the broker **acknowledgement**, never on the request.
- [ ] Any order that filled during the cancel window recorded as an execution, not discarded.
- [ ] `pending_order_cancellations()` is empty before sign-off.

## Liquidation waves
- [ ] Each wave is `<= max_adv_slice_pct` of ADV, and the cap has been calibrated per instrument (10% is a library default, not a rule).
- [ ] Long positions unwind via `SELL`, shorts via `BUY`.
- [ ] Each slice was handed to an execution algo — never sent as a single market order.
- [ ] No symbol was re-sliced while a previous wave was still outstanding.
- [ ] `report.unsliceable_symbols` checked every wave and escalated; no unwind loop runs unbounded.
- [ ] Dead child orders released with `cancel_slice()` before the symbol is re-sliced.

## Fill recording
- [ ] Every `record_slice_execution()` call carries the broker's `execution_id`.
- [ ] Partial fills recorded individually, not netted at end of day.
- [ ] Realized P&L sourced from the broker or the accounting system, not recomputed.
- [ ] No overfill was clamped; every flip was recorded as a `ReconciliationBreak`.

## Completion
- [ ] Every position flat within `QUANTITY_EPSILON`; no residual fractions left open.
- [ ] `open_slice_ids` empty.
- [ ] All reconciliation breaks investigated against broker records and resolved; `acknowledge_breaks=True` used only as a recorded human decision.
- [ ] `return_capital_to_treasury()` succeeded and state is `DECOMMISSION_COMPLETE`.
- [ ] Final report retained: initial notional, liquidated notional, realized P&L, breaks, audit trail.
- [ ] Post-unwind position reconciliation against the broker confirms flat independently of the engine.

## Testing
- [ ] `python -m unittest discover -s skills/strategy-decommissioning-and-position-unwind-procedure/scripts` — 100% pass rate.

## Sign-off

- Unwind executed by: ___________________________
- Reconciliation reviewed by: ___________________________
- Capital return approved by: ___________________________
- Date: ___________________________
