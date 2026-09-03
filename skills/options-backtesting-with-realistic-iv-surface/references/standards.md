# Backtesting Methodology Standards — options-backtesting-with-realistic-iv-surface

## Surface parameters

All smile coefficients are calibrated **at the reference tenor** $T_{\text{ref}} = 30/365$,
where the term scale $s(T_{\text{ref}}) = 1$ by construction. A skew slope quoted without
its tenor is not a usable number.

| Parameter | Symbol | Description | Typical equity value |
|---|---|---|---|
| ATM volatility | $\sigma_{\text{atm}}$ | At-the-money implied volatility for the tenor priced | 15–30% |
| Skew slope | $\alpha$ | Linear moneyness coefficient at $T_{\text{ref}}$ (negative = put skew) | −0.20 to −0.40 |
| Smile curvature | $\beta$ | Quadratic convexity coefficient at $T_{\text{ref}}$ | 0.30 to 0.80 |
| Term decay | $\gamma$ | Power-law exponent for skew decay in $T$ | 0.4 to 0.5 |
| Dividend yield | $q$ | Continuous yield in the BSM drift $r - q$ | 0 to ~0.04 |

The $\alpha$ / $\beta$ ranges are conventional working values for equity index smiles, not
calibrated outputs; fit them per underlying against quoted chains before relying on them.

## Term structure of skew

The smile offset is scaled by $s(T) = \min\left(4.0,\, (T_{\text{ref}}/T)^{\gamma}\right)$.

A power-law decay of the at-the-money skew in time to maturity is a standard stylized fact
of equity index implied volatility surfaces:

- **SSVI baseline, $\gamma = 0.5$.** Gatheral & Jacquier, *Arbitrage-free SVI volatility
  surfaces*, Quantitative Finance 18(6), 2014 (arXiv:1204.0646). The SSVI power-law
  parameterization produces an ATM skew proportional to $T^{-1/2}$.
- **Rough-volatility estimate, $\gamma \approx 0.4$.** Gatheral, Jaisson & Rosenbaum,
  *Volatility is rough*, Quantitative Finance 18(6):933–949, 2018 (arXiv:1410.3394). The
  RFSV model reproduces a short-maturity ATM skew decaying as $T^{H-1/2}$ with Hurst
  exponent $H$ of order $0.1$.

**Applicability limits.** The power law is documented empirically over maturities beyond
roughly one month; sub-month behaviour deviates from it, and recent work reports a steeper
decay than $T^{-1/2}$ at long SPX maturities. $\gamma$ is therefore a calibration input,
not a constant — fit it per underlying. `MAX_SKEW_TERM_SCALE = 4.0` bounds extrapolation
into the sub-month region (with the defaults it binds only below ~1.9 days to expiry) and
is a numerical guard, not a market observation.

## Pricing model

European Black-Scholes-Merton with continuous dividend yield $q$ — Merton, *Theory of
Rational Option Pricing*, Bell Journal of Economics and Management Science 4(1), 1973.

$$d_1 = \frac{\ln(S/K) + (r - q + \tfrac{1}{2}\sigma^2)T}{\sigma\sqrt{T}}, \qquad d_2 = d_1 - \sigma\sqrt{T}$$

$$C = S e^{-qT} N(d_1) - K e^{-rT} N(d_2), \qquad P = K e^{-rT} N(-d_2) - S e^{-qT} N(-d_1)$$

Put-call parity, which the engine must satisfy exactly and never computes:

$$C - P = S e^{-qT} - K e^{-rT}$$

## Greek conventions

| Greek | Definition | Reported unit |
|---|---|---|
| $\Delta$ | $\partial V/\partial S$ | per \$1 of underlying; call $= e^{-qT}N(d_1)$, put $= -e^{-qT}N(-d_1)$ |
| $\Gamma$ | $\partial^2 V/\partial S^2$ | per \$1$^2$; $= e^{-qT}\phi(d_1)/(S\sigma\sqrt{T})$ |
| $\Theta$ | $\partial V/\partial t$ | per **calendar day** (annual value $\div$ 365) |
| $\nu$ | $\partial V/\partial \sigma$ | per **1 volatility point** (20% → 21%), i.e. annual value $\div$ 100 |

Reference: standard BSM Greeks with the $e^{-qT}$ dividend adjustment.
