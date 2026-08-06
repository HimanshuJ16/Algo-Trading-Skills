---
name: variance-swap-and-volatility-derivative-pricing
description: "Institutional quantitative pricing skill for Variance Swaps, Volatility Swaps, and volatility derivatives using static log-contract option strip replication (Demeterfi et al., 1999), realized log-return variance calculation, convexity adjustment, and Mark-to-Market (MTM) valuation."
domain: Multi-Asset Quantitative Derivatives & Volatility Trading
subdomain: Exotics & Volatility Derivative Pricing
tags:
- variance-swap
- volatility-swap
- static-replication
- log-contract
- convexity-adjustment
- realized-variance
- options-pricing
- mtm-valuation
brokers_frameworks:
- isda-matrix
- quantlib
- scipy
version: 1.1.0
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when pricing, hedging, or valuing OTC **Variance Swaps**, **Volatility Swaps**, or VIX volatility derivatives in quantitative trading desks.

This skill provides institutional mechanisms to:
- Calculate **Annualized Realized Variance ($\sigma^2_{\text{realized}}$)** from daily price log-returns ($S_i$).
- Pricing **Fair Variance Strike ($K_{\text{var}}$)** via Demeterfi, Derman, Kamal, & Zou (1999) static log-contract replication using out-of-the-money (OTM) European option strips.
- Calculate **Fair Volatility Strike ($K_{\text{vol}}$)** incorporating convexity adjustments ($\text{Var}(\sigma)$).
- Compute **Mark-to-Market (MTM) Present Value** for seasoned contracts combining historical realized variance and remaining fair variance strikes.
- Convert between **Vega Notional ($N_{\text{vega}}$)** and **Variance Notional ($N_{\text{var}}$)** ($N_{\text{var}} = \frac{N_{\text{vega}}}{2 K_{\text{vol}}}$).

## Prerequisites

- Python 3.9+
- Standard Python libraries (`math`, `typing`).
- Option chain market data containing strike prices ($K_i$), option types (`CALL`/`PUT`), and OTM option market prices ($Q(K_i)$).

## Workflow

1. **Construct Contract Specifications**: Define `VarianceSwapContract` specifying symbol, swap type (`VARIANCE_SWAP`), volatility strike $K_{\text{vol}}$ (e.g. `20.0`), vega notional $N_{\text{vega}}$, maturity $T$ in years, spot price $S_0$, and risk-free rate $r$.
2. **Replicate Fair Variance Strike ($K_{\text{var}}$)**: Pass OTM European option quotes to `calculate_fair_strikes(spot, r, t, option_strip)` to compute $K_{\text{var}}$ via discretized numerical integration over the strike grid.
3. **Calculate Realized Variance**: Pass daily historical asset prices to `calculate_realized_variance(price_history)` to compute log-return variance $\sigma^2_{\text{realized}}$.
4. **Compute Mark-to-Market (MTM) Valuation**: Call `price_variance_swap_mtm()` for seasoned contracts, blending elapsed realized variance and remaining fair variance strikes.
5. **Monitor Risk & Convexity**: Track variance vega, delta, and convexity adjustments ($K_{\text{var}} - K_{\text{vol}}^2$).

## Common Pitfalls

- **Confusing Vega Notional with Variance Notional**: PnL is linear in variance, NOT volatility. Entering a $100,000 variance notional trade believing it is vega notional causes a massive 20x-40x risk over-exposure.
- **Using In-The-Money (ITM) Options in Replication**: Static replication MUST use strictly OTM Puts ($K < F_0$) and OTM Calls ($K \ge F_0$). Including ITM options inflates bid-ask spreads and liquidity noise.
- **Truncating Option Strike Grids**: Truncating option strips too close to the money understates tail risk and distorts the fair variance strike $K_{\text{var}}$.
- **Ignoring Jump Risk / Volatility Skew**: Extreme out-of-the-money put skew significantly raises $K_{\text{var}}$ relative to at-the-money implied volatility.

## Verification

Run the unit test suite to validate realized variance calculations, static log-contract option strip replication, volatility swap convexity adjustments, and seasoned contract MTM valuations:

```bash
python -m unittest discover -s skills/variance-swap-and-volatility-derivative-pricing/scripts
```

## Related Skills

- `vix-and-volatility-index-derivative-strategies`
- `warrants-and-structured-product-integration`
- `total-return-swap-synthetic-exposure`
- `transfer-learning-across-correlated-instruments`

