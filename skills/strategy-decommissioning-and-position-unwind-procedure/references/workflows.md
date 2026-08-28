# Workflows — strategy-decommissioning-and-position-unwind-procedure

Detailed procedure behind `SKILL.md`. The engine is a ledger and state machine; every step
below assumes a separate execution algo actually works the child orders.

## 0. Before initiating

1. Reconcile the strategy's positions against the broker of record. The engine trusts what
   you load; a position missing at load is a position that will never be unwound and will
   never appear in a reconciliation break.
2. Enumerate every **working** order belonging to the strategy — resting limits, stops,
   parked GTCs, algo parents on the venue. These are a separate exposure from the position.
3. Obtain a current ADV per symbol. A stale ADV from a calmer regime produces a wave the
   market can no longer absorb.
4. Confirm no leg of a hedged structure is being decommissioned in isolation.
5. Write the decommissioning reason down. It is a required, non-defaultable argument
   precisely so it lands in the audit trail as something a human decided.

## 1. Decommissioning initiation

```python
engine = StrategyDecommissioningEngine("STRAT_MOMENTUM_ALPHA")
engine.load_positions([
    StrategyPosition("AAPL", quantity=1000.0, market_price=150.0, avg_daily_volume=5000.0),
    StrategyPosition("MSFT", quantity=-500.0, market_price=300.0, avg_daily_volume=2000.0),
])
engine.initiate_decommissioning(
    reason="IR 0.31 below the 0.50 floor for 3 consecutive months; committee vote 2026-08-14",
    working_order_ids=["ORD-8831", "ORD-8832"],
)
```

State becomes `ORDER_ENTRY_BLOCKED`, `new_entries_allowed` becomes `False`, and
`initial_total_notional_usd` is marked once at 300,000 (1,000 × 150 + 500 × 300). It does not
move again for the life of the unwind.

Wire the gate into the strategy:

```python
def on_signal(signal):
    engine.assert_entry_allowed(signal.symbol)   # raises EntryBlockedError once retired
    ...
```

Exits and unwind orders are not entries and must not call this gate.

## 2. Working-order cancellation

Send the cancels through the broker, then confirm each one **on the acknowledgement, not on
the request**:

```python
for order_id in ("ORD-8831", "ORD-8832"):
    broker.cancel(order_id)
    ack = broker.await_cancel_ack(order_id)
    if ack.status == "CANCELLED":
        engine.record_order_cancellation(order_id)
    elif ack.status == "FILLED":
        engine.record_slice_execution("ORD-" + order_id, ack.symbol, ack.qty, ack.price,
                                      ack.realized_pnl, execution_id=ack.exec_id)
```

An order can fill in the gap between the cancel request and the acknowledgement — that fill
changes the inventory and must be recorded, not discarded. `record_order_cancellation()`
rejects an id that was not registered at initiation, so a stray confirmation cannot create a
false audit record. Until every id is confirmed, the engine will not promote to
`FULLY_UNWOUND`, even with a flat book.

## 3. Liquidation wave generation

```python
report = engine.generate_unwind_liquidation_slices()
```

Per symbol with a non-flat position and no outstanding slice:

```
cap        = (max_adv_slice_pct / 100) * avg_daily_volume
if |qty| <= cap:                       # final wave
    slice  = |qty|                     # odd lot included on purpose
    final  = True
else:
    slice  = floor(cap / lot_size) * lot_size
    final  = False
    if slice == 0: symbol -> unsliceable_symbols, no wave emitted
side       = SELL if qty > 0 else BUY
```

Worked example, first wave of the book above:

| Symbol | Position | ADV | Cap (10%) | Wave | Side | `remaining_after_slice_quantity` | Final? |
|---|---|---|---|---|---|---|---|
| AAPL | +1,000 | 5,000 | 500 | 500 | SELL | +500 | no |
| MSFT | −500 | 2,000 | 200 | 200 | BUY | −300 | no |

`remaining_after_slice_quantity` is a **projection** — what the position becomes if the wave
fills completely. It is not the current inventory; read `engine.positions[symbol].quantity`
for that.

