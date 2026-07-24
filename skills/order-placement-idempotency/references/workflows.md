# Deep Workflow Reference — order-placement-idempotency

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Pre-Network Write-Ahead Intent Logging:**
   - Generate a deterministic 24-character SHA-256 idempotency key via `make_idempotency_key()`.
   - Write `PENDING` intent record to `OrderLedger` SQLite database BEFORE issuing network HTTP requests.

2. **Distinct Outcome Handling (3-Branch Protocol):**
   - **Confirmed Success:** Update ledger status to `PLACED` with returned `broker_order_id`.
   - **Confirmed Failure:** Update ledger status to `REJECTED` with explicit rejection reason.
   - **Network Timeout / Exception:** Mark ledger status `UNKNOWN` and invoke broker order book reconciliation.

3. **Broker Order Book Reconciliation:**
   - On `UNKNOWN` status, query broker order book via `_reconcile_unknown()` matching `client_order_id` or `(symbol, side, quantity, price)` tuple before deciding whether to retry or link existing orders.

4. **Startup Crash Recovery:**
   - Sweep all `PENDING` and `UNKNOWN` ledger entries at bot startup to reconcile in-flight orders from prior crashes before generating new trading signals.

## Failure Modes Observed in Production

- **Blind Retries on Timeout:** Retrying orders on network timeouts without reconciling against broker order books, resulting in double fills.
- **Post-Network Intent Logging:** Recording order intent after making HTTP calls, losing intent records when process crashes mid-network call.
- **In-Memory-Only Order Tracking:** Keeping order IDs exclusively in RAM, losing state on process restarts.
- **Unscoped Idempotency Keys:** Reusing short or non-unique idempotency keys across trading sessions.

## Production Implementation Reference

- Reference code: `scripts/order_ledger.py` (`OrderLedger`, `IdempotentOrderRouter`, `OrderIntentStatus`).
- Automated unit tests: `scripts/test_order_ledger.py`.
