# Pre-Flight Checklist

## Sizing correctness

- [ ] Does `kelly_fraction` actually change deployed capital? Run the same portfolio at 1.0, 0.5 and 0.25 — if the allocations are identical, the fraction is being normalised away and the over-leverage safeguard does not exist.
- [ ] Is the Kelly multiplier at or below 0.5 (Half-Kelly), and 0.25 for volatile books?
- [ ] Is the allocation a pure function of the inputs? Shuffle the strategy dict and confirm the targets are unchanged.
- [ ] Does the sum of targets ever exceed total fund capital? It must not.
- [ ] Is any strategy funded above `min(max_capacity, fund × fractional Kelly)`? It must not be.

## Inputs

- [ ] Are statistics computed from **closed** trades only, over a fixed trailing window?
- [ ] Is the trade sample large enough that the win-rate standard error is small relative to the edge?
- [ ] Is `max_capacity` sourced from liquidity analysis rather than inferred from performance?
- [ ] Are NaN, negative capital, and out-of-range win rates rejected at the boundary rather than silently sized on?

## Constraints and behaviour

- [ ] Is there a hard capacity ceiling on **every** strategy, with no performance-based override path?
- [ ] Is undeployed capital held as cash rather than redistributed to fill the fund?
- [ ] Have you confirmed there is no minimum-allocation floor forcing capital into edgeless strategies?
- [ ] Are reallocation intervals long enough (weekly/monthly) to measure edge rather than variance?

## Downstream

- [ ] Does the OMS treat a negative delta as a buying-power reduction (block new entries, let positions exit naturally) rather than a forced liquidation?
- [ ] Are drawdown circuit breakers and kill switches implemented independently of this engine, not derived from it?
- [ ] Is live cross-strategy correlation reviewed on the same cadence, given that summed independent Kelly fractions overstate safe leverage for correlated strategies?
- [ ] Is every reallocation logged (targets, deltas, deployed vs. cash, gross Kelly demand) for post-hoc audit?