Route each slice to an execution algo. The wave quantity is a ceiling for the whole wave, not
an order to be sent in one clip.

Three lists in the report must be read before looping again:

- `open_slice_ids` — waves authorised and not yet fully filled. Their symbols are skipped by
  the next generation call.
- `unsliceable_symbols` — participation cap below one lot. These positions never shrink on
  their own; escalate to a block trade, a wider cap, or manual liquidation.
- `reconciliation_breaks` — see §5.

If a child order dies unfilled or partially filled, release it so the symbol can be
re-sliced next wave:

```python
engine.cancel_slice(slice_id, reason="venue halt, unfilled remainder pulled")
```

Any quantity that filled before the cancel must already have been recorded.

## 4. Fill recording

```python
engine.record_slice_execution(
    slice_id=child.slice_id,
    symbol=child.symbol,
    executed_qty=fill.quantity,        # unsigned, positive, the amount that reduced the position
    executed_price=fill.price,
    realized_pnl=fill.realized_pnl,    # from the broker / accounting system
    execution_id=fill.exec_id,         # idempotency key - always supply it
)
```

- Partial fills are cumulative: the slice stays open until filled quantity reaches the wave
  size, then closes automatically and frees the symbol for the next wave.
- `execution_id` is the only duplicate-fill defence. Fill webhooks retry; a replay without an
  id decrements the position twice and the engine will report the book flat while half of it
  is still open. Omitting it produces a warning, not an error, because a manual entry from a
  trade blotter is a legitimate case — but it is never the default path.
- A fill against an unknown `slice_id` is accepted with a warning and treated as an
  out-of-band liquidation, because a manual flatten outside the engine is real and the
  inventory must reflect it.
- A fill for a symbol not in the inventory raises. That is an attribution error: someone must
  determine which strategy owns it before it is booked anywhere.

## 5. Reconciliation breaks

An execution larger than the remaining position is applied truthfully:

```
AAPL +1,000, fill 1,500  ->  position -500, ReconciliationBreak raised
```

The engine does not clamp to zero. A clamp would report a closed book while an unintended,
unhedged short sits in the market. The consequences are deliberate:

- The position is non-flat, so `FULLY_UNWOUND` is unreachable.
- The next `generate_unwind_liquidation_slices()` emits a `BUY` wave to close the flip.
- `return_capital_to_treasury()` refuses while any break is unacknowledged.

Investigate the break against the broker's records. Only once it is genuinely resolved should
someone pass `acknowledge_breaks=True` — that records the human decision; it does not remove
the break from the report.

## 6. Completion and treasury return

```python
final = engine.return_capital_to_treasury()
```

The call raises unless **all** of the following hold:

1. Every loaded position is flat within `QUANTITY_EPSILON`.
2. No authorised slice is still open.
3. Every working order registered at initiation is confirmed cancelled.
4. No unacknowledged reconciliation break exists.

The error message names which gate failed, including the residual quantities. State becomes
`DECOMMISSION_COMPLETE`; further fills are rejected as reconciliation incidents rather than
routine executions, and capital returns cannot be repeated.

## 7. Reporting semantics

| Field | Meaning |
|---|---|
| `initial_total_notional_usd` | Marked once, at load time, from loaded quantities × loaded prices. Never moves. |
| `remaining_notional_usd` | Current open quantities × loaded prices. |
| `liquidated_notional_usd` | Cumulative `executed_qty × executed_price` of **recorded fills**. Never the notional of slices merely authorised. |
| `total_realized_pnl_usd` | Sum of caller-supplied realized P&L. |
| `new_entries_allowed` | Derived from state: `True` only while `ACTIVE`. |

`initial ≠ remaining + liquidated` in general, and that is correct: remaining is marked at
the load price while liquidated is booked at actual fill prices. The difference is execution
performance, not an accounting error. Attribute it with
`transaction-cost-analysis-tca-integration`.

`engine.audit_trail` holds `(UTC ISO-8601 timestamp, note)` for every state change, fill,
cancellation and break — the record RTS 6 Art. 17(3) reconciliation depends on.
