---
name: variance-swap-and-volatility-derivative-pricing
description: >-
  Use when pricing, marking or risk-managing an OTC variance or volatility swap,
  replicating the fair variance strike from an out-of-the-money option strip and
  accruing realised variance on a seasoned position.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: multi-asset-derivatives
  tags: variance-swap, volatility-swap, static-replication, log-contract, convexity-adjustment, realized-variance, options-pricing, mtm-valuation
  brokers_frameworks: "Demeterfi-Derman-Kamal-Zou (1999); Cboe Volatility Index Mathematics Methodology; ISDA Equity Derivatives Definitions; Python Standard Library (math)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when you need a fair variance strike, an accrued realized variance, or
a mark on a seasoned variance swap — pricing a new trade off a listed option chain,
marking a live position for variation margin, or checking a dealer's quote.

The engine provides:

- **Fair variance strike $K_{\text{var}}$** by static log-contract replication over an
  OTM option strip (Demeterfi, Derman, Kamal & Zou, *More Than You Ever Wanted To Know
  About Volatility Swaps*, Goldman Sachs, March 1999 — "DDKZ" — Equation 27).
- **Annualized realized variance** from a price history, zero-mean convention.
- **Fair volatility strike $K_{\text{vol}}$** with the convexity correction, given a
  vol-of-vol input.
- **Notional conversion** $N_{\text{var}} = N_{\text{vega}} / (2 K_{\text{vol}})$.
- **Seasoned MTM**: the accrued variance leg blended with the forward leg priced off
  today's strip.

**Units.** Everything is in volatility points squared. A 20% volatility is
`K_vol = 20.0` and `K_var = 400.0` — never `0.20` / `0.04`. Vega notional is dollars
per volatility point; variance notional is dollars per variance point.

## When NOT to Use

- **To settle a contract.** `calculate_realized_variance` divides by the number of
  returns actually observed. A term sheet divides by the *expected* observation count
  fixed at inception and specifies market-disruption handling for missing days. Use
  this for accrual-to-date monitoring; settle from the confirmation.
- **To strike a volatility swap at `sqrt(K_var)`.** There is no static replication of
  a volatility swap. `sqrt(K_var)` is DDKZ's *naive* estimate (Equation 44) and is a
  strict **upper bound** — striking there means the variance swap dominates the
  volatility swap at every realized volatility. Supply `vol_of_vol_points`.
  `price_variance_swap_mtm` refuses a `VOLATILITY_SWAP` contract for the same reason.
- **On a jumpy underlying, without a haircut.** Replication is exact only for a
  continuous path. A single downward jump of size $J$ leaves a residual whose leading
  term is cubic, $\frac{2}{3T}J^3$ (DDKZ Equations 40–42) — a 10% one-day gap is worth
  ~7.2 variance points on a one-year swap (DDKZ Table 5).
- **On a thin or one-sided chain.** See the truncation pitfall below. The engine
  raises on a one-sided strip rather than returning a number.
- **For VIX futures or listed volatility ETPs.** Those are forwards on an index, not
  OTC swaps — see `vix-and-volatility-index-derivative-strategies`.

## Prerequisites

- Python 3.10+, standard library only (`math`, `dataclasses`, `enum`, `logging`).
- An option chain for the swap's maturity: strike, `CALL`/`PUT`, and a price per
  quote. A full two-sided chain is fine — ITM quotes are discarded and each strike
  contributes once.
- The continuously compounded risk-free rate to maturity, and the spot **as of the
  valuation date** (not inception).

## Workflow

1. **Fix the units before anything else.** Confirm whether the desk quotes
   $K_{\text{vol}}$ in points (20.0) or decimals (0.20), and whether the notional on
   the term sheet is vega or variance. At a 20% strike these differ by a factor of 40
   — see the first pitfall.
