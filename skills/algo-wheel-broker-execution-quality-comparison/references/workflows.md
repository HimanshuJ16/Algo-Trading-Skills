# Workflows for Algo Wheel Broker Comparison

## Systematic Broker Evaluation Loop

1. **Trade Execution**: During the trading day, the `AlgoWheel` routes orders to Broker A, B, and C based on their current percentage allocations (e.g., 60%, 30%, 10%).
2. **Data Capture**: Every execution logs the `decision_price` (the mid-price at the exact millisecond the algorithm decided to trade) and the `fill_price`.
3. **End of Day (EOD) Batch**: The `AlgoWheelEvaluator` runs over all $T+0$ executions.
4. **TCA Ranking**: The engine calculates the average Implementation Shortfall (IS) in basis points for each broker.
5. **Wheel Rotation**: The engine outputs the new target allocations for $T+1$. If the broker in the 10% canary slot outperforms the primary broker, the allocations flip dynamically, ensuring the fund is constantly optimizing for best execution.
