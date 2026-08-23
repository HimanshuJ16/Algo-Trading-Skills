# Workflows for Decentralized Exchange (DEX) Integration Uniswap Style

1. **Pool Reserve & Fee Parsing**:
   - Query pool reserves $x, y$ and the venue's fee. The fee is per-venue and per-tier, not
     a constant: Uniswap v2 = 30 bps (997/1000), PancakeSwap v2 = 25 bps (9975/10000).
   - Reject non-positive reserves up front (`INSUFFICIENT_LIQUIDITY`).
   - Confirm the pool is a constant-product pool. Concentrated-liquidity (v3/v4),
     stableswap, and weighted pools use different invariants and must not be quoted here.

2. **Swap Output & Price Impact Calculation**:
   - $\Delta y = \frac{y \cdot \gamma \cdot \Delta x}{x + \gamma \cdot \Delta x}$, where $\gamma = 1 - \text{fee}$.
   - Enforce $\Delta x > 0$ before dividing (`INSUFFICIENT_INPUT_AMOUNT`): a zero input is a
     division by zero, and a negative input silently reverses the trade.
   - Validate the request's token pair against the pool's orientation. Never infer direction.
   - $\text{Price Impact \%} = (1 - \frac{\Delta y / \Delta x}{y / x}) \times 100$ — canonical and
     fee-inclusive. Report $\frac{\Delta x}{x + \Delta x} \times 100$ alongside it as the
     fee-excluded component, so ceilings can be calibrated independently of the fee tier.
   - Do not round inside this path. Round at the display boundary only.

3. **Slippage & Deadline Construction**:
   - $\text{amountOutMin} = \Delta y \times (1 - \text{slippage})$. Derive the on-chain value with
     integer floor division on base units so the floor is actually attainable.
   - $t_{\text{deadline}} = t_{\text{now}} + \text{deadline\_seconds}$, in absolute unix seconds. The router
     compares it against `block.timestamp`, so local clock skew shortens the real window —
     do not set it so tight that a normal inclusion delay reverts the swap.
   - Treat `amountOutMin` as the sandwich defence and the deadline as the staleness guard.
     They are not interchangeable.

4. **Pre-Submission Gate**:
   - Reject on price impact above the ceiling, and on a realized or re-quoted output below
     `amountOutMin`. Evaluate every gate and report all failures, so an operator does not
     fix one rejection only to hit the next.
   - Do not widen slippage to clear a rejection; reduce size or route privately.

5. **Transaction Execution** (outside this engine):
   - Dispatch via the venue's router, preferring a private relay where one exists for the
     chain. Re-quote against live reserves immediately before signing — the quote computed
     here is a prediction, not a guarantee.
