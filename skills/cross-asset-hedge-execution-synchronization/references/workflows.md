# Workflows for Cross-Asset Hedge Execution Synchronization

1. **Fill Event Ingestion**:
   - Ingest each primary fill ($Q_{primary}, t_{primary\_fill}$) as its own event, carrying a stable unique `fill_id`.
   - Validate `strategy_id`, symbol, and quantity (finite, non-zero) before any order is generated. Reject a hedge quantity that rounds to zero instead of emitting a zero-quantity order.
   - Decision point: `generate_hedge_order` raises if `fill_id` was already hedged — live or finalized. That is a redelivery (gateway replay, FIX `PossResend`), not a new fill. Reconcile against the OMS to establish whether the hedge is already working; do not catch the error and re-dispatch, which would both discard the live hedge's accumulated fill state and put a duplicate hedge on the market.
2. **Hedge Order Generation**:
   - Calculate $\text{Hedge Qty} = -1.0 \times Q_{primary} \times \text{Hedge Ratio}$, where the ratio includes the contract multiplier (e.g. 0.50 delta × 100 shares/contract = 50.0).
   - One hedge order per primary fill event — never batch waiting for the primary order to complete.
3. **Execution Routing & Dispatch Marking**:
   - Immediately dispatch the hedge order to the execution venue.
   - Record the dispatch timestamp via `mark_dispatched(hedge_order_id, dispatch_timestamp_ms)`; the returned dispatch latency is audited against `max_sync_delay_ms` independently of fill latency. A duplicate dispatch with a different timestamp raises — resolve it against the OMS before resubmitting.
4. **Incremental Fill Processing**:
   - Call `process_hedge_fill()` once per hedge fill callback. Quantities accumulate; a partial fill returns `PARTIALLY_FILLED` with `unhedged_exposure_qty` set to the residual and the order remaining pending.
   - Reject wrong-side fills (sign opposite to the target) — they mean position books disagree and must be investigated, not retried.
   - Final state on completion: `SYNCHRONIZED_OK` (within SLA) or `SYNC_DELAY_BREACH` (completed late — audit flag; the gateway should aggressively reprice any remaining quote).
5. **Timeout Enforcement Loop**:
   - Drive `enforce_unhedged_timeouts(now_ms)` from a periodic timer (event-loop tick or scheduling thread), not from fill callbacks.
   - Any hedge still incomplete `unhedged_timeout_ms` after its primary fill is flagged `UNHEDGED_TIMEOUT_UNWIND`, removed from pending tracking, and handed to the unwind callback so the primary leg is flattened.
   - A fill arriving after the timeout routes to the same unwind callback rather than merely returning the flag. A late *partial* fill finalizes the order, so the sweep will not revisit it — if that path did not unwind, the residual exposure would leave tracking with the primary leg unprotected.
   - Do not re-submit a timed-out hedge without first cancelling/reconciling against the venue — the original order may still be live and a blind re-send doubles the hedge.
   - If the unwind callback itself fails, the sweep logs critical and continues — escalate manually, since the primary leg is now unprotected.
   - The sweep may run on a different thread from the fill callbacks: hedge state is lock-guarded, and the unwind callback is invoked outside that lock so a blocking handler cannot stall fill processing or deadlock against a handler that re-enters the synchronizer.
