---
name: cold-start-handling-for-newly-listed-instruments
description: >-
  Use when an instrument has too little price history to estimate risk from, such as an
  IPO, spin-off or new token, and a rolling volatility window would return NaN or a
  meaningless number that then sizes a position.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: financial-ml
  tags: risk-management, cold-start, ipo, shrinkage, volatility-estimation, position-sizing
  brokers_frameworks: ""
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when an instrument must be sized and risk-managed before it has enough
history to estimate from: a recent IPO or direct listing, a spin-off, a newly listed
token, a new futures contract month, or a symbol that has just re-listed. A 30-day
rolling volatility over 5 observations is not a small-sample estimate of volatility — for
i.i.d. normal returns the sample variance from `nu = n - 1` degrees of freedom has
relative variance `2 / nu`, so five days give a variance estimate with roughly 70%
relative standard error. Fed to a volatility-scaled or Kelly-style sizer, that noise
becomes leverage.

`ColdStartHandler` answers two questions that are deliberately kept apart:

1. **What volatility should I use?** The sample variance is blended toward a peer-group
   prior with the conjugate weight `w = nu / (nu + nu_0)`, applied to *variances*. `nu_0`
   ("prior strength") is the prior's weight in units of this instrument's own days.
2. **How much capital may it take?** A separate linear ramp, `min(1, n_obs / warmup)`,
   times your base allocation. This is a risk-appetite policy, not an estimator.

## When NOT to Use

- **As a pre-trade risk control.** This is a strategy-side sizing input that trusts its
  own configuration. Where SEC Rule 15c3-5 or an equivalent applies, the hard limits must
  sit in the broker-dealer's automated pre-trade layer; put this in front of that, never
  instead of it. See `sec-rule-15c3-5-risk-controls-us`.
- **When no defensible peer group exists.** Shrinking toward an arbitrary prior is worse
  than declining to trade the name: the output looks confident and is not. A newly listed
  token with no comparable, or a first-of-its-kind structure, has no prior worth using.
- **When the short history is not the binding constraint.** If the instrument cannot be
  borrowed, has no locate (`us-reg-sho-short-sale-locate-requirements`), trades in a
  10-lot a day, or is still inside its first-day auction, sizing is the wrong lever.
  See `liquidity-adjusted-position-sizing`.
- **For the model's *features*, not its risk inputs.** Cold-starting a learned signal for
  a new symbol is a different problem — see
  `categorical-feature-encoding-for-instrument-identity` for identity encoding and
  `transfer-learning-across-correlated-instruments` for borrowing a correlated
  instrument's fitted structure.
- **When the sample is short for a reason other than newness.** A halted, suspended, or
  thinly traded old instrument has a short *usable* sample but not a cold start; treat
  stale data as stale, not as young.

## Prerequisites

- A count of **usable return observations**, not a calendar difference. Listing date
  minus today over-counts: halts, no-trade sessions and missing bars are not
  observations. See `references/workflows.md`.
- A peer prior that is a **single-name** volatility, not a sector-ETF volatility. An ETF
  is diversified; its volatility is systematically below that of its constituents, so
  using it as the prior shrinks a new listing toward a number no single stock realizes.
  Use the median (not mean) realized volatility of comparable single names.
- Sample volatility and prior in the **same units** — both annualized, or neither. The
  module cannot detect a units mismatch; it will happily blend a daily sigma with an
  annualized one.
- A `nu_0` chosen from the prior's own uncertainty, not by feel: for a prior whose
  estimate of `sigma**2` has relative variance `v`, `nu_0 = 2 / v`.
- A warmup window justified by something. Market-structure anchors beat round numbers:
  IPO lock-up expiry (typically 180 days, contractual), index seasoning (S&P U.S.
  indices: 12 months on an eligible exchange). See `references/standards.md`.

## Workflow

1. **Count usable observations.** Count bars actually present with valid prices. Exclude
   sessions the instrument was halted for the whole of, and exclude the listing auction
   itself — the IPO cross is a price discovery event, not a return.
2. **Select the peer group before you need it.** Same sector, comparable size, comparable
   float, and — where relevant — same recency-of-listing cohort. Freeze the selection
   rule; picking the peer group after seeing the instrument's early prints is a decision
   made on the data you are trying to shrink.
3. **Refuse rather than default.** If no prior is available, do not substitute zero and
   do not substitute the index. `process_instrument` raises on a missing or
   non-positive prior deliberately: a zero prior asserts a riskless instrument, and any
   volatility-scaled sizer divides by it.
4. **Estimate with `process_instrument(symbol, n_obs, observed_volatility,
   peer_prior_volatility)`.** Below two observations there is no sample variance at all;
   the observed value is ignored (including if it arrives as NaN) and the prior is
   returned unchanged, with `used_observed_volatility=False` recording that.
