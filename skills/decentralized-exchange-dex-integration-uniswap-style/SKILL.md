---
name: decentralized-exchange-dex-integration-uniswap-style
description: Quantitative AMM DEX integration engine for Uniswap v2/v3 style pools,
  calculating constant product swap outputs (x*y=k), price impact, slippage tolerance,
  and MEV deadline protections.
domain: Decentralized Finance (DeFi) & DEX
subdomain: AMM Trading & Execution
tags:
- uniswap
- amm
- constant-product
- price-impact
- slippage-tolerance
- mev-protection
- dex-trading
brokers_frameworks:
- Uniswap v2/v3
- web3.py
- Python Dataclasses
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in DeFi algorithmic trading bots, DEX-CEX arbitrage algorithms, and yield routers swapping tokens on Uniswap v2/v3, Sushiswap, or PancakeSwap AMM pools. Unlike traditional order-book exchanges, AMM DEX execution relies on the Constant Product Formula ($x \cdot y = k$). Large trade sizes shift the pool reserves, creating significant **Price Impact** and exposing transactions to **MEV Sandwich Attacks** in public mempools.

## Prerequisites

- AMM Pool reserve state ($x$: Reserve In, $y$: Reserve Out, `fee_pct` e.g. 0.30%).
- Trade parameters (`token_in`, `token_out`, `amount_in`, `max_slippage_pct` e.g. 0.50%, `deadline_seconds` e.g. 60s).

## Workflow

1. **Spot Price & Swap Output Calculation**:
   - $\text{Spot Price} = \frac{y}{x}$.
   - Net Input: $\Delta x_{\text{net}} = \Delta x \times (1.0 - \text{fee})$.
   - Output Amount: $\Delta y = \frac{y \cdot \Delta x_{\text{net}}}{x + \Delta x_{\text{net}}}$.
2. **Price Impact Audit**:
   - $\text{Execution Price} = \frac{\Delta y}{\Delta x}$.
   - $\text{Price Impact \%} = \left(1.0 - \frac{\text{Execution Price}}{\text{Spot Price}}\right) \times 100\%$.
3. **Slippage & MEV Safeguards**:
   - Minimum Amount Out: $\Delta y_{\text{min}} = \Delta y \times (1.0 - \text{max\_slippage\_pct})$.
   - Set execution deadline timestamp: $t_{\text{deadline}} = t_{\text{current}} + \text{deadline\_seconds}$.
4. **Audit Report Generation**: Output structured `UniswapSwapExecutionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Price Impact on Large Swaps**: Swapping large order sizes relative to pool reserves, incurring severe $> 10\%$ price impact losses.
- **Un-capped Slippage Tolerances**: Setting max slippage to $5.0\%$, inviting MEV searchers to front-run and sandwich the transaction in the mempool.
- **Infinite Approval Allowance Risks**: Granting un-capped `uint256` token approvals to un-verified DEX contracts.

## Verification

- Instantiate `UniswapDexIntegrationEngine`. Set up ETH/USDC pool ($x = 1,000$ ETH, $y = 3,000,000$ USDC, Spot = \$3,000/ETH, fee = 0.30%). Execute a swap of 10 ETH. Verify engine calculates output $\Delta y \approx 29,435.30$ USDC, price impact $\approx 1.88\%$, and establishes minimum output threshold $\Delta y_{\text{min}}$ at 0.5% max slippage.
- Run `python scripts/test_decentralized_exchange_dex_integration_uniswap_style.py`.

## Related Skills

- `cross-chain-address-reuse-privacy-risk`
- `smart-contract-approval-scope-minimization`
---
