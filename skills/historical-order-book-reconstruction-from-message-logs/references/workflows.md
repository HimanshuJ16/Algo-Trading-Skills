# Workflows — historical-order-book-reconstruction-from-message-logs

## 1. Establish the source feed's semantics before writing any mapping

Answer these against the feed's own specification, not by analogy:

1. **Is there a separate total-deletion message?** ITCH has `D` alongside the partial
   cancel `X`; LOBSTER has type 3 alongside type 2. If you map both onto one type you
   must invent a share count for the deletion, which corrupts the book.
2. **Does a cancel/execute carry a delta or an absolute remaining size?** ITCH carries a
   *deduction* and effects are cumulative. Treating a deduction as an absolute size (or
   vice versa) fails silently on the second modify for the same order.
3. **Does a replace mint a new order id?** ITCH `U` does, and all later updates use it.
4. **Does a replace carry the side?** ITCH `U` does not — inherit it from the original.
5. **What is the price precision?** Set `price_scale` to match (10,000 for ITCH/LOBSTER).
6. **Which messages must be excluded?** Hidden executions, cross prints and halts do not
   touch a resting displayed order.
7. **Are prices already divided by the scale?** Passing raw wire integers through yields a
   book 10,000x too high that still looks internally consistent; set `max_price` to catch it.

## 2. Configure the engine

```python
engine = HistoricalOrderBookReconstructEngine(
    symbol="AAPL",
    price_scale=10_000,   # ITCH Price (4) / LOBSTER dollar*10000
    strict=False,         # True to fail fast on the first integrity violation
    max_price=200_000.0,  # optional: Nasdaq's documented max Price (4); catches
                          # raw wire integers passed through unscaled
)
```

Choose `strict` deliberately:

| Context | Setting | Why |
|---|---|---|
| Validated production replay pipeline | `strict=True` | A gap means the output is invalid; fail the run rather than emit a wrong book. |
| Exploratory replay of an imperfect archive | `strict=False` | Survey how bad the log is, then decide; counts are exact either way. |

## 3. Replay in chronological order

```python
for msg in ordered_message_log:
    engine.process_l3_message(msg)
    snapshot = engine.get_l2_reconstructed_snapshot(top_n_levels=5)
    ...  # consume snapshot before advancing — this is what avoids lookahead bias
```

Build messages with the named constructors so the per-type field meaning is explicit:

```python
L3OrderMessage.add("ID_1", "BUY", 100.00, 10, ts)      # new resting order
L3OrderMessage.cancel("ID_1", 4, ts)                   # deduct 4 shares
L3OrderMessage.delete("ID_1", ts)                      # remove whole order
L3OrderMessage.execute("ID_1", 6, ts)                  # deduct 6 shares
L3OrderMessage.replace("OLD", "NEW", 99.50, 12, ts)    # absolute new size at new id
```

The original positional form `L3OrderMessage(order_id, msg_type, side, price, quantity,
timestamp_nanos)` still works unchanged.

## 4. State transitions applied per message type

| Type | Order map | Level aggregate | On failure to match |
|---|---|---|---|
| `ADD` | insert | `+qty`, `+1` order | duplicate id → `DUPLICATE_ORDER_ID`, stale order superseded |
| `CANCEL` | `qty -= n`; remove at zero | `-n` (order count unchanged unless removed) | missing → `UNKNOWN_ORDER`; `n >` remaining → `OVER_CANCEL`, order removed |
| `EXECUTE` | `qty -= n`; remove at zero | `-n` | missing → `UNKNOWN_ORDER`; `n >` remaining → `OVER_EXECUTE`, order removed |
| `DELETE` | remove | `-qty`, `-1` order | missing → `UNKNOWN_ORDER` |
| `REPLACE` | remove original, insert under `new_order_id`, **side inherited** | old level `-qty/-1`, new level `+qty/+1` | original missing → `UNKNOWN_ORDER`, **nothing created** |

A price level is deleted from the aggregate once its order count reaches zero, so it never
appears as a phantom empty level in a snapshot.

## 5. Audit the replay before using its output

```python
report = engine.get_l2_reconstructed_snapshot(top_n_levels=10)

if report.integrity_violation_count:
    # The reconstructed book diverged from the message log. Any microstructure
    # statistic computed from this replay is contaminated.
    raise RuntimeError(f"replay not clean: {report.integrity_violations_by_kind}")

if report.is_crossed_book or report.is_locked_book:
    # mid_price and spread are populated but not meaningful; a crossed book
    # yields a negative spread.
    ...
```

`engine.violations` holds the first `max_retained_violations` records (id, timestamp, kind,
detail) for triage; `violations_by_kind` counts are exact and uncapped.

## 6. Interpreting each violation kind

| Kind | Most likely cause | Response |
|---|---|---|
| `UNKNOWN_ORDER` | The `ADD` was dropped, or the log was sliced mid-session so the order rests from before the window. | Check the log's start boundary first — a burst at the start of a replay is usually a windowing artefact, not corruption. |
| `DUPLICATE_ORDER_ID` | A `DELETE`/`EXECUTE` was dropped, leaving a stale order live. | Investigate the gap; the supersede is a heuristic recovery. |
| `OVER_CANCEL` / `OVER_EXECUTE` | An earlier partial modify for the same order was dropped, so the tracked size is too large. | Definitionally impossible in a well-formed feed — treat as hard evidence of message loss. |
| `TIMESTAMP_REGRESSION` | The log was merged from multiple sources, or sorted on the wrong key. | Re-sort and re-run; the book is untrustworthy from that point onward. |

## 7. Performance note

Snapshotting after every message is the workload this engine is built for, and it is the
one where a naive implementation collapses. Re-aggregating every live order per snapshot
costs O(N) per message; maintaining the aggregate incrementally costs O(1) per message plus
O(L log n) at snapshot time over the *distinct price levels* L.

Measured on this implementation, 20,000 resting orders across 802 distinct price levels
(401 bid, 401 ask):

| Workload | Rebuild-per-snapshot | Incremental |
|---|---|---|
| Ingest only, no snapshots | 0.014 s | 0.051 s |
| Snapshot after every message | 52.7 s | 0.68 s |

The incremental engine pays ~3.4× on pure ingest (input validation and tick conversion) and
wins ~78× on the intended workload, with the gap widening as the book grows. If you truly
only ingest and snapshot rarely, that trade-off is worth knowing about.
