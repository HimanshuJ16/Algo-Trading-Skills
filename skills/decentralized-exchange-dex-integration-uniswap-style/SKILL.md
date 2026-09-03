---
name: decentralized-exchange-dex-integration-uniswap-style
description: >-
  Use before submitting a swap to a Uniswap v2 style constant-product pool, to compute
  output from x*y=k, price impact, the amountOutMin slippage floor and the router
  deadline. Concentrated-liquidity v3 and v4 pools behave differently.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: uniswap, amm, constant-product, price-impact, slippage-tolerance, mev-protection, dex-trading
  brokers_frameworks: "{'Uniswap v2 (and x*y=k forks': 'Sushiswap, PancakeSwap v2)'}; web3.py; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill in DeFi algorithmic trading bots, DEX-CEX arbitrage algorithms, and yield routers swapping tokens on Uniswap v2, Sushiswap, PancakeSwap v2, or other constant-product AMM forks. Unlike traditional order-book exchanges, AMM execution follows the Constant Product Formula ($x \cdot y = k$): large trades shift pool reserves, creating **Price Impact**, and any transaction sitting in a public mempool is exposed to **MEV sandwich attacks**. This engine quotes the swap, measures impact, and derives the two on-chain safeguards — `amountOutMin` and `deadline` — before anything is signed.

## When NOT to Use

- **Uniswap v3 / v4 concentrated-liquidity pools.** v3 behaves like $x \cdot y = k$ only *between adjacent initialised ticks*, and on **virtual** reserves; crossing a tick changes in-range liquidity. Applying this formula to a v3 pool's token balances produces materially wrong output. Use the v3 Quoter contract instead.
- **Stableswap / weighted pools** (Curve, Balancer) — different invariants entirely.
- **As an execution client.** This engine signs nothing and broadcasts nothing. It models the swap against in-memory reserves; the router recomputes output against live reserves at inclusion time.
- **To derive a byte-exact `amountOutMin` from float reserves.** The contract uses uint256 floor division. Use `get_amount_out_integer()` on raw base units (wei) for any value that must survive an on-chain comparison.
- **Fee-on-transfer or rebasing tokens.** The amount the pair receives differs from the amount sent, which is why the router exposes separate `...SupportingFeeOnTransferTokens` entry points.

## Prerequisites

- AMM pool reserve state ($x$: `reserve_in`, $y$: `reserve_out`, `fee_pct`). **`fee_pct` is a fraction, not a percent**: `0.003` == 0.30%. Fee is per-venue — Uniswap v2 is 0.30% (997/1000), PancakeSwap v2 is 0.25% (9975/10000).
- Trade parameters (`token_in`, `token_out`, `amount_in`, `max_slippage_pct`, `max_price_impact_pct`, `deadline_seconds`). **Both tolerances are fractions**: `0.005` == 0.50%, `0.05` == 5.0%. Values outside the permitted range are rejected rather than silently applied.

## Workflow

1. **Spot Price & Swap Output Calculation**:
   - $\text{Spot (mid) Price} = \frac{y}{x}$ — the rate of a theoretical infinitesimal trade.
   - Net input: $\Delta x_{\text{net}} = \Delta x \times (1 - \text{fee})$.
   - Output: $\Delta y = \frac{y \cdot \Delta x_{\text{net}}}{x + \Delta x_{\text{net}}}$.
   - **Decision point — the request's token symbols must match the pool's orientation.** Do not infer direction: a reversed request computed against the pool's orientation produces a wrong-direction trade whose report still echoes the symbols you sent. Register the reversed pool explicitly.
2. **Price Impact Audit**:
   - $\text{Execution Price} = \frac{\Delta y}{\Delta x}$; $\text{Price Impact \%} = \left(1 - \frac{\text{Execution Price}}{\text{Spot Price}}\right) \times 100$.
   - This is Uniswap's canonical `computePriceImpact` and is **fee-inclusive**: as $\Delta x \to 0$ the impact tends to the pool fee, not to zero.
   - **Decision point — calibrate the ceiling to the fee tier.** On a 1% pool a dust trade already reports 1% impact, consuming most of a 5% ceiling before size matters. Use the separately reported `reserve_shift_impact_pct` = $\frac{\Delta x}{x + \Delta x} \times 100$ (the fee-excluded component) when you want to bound size alone.
