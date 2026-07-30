---
name: ftx-style-exchange-post-collapse-risk-lessons
description: >-
  Crypto counterparty risk engine implementing post-FTX collapse risk frameworks, auditing Proof of Reserves (PoR), native token collateral concentration, off-exchange settlement (OES), and automated venue de-risking.
domain: Crypto Custody & Security
subdomain: Exchange Counterparty & Solvency Risk
tags: ["ftx-collapse-lessons", "proof-of-reserves", "off-exchange-settlement", "counterparty-risk", "crypto-custody", "exchange-solvency", "de-risking"]
brokers_frameworks: ["Merkle Tree PoR", "Copper ClearLoop", "Fireblocks OES", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in crypto quantitative funds, custodial risk management platforms, and exchange counterparty monitoring systems. The 2022 collapse of FTX highlighted catastrophic vulnerabilities in centralized exchanges: customer asset commingling, unbacked exchange token collateral (FTT), lack of Proof of Reserves (PoR), and un-segregated affiliate trading (Alameda Research). This module enforces post-collapse risk frameworks, auditing Merkle-tree PoR coverage, native token exposure caps ($< 5\%$), Off-Exchange Settlement (OES) adoption, and triggering automated venue de-risking.

## Prerequisites

- Exchange solvency metrics (`por_coverage_ratio`, `native_token_collateral_ratio`, `uses_off_exchange_settlement`, `exchange_nav_exposure_pct`).
- Counterparty risk limits (max 20% NAV per exchange, max 5% native token ratio, min 100% PoR).

## Workflow

1. **Proof of Reserves (PoR) & Liability Audit**:
   - Verify Merkle-tree PoR ratio = Total Verifiable On-Chain Assets / Total Client Liabilities ($\ge 1.00$).
2. **Native Token & Affiliate Concentration Audit**:
   - Audit exchange native token exposure ($R_{\text{native}} < 5.0\%$).
3. **Off-Exchange Settlement (OES) Verification**:
   - Verify if exchange supports tri-party off-exchange settlement (Copper ClearLoop, Fireblocks OES) so capital remains in bankruptcy-remote custody.
4. **Venue De-Risking Protocol**:
   - Compute Exchange Risk Index Score $R_{\text{venue}} \in [0, 100]$.
   - If $R_{\text{venue}} > 50$ OR PoR $< 100\%$ OR Native Token Ratio $> 10\% \implies$ Trigger `EXCHANGE_DERISK_TRIGGERED` (Initiate immediate withdrawal to cold storage).
   - Else $\implies$ Flag `VENUE_RISK_ACCEPTABLE`.
5. **Audit Report Generation**: Output structured `ExchangePostCollapseRiskReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Proof of Reserves with Solvency Audits**: Accepting a simple wallet snapshot as PoR without auditing Proof of Liabilities (total client deposits).
- **Concentrating Margin in Exchange Native Tokens**: Holding $> 20\%$ of collateral in exchange utility tokens, risking total loss if the token illiquidity spiral occurs.
- **Keeping 100% Trading Capital on Exchange Hot Wallets**: Failing to adopt Off-Exchange Settlement (OES) solutions, exposing total fund NAV to exchange bankruptcy.

## Verification

- Instantiate `ExchangePostCollapseRiskEngine`. Audit Venue A (PoR = 105%, Native Token = 2%, OES Enabled, NAV = 15%) $\implies$ verify engine outputs `VENUE_RISK_ACCEPTABLE` (Risk Score = 10). Audit Venue B (PoR = 85%, Native Token = 35%, OES Disabled, NAV = 40%) $\implies$ verify engine triggers `EXCHANGE_DERISK_TRIGGERED` (Risk Score = 85) and recommends emergency capital withdrawal.
- Run `python scripts/test_ftx_style_exchange_post_collapse_risk_lessons.py`.

## Related Skills

- `exchange-proof-of-reserves-verification`
- `hot-cold-wallet-split-for-trading-bots`
---
