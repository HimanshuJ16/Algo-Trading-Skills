# Deep Workflow Reference — options-backtesting-with-realistic-iv-surface

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Construct Dynamic IV Surface**:
   - Compute strike moneyness $m = K/S$ and evaluate parametric smile/skew model:
     $\sigma(m, T) = \sigma_{\text{atm}} + \alpha(m - 1) + \beta(m - 1)^2$

2. **Price Options via Black-Scholes**:
   - Compute $d_1$, $d_2$, and evaluate Call/Put prices with strike-specific IV.

3. **Calculate Option Greeks**:
   - Delta ($\Delta$), Gamma ($\Gamma$), Theta ($\Theta$), Vega ($\nu$) for portfolio risk management.

4. **Audit Flat IV vs Skewed IV Pricing Error**:
   - Compare strategy P&L under flat ATM IV vs realistic IV surface to quantify mispricing drag.

## Production Implementation Reference

- Reference code: `scripts/options_iv_backtester.py` (`OptionsIVSurfaceEngine`, `OptionPricingResult`, `OptionGreeks`).
- Automated unit tests: `scripts/test_options_iv_backtester.py`.
