---
name: crypto-transaction-tax-lot-tracking
description: >-
  Quantitative crypto tax accounting engine for tracking tax lots across crypto-to-crypto swaps, DEX trades, gas fee deductions, and enforcing HIFO/FIFO tax optimization for IRS Form 8949 reporting.
domain: Tax Accounting & Optimization
subdomain: Crypto Tax Accounting
tags: ["crypto-tax", "tax-lot-tracking", "crypto-to-crypto-swap", "hifo", "fifo", "gas-fee-deduction", "form-8949"]
brokers_frameworks: ["IRS Form 8949", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in crypto algorithmic trading bots, treasury management engines, and fund accounting platforms to track crypto tax lots and calculate capital gains/losses. Unlike traditional equities, **Crypto-to-Crypto Swaps** (e.g. BTC $\to$ ETH or ETH $\to$ USDC) and **Gas Fee Dispositions** (paying gas in ETH/SOL) are taxable events under IRS rules. This module calculates USD Fair Market Value (FMV), incorporates gas fee adjustments, and supports HIFO, FIFO, and LIFO tax lot matching.

## Prerequisites

- Active crypto tax lot inventory (`asset`, `acquisition_timestamp`, `quantity`, `unit_cost_basis_usd`).
- USD Fair Market Value feed ($P_{\text{USD}}$) for traded crypto assets.

## Workflow

1. **Tax Lot Acquisition Registration**:
   - Ingest buy, mining, or staking rewards and calculate USD cost basis ($C_{\text{usd}} = Q \times P_{\text{usd}} + \text{Fee}_{\text{usd}}$).
2. **Crypto-to-Crypto Swap & Disposal Processing**:
   - For disposal of asset $A$ for asset $B$:
   - Calculate gross proceeds: $\text{Proceeds}_{\text{gross}} = Q_B \times P_{B, \text{usd}}$.
   - Subtract gas fee: $\text{Proceeds}_{\text{net}} = \text{Proceeds}_{\text{gross}} - \text{GasFee}_{\text{usd}}$.
3. **Tax Lot Matching (HIFO / FIFO / LIFO)**:
   - Rank candidate tax lots for asset $A$:
     - `HIFO`: Sort by highest cost basis ($\max C_{\text{unit}}$).
     - `FIFO`: Sort by oldest acquisition timestamp.
     - `LIFO`: Sort by newest acquisition timestamp.
   - Deduct quantity and compute $\text{Realized PnL} = \text{Proceeds}_{\text{net}} - \text{Cost Basis}_{\text{lot}}$.
4. **Audit Report Generation**: Output structured `CryptoTaxAuditReport` (Form 8949 breakdown).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Crypto-to-Crypto Swaps**: Treating ETH $\to$ USDC or BTC $\to$ ETH swaps as non-taxable transfers, causing massive tax audit non-compliance.
- **Un-tracked Gas Fee Dispositions**: Failing to realize capital gain/loss when disposing of ETH to pay for DEX gas fees.
- **Mixing Up Exchange Accounts**: Aggregating tax lots across separate legal entities or personal vs corporate wallets.

## Verification

- Instantiate `CryptoTaxLotTrackerEngine`. Register 2 tax lots for ETH: Lot 1 (\$1,500 cost basis, 10 ETH) and Lot 2 (\$3,000 cost basis, 5 ETH). Perform a crypto-to-crypto swap of 4 ETH for 12,000 USDC (FMV = \$3,000/ETH = \$12,000) with \$50 gas fee using `HIFO`. Verify engine selects Lot 2 (\$3,000 basis), realizing a \$50 loss after gas fee deduction.
- Run `python scripts/test_crypto_transaction_tax_lot_tracking.py`.

## Related Skills

- `cross-strategy-tax-lot-optimization`
- `fifo-vs-specific-lot-tax-accounting-methods`
---
