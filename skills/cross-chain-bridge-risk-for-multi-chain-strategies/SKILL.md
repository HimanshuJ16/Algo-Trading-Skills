---
name: cross-chain-bridge-risk-for-multi-chain-strategies
description: Quantitative crypto risk management module for evaluating cross-chain
  bridge security, wrapped token de-pegging, finality latency SLAs, and enforcing
  in-flight capital caps.
domain: Crypto Risk & DeFi
subdomain: Multi-Chain Bridge Risk
tags:
- cross-chain
- bridge-risk
- de-peg
- wrapped-token
- finality-latency
- in-flight-caps
- stargate
- wormhole
brokers_frameworks:
- DeFi Protocols
- Python Dataclasses
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing multi-chain DeFi arbitrage algorithms, cross-chain yield farming strategies, or multi-chain liquidity rebalancing pipelines. Cross-chain bridges (Stargate, Wormhole, LayerZero, Arbitrum Canonical Bridge) carry unique smart contract, validator multisig, and wrapped token de-pegging risks. This module monitors wrapped asset parity ($|P_{\text{wrapped}} - P_{\text{native}}| / P_{\text{native}}$), tracks finality delays, and enforces strict in-flight capital caps per bridge protocol ($\le 15\%$ NAV).

## Prerequisites

- Real-time prices for native assets ($P_{\text{native}}$) and bridge-wrapped assets ($P_{\text{wrapped}}$).
- Bridge protocol registry with attributes: `finality_delay_minutes`, `max_nav_pct_cap`, `tvl_usd`, `audit_score`.

## Workflow

1. **Bridge Protocol Audit**: Ingest bridge parameters and calculate current in-flight capital.
2. **Wrapped Token De-Peg Audit**:
   - $\text{Depeg Pct} = \left|\frac{P_{\text{wrapped}} - P_{\text{native}}}{P_{\text{native}}}\right| \times 100\%$.
   - If $\text{Depeg Pct} \ge 1.0\% \implies$ Trigger `DEPEG_ALERT` and block new bridge transfers.
3. **Pre-Transfer Risk Evaluation**:
   - For proposed transfer of amount $V$:
   - Check if $\frac{\text{Current In-Flight} + V}{\text{NAV}} > \text{Max Bridge NAV Cap}$.
   - Check if $\text{Finality Delay} > \text{Max Allowed SLA Delay}$.
4. **Bridge Routing & Failover**:
   - Route to lowest-risk bridge meeting finality and capital cap constraints.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Single Bridge Concentration**: Concentrating 80% of fund capital in a single bridge contract, exposing the fund to total loss upon smart contract exploit (e.g. Wormhole, Ronin, Nomad hacks).
- **Ignoring Optimistic Rollup Withdrawal Delays**: Assuming Arbitrum/Optimism canonical bridge transfers settle instantly, getting capital locked in 7-day challenge periods.
- **Unmonitored Wrapped Asset De-pegs**: Continuing to bridge assets into a wrapped token that is experiencing liquidity drain and de-pegging from native backing.

## Verification

- Instantiate `CrossChainBridgeRiskManager`. Register `Bridge_Alpha` (Limit 15% NAV, Finality 15 mins) and `Bridge_Beta` (Limit 15% NAV, Finality 10,080 mins / 7 days). Set native ETH = $3,000 and wrapped wETH = $2,910 (3.0% de-peg). Verify manager blocks transfers on `Bridge_Alpha` due to de-peg alert. Correct price to $2,995 (0.17% de-peg) and test order routing approval.
- Run `python scripts/test_cross_chain_bridge_risk_for_multi_chain_strategies.py`.

## Related Skills

- `cross-chain-address-reuse-privacy-risk`
- `smart-contract-audit-requirements-before-defi-integration`
---
