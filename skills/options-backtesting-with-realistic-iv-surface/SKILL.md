---
name: options-backtesting-with-realistic-iv-surface
description: Use when backtesting options strategies (straddles, vertical spreads,
  iron condors) to price each leg off a parametric implied volatility (IV) surface
  across strike moneyness and term structure, instead of a flat ATM volatility that
  misprices the wings and short-dated skew.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- options-backtesting
- iv-surface
- volatility-smile
- greeks-calculation
- black-scholes
- derivatives
brokers_frameworks:
- Options IV Surface Engine
- Black-Scholes-Merton
- Python Standard Library (math)
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when backtesting options strategies (e.g. delta-neutral straddles, vertical spreads, calendar spreads, iron condors) and every leg is currently being priced at a single ATM volatility. A flat IV misprices in two directions at once: across strikes it ignores the equity put skew, so OTM put hedges are systematically bought too cheaply in the backtest; and across expirations it ignores the fact that skew is far steeper for a one-week option than a two-year one, so a calendar or diagonal appears to earn a spread that does not exist. This skill evaluates $\sigma(K/S, T)$ from a quadratic moneyness smile whose offset decays as a power law in $T$, then prices European options and Greeks on it with Black-Scholes-Merton.

## When NOT to Use

- **On American-style options where early exercise matters.** The engine prices European exercise only. Every listed US single-stock option is American, and this engine will underprice ITM puts and calls on a stock about to go ex-dividend. Do not use it to decide whether to exercise — see `early-exercise-assignment-risk-management` and `american-vs-european-style-option-exercise-handling`.
- **To calibrate a surface to live market quotes.** `get_strike_iv` evaluates a closed form; it does not fit quotes and it does not check calendar or butterfly no-arbitrage constraints. For that, use `options-implied-volatility-surface-construction`.
- **Across a discrete cash dividend without adjusting spot yourself.** Only a continuous yield $q$ is modelled. Discrete dividends must be removed from $S$ by the caller before pricing.
- **As a fill simulator.** It returns theoretical mid prices with no spread, no slippage and no exercise/assignment mechanics. Pair it with `execution-realistic-simulation` and `transaction-cost-analysis-tca-integration`.

## Prerequisites

- Underlying price series $S$ (already adjusted for discrete dividends paid before expiry), strike $K$, time to expiration $T$ in years, and risk-free rate $r$.
- ATM implied volatility $\sigma_{\text{atm}}$ for the tenor being priced.
- Smile parameters $\alpha$ (skew slope) and $\beta$ (smile curvature), **calibrated at the 30-day reference tenor** — quoting $\alpha$ without its tenor is meaningless.
- Term-decay exponent $\gamma$ (`skew_term_decay`, default $0.5$) and continuous dividend yield $q$ (default $0$).

## Workflow

1. **Evaluate the surface $\sigma(m, T)$**:
   - Moneyness $m = K/S$; smile offset $\text{offset} = \alpha (m - 1) + \beta (m - 1)^2$.
   - Term scale $s(T) = \min\left(4.0,\; (T_{\text{ref}}/T)^{\gamma}\right)$ with $T_{\text{ref}} = 30/365$, so $s(T_{\text{ref}}) = 1$.
   $$\sigma(m, T) = \sigma_{\text{atm}} + \left[\alpha (m - 1) + \beta (m - 1)^2\right] \cdot s(T)$$
   - **Decision point — $\alpha$ and $\beta$ are tenor-anchored.** They describe the smile at $T_{\text{ref}}$, not at every expiration. Re-fitting them per expiration *and* leaving $s(T)$ on double-counts the term decay; set `skew_term_decay=0.0` if you calibrate per tenor.
   - **Decision point — a clamped IV is not a quote.** If the engine logs a clamp at `MIN_STRIKE_IV`/`MAX_STRIKE_IV`, the quadratic has been extrapolated past its valid range. Narrow the strike universe rather than trading the clamped value.

2. **Price with Black-Scholes-Merton at the strike IV**:
   - $d_1 = \dfrac{\ln(S/K) + (r - q + \tfrac{1}{2}\sigma^2) T}{\sigma \sqrt{T}}$, $\quad d_2 = d_1 - \sigma\sqrt{T}$.
   - $\text{Call} = S e^{-qT} N(d_1) - K e^{-rT} N(d_2)$, $\quad \text{Put} = K e^{-rT} N(-d_2) - S e^{-qT} N(-d_1)$.
   - **Decision point — both legs of a spread at the same strike must use the same $\sigma$.** The surface depends on moneyness, not on call/put, so put-call parity holds exactly. If it does not, something downstream is rounding or re-deriving IV per leg.

