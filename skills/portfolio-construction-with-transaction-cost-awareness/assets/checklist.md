# Pre-Flight Checklist

## Inputs
- [ ] Are weights expressed as fractions (`0.40` = 40%), never percentages?
- [ ] Are `expected_return` values stated over the **same horizon** as the rebalance cost?
- [ ] Are non-finite weights and returns rejected before pricing, rather than propagating to a NaN net return?
- [ ] Are duplicate symbols netted upstream?

## Band and cost model
- [ ] Are No-Trade Buffer Bands applied to suppress micro-rebalancing trades?
- [ ] Is the inclusive band boundary understood (exactly at threshold means suppress), including that the comparison is float-tolerant so representation error cannot leak a boundary trade through?
- [ ] Is the band policy chosen deliberately — snap to target, or trade to the band edge (the proportional-cost optimum)?
- [ ] Are linear commissions and bid-ask spreads modeled, with their inconsistent units (`commission_rate` decimal, `spread_cost_bps` bps) read correctly?
- [ ] Is quadratic market impact included, and is `impact_coeff` **calibrated to your own realized slippage** rather than left at the placeholder default?
- [ ] Is it understood that quadratic impact is a tractability approximation and that empirical impact is concave (square-root law)?
- [ ] Are costs charged on the **executed** delta, not the proposed one?

## Audits before routing
- [ ] Is net expected return calculated on **final** weights after deducting total transaction cost?
- [ ] Is the turnover convention explicit (two-way L1 vs one-way half-sum), and does the limit apply to the right one?
- [ ] Is `turnover_limit_breached` gated by the caller, given that the engine returns the plan unclamped?
- [ ] Is `is_self_financing` checked, and is the cash funding leg routed when `net_weight_change` is non-zero?
- [ ] Is `ENGINE_DISABLED` handled as "no plan produced", not as "no trade needed"?

## Scope
- [ ] Is it understood that this engine performs **no optimization** — `target_weight` is an input from an upstream allocator?
- [ ] Are share-level concerns (lot sizing, tick rounding, minimum notional) and execution scheduling handled elsewhere?
