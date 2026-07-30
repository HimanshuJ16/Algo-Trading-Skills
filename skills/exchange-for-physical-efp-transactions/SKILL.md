---
name: exchange-for-physical-efp-transactions
description: >-
  Quantitative derivatives and commodities execution engine for modeling Exchange for Physical (EFP / EFRP) transactions under CME Rule 538, evaluating basis arbitrage spread, and validating physical-to-futures quantity equivalence.
domain: Venue Integration & Derivatives
subdomain: Off-Exchange & Privately Negotiated Derivatives (EFRP)
tags: ["efp", "efrp", "cme-rule-538", "basis-trading", "futures-spot-swap", "physical-settlement", "commodities"]
brokers_frameworks: ["CME Rule 538", "ICE EFP", "Eurex Rule 4.6", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in physical commodity trading desks, spot-futures basis arbitrage strategies, index arbitrage operations, and institutional Block/EFRP execution systems. An **Exchange for Physical (EFP)** (governed by **CME Rule 538** and **Eurex Rule 4.6**) allows two counterparties to privately negotiate a simultaneous exchange of an exchange-traded futures contract for an equivalent quantity of the underlying physical commodity, spot forex, or physical equity basket. EFP trades eliminate leg execution risk and capture basis mispricing.

## Prerequisites

- Futures contract details (`futures_symbol`, `contract_multiplier`, `futures_price_usd`).
- Physical cash asset details (`physical_symbol`, `physical_qty`, `spot_price_usd`).
- Market risk-free rate ($r$) and time to expiration ($T$).

## Workflow

1. **Quantity Equivalence Audit**:
   - $\text{Required Physical Quantity} = \text{Futures Contracts Count} \times \text{Contract Multiplier}$.
   - If $|\text{Actual Physical Qty} - \text{Required Physical Qty}| > 1e-4 \implies$ Reject EFP (`QUANTITY_MISMATCH`).
2. **EFP Basis Spread & Theoretical Fair Value Calculation**:
   - $\text{Observed EFP Basis} = P_{\text{futures}} - P_{\text{spot}}$.
   - $\text{Theoretical Basis} = P_{\text{spot}} \times (e^{r \cdot T} - 1.0)$.
   - $\text{Basis Arbitrage Discrepancy} = \text{Observed Basis} - \text{Theoretical Basis}$.
3. **CME Rule 538 Bona Fide Trade Audit**:
   - Ensure transaction has non-transitory economic substance and transfer of physical ownership.
4. **Audit Report Generation**: Output structured `EfpAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Transitory EFRP Violation**: Executing offsetting spot/futures trades that immediately collapse without genuine transfer of physical risk, breaching CME Rule 538 prohibitions.
- **Quantity Discrepancy Errors**: Submitting an EFP where 10 Gold futures contracts ($1,000\text{ oz}$) are matched against $900\text{ oz}$ of physical gold.
- **Ignoring Carrying Cost in Basis Math**: Omitting financing costs, storage fees, or dividend yields when evaluating EFP fair value.

## Verification

- Instantiate `ExchangeForPhysicalEngine`. Input 10 Gold futures contracts (`GC_202612`, multiplier = 100 troy oz, price = \$2,500.00/oz) swapped against 1,000 oz of physical spot gold @ \$2,490.00/oz. Time to expiry $T = 0.25$ years, $r = 4.0\%$. Verify engine validates quantity equivalence ($1,000 = 10 \times 100$), calculates observed basis (+\$10.00/oz), theoretical basis (+\$24.95/oz), and outputs `EFP_APPROVED`. Submit mismatched physical qty ($900\text{ oz}$). Verify engine rejects with `QUANTITY_MISMATCH`.
- Run `python scripts/test_exchange_for_physical_efp_transactions.py`.

## Related Skills

- `dividend-futures-and-forward-modeling`
- `synthetic-continuous-futures-contract-construction`
---