3. **Settle expiring positions on intrinsic value, not on an epsilon tenor.**
   - `tte_years=0` returns the terminal payoff with zero time value, zero gamma and `is_expired=True`. A negative tenor raises — it means the backtest clock ran past expiry without settling the contract.

4. **Aggregate Greeks and rebalance the delta hedge** on net portfolio delta $\sum \Delta_i$, remembering theta is per calendar day and vega is per one volatility point.

5. **Audit the skew drag**: re-run the backtest with `skew_alpha=0.0, smile_beta=0.0` and difference the P&L. That gap is the mispricing a flat-IV backtest was booking as alpha.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Pricing OTM puts at flat ATM volatility.** At $\sigma_{\text{atm}} = 20\%$, $\alpha = -0.30$, $\beta = 0.50$, a 30-day $K/S = 0.90$ put's IV is $23.5\%$, not $20\%$. A backtest that buys crash protection at the ATM vol books a hedge cost it could never have paid, and the error compounds every roll.
- **Applying one smile at every expiration.** Skew is not flat in $T$: with $\gamma = 0.5$ the same $10\%$-OTM put carries roughly $2\times$ the skew offset at one week that it carries at one month, and about $0.2\times$ at two years. A calendar spread backtested on a $T$-invariant smile earns a spread that does not exist in the market.
- **Flooring theoretical prices at a minimum tick.** Returning $\$0.01$ for an option worth $10^{-9}$ invents premium on exactly the wings a short-premium backtest is supposed to let expire worthless, and it breaks put-call parity. Quantize to the tick at the fill-simulation layer, never inside the pricer.
- **Rounding the theoretical price to cents.** Rounding a $\$100$ ATM straddle's legs to $\$0.01$ shifts $C - P$ off $S e^{-qT} - K e^{-rT}$ by a fraction of a cent per leg — which is larger than the edge most spread strategies are trying to measure.
- **Accepting an unrecognised option type.** Any pricer that treats "not a call" as "a put" will silently return a put price for `"C"`. Assert on the returned `option_type` rather than trusting the string you passed.
- **Letting `NaN` reach the pricer.** A `NaN` spot from a bad tick propagates through $d_1$ and can emerge as a plausible-looking price rather than an error. Reject non-finite market data at the boundary.
- **Ignoring dividends in $d_1/d_2$.** Continuous $q$ shifts the drift to $r - q$; discrete cash dividends before expiry require replacing $S$ with $S - \sum D_i e^{-r t_i}$ *before* pricing. Neither is optional for single-name equity backtests.
- **Assuming options are never assigned early.** European pricing on an American contract omits the early-exercise premium entirely.

## Verification

- With `OptionsIVSurfaceEngine(risk_free_rate=0.10, skew_alpha=0.0, smile_beta=0.0)`, price $S=42$, $K=40$, $T=0.5$, $\sigma=0.20$: the call is $\$4.76$ and the put $\$0.81$, reproducing the standard Black-Scholes worked example in Hull.
- Confirm $\sigma(0.90, 30\text{d}) = 0.235$ exactly for $\alpha=-0.30$, $\beta=0.50$, $\sigma_{\text{atm}}=0.20$ — the documented formula applied undamped at the reference tenor.
- Confirm put-call parity: $C - P = S e^{-qT} - K e^{-rT}$ to at least 10 decimal places, on the skewed surface and with $q > 0$.
- Confirm each analytic Greek matches a central finite difference of the price function.
- Confirm $s(4 T_{\text{ref}}) = 0.5$ and $s(T_{\text{ref}}/4) = 2.0$ — the $T^{-1/2}$ decay.
- Run `python -m unittest discover -s skills/options-backtesting-with-realistic-iv-surface/scripts` and confirm 100% pass rate.

## Related Skills

- `options-implied-volatility-surface-construction`
- `vectorized-vs-event-driven-backtest-tradeoffs`
- `transaction-cost-analysis-tca-integration`
- `options-greeks-real-time-portfolio-aggregation`
- `early-exercise-assignment-risk-management`
- `american-vs-european-style-option-exercise-handling`
- `execution-realistic-simulation`
