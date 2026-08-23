---
name: counterparty-credit-risk-for-otc-derivatives
description: Quantitative OTC derivatives risk engine for computing Current Exposure
  (CE), Potential Future Exposure (PFE), Credit Valuation Adjustment (CVA), ISDA netting
  sets, and CSA collateral margins.
domain: Risk Management & Derivatives
subdomain: Counterparty Credit Risk
tags:
- counterparty-risk
- otc-derivatives
- pfe
- cva
- isda
- csa
- netting
- sa-ccr
brokers_frameworks:
- ISDA Standard
- SA-CCR
- Python Dataclasses
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when trading Over-The-Counter (OTC) derivatives (e.g. Interest Rate Swaps, FX Forwards, Equity Swaps, Credit Default Swaps) with bilateral counterparties. Unlike exchange-traded futures backed by central clearing houses (CCPs), OTC derivatives carry bilateral **Counterparty Credit Risk (CCR)**. This module calculates SA-CCR-grounded Replacement Cost (RC), a simplified PFE add-on with the regulatory over-collateralisation multiplier, Exposure at Default (EAD = 1.4 × (RC + PFE)), a single-period CVA proxy, and enforces ISDA Master Agreement close-out netting and CSA threshold/MTA collateral call triggers.

Formula grounding: BCBS 279, *"The standardised approach for measuring counterparty credit risk exposures"* (Basel Committee on Banking Supervision, March 2014, rev. April 2014), consolidated into the Basel Framework as CRE52.

## When NOT to Use

- **Cleared derivatives**: trades routed through a CCP replace bilateral counterparty risk with CCP risk — use CCP margin/risk tooling instead.
- **Regulatory capital reporting**: the PFE add-on here is a simplified `notional × supervisory factor` sum. It omits duration-based adjusted notionals, supervisory deltas, maturity factors, and hedging-set correlation aggregation (BCBS 279 paras 151-184), so it must not be reported as a regulatory SA-CCR EAD.
- **CVA desk pricing**: the CVA here is a single-period, undiscounted proxy (`(1-R) × EAD × PD`), not the time-bucketed discounted-expected-exposure CVA used for pricing or xVA P&L.
- **Two-way CSAs with haircuts**: collateral is taken at value, one-way (counterparty posts) only.

## Prerequisites

- Active OTC contract mark-to-market (MTM) values and notionals.
- ISDA/CSA parameters: `threshold`, `minimum_transfer_amount` (MTA), posted collateral, net independent collateral amount (NICA, default 0), counterparty probability of default ($PD$), and recovery rate ($R$).
- A verified, legally enforceable ISDA Master Agreement before applying netting (see `references/standards.md`).

## Workflow

1. **Netting Set Grouping**: Aggregate all active MTM contract values under the same ISDA Netting Set ID. Only net under a legally enforceable Master Agreement.
2. **Replacement Cost (RC)** — BCBS 279 para 144, margined netting set:
   - $V = \sum V_{mtm, i}$, $C$ = posted collateral, $NICA$ = net independent collateral.
   - $RC = \max(V - C,\ TH + MTA - NICA,\ 0)$. The $TH + MTA$ term is a **floor**: a margined netting set always carries at least the uncollateralised band as exposure, even when MTM is deeply negative.
   - Decision point: an *unmargined* netting set is represented by $TH = MTA = NICA = 0$, which collapses the formula to $\max(V - C, 0)$ (para 136).
3. **PFE Add-On & Multiplier**:
   - Add-on aggregate: $\text{AddOn} = \sum_i (\text{Notional}_i \times SF_i)$ with $SF$ from the canonical supervisory factor table in the script (`SA_CCR_SUPERVISORY_FACTORS`, BCBS 279 Table 2 — interest rate 0.5%, FX 4%, equity single-name 32%, equity index 20%).
   - Multiplier (para 149): $m = \min\left(1,\ 0.05 + 0.95 \cdot e^{(V - C) / (2 \cdot 0.95 \cdot \text{AddOn})}\right)$; $PFE = m \times \text{AddOn}$.
   - Decision point: if $V - C \ge 0$ the multiplier is exactly 1 and can be skipped; it only reduces PFE when the set is over-collateralised.
4. **Exposure at Default** — BCBS 279 para 128: $EAD = 1.4 \times (RC + PFE)$. The $\alpha = 1.4$ multiplier is mandatory in SA-CCR; omitting it understates EAD by 40%.
5. **CVA Proxy**: $CVA = (1 - R) \times EAD \times PD$ — single-period, undiscounted; see When NOT to Use before quoting this as a price.
6. **Pre-Trade Limit & CSA Collateral Call Audit**:
   - Delivery amount $= \max(0,\ V - C - TH)$. A margin call triggers only when the delivery amount $\ge MTA$ (inclusive boundary).
   - If $EAD >$ Max Credit Limit, block the trade or trigger a mandatory collateral top-up.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Gross Exposure Without Netting**: Calculating credit exposure contract-by-contract without ISDA legal close-out netting, grossly overstating credit exposure.
- **Hard-coding supervisory factors from memory**: the frequently mis-remembered "equity 6%" is wrong — BCBS 279 Table 2 sets equity single-name at 32%, equity index at 20%, FX at 4%, interest rate at 0.5%. Always source factors from `SA_CCR_SUPERVISORY_FACTORS` or the primary table.
- **Omitting alpha = 1.4**: computing EAD as RC + PFE understates regulatory exposure by 40%; SA-CCR mandates EAD = 1.4 × (RC + PFE).
- **Dropping the TH + MTA floor in RC**: using $\max(0, V - C - TH)$ (a common textbook shortcut) understates a margined set's exposure whenever $V - C$ falls below the threshold band — para 144 floors RC at $TH + MTA - NICA$.
- **Ignoring the PFE multiplier for over-collateralised sets**: when $V - C < 0$, PFE must be scaled by the para 149 multiplier (floor 5%), otherwise collateralised sets are over-penalised.
- **Ignoring Margin Transfer Thresholds (MTA)**: Failing to factor Minimum Transfer Amount (MTA) into collateral call triggers, leading to un-collateralized micro-exposure drift; the trigger fires only at delivery amount ≥ MTA.
- **Static Default Probabilities ($PD$)**: Using static credit ratings without updating market-implied $PD$ derived from CDS spreads.

## Verification

- Instantiate `OtcCounterpartyRiskEngine`. Register 3 contracts under Netting Set `ISDA_BANK_A`: equity swap +$500k MTM, $1M notional, SF 32%; FX forward −$200k MTM, $500k notional, SF 4%; rates swap +$100k MTM, $2M notional, SF 0.5%. Post $300k collateral, TH = $100k, MTA = $50k.
  - $V = \$400k$; AddOn $= 320k + 20k + 10k = \$350k$; $V - C = \$100k \ge 0$ so multiplier $= 1$.
  - $RC = \max(100k,\ 150k,\ 0) = \$150k$ (TH+MTA floor binds).
  - $EAD = 1.4 \times (150k + 350k) = \$700k$.
  - $CVA = 0.60 \times 700k \times 0.02 = \$8{,}400$.
  - Delivery amount $= \max(0, 400k - 300k - 100k) = 0 <$ MTA → no margin call.
- Run `python -m unittest discover -s skills/counterparty-credit-risk-for-otc-derivatives/scripts`.

## Related Skills

- `counterparty-and-broker-concentration-risk`
- `broker-account-margin-call-handling`