5. **Size against `estimated_volatility`, cap against `max_position_cap_pct`.** They are
   independent controls. Note that the cap is a *ceiling*, not a target: a
   volatility-scaled sizer will usually ask for less during probation anyway, and the cap
   only binds when it does not.
6. **Do not treat graduation as certainty.** `is_probationary` going False means the size
   cap has reached the base allocation. It does not mean the volatility estimate is
   unshrunk — with `nu_0 = 10`, an instrument with 250 observations still carries about
   4% prior weight, and that is correct. If you want the prior gone, lower `nu_0`.
7. **Re-evaluate on every rebalance, not once at onboarding.** Both outputs move with
   `n_obs`; a cached day-one decision keeps a stale cap for the rest of the ramp.
8. **Watch for the events that reset the argument.** Lock-up expiry, index addition, and
   the first earnings report all change the float and the volatility regime *after* the
   instrument has technically graduated. A 30-day warmup that ends before the first
   lock-up tranche unlocks has measured a float that no longer exists.

> Full procedure: see `references/workflows.md`.
> Standards and sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Blending standard deviations instead of variances.** `w * sigma_obs + (1 - w) *
  sigma_peer` is not the conjugate posterior and, because the square root is concave, it
  *understates* volatility whenever the sample and prior disagree. For a 5-day IPO at
  `sigma_obs = 0.80` against a `0.20` prior, at the same weight `w = 4/14`, the
  standard-deviation blend returns `0.37` where the variance blend returns `0.46`; the
  pre-2.0 module returned `0.30`, lower still because it also weighted the sample by
  `n / warmup`. A risk control should not be biased toward the low side. The
  standard-deviation blend remains reachable via `shrink_in_variance_space=False`, for
  comparison during migration only.
- **`0.0 * float("nan")` is `nan`, not `0.0`.** The natural "weight the sample by zero
  when there is no sample" implementation emits NaN into the sizer for exactly the
  instrument the skill exists for. Absence of a sample has to be a branch, not a weight.
- **Using a sector ETF as the single-name prior.** Diversification makes the ETF's
  volatility structurally lower; every new listing is then shrunk toward a number no
  constituent realizes, and probation systematically over-sizes.
- **Letting shrinkage end abruptly at the warmup boundary.** A weight of `n / N` says a
  29-day estimate is 97% trustworthy and a 30-day estimate is exact. Neither is true, and
  the jump puts a discontinuity in the sizing of every instrument on its graduation day.
- **Counting calendar days as observations.** IPOs halt — LULD trading pauses in the
  first sessions are routine — and a halted session contributes no return. Calendar
  counting graduates the instrument early on data it does not have.
- **Treating the first-day price range as volatility.** The IPO cross and the first
  session's price discovery are not draws from the return distribution you are trying to
  estimate; including them inflates the sample and, once weighted, the blend.
- **Dropping newly listed names entirely for 60 days.** The opposite failure: a hard
  exclusion forgoes post-IPO drift and liquidity-driven opportunity and, worse, hides the
  onboarding path from testing until the day it silently switches on. Use
  `probation_floor_pct` to throttle rather than exclude.
- **Forgetting the borrow side.** A newly listed name is frequently hard or impossible to
  borrow; a shrunk volatility says nothing about whether the short leg is executable.

## Verification

- Run `python -m unittest discover -s skills/cold-start-handling-for-newly-listed-instruments/scripts` (29 tests),
  or `python tools/run_all_tests.py` for the whole repo.
- Documented example: `ColdStartHandler(warmup_period_days=30, prior_strength_days=10.0)`
  with `n_obs=5`, `observed_volatility=0.80`, `peer_prior_volatility=0.20` returns
  `confidence_weight = 4/14 = 0.2857`, `estimated_volatility = sqrt((10*0.04 + 4*0.64) /
  14) = 0.4598`, and `max_position_cap_pct = 5/30 = 0.1667`.
- Feed `n_obs=0` with `observed_volatility=float("nan")` and confirm the returned
  volatility is exactly the peer prior and `used_observed_volatility` is False.
- Sweep `n_obs` from 0 upward and confirm `max_position_cap_pct` is non-decreasing, never
  exceeds the base allocation, and reaches it exactly at `warmup_period_days`.
- Confirm a zero or NaN peer prior raises rather than returning a zero or NaN volatility.

## Related Skills

- `categorical-feature-encoding-for-instrument-identity`
- `transfer-learning-across-correlated-instruments`
- `dynamic-position-sizing-based-on-realized-volatility`
- `liquidity-adjusted-position-sizing`
- `instrument-universe-change-detection-and-alerting`
- `point-in-time-index-constituent-tracking`
- `us-reg-sho-short-sale-locate-requirements`
- `new-strategy-onboarding-checklist`
