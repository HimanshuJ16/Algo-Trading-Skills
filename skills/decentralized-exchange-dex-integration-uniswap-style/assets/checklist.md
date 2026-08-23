# Pre-Flight Checklist

## Pool & inputs
- [ ] Is the pool actually a constant-product ($x \cdot y = k$) pool, not v3/v4 concentrated liquidity, stableswap, or a weighted pool?
- [ ] Are reserves ($x, y$) positive and the venue's true fee used (Uniswap v2 30 bps, PancakeSwap v2 25 bps)?
- [ ] Is `fee_pct` expressed as a fraction (`0.003` == 0.30%)?
- [ ] Is `amount_in` strictly positive (`INSUFFICIENT_INPUT_AMOUNT` otherwise)?
- [ ] Do the request's token symbols match the pool's orientation, rather than being inferred?

## Pricing
- [ ] Is price impact calculated and audited before execution?
- [ ] Is the impact ceiling calibrated to the pool's fee tier (a 1% tier reports 1% impact even on a dust trade)?
- [ ] Are prices and amounts left unrounded inside the calculation path?
- [ ] For any value submitted on-chain, is it derived with integer floor division on base units?

## Safeguards
- [ ] Is `amountOutMin` enforced against the realized output, not merely computed?
- [ ] Are slippage and impact tolerances within the permitted fraction range (no `0.5` meaning "0.5%")?
- [ ] Is the deadline an absolute timestamp with enough room for normal inclusion delay?
- [ ] Is it understood that the deadline guards staleness while `amountOutMin` guards sandwiching?
- [ ] Is a private relay used where the chain offers one and the swap is worth attacking?
- [ ] Are token approvals scoped rather than un-capped `uint256`?
