# Workflows for Conditional Order Logic

1. **Trigger Definition**:
   - Construct atomic conditions (e.g., `PriceCondition(symbol='AAPL', field='last', op='>=', target=150.0)`).
   - Combine with `AndCondition([cond1, cond2])` or `OrCondition([cond3, cond4])`.
2. **Order Registration**:
   - Pair condition tree with child order: `ConditionalOrderTrigger(parent_id, condition_tree, child_order)`.
3. **Tick Ingestion & Evaluation**:
   - Ingest `market_state` dictionary: `{symbol: {field: value}}`.
   - Call `trigger.evaluate(market_state)`.
4. **Execution Dispatch**:
   - If `TRUE` and state is `DORMANT`: Update state to `TRIGGERED` and emit `child_order`.
