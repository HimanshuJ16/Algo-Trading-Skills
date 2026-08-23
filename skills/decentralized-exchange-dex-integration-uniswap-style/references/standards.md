# Standards for Decentralized Exchange (DEX) Integration Uniswap Style

## Engineering standards

| Metric | Engineering Standard |
|---|---|
| Constant Product Precision | Swap outputs MUST be calculated with the venue's own constant-product formula. Any value submitted on-chain (notably `amountOutMin`) MUST be derived with uint256 floor division on raw base units, not from float reserves. |
| Input Preconditions | `amountIn > 0` and `reserveIn > 0 && reserveOut > 0` MUST be enforced before quoting, matching `UniswapV2Library`'s own `require` statements. |
| Token Orientation | The request's token pair MUST be validated against the pool's orientation. Direction MUST NOT be inferred. |
| Unit Discipline | Fee and tolerance parameters are fractions (`0.003` == 0.30%). Out-of-range tolerances MUST be rejected, never silently applied. |
| Output Floor Enforcement | `amountOutMin` MUST be checked against the realized/quoted output at execution time, not merely computed. |
| Numerical Fidelity | Prices and amounts MUST NOT be rounded to fixed decimals inside the calculation path — inverted-scale pairs and 18-decimal tokens both lose material precision. |

## Protocol facts (verified)

| Fact | Source |
|---|---|
| `getAmountOut(amountIn, reserveIn, reserveOut)` = `amountIn*997*reserveOut / (reserveIn*1000 + amountIn*997)`, with `require(amountIn > 0, 'INSUFFICIENT_INPUT_AMOUNT')` and `require(reserveIn > 0 && reserveOut > 0, 'INSUFFICIENT_LIQUIDITY')`. Integer floor division. | [UniswapV2Library.sol](https://github.com/Uniswap/v2-periphery/blob/master/contracts/libraries/UniswapV2Library.sol) |
| Router enforces `require(amounts[amounts.length - 1] >= amountOutMin, 'UniswapV2Router: INSUFFICIENT_OUTPUT_AMOUNT')` and `modifier ensure(uint deadline) { require(deadline >= block.timestamp, 'UniswapV2Router: EXPIRED'); }` — the deadline is an absolute timestamp compared against **chain** time. | [UniswapV2Router02.sol](https://github.com/Uniswap/v2-periphery/blob/master/contracts/UniswapV2Router02.sol) |
| Price impact = `(midPrice * inputAmount - outputAmount) / (midPrice * inputAmount)`. Because `outputAmount` is net of the LP fee, this canonical measure is fee-inclusive. Mid price is defined as the rate of a theoretical infinitesimal trade. | [`computePriceImpact`, @uniswap/sdk-core](https://github.com/Uniswap/sdk-core/blob/main/src/utils/computePriceImpact.ts); [v2 SDK pricing guide](https://developers.uniswap.org/docs/sdks/v2/guides/pricing) |
| Uniswap v3 offers 1%, 0.30%, 0.05% and 0.01% fee tiers, uses **virtual** reserves, and behaves like `x*y=k` only between adjacent initialised ticks — liquidity changes when a swap crosses a tick. | [Uniswap v3 concentrated liquidity docs](https://developers.uniswap.org/docs/get-started/concepts/liquidity-providers/concentrated-liquidity); [v3 whitepaper](https://app.uniswap.org/whitepaper-v3.pdf) |
| PancakeSwap Exchange v2 charges a fixed **0.25%** swap fee (9975/10000), of which 0.17% goes to LPs — not the 0.30% of canonical Uniswap v2. | [PancakeSwap fees docs](https://docs.pancakeswap.finance/trade/pancakeswap-exchange/fees-and-routes) |
| Flashbots Protect RPC routes transactions to a private mempool "hidden from frontrunning and sandwich bots", and transactions are "only included in the block if they do not revert", so users "do not pay fees for failed transactions". Ethereum-specific — it does not cover swaps on BNB Chain or other L1/L2s. | [Flashbots Protect overview](https://docs.flashbots.net/flashbots-protect/overview) |

## Risk guidance (not a rule — calibrate and document your own)

Neither Uniswap nor any regulator publishes a mandatory slippage cap or a notional
threshold above which private relays are required. The following are engineering
recommendations with stated rationale, not standards:

- Keep `max_slippage_pct` as tight as the pair's volatility allows. A wide tolerance does
  not prevent reverts, it defines how much value a sandwich bot may extract — the
  attacker's profit is bounded by the gap between your quote and your `amountOutMin`.
- Prefer a private relay (e.g. Flashbots Protect on Ethereum) for any swap whose
  extractable value exceeds the searcher's gas cost, rather than applying a fixed notional
  threshold — profitability, not size, determines whether you are a target.
- Size relative to reserves, not notional: impact is driven by $\Delta x / x$.
