---
name: quanto-options-and-cross-currency-derivative-structures
description: >-
  Use when pricing or risk-managing a European quanto option — a foreign-currency-denominated underlying settled in the domestic currency at a contractually fixed exchange rate. Applies the Black-Scholes cross-currency drift adjustment $r_f - q - \rho\sigma_S\sigma_X$, discounts at the domestic rate, and returns quanto Delta, Gamma, the two-channel quanto Vega, and correlation sensitivity $\partial V/\partial\rho$ — with the two conventions that decide the sign (FX quoted domestic-per-foreign, strike in foreign units) stated explicitly.
domain: Derivatives & Cross-Currency Structuring
subdomain: Exotic Derivatives & FX Risk Engineering
tags: ["quanto-options", "cross-currency", "black-scholes", "correlation-drift", "quanto-delta", "quanto-vega", "derivatives-pricing", "fx-correlation-risk"]
brokers_frameworks: ["Black-Scholes-Merton", "Haugh IEOR E4707 Quanto Formulation", "Python Standard Library (math)"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when an underlying trades in one currency but the option's payoff is
cash-settled in another **at a rate fixed in the term sheet** — a USD-settled option
on the Nikkei 225, a EUR-settled option on WTI, a USD-settled option on a
KRW-denominated index. The buyer wants the foreign asset's return and explicitly
does *not* want the FX exposure that normally comes with it.

That FX exposure does not disappear; it is transferred to the seller, who prices it
in through the drift. Under the **domestic** risk-neutral measure the foreign asset
no longer drifts at $r_f - q$ but at

$$\mu_{\text{quanto}} = r_f - q - \rho\,\sigma_S\,\sigma_X$$

The engine prices the European call and put on that drift, discounts at the
**domestic** rate $r_d$, scales by the fixed conversion multiplier $F_X$, and
returns the Greeks — including the two that a plain Black-Scholes port gets wrong:
Vega (which has a second channel through the drift) and $\partial V/\partial\rho$
(which is *negative for a call and positive for a put*).

**Two conventions decide every sign in this model. Read them before passing data in.**

- **FX direction.** $X_t$ is the cost in **domestic** currency of one unit of the
  **foreign** currency — "domestic per foreign". `correlation` is the correlation
  between the foreign asset's return (in foreign currency) and the return on $X$ in
  *that* direction. A correlation estimated against the inverted quote has the
  opposite sign, and the drift then moves by $2\rho\sigma_S\sigma_X$ — 1.8
  percentage points of annual drift at the engine's defaults. The engine cannot
  detect this; nothing about a number in $[-1, 1]$ reveals which quote it came from.
- **Strike currency.** `strike_price` is in the **foreign** asset's own units, the
  same units as `spot_price`, and the whole payoff is then multiplied by $F_X$:
  $\text{payoff} = F_X\max(S_T - K, 0)$. If your term sheet fixes the strike in
  domestic currency, divide it by $F_X$ first.

## When NOT to Use

- **For a composite ("compo") option.** A compo converts the payoff at the
  *prevailing spot* FX rate and fixes the strike in domestic currency; a quanto
  converts at a *fixed* rate. They are different contracts with different FX risk,
  not two settings of one model — this engine prices only the quanto. Confusing the
  two is the most common structuring error in this product family.
- **For an American or Bermudan quanto.** European exercise only. There is no early-exercise
  premium anywhere in this model. See `american-vs-european-style-option-exercise-handling`.
- **As a smile-aware pricer.** A single flat $\sigma_S$ with no skew and no term
  structure. Real quanto desks price the skew, and the quanto adjustment itself
  becomes skew-dependent. Calibrate the surface with
  `options-implied-volatility-surface-construction` first and treat this engine's
  output as a flat-vol reference point, not a mark.
- **With a stale or long-horizon correlation.** $\rho$ is assumed constant to
  expiry. Asset-FX correlation is among the least stable inputs in derivatives and
  reverses sign in stress. See Common Pitfalls and
  `cross-asset-correlation-regime-shifts`.
- **At or past expiry.** $T \le 0$ raises rather than returning intrinsic — an
  expired contract is a settlement problem, not a pricing one. See
  `physical-vs-cash-settlement-handling`.
- **On a floating-FX structure of any kind**, including dual-currency notes and
  FX-linked coupons. The fixed multiplier $F_X$ is load-bearing.

## Prerequisites

- Contract terms: `spot_price` and `strike_price` (both in the **foreign** asset's
  currency), `time_to_expiry_years` $> 0$, `fixed_fx_rate` $> 0$ (the contractual
  domestic-per-foreign multiplier), `option_type` (`'CALL'` or `'PUT'`).
- Market data, all continuously compounded annualized decimals: `domestic_rate`
  ($r_d$, discounting only), `foreign_rate` ($r_f$, asset drift only),
  `dividend_yield` ($q$), `asset_volatility` ($\sigma_S > 0$), `fx_volatility`
  ($\sigma_X \ge 0$), `correlation` ($\rho \in [-1, 1]$).
- A correlation estimate whose FX quoting direction you have confirmed is
  domestic-per-foreign. See `currency-pair-quoting-convention-normalization`.

## Workflow

1. **Normalize the inputs before pricing** — the engine validates, but the two
   conventions are yours to get right:
   - **Decision point — confirm the FX quote direction of your correlation
     estimate, not just its magnitude.** If $\rho$ was regressed against a
     foreign-per-domestic series, negate it. There is no downstream check that
     catches this: a wrong-signed $\rho$ produces a perfectly plausible price.
   - **Decision point — convert a domestic-currency strike to foreign units**
     ($K_{\text{foreign}} = K_{\text{domestic}} / F_X$) before passing it in.
   - Reject $|\rho| > 1$, negative volatilities, non-positive $S$/$K$/$T$/$F_X$, and
     NaN/Inf at the boundary. The engine raises `ValueError` on all of these; do not
     wrap that in a retry. A NaN that passes silently produces a report whose every
     field is NaN and whose status still reads `QUANTO_PRICING_SUCCESSFUL`.
2. **Compute the quanto drift and forward**:
   - $\mu_{\text{quanto}} = r_f - q - \rho\sigma_S\sigma_X$, quanto forward
     $F = S e^{\mu_{\text{quanto}}T}$ (in foreign units).
   - **Decision point — $r_d$ discounts, $r_f$ drifts, and they are never
     interchangeable.** $r_d$ appears only in $e^{-r_dT}$; $r_f$ appears only in
     $\mu_{\text{quanto}}$ and therefore inside $d_1$. Swapping them is a
     first-order error that survives every plausibility check because the price
     stays positive and near the money.
3. **Price the European payoff on that drift**:
   - $d_1 = \dfrac{\ln(S/K) + \left(\mu_{\text{quanto}} + \tfrac{1}{2}\sigma_S^2\right)T}{\sigma_S\sqrt{T}}$, $d_2 = d_1 - \sigma_S\sqrt{T}$.
   - $V_{\text{call}} = F_X e^{-r_dT}\left[F\,N(d_1) - K\,N(d_2)\right]$;
     $V_{\text{put}} = F_X e^{-r_dT}\left[K\,N(-d_2) - F\,N(-d_1)\right]$.
   - **Decision point — an unrecognized `option_type` must raise, not default.**
     A branch of the form `if type == "CALL" ... else: <put>` prices `'CAL'`,
     `'C'`, and `''` as puts. Version 1.0.0 did exactly that and echoed the raw
     string back into the report, so the audit trail did not record which side was
     priced.
4. **Compute the Greeks — two of them are not the Black-Scholes ones**:
   - Delta $= \pm F_X e^{-r_dT} e^{\mu_{\text{quanto}}T} N(\pm d_1)$, Gamma
     $= F_X e^{-r_dT} e^{\mu_{\text{quanto}}T} n(d_1) / (S\sigma_S\sqrt{T})$.
   - **Decision point — Vega has two channels.** $\sigma_S$ enters $d_1/d_2$ *and*
     the drift, because the adjustment is $\rho\sigma_S\sigma_X$. The total is
     $\partial V/\partial\sigma_S = \underbrace{F_Xe^{-r_dT}Fn(d_1)\sqrt{T}}_{\text{spot}} + \underbrace{(\partial V/\partial\mu)(-\rho\sigma_X)}_{\text{drift}}$.
     Both components are reported separately. **If your call and put vegas come out
     equal, you have only the spot channel** — that equality holds in plain
     Black-Scholes and is false for a quanto.
   - **Decision point — $\partial V/\partial\rho$ changes sign between call and
     put.** Higher $\rho$ lowers the drift, so it *cheapens the call* and *enriches
     the put*. Version 1.0.0 returned the correct magnitude with a negative sign for
     both, which makes a mixed book's correlation exposures add instead of net.
5. **Read the report** — `QuantoOptionPricingReport`. Nothing in it is rounded;
   quantize at the presentation layer, where the notional is known.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Feeding in a correlation estimated against the inverted FX quote**: the sign
  flips, the drift moves by $2\rho\sigma_S\sigma_X$, and the price is wrong by
  roughly twice the whole quanto adjustment — while still looking entirely
  reasonable. At $\rho = 0.30$, $\sigma_S = 20\%$, $\sigma_X = 15\%$ that is 1.8
  points of annual drift.
- **Passing a domestic-currency strike**: the model's $K$ is in foreign units and
  the payoff is scaled by $F_X$ afterwards. A JPY-referenced strike passed against a
  USD conversion is off by two orders of magnitude and will *not* raise.
- **Reporting the Black-Scholes vega as the quanto vega**: it omits the drift
  channel. At the engine's defaults it overstates call vega by 6.9% (37.91 vs 35.48)
  and understates put vega by 4.8% (37.91 vs 39.81). The giveaway is identical call
  and put vega.
- **Netting $\partial V/\partial\rho$ across a book without checking the sign per
  side**: calls are negative, puts positive. Signing both the same way turns a
  partially hedged correlation position into an apparently doubled one, or hides a
  real one.
- **Swapping $r_d$ and $r_f$**: $r_d$ discounts, $r_f$ drifts. The resulting price is
  still positive and still near the money, so nothing downstream flags it.
- **Treating $\rho$ as a stable parameter**: asset-FX correlation is regime-dependent
  and inverts in stress — exactly when the quanto book is largest. Re-mark
  $\partial V/\partial\rho$ against a stressed $\rho$, not just the trailing
  estimate; the engine gives you the sensitivity precisely so this can be done.
- **Assuming the quanto adjustment is small because $\sigma_X$ is small**: it scales
  with the *product* $\rho\sigma_S\sigma_X$ and with $T$. On a five-year structure
  at $\rho = 0.5$, $\sigma_S = 30\%$, $\sigma_X = 12\%$ it is 1.8% per year, 9% of
  drift over the life.
- **Rounding Greeks inside the engine**: version 1.0.0 rounded `quanto_gamma` to 6
  decimals. On a Nikkei-scale underlying ($S \approx 38{,}000$) the true gamma is
  $4.9881789\times10^{-5}$ and that rounding returned $5\times10^{-5}$ — one
  significant figure, a 0.24% error in the hedge ratio.

## Verification

- **Degenerate case**: with $\sigma_X = 0$ and $r_d = r_f = 5\%$, $q = 0$, the quanto
  adjustment and the rate mismatch both vanish and the price must collapse onto the
  standard worked example $\text{BS}(100, 100, 1\text{y}, 5\%, 20\%) = 10.450584$.
- **Independent formulation**: Haugh shows a quanto call equals $F_X$ times a
  Merton call with dividend yield $q_f = q + r_d - r_f + \rho\sigma_X\sigma_S$ and
  strike $K/F_X$. At the defaults that gives $8.156533$ (call) and $7.104404$ (put),
  matched to $10^{-10}$ across a strike $\times$ tenor $\times$ correlation grid.
- **Drift and $d_1$**: $\mu_{\text{quanto}} = 0.02 - 0 - 0.30 \times 0.20 \times 0.15 = 0.011$;
  $d_1 = (0 + 0.011 + 0.02)/0.20 = 0.155$; $d_2 = -0.045$.
- **Put-call parity**: $C - P = F_X e^{-r_dT}(Se^{\mu_{\text{quanto}}T} - K)$ to $10^{-12}$.
- **Monte Carlo**: 400,000 antithetic paths of $S_T$ under the domestic measure
  reproduce the call price to within $0.02$.
- **Greeks vs. finite differences**: every Greek matches a Richardson-extrapolated
  central difference of the *price* to at least 6 decimal places, at both positive
  and negative $\rho$.
- **Vega (regression)**: total vega is $35.479670$ for the call and $39.807548$ for
  the put; the spot component alone is $37.910160$ for both. Version 1.0.0 returned
  $37.910160$ for both sides.
- **Correlation sensitivity (regression)**: $\partial V/\partial\rho = -1.620327$
  (call) and $+1.264925$ (put). Version 1.0.0 returned $-1.264925$ for the put —
  right magnitude, inverted sign.
- **Negative checks**: non-positive `spot_price` / `strike_price` /
  `time_to_expiry_years` / `asset_volatility` / `fixed_fx_rate`, negative
  `fx_volatility`, $|\rho| > 1$, NaN or Inf in any numeric field, and an
  `option_type` such as `'CAL'`, `'C'`, or `''` must all raise `ValueError`.
  $\sigma_X = 0$ and $\rho = \pm 1$ must be accepted.
- Run `python -m unittest test_quanto_options_and_cross_currency_derivative_structures`
  from the `scripts/` directory and confirm a 100% pass rate.

## Related Skills

- `options-implied-volatility-surface-construction`
- `currency-pair-quoting-convention-normalization`
- `multi-currency-pnl-and-fx-conversion`
- `options-greeks-real-time-portfolio-aggregation`
- `cross-asset-correlation-regime-shifts`
- `american-vs-european-style-option-exercise-handling`