2. **Build the contract.** `VarianceSwapContract(...)` with `strike_vol_pct`,
   `vega_notional_usd`, `t_years`, and the inception `spot_price` / `risk_free_rate`.
   Read `variance_notional_usd` rather than dividing by hand.
3. **Replicate the fair variance strike.** Call
   `calculate_fair_strikes(spot, r, t_years, option_strip)`. The engine picks
   $S^* = K_0$, the largest available strike at or below the forward
   $F = S_0 e^{rT}$, uses puts below it, calls above it, and the **average of the put
   and call at $K_0$** (the Cboe convention). It then applies DDKZ Equation 27:

   $$K_{\text{var}} = \frac{2}{T}\left[rT - \left(\frac{F}{S^*} - 1\right) - \ln\frac{S^*}{S_0}\right] + \frac{2}{T}e^{rT}\sum_i \frac{\Delta K_i}{K_i^2}Q(K_i)$$

   The bracketed term is **not** zero in general. It vanishes only when $S^* = F$
   exactly, which a discrete strike grid almost never delivers.
4. **Read the replication diagnostics, don't just take the number.** Check
   `reference_strike`, `min_strike`, `max_strike`, and `num_options_used`. If the
   engine logged a truncation warning, treat $K_{\text{var}}$ as a *lower* bound and
   decide whether the missing wings are material at this maturity — they are not the
   same size at three months and at one year.
5. **Decide the volatility strike deliberately.** For a variance swap, stop at step 4.
   For a volatility swap, pass `vol_of_vol_points` — the standard deviation of
   realized volatility in volatility points, from a model or a VIX-of-VIX style
   market. Under DDKZ's Appendix D normal-volatility assumption
   $K_{\text{var}} = K_{\text{vol}}^2 + \operatorname{Var}(\sigma_R)$ exactly, so
   $K_{\text{vol}} = \sqrt{K_{\text{var}} - \text{vol-of-vol}^2}$ and
   `convexity_adjustment_pct` is that variance. Leaving the default of `0.0` gives the
   naive upper bound, not a tradeable strike.
6. **Compute the accrued variance.** `calculate_realized_variance(price_history)` on
   the official closing prices named in the confirmation, with the term sheet's
   annualization factor (DDKZ use 260; the default here is 252). The sample mean is
   deliberately not subtracted.
7. **Mark the seasoned contract.** `price_variance_swap_mtm(..., current_spot=...,
   current_risk_free_rate=...)`. **Pass both.** They default to the *inception* values
   with a warning, which puts the forward — and with it the put/call boundary — in the
   wrong place. The blend is exact because variance is additive in time:

   $$V_{\text{exp}} = \frac{t}{T}\sigma^2_{\text{realized}} + \frac{T-t}{T}K_{\text{var,rem}}, \qquad \text{MTM} = e^{-r(T-t)}N_{\text{var}}\left(V_{\text{exp}} - K_{\text{var,strike}}\right)$$

8. **Re-mark on a schedule, not on a move.** Both legs move: the accrued leg grows
   with each new close, and the forward leg re-prices with the strip.

## Common Pitfalls

- **Quoting variance notional as if it were vega notional.** P&L is linear in
  *variance*, not volatility. $N_{\text{var}} = N_{\text{vega}} / (2K_{\text{vol}})$,
  so a $100,000 vega-notional trade at a 20% strike is a $2,500 variance notional —
  a factor of **40**. Sizing the variance notional at $100,000 is a 40x
  over-exposure, and the error only shows up once realized volatility moves.
- **Passing a full option chain and trusting the $\Delta K$ grid.** A two-sided chain
  quotes a put *and* a call at every strike. If $\Delta K_i$ is computed over the raw
  list, every interior spacing is halved and $K_{\text{var}}$ is understated by
  roughly half. Collapse to one OTM price per strike **first**, then build the grid.
  This engine does; a hand-rolled sum usually does not.