3. **Slippage & MEV Safeguards**:
   - $\Delta y_{\text{min}} = \Delta y \times (1 - \text{max\_slippage\_pct})$ — this is what the router enforces via `require(amounts[last] >= amountOutMin, 'INSUFFICIENT_OUTPUT_AMOUNT')`, and it is the actual defence against a sandwich attack.
   - $t_{\text{deadline}} = t_{\text{current}} + \text{deadline\_seconds}$, an **absolute** unix timestamp checked by the router's `require(deadline >= block.timestamp, 'EXPIRED')`. A deadline bounds how long a pending transaction can sit before executing at a stale price; **it does not prevent sandwiching**.
   - **Decision point — a wide tolerance is not a way to stop reverts.** Widening `max_slippage_pct` to make a failing swap succeed hands the difference to a sandwich bot. Reduce size or route privately instead.
4. **Audit Report Generation**: Output structured `UniswapSwapExecutionReport`. Every failed gate is listed in `rejection_reasons`, not just the first.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Passing `0.5` to mean "0.5%"**: every tolerance in this module is a fraction despite the `_pct` suffix, so `0.5` authorises a **50%** worse fill — an open invitation to a sandwich bot. The engine now rejects tolerances outside the permitted range instead of applying them.
- **Submitting a negative or zero `amount_in`**: `UniswapV2Library` reverts with `INSUFFICIENT_INPUT_AMOUNT`; an unvalidated model instead produces a negative output and moves reserves the wrong way, corrupting every subsequent quote.
- **Treating the quote as a guarantee**: the router recomputes output against live reserves at inclusion time. The quote is a prediction; `amountOutMin` is the only guarantee. Compute the floor, then actually check the realized output against it.
- **Mistaking the deadline for MEV protection**: a deadline guards against stale execution, not against being sandwiched. Sandwich resistance comes from a tight `amountOutMin` plus keeping the transaction out of the public mempool.
- **Applying v2 math to a v3 pool**: concentrated liquidity invalidates $x \cdot y = k$ across tick boundaries; the resulting quote can be far from executable.
- **Rounding prices to fixed decimals**: a pair quoted at inverted scale (spot $\approx 0.000333$) rounds to `0.0003` at 4 dp — a ~10% error — and 6-dp amount rounding is meaningless for 18-decimal tokens. Round only at the display boundary.
- **Ignoring Price Impact on Large Swaps**: swapping large sizes relative to reserves incurs severe impact; impact grows with $\frac{\Delta x}{x}$, not with notional value.
- **Infinite Approval Allowance Risks**: granting un-capped `uint256` token approvals to un-verified DEX contracts.

## Verification

- Instantiate `UniswapDexIntegrationEngine`. Set up an ETH/USDC pool ($x = 1{,}000$ ETH, $y = 3{,}000{,}000$ USDC, spot = \$3,000/ETH, `fee_pct` = 0.003). Execute a swap of 10 ETH. Verify $\Delta y = 29{,}614.741032$ USDC (from `getAmountOut`: $\frac{10 \cdot 997 \cdot 3{,}000{,}000}{1000 \cdot 1000 + 10 \cdot 997}$), execution price $2{,}961.4741032$, fee-inclusive price impact $1.28419656\%$, fee-excluded reserve-shift impact $0.990099\%$, and $\Delta y_{\text{min}} = 29{,}466.667$ at 0.50% max slippage.
- Negative checks: `amount_in` of `0` or `-100` must raise `INSUFFICIENT_INPUT_AMOUNT`; a `USDC -> ETH` request against an `ETH -> USDC` pool must raise `TOKEN_ORIENTATION_MISMATCH`; `max_slippage_pct=0.5` must raise; a realized output below `min_amount_out` must reject with `INSUFFICIENT_OUTPUT_AMOUNT`.
- Run `python -m unittest discover -s skills/decentralized-exchange-dex-integration-uniswap-style/scripts`.

## Related Skills

- `cross-chain-address-reuse-privacy-risk`
- `smart-contract-approval-scope-minimization`
- `smart-contract-audit-requirements-before-defi-integration`
