---
name: total-return-swap-synthetic-exposure
description: "Institutional financial engineering skill for Total Return Swaps (TRS), pricing synthetic asset exposure, modeling total return legs (capital gains + manufactured dividends), SOFR funding legs, periodic net resets, and ISDA variation margin calls."
domain: Multi-Asset Derivatives
subdomain: Swaps & Synthetic Structures
tags:
- total-return-swap
- trs
- synthetic-exposure
- derivatives
- sofr-funding
- manufactured-dividends
- isda-margin
- prime-brokerage
brokers_frameworks:
- isda-master-agreement
- qtg
- quantlib
version: 1.1.0
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when engineering, pricing, risk-managing, or accounting for **Total Return Swaps (TRS)** to achieve synthetic economic exposure to underlying stocks, ETFs, bond baskets, or commodity indices without physical asset ownership.

This skill provides institutional mechanisms to:
- Model **Total Return Leg** payments (capital appreciation/depreciation + manufactured dividends after tax withholding).
- Model **Funding Leg** interest calculations using benchmark rates (SOFR, ESTR, SONIA, EURIBOR) plus prime broker spread bps over `ACT/360` or `ACT/365` day-count fractions.
- Process periodic net cash flow reset settlements for both Receiver (Long Synthetic) and Payer (Short Synthetic) positions.
- Track exact synthetic share delta ($\Delta$) and ISDA CSA Initial/Variation Margin requirements.

## Prerequisites

- Python 3.9+
- Understanding of ISDA Master Agreements and Credit Support Annex (CSA) collateral rules.
- Access to benchmark rate fixings (SOFR, ESTR) and corporate action dividend feeds (ex-date, gross amount, withholding tax %).

## Workflow

1. **Configure Contract Parameters**: Instantiate `TRSContractConfig` with swap ID, reference symbol, notional USD, initial reference price, share quantity, funding benchmark (`SOFR`), funding spread (e.g. 50 bps), and ISDA margin ratios.
2. **Define Reset Period & Dividends**: Create `TRSResetPeriod` specifying period start/end dates, start/end reference asset prices, average benchmark rate, and any `DividendEvent` occurring during the period.
3. **Calculate Total Return Leg**: Invoke `calculate_total_return_leg()` to derive capital gains/losses and net manufactured dividends (gross dividend minus tax withholding).
4. **Calculate Funding Leg**: Call `calculate_funding_leg()` to compute interest expense: $\text{Notional} \times (\text{SOFR} + \text{Spread}) \times \text{DayFraction}$.
5. **Process Periodic Settlement**: Call `process_reset_period()` specifying position side (`RECEIVER_TOTAL_RETURN` for Long, `PAYER_TOTAL_RETURN` for Short). The engine outputs `TRSSettlement` with net cashflow, current Mark-to-Market (MtM), synthetic share delta, and variation margin due.

## Common Pitfalls

- **Ignoring Dividend Tax Withholding**: Manufactured dividends paid by the TRS Payer are subject to cross-border withholding tax rules (e.g. 15% US withholding under Section 871(m)). Failing to deduct tax withholding overstates synthetic long returns.
- **Mismatched Day-Count Conventions**: Using `ACT/365` for USD SOFR funding legs instead of the standard `ACT/360` market convention creates funding interest calculation errors.
- **Confusing Notional Reset vs. Fixed Share Reset**: In a share-locked TRS, quantity of shares remains fixed while notional resets each period based on starting share price. In a fixed-notional TRS, share count changes at each reset.
- **Neglecting Financing Drag in Backtests**: Backtesting synthetic equity long strategies without accounting for the funding leg ($\text{SOFR} + \text{Spread}$) severely overstates net strategy Sharpe ratio and returns.

## Verification

Run the test suite to validate total return leg calculations, funding interest math, manufactured dividend withholdings, and reset cash flows:

```bash
python -m unittest discover -s skills/total-return-swap-synthetic-exposure/scripts
```

## Related Skills

- `cross-margining-across-asset-classes`
- `dividend-futures-and-forward-modeling`
- `capital-efficiency-across-cross-margined-strategies`
- `multi-leg-strategy-margin-optimization`

