---
name: warrants-and-structured-product-integration
description: "Institutional equity derivatives skill for pricing covered warrants, Turbo Warrants / CBBCs, and structured products, modeling Entitlement Ratios (R_ent), evaluating Mandatory Call Event (MCE) knock-out barriers, computing Effective Gearing, and generating real-time Delta Hedge rebalancing signals."
domain: Multi-Asset Derivatives & Structured Products
subdomain: Warrants & Exotic Derivatives Integration
tags:
- warrants
- covered-warrants
- cbbc
- turbo-warrants
- entitlement-ratio
- effective-gearing
- knock-out-barrier
- delta-hedging
brokers_frameworks:
- hkex-warrants
- euronext-warrants
- sgx-warrants
- cboe
- borsa-italiana
version: "1.1.0"
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when trading covered warrants, Turbo Warrants / CBBCs (Callable Bull/Bear Contracts), Autocallable Notes, or managing market maker delta hedges on major warrant exchanges (**HKEX**, **Euronext**, **SGX**, **Borsa Italiana**).

This skill provides institutional mechanisms to:
- Price Covered Call/Put Warrants scaled by **Entitlement Ratios ($R_{\text{ent}}$)** (e.g. $10\ \text{warrants} : 1\ \text{share}$).
- Evaluate **Mandatory Call Events (MCE)** for Turbo Warrants / CBBCs when spot breaches barrier prices ($S \le B_{\text{knockout}}$ for Bull CBBCs).
- Compute Black-Scholes Greeks ($\Delta, \Gamma, \Theta, \text{Vega}$) scaled by $R_{\text{ent}}$.
- Calculate **Simple Gearing** ($\frac{S \times R}{P}$) and **Effective Gearing (Delta Gearing)** ($\text{Gearing} \times \Delta_{\text{raw}}$).
- Generate real-time **Delta Hedge Rebalancing Signals** ($\text{Shares} = N_{\text{warrants}} \times \Delta_{\text{warrant}}$).

## Prerequisites

- Python 3.9+
- Standard Python libraries (`datetime`, `dataclasses`, `math`, `typing`).
- Real-time underlying equity spot prices, interest rates, and warrant term sheets.

## Workflow

1. **Construct Warrant Contract**: Define `WarrantContract` specifying warrant type (`COVERED_CALL`, `COVERED_PUT`, `TURBO_BULL_CBBC`, `TURBO_BEAR_CBBC`), strike price, barrier price, entitlement ratio $R_{\text{ent}}$, and days to expiry.
2. **Price Warrant & Compute Greeks**: Call `price_warrant(spot_price, contract)` to execute Black-Scholes valuation scaled by $R_{\text{ent}}$ and check knock-out status.
3. **Analyze Effective Gearing**: Evaluate `effective_gearing` to measure true leverage sensitivity to 1% underlying equity movements.
4. **Calculate Delta Hedge Signal**: Invoke `calculate_delta_hedge_signal(valuation, position_warrants, current_hedged_shares)` to compute required underlying shares and rebalance action (`BUY`, `SELL`, `HOLD`).
5. **Monitor Knock-Out Barriers**: Continuously check spot prices against barrier levels for Turbo Warrants / CBBCs.

## Common Pitfalls

- **Ignoring the Entitlement Ratio ($R_{\text{ent}}$)**: Warrant prices and delta MUST be scaled by the entitlement ratio ($R_{\text{ent}} = 0.1$). Sizing delta hedges assuming a 1:1 ratio leads to a 10x delta over-hedge error.
- **Un-Hedged Knock-Out Discontinuity (CBBC Gap Risk)**: When a Turbo Warrant / CBBC knocks out ($S \le B$), its delta instantly drops from $\Delta \approx R_{\text{ent}}$ to $0$. The delta hedging engine must immediately dump all underlying hedge shares to prevent unhedged long equity exposure.
- **Confusing Simple Gearing with Effective Gearing**: Simple Gearing ignores delta. An out-of-the-money warrant with high Simple Gearing may have an Effective Gearing near zero because its delta is near zero.
- **Dilutive vs Covered Warrant Misunderstanding**: Company-issued warrants dilutive to equity require share count adjustment math. Third-party covered warrants (issued by banks) carry zero corporate dilution but require market maker delta hedging.

## Verification

Run the unit test suite to validate covered warrant pricing, entitlement ratio scaling, effective gearing math, Turbo knock-out MCE detection, and delta hedge signal generation:

```bash
python -m unittest discover -s skills/warrants-and-structured-product-integration/scripts
```

## Related Skills

- `variance-swap-and-volatility-derivative-pricing`
- `vix-and-volatility-index-derivative-strategies`
- `total-return-swap-synthetic-exposure`
- `tick-size-pilot-program-impact-assessment`

