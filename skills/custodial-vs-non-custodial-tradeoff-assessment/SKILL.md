---
name: custodial-vs-non-custodial-tradeoff-assessment
description: Quantitative institutional framework for evaluating trade-offs between
  Custodial CEX, Off-Exchange Settlement (Fireblocks/Copper), and Non-Custodial DEX
  architectures based on latency, gas, and counterparty risk.
domain: Crypto Custody & Security
subdomain: Custody Architecture Design
tags:
- crypto-custody
- cex-vs-dex
- non-custodial
- off-exchange-settlement
- fireblocks
- counterparty-risk
- mpc-wallet
brokers_frameworks:
- Fireblocks MPC
- Copper ClearLoop
- Python Dataclasses
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing institutional crypto trading infrastructure, fund treasury management, or choosing between Centralized Exchanges (CEX), Off-Exchange Settlement networks (Fireblocks, Copper ClearLoop), and Decentralized Exchanges (DEX). CEX architectures offer ultra-low latency ($\le 1\text{ ms}$) and high liquidity but expose funds to exchange counterparty default risk (e.g. FTX, Celsius). Non-custodial DEX architectures provide key self-sovereignty but incur gas volatility, MEV sandwich risks, and block inclusion delays.

## Prerequisites

- Strategy requirements: `required_latency_ms`, `monthly_volume_usd`, `max_counterparty_risk_pct`, `gas_sensitivity`.
- Custody model profiles: `CUSTODIAL_CEX`, `HYBRID_OFF_EXCHANGE_SETTLEMENT`, `NON_CUSTODIAL_DEX`.

## Workflow

1. **Strategy Metric Ingestion**: Ingest strategy latency budget, trading frequency, and counterparty risk limits.
2. **Architecture Trade-Off Scoring**:
   - Compute **Counterparty Risk Score**: Penalize `CUSTODIAL_CEX` if strategy counterparty limit $< 20\%$.
   - Compute **Latency Score**: Penalize `NON_CUSTODIAL_DEX` if required latency $\le 100\text{ ms}$.
   - Compute **Gas & Slippage Score**: Penalize `NON_CUSTODIAL_DEX` for high-frequency trading.
3. **Suitability Ranking**:
   - Rank architectures by composite score ($0.0$ to $100.0$).
4. **Recommendation Output**: Output structured `CustodyTradeoffReport` with architecture selection and risk mitigations.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Un-hedged CEX Exchange Risk**: Storing $100\%$ of fund AUM directly in CEX hot wallets without leveraging Off-Exchange Settlement (ClearLoop/Fireblocks).
- **Attempting HFT on Non-Custodial DEXs**: Running sub-second arbitrage algorithms directly on Ethereum L1, incurring astronomical gas fees and MEV front-running.
- **Ignoring MPC Key Sharding**: Assuming self-custody requires storing a single un-encrypted private key on a server rather than using MPC (Multi-Party Computation) key sharding.

## Verification

- Instantiate `CustodialTradeoffAssessorEngine`. Evaluate an HFT Market Making strategy (Latency budget $1\text{ ms}$, Volume \$50M/month). Verify engine recommends `CUSTODIAL_CEX` or `HYBRID_OFF_EXCHANGE_SETTLEMENT`. Evaluate a Long-Term Treasury strategy (Latency budget $10,000\text{ ms}$, Zero counterparty tolerance). Verify engine recommends `NON_CUSTODIAL_DEX` or MPC Self-Custody.
- Run `python scripts/test_custodial_vs_non_custodial_tradeoff_assessment.py`.

## Related Skills

- `hot-cold-wallet-split-for-trading-bots`
- `multi-party-computation-mpc-custody-solutions`
---
