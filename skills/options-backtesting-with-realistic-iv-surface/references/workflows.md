# Deep Workflow Reference — options-backtesting-with-realistic-iv-surface

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Validate the market inputs before they reach the pricer.**
   - Reject non-finite $S$, $K$, $T$, $\sigma_{\text{atm}}$ and $q$; reject $S \le 0$,
     $K \le 0$, $\sigma_{\text{atm}} \le 0$ and $T < 0$; reject any `option_type` that is
     not `CALL` or `PUT`.
   - A pricer that coerces bad input into a plausible number is worse than one that
     raises: a `NaN` spot from a single bad tick will otherwise surface as a real-looking
     premium somewhere deep in the backtest P&L.

2. **Evaluate the surface $\sigma(m, T)$.**
   - $m = K/S$; $\text{offset} = \alpha(m-1) + \beta(m-1)^2$.
   - $s(T) = \min(4.0,\ (T_{\text{ref}}/T)^{\gamma})$, $T_{\text{ref}} = 30/365$.
   - $\sigma(m,T) = \sigma_{\text{atm}} + \text{offset} \cdot s(T)$, clamped to
     $[0.05, 3.0]$ with the clamp logged.
   - $\alpha$ and $\beta$ are anchored at $T_{\text{ref}}$. If you instead calibrate them
     separately for each expiration, set $\gamma = 0$ — otherwise the term decay is
     applied twice.

3. **Price with Black-Scholes-Merton at the strike IV.**
   - Use the strike-specific $\sigma$, not $\sigma_{\text{atm}}$, in $d_1$, $d_2$ *and* in
     every Greek. Using the skewed IV for the price but the ATM IV for vega is a common
     and silent inconsistency.
   - Drift is $r - q$. Discrete cash dividends are not modelled: substitute
     $S' = S - \sum_i D_i e^{-r t_i}$ over dividends paid before expiry, before pricing.

4. **Calculate Greeks.**
   - $\Delta$, $\Gamma$, $\Theta$, $\nu$ with the $e^{-qT}$ adjustment (see
     `references/standards.md` for the exact expressions and reported units).
   - Nothing is rounded. Rounding a theoretical price to the tick breaks put-call parity;
     quantize at the fill-simulation layer instead.

5. **Settle expiries explicitly.**
   - At $T = 0$ return the intrinsic payoff with $\Gamma = \Theta = \nu = 0$ and
     $\Delta \in \{-1, 0, 1\}$, flagged `is_expired=True`. Evaluating Black-Scholes at an
     epsilon tenor instead reports residual time value and an unbounded gamma on a
     contract that has neither.
   - $T < 0$ is an error, not a tiny positive tenor: it means the backtest clock passed
     expiry without settling the position.

6. **Aggregate portfolio Greeks and rebalance the hedge** on $\sum_i \Delta_i$, netting
   across legs before routing (see `multi-order-netting-before-routing`).

7. **Audit flat-IV vs surface pricing error.**
   - Re-run the identical backtest with `skew_alpha=0.0, smile_beta=0.0` and difference
     the P&L series. The gap is the skew mispricing a flat-IV backtest was capitalising
     as strategy alpha; report it alongside the headline return.

## Verification Values

Reproducible checks used by the unit tests:

| Check | Configuration | Expected |
|---|---|---|
| Black-Scholes benchmark (Hull) | $S=42$, $K=40$, $r=0.10$, $\sigma=0.20$, $T=0.5$, flat smile | call $4.76$, put $0.81$ |
| Merton with yield | as above, $q=0.03$ | call $4.282312$, put $0.956787$ |
| Undamped smile at $T_{\text{ref}}$ | $m=0.90$, $\alpha=-0.30$, $\beta=0.50$, $\sigma_{\text{atm}}=0.20$ | $\sigma = 0.235$ |
| Term decay | $\gamma=0.5$ | $s(4T_{\text{ref}}) = 0.5$, $s(T_{\text{ref}}/4) = 2.0$ |
| Put-call parity | any strike, any $q$ | $C - P = Se^{-qT} - Ke^{-rT}$ to 1e-10 |
| Greeks | central finite differences of the price function | analytic $=$ numerical |

## Production Implementation Reference

- Reference code: `scripts/options_iv_backtester.py`
  (`OptionsIVSurfaceEngine`, `OptionPricingResult`, `OptionGreeks`).
- Automated unit tests: `scripts/test_options_iv_backtester.py`.
- Documented limitations (European exercise only, continuous yield only, parametric not
  fitted, no arbitrage checks) are listed in the module docstring and in the
  "When NOT to Use" section of `SKILL.md`.