- **Dropping the $S^*$ anchor term because "it's zero at the forward".** It is zero
  when $S^* = F$. Once you anchor on a traded strike $K_0 \ne F$ — which you must, to
  avoid a gap at the boundary — the term is live. It reduces to Cboe's
  $-\frac{1}{T}\left(\frac{F}{K_0}-1\right)^2$ to second order. Omitting it, or
  substituting $\frac{1}{T}\left(\frac{F}{S_0} - 1 - \ln\frac{F}{S_0}\right)$, biases
  $K_{\text{var}}$ by ~13 variance points at $r = 5\%$, $T = 1$.
- **Reading a truncated strip as a fair price.** A finite strike range **always**
  understates the fair variance, and the shortfall grows with maturity: DDKZ Table 4
  prices a flat-25%-vol underlying at $(25.0)^2$ from a 50%–200% strike range but at
  only $(23.0)^2$ from a 75%–125% range at one year — two full volatility points. At
  three months the same narrow range costs only 0.1 points. Never treat a
  narrow-strip $K_{\text{var}}$ as a fair mid.
- **Accepting a one-sided strip.** A calls-only chain silently contributes nothing
  below the forward; the arithmetic still returns a number, and it is badly low. Check
  that both wings are present before integrating — this engine raises instead.
- **Substituting the strike for missing data.** If a seasoned contract has accrued
  time but no price history, or remaining time but no strip, there is no mark. Filling
  the gap with $K_{\text{var,strike}}$ produces a mark of exactly zero P&L on the
  missing leg, which reads as "flat" rather than "unknown" on a risk blotter.
- **"Fixing" the zero-mean convention into a sample variance.** DDKZ (page 2) note the
  zero-mean method "is theoretically preferable, because it corresponds most closely
  to the contract that can be replicated by options portfolios". Subtracting the
  sample mean makes a trending underlying look calm.

## Verification

```bash
python -m unittest discover -s skills/variance-swap-and-volatility-derivative-pricing/scripts
```

The suite checks the engine against sources outside itself, not against its own
arithmetic:

- A dense, wide strip of flat-volatility Black-Scholes prices must return
  $K_{\text{var}} = \sigma^2$ — DDKZ Figure 5 states the theoretical value is exactly
  $(20)^2 = 400$ at $\Delta K \to 0$.
- The DDKZ Table 1 worked example (page 21: $S_0 = 100$, $r = 5\%$, $T = 0.25$,
  strikes 50–150 spaced 5 apart, 20% ATM vol with a 1-point-per-5-strike skew) must
  reproduce the paper's $K_{\text{var}} = (20.467)^2$.
- The DDKZ Table 4 truncation cases must reproduce $(25.0)^2$ wide versus $(24.9)^2$
  and $(23.0)^2$ narrow, at three months and one year respectively.
- A full two-sided chain and the equivalent OTM-only strip must agree.
- $K_{\text{var}} - K_{\text{vol}}^2$ must equal the supplied vol-of-vol variance
  exactly, and $K_{\text{vol}} < \sqrt{K_{\text{var}}}$ strictly.

Then work `assets/checklist.md` before a trade goes out.

## Related Skills

- `vix-and-volatility-index-derivative-strategies` — listed volatility forwards, where
  the same $\Delta K / K^2$ weighting appears as the published index methodology.
- `options-implied-volatility-surface-construction` — build and clean the strip this
  skill integrates over.
- `options-greeks-real-time-portfolio-aggregation` — aggregate the option-strip hedge
  alongside the swap.
- `tail-risk-hedging-with-options` — the downside-skew exposure that lifts
  $K_{\text{var}}$ above ATM implied variance.
- `quanto-options-and-cross-currency-derivative-structures` — the cross-currency
  analogue when the underlying and the settlement currency differ.
- `total-return-swap-synthetic-exposure` — the other major OTC swap wrapper.
- `warrants-and-structured-product-integration` — structured wrappers that embed
  variance exposure.
