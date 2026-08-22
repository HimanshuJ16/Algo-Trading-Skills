# Workflows for Conditional Order Logic

1. **Confirm simulation is necessary**:
   - Check whether the broker or venue supports the trigger natively (`broker-order-type-capability-matrix`). A broker-resident trigger survives a client outage; this engine does not.
   - Simulate only the conditions native order types cannot express (cross-asset, mixed price/volume/time trees).

2. **Trigger Definition**:
   - Construct atomic conditions, naming the trigger price type explicitly:
     `PriceCondition(symbol='AAPL', field='last', operator='>=', target_value=150.0)`.
   - Add staleness enforcement wherever the input can go quiet:
     `PriceCondition('SPY', 'last', '>=', 500.0, max_quote_age_seconds=5.0)`.
   - For a relative-value trigger use `CrossAssetCondition('ES', 'last', '>=', 'SPY', 'last', ratio=10.0, offset=2.0)`.
   - For a wall-clock gate use `TimeCondition('>=', datetime(2026, 1, 2, 15, 50, tzinfo=ZoneInfo('America/New_York')))` — a naive datetime is rejected.
   - Combine with `AndCondition([...])`, `OrCondition([...])`, `NotCondition(cond)`. Empty composites raise.
   - `'=='` requires an explicit `tolerance`; exact float equality on a price feed is not a usable predicate.

3. **Order Registration**:
   - Build a validated payload: `ChildOrderPayload('AAPL', 'BUY', 100, 'LIMIT', 150.10)` — quantity, side, order type and limit price are checked at construction, not at fire time.
   - Pair it with the tree: `ConditionalOrderTrigger('TRIG_1', tree, payload)`.
   - Register with the engine, using an OCO group for bracket legs:
     `engine.register(trigger, oco_group='AAPL_BRACKET')`. Duplicate `trigger_id`s are rejected.

4. **Tick Ingestion & Evaluation**:
   - Market state is `{symbol: {field: value, 'timestamp': epoch_seconds_utc}}`.
   - Call `released = engine.process_tick(market_state)`, or pass an explicit `now` for deterministic replay/backtests. The engine pins one clock per tick.
   - `trigger.condition_tree.evaluate_tristate(market_state, now)` returns `True` / `False` / `None`; `evaluate(...)` is the fail-safe boolean projection (UNKNOWN → `False`).

5. **Execution Dispatch**:
   - `process_tick` returns the payloads released by this tick, in registration order. Each fired trigger has transitioned `DORMANT` → `TRIGGERED` exactly once, under a lock.
   - Dormant siblings in the same OCO group are cancelled before the next tick.
   - Hand each payload to pre-trade risk control, then to the router. A fired trigger is an intent, not a fill.

6. **Cancellation & Monitoring**:
   - `engine.cancel(trigger_id)` (or `trigger.cancel()`) cancels a dormant trigger and returns `False` if it has already fired — the race between a cancel request and a fire resolves one way only.
   - Alert on trees stuck at UNKNOWN: that is a dead feed, and the trigger will not fire when the level trades.
