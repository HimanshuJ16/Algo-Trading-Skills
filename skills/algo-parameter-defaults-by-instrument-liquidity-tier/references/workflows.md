# Workflows for Algo Parameter Defaults

## Execution Pipeline Architecture

1. **Pre-Trade Analysis**: Before submitting an order to the execution EMS, the algorithm calculates the 30-day Average Daily Volume (ADV) of the target instrument.
2. **Tier Lookup**: The ADV is passed to the `ExecutionParameterManager` to request an `ExecutionProfile`.
3. **Execution Configuration**:
   - The EMS engine dynamically configures its child-order slicer based on the returned profile.
   - Example: A highly illiquid asset receives an `IS` (Implementation Shortfall) profile with `cross_spread_allowed=False`. The slicer will refuse to send Market orders, strictly using limit orders pegged 20 bps deep.
4. **Walk-Forward Tuning**: Every 30 days, the ADV thresholds and `passive_buffer_bps` parameters are swept against actual post-trade slippage (TCA) to retune the tier definitions.