---
name: options-implied-volatility-surface-construction
description: >-
  Use when calibrating an implied volatility surface to live option quotes: inverts Black-Scholes market prices to implied volatilities, least-squares fits a quadratic moneyness smile per expiration, and audits the two static no-arbitrage conditions — calendar spread (total implied variance non-decreasing at fixed log-forward moneyness) and butterfly (call price convex in strike, so the risk-neutral density is non-negative).
domain: Quantitative Finance & Derivatives Modeling
subdomain: Implied Volatility Surface Calibration & Arbitrage Verification
tags: ["iv-surface", "volatility-smile", "black-scholes", "arbitrage-free", "calendar-spread", "butterfly-arbitrage", "options-pricing", "derivatives"]
brokers_frameworks: ["Black-Scholes-Merton", "Gatheral-Jacquier Static Arbitrage Conditions", "Python Standard Library (math)"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when you have discrete exchange-traded option quotes and need the surface $\sigma(K, T)$ they imply, *plus* evidence that the surface you fitted does not contain a free lunch. Naive interpolation and unconstrained fitting both routinely produce surfaces with static arbitrage: total implied variance that falls with maturity (a calendar spread bought for a negative price) or a call price concave in strike (a butterfly with negative cost, equivalently a negative risk-neutral probability density). A backtest priced off such a surface will find "edge" that is an artifact of the fit.

The engine inverts quoted prices to implied volatilities by bisection, least-squares fits the quadratic smile $\sigma(m) = \sigma_{\text{ATM}} + \alpha(m-1) + \beta(m-1)^2$ per expiration, evaluates the surface on a strike $\times$ expiration grid, and audits both no-arbitrage conditions against the definitions in Gatheral & Jacquier, *Arbitrage-free SVI volatility surfaces* (arXiv:1204.0646).

## When NOT to Use

- **To price a backtest off a surface you already trust.** This engine calibrates and audits; it has no Greeks and no term-structure decay of the skew. Use `options-backtesting-with-realistic-iv-surface`, which evaluates a parametric surface with a power-law skew decay and returns analytic Greeks — and which deliberately does *not* check arbitrage, delegating that here.
- **On American option prices without adjustment.** Every listed US single-stock option is American. Inverting an American price through a European formula returns a volatility inflated by the early-exercise premium, worst for ITM puts and for calls across an ex-dividend date. See `american-vs-european-style-option-exercise-handling`.
- **Across a discrete cash dividend without adjusting spot.** Only a continuous yield $q$ is modelled. Remove the present value of dividends paid before expiry from $S$ yourself, first.
- **As a far-wing extrapolator.** The quadratic smile violates Lee's moment formula asymptotically (see Common Pitfalls). Do not evaluate it far outside the moneyness range your quotes actually cover.
- **As a proof of no arbitrage.** The conditions are statements for all $k \in \mathbb{R}$ and all $t > 0$; the engine checks the finite grid you hand it. A clean report is evidence on that grid, not a theorem.
- **As raw SVI.** This engine fits a quadratic in moneyness, not the five-parameter SVI form $w(k) = a + b\{\rho(k-m) + \sqrt{(k-m)^2 + \sigma^2}\}$. The *arbitrage conditions* used here come from the SVI literature; the parameterization does not. Earlier versions of this skill described the model as "SVI / quadratic smile" — that was wrong and has been removed.

## Prerequisites

- Option market quotes (`strike`, `tte_years`, `market_price`, `option_type`), one expiration at a time for calibration, with spot already adjusted for discrete dividends paid before expiry.
- `IVSurfaceConfig`: `spot_price`, `risk_free_rate` (continuously compounded), `atm_vol`, `skew_alpha`, `smile_beta`, `dividend_yield`.
- Quotes whose vega is large enough to identify a volatility. Deep out-of-the-money short-dated quotes carry almost no volatility information and the engine will say so.

## Workflow

1. **Invert market prices to implied volatilities** — `implied_volatility_from_price`:
   - **Decision point — check the no-arbitrage price bounds before solving.** A call must satisfy $\max(Se^{-qt} - Ke^{-rt}, 0) < C < Se^{-qt}$; a put, $\max(Ke^{-rt} - Se^{-qt}, 0) < P < Ke^{-rt}$. A quote at or outside those bounds has *no* implied volatility. The engine raises rather than returning a clamped number, because a fabricated wing volatility survives into the fit and then into every price derived from it.
   - **Decision point — bisection, not Newton-Raphson.** The BS price is strictly increasing in $\sigma$, so a bracketed bisection converges unconditionally. Newton's step is $\text{residual}/\text{vega}$, and vega collapses towards zero deep in and out of the money and at very short expiry, where the step explodes.
   - **Decision point — a converged solver is not the same as an identified volatility.** The engine estimates the resolution as (float64 price resolution) / vega and logs a warning when it exceeds $10^{-6}$. Exclude flagged quotes from the fit; do not let a number that is arbitrary to $\pm 0.0075$ pull the wings.
2. **Calibrate the smile per expiration** — `calibrate_smile_from_quotes`:
   - Least-squares fit of $\sigma(x) = \sigma_{\text{ATM}} + \alpha x + \beta x^2$ where $x = K/S - 1$, solved from the $3\times3$ normal equations.
   - **Decision point — one expiration per fit.** Mixing tenors fits a surface cross-section as if it were a slice; the engine raises. Fewer than three distinct moneyness levels leaves the coefficients unidentified and also raises, rather than returning an arbitrary solution to a singular system.
3. **Evaluate the grid and audit static arbitrage** — `construct_surface_grid`:
   - **Calendar spread**: total implied variance $w(k, \tau) = \sigma^2 \tau$ must be non-decreasing in $\tau$ at **fixed log-forward moneyness** $k = \ln(K/F_\tau)$, $F_\tau = Se^{(r-q)\tau}$ — Gatheral & Jacquier Lemma 2.1, whose proof compares $K_1/F_{\tau_1} = K_2/F_{\tau_2}$.
   - **Decision point — auditing at fixed *strike* is the wrong comparison.** Whenever $r \neq q$ the forward drifts, so the same strike sits at a different $k$ at each expiration. At $r = 5\%$ a one-year gap moves the forward ~5%, several strikes on a listed chain. This is a real defect that hides real violations: see Verification for a surface every fixed-strike scan calls monotone and the correct fixed-$k$ scan rejects.
   - **Butterfly**: for each consecutive strike triple, the spacing-weighted butterfly $w_1 C(K_1) + w_3 C(K_3) - C(K_2)$, with $w_1 = (K_3-K_2)/(K_3-K_1)$ and $w_3 = (K_2-K_1)/(K_3-K_1)$, must be non-negative. This is the tradeable form of $\partial^2 C/\partial K^2 \ge 0$, which by Breeden & Litzenberger (1978) is the risk-neutral density.
   - **Decision point — use the spacing weights, not $0.5/0.5$.** Listed chains are not equally spaced. On strikes $(90, 95, 150)$ the equal-weighted butterfly is negative on a perfectly benign surface; the correctly weighted one is positive.
   - **Decision point — a fit that passes slice-by-slice can still fail as a surface.** Independent per-expiration calibration says nothing about calendar monotonicity. Always audit after calibrating.
4. **Read the report** — `IVSurfaceConstructionReport`:
   - **Decision point — "no violations found" is not "arbitrage-free".** A grid with one expiration cannot be audited for calendar arbitrage, and one with two strikes cannot be audited for butterfly arbitrage. `calendar_audit_performed` / `butterfly_audit_performed` record what actually ran, the status is `UNAUDITED_SURFACE`, and `is_arbitrage_free` is `False`. Unaudited is not clean.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Auditing calendar arbitrage at fixed strike instead of fixed log-forward moneyness**: the condition $\partial_\tau w \ge 0$ is stated at constant $K/F$. With $r \neq q$ the forward moves between expirations, so a fixed-strike scan compares two different points on the surface and reports a steeply skewed, genuinely arbitrageable surface as monotone.
- **Retrying a quote outside the no-arbitrage bounds with a clamped volatility**: below intrinsic there is no solution. Clamping to a floor produces a plausible number that then anchors the whole wing of the fit.
- **Trusting an 8-decimal implied volatility from a near-zero-vega quote**: a three-month 40%-OTM call at 8% volatility has a total time value of ~$2.6 \times 10^{-15}$; every volatility within roughly $\pm 0.0075$ prices to the same float64 number. Push slightly further out and the price underflows to exactly intrinsic and carries no volatility information at all.
- **Equal-weighting an unequally-spaced butterfly**: reports arbitrage that does not exist, and can mask arbitrage that does.
- **Extrapolating the quadratic smile into the far wings**: Lee, *The Moment Formula for Implied Volatility at Extreme Strikes* (Mathematical Finance 14(3), 2004), proves that absence of arbitrage bounds the tail by $\sigma^2_{\text{BS}} \le \beta|k|/\tau$ with $\beta \le 2$ — total implied variance may grow at most **linearly** in $|k|$. A quadratic-in-moneyness $\sigma$ grows it like $|k|^4$. The $[0.05, 3.0]$ clamp bounds the damage numerically; it does not make the wings arbitrage-free.
- **Treating a clamped wing as an audited one**: when the IV clamp binds, the surface there is no longer the parametric one and a wing that would have failed can pass. Every binding clamp is logged; a report carrying clamp warnings is inconclusive in the wings.
- **Silently substituting the flat ATM level for a missing tenor in the term structure**: that fabricates a term structure and can turn a real calendar violation into a clean report. The engine raises on a missing key.
- **Rounding implied volatilities before squaring them into variances**: rounding to 4dp can flip a marginal calendar comparison in either direction. Nothing in this engine is rounded; quantize at the presentation layer.

## Verification

- **Pricing**: `black_scholes_price("CALL", 100, 100, 1.0, 0.20, 0.05)` $= 10.4506$, the standard worked example. Confirm put-call parity $C - P = Se^{-qT} - Ke^{-rT}$ holds to $10^{-12}$ with $q > 0$. Confirm `tte=0` and `vol=0` return discounted intrinsic, not a clamped time value.
- **Smile**: with $\sigma_{\text{ATM}}=0.20$, $\alpha=-0.30$, $\beta=0.50$, hand-compute $\sigma(0.90) = 0.20 + 0.030 + 0.005 = 0.2350$ and $\sigma(1.10) = 0.20 - 0.030 + 0.005 = 0.1750$. Version 1.0.0 returned $0.2175$ and $0.1875$ — it scaled the offset by an undocumented $0.5$.
- **Inversion**: round-trip price$\to$IV$\to$price across strikes $70$–$140$ and tenors one month to two years. Every case where the round trip is worse than $10^{-6}$ must be flagged by the conditioning warning.
- **Calendar (regression)**: with $\alpha = -2.0$, $\beta = 0$, strikes $(98, 100, 102)$ and $\tau \in \{0.5, 1.0\}$, the fixed-strike scan reports all three strikes monotone. The correct fixed-$k$ scan finds a violation at $k = \ln(102/F_{0.5}) = -0.005197$: $w = 0.012800$ at $\tau=0.5$ falls to $0.011741$ at $\tau=1.0$.
- **Calendar (term structure)**: 40% ATM vol at three months falling to 15% at one year gives $w(0.25) = 0.0400 > w(1.0) = 0.0225$ $\implies$ `CALENDAR_ARBITRAGE_VIOLATION`.
- **Butterfly**: with $\alpha = -2.0$, strikes $(95, 100, 105)$ at $\tau = 1$, the smile gives $\sigma = (0.30, 0.20, 0.10)$ and the butterfly prices to $-0.0269$ $\implies$ `BUTTERFLY_ARBITRAGE_VIOLATION`.
- **Unaudited**: a one-expiration grid and a two-strike grid must both report `UNAUDITED_SURFACE` with `is_arbitrage_free` $=$ `False`.
- **Negative checks**: non-positive strike/spot/tte/atm_vol, NaN or Inf anywhere, an unknown `option_type` such as `"C"`, mixed expirations in one calibration, fewer than three distinct moneyness levels, and a missing `atm_vol_by_tte` key must all raise `ValueError`.
- Run `python -m unittest discover -s skills/options-implied-volatility-surface-construction/scripts` and confirm a 100% pass rate.

## Related Skills

- `options-backtesting-with-realistic-iv-surface`
- `options-chain-data-normalization-across-vendors`
- `options-greeks-real-time-portfolio-aggregation`
- `american-vs-european-style-option-exercise-handling`
- `real-time-greeks-recalculation-on-market-moves`
