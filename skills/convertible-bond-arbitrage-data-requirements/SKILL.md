---
name: convertible-bond-arbitrage-data-requirements
description: >-
  Use when defining the data contract for a convertible bond arbitrage strategy and
  computing the screening metrics on top of it: parity, conversion premium, bond floor
  from the issuer credit spread, delta hedge size and net carry.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: multi-asset-derivatives
  tags: convertible-bond, arbitrage, delta-hedging, parity, conversion-premium, credit-spread, borrow-rate
  brokers_frameworks: "Python Standard Library; Generic Fixed Income"
  version: "1.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when defining the **data contract** for a Convertible Bond (CB) arbitrage
strategy, or when computing the screening metrics that sit on top of it: parity,
conversion premium, bond floor, delta hedge size, and net carry. CB arbitrage buys a
convertible (a hybrid of straight debt plus an equity conversion option) and shorts the
underlying equity to isolate cheap implied volatility, carry, or credit mispricing while
holding the package delta-neutral.

## When NOT to Use

- **As a convertible pricing model.** Delta and implied volatility are *inputs* here.
  Producing them requires a CB model (binomial / Tsiveriotis-Fernandes style) that
  handles credit-risky conversion, issuer calls, puts and soft-call triggers. This
  module deliberately does not implement one.
- **For busted (credit-like) converts.** When parity sits far below the bond floor, the
  position is a credit trade, not a volatility trade; the screening logic flags this
  case and refuses to call it attractive.
- **For live order routing.** The short leg has its own regulatory and borrow-side
  obligations that this module does not enforce — see Related Skills.

## Prerequisites

- Bond static terms: par value, conversion ratio (per the *same* nominal as par value),
  coupon rate, coupon frequency, maturity date.
- CB market data: clean price (points of par) and accrued interest.
- Equity market data: spot price, borrow fee, expected dividend yield.
- Credit data: issuer credit spread in basis points (required for the bond floor).
- Model/vendor analytics: equity delta and implied volatility.
- Funding terms: repo financing rate for the long CB leg, and the rate and haircut your
  prime broker applies to short-sale proceeds.
- Python 3.10+ (standard library only).

## Workflow

1. **Audit data completeness before computing anything.**
   `audit_data_completeness()` separates *missing* inputs from *present but invalid*
   ones (NaN, infinite, negative, delta outside `[0, 1]`). `evaluate_arbitrage()` raises
   on a failed audit rather than screening on partial data — a single NaN price would
   otherwise propagate into every metric and surface as a silent "not attractive".
2. **Compute parity and conversion premium, and state the basis.**
   `Parity = conversion ratio x stock price`.
   `Conversion premium % = (CB price - parity) / parity x 100`.
   The CB price may be the quoted clean price (market convention, the default) or the
   full price including accrued interest. The two differ by up to a full coupon period,
   so a premium reported without its basis is not comparable across sources; configure
   it explicitly with `premium_basis`.
3. **Size the short equity leg — check the delta convention first.**
   `Short shares = CB quantity x conversion ratio x delta`, where delta is the
   **per-share delta in `[0, 1]`**. Desks and vendors also quote CB delta as *shares per
   bond* in `[0, conversion_ratio]`; passing that value into the same formula over-hedges
   by a factor of the conversion ratio. The `[0, 1]` bound is enforced to catch it.
   Round to the venue's lot size and carry the rounding residual as known open exposure.
4. **Compute the bond floor from the issuer credit spread.**
   The floor is the PV of the straight-bond cash flows discounted at
   `risk-free + credit spread`. It is the downside protection the trade is being paid
   for; it moves with the spread, so it must be recomputed on spread updates, not
   treated as a constant. If parity has fallen far below the floor, the convert is
   busted and equity-vol screening no longer applies.
5. **Compute carry over the whole package, not the bond alone.**
   `Net carry = coupon + interest on short proceeds - repo financing - stock borrow fee
   - dividends payable in lieu on the short`. The last three hedge-leg terms scale with
   the **short position market value** (`delta x parity`), not with bond notional —
   applying a borrow rate directly to bond notional misstates the drag whenever delta or
   the parity/price ratio is away from 1.
6. **Screen, then decide.** Cheap vol (`HV - IV` above threshold), a tolerable premium
   and acceptable carry make a *candidate*, not a trade. All thresholds are configurable
   (`ScreenThresholds`) and their defaults are desk heuristics with no authoritative
   basis — calibrate them against your own book before trading on them.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Charging the borrow fee against bond notional.** The stock loan fee and the
  dividends owed in lieu are charged on the *short equity market value* (`delta x
  parity`). At delta 0.60 with parity 900 against a 1,000 bond, that base is 540, not
  1,000 — a fee applied to bond notional overstates the drag by ~85% here, and the sign
  of net carry can flip.
- **Forgetting dividends on the short leg.** A short seller owes the lender substitute
  payments equal to any dividends paid. On a dividend-paying underlying this is often
  larger than the borrow fee and turns a positive-carry screen negative.
- **Ignoring the stock borrow fee entirely.** A hard-to-borrow underlying at a 15% fee
  wipes out coupon carry and volatility edge outright; borrow recall additionally forces
  an unplanned unwind of the hedge at the worst moment.
- **Mixing up the two delta conventions.** See workflow step 3 — the failure mode is a
  short position sized `conversion_ratio` times too large, which is a directional bet,
  not a hedge.
- **Comparing conversion premiums computed on different price bases.** Clean-basis and
  full-basis premiums are not the same number; vendor screens do not always say which
  they use.
- **Static delta hedging.** The package is long gamma: delta moves with spot, so an
  un-rebalanced hedge silently accumulates directional equity risk.
- **Treating the convertible as risk-free debt.** The bond floor is only a floor while
  the issuer performs. Spread widening lowers the floor and hits the long CB leg at the
  same time the equity leg is usually gaining least — the 2005 GM episode is the
  standard example of both legs losing together.

## Verification

- `ConvertibleBondArbitrageEngine().calculate_parity(20.0, 45.0)` must return `900.0`
  (par 1,000, conversion ratio 20, spot 45).
- With a CB clean price of 99.0 (990 per bond), the clean-basis conversion premium must
  be `10.0`% — independently: market conversion price `990 / 20 = 49.50`, premium per
  share `4.50`, ratio `4.50 / 45.00 = 10%`.
- `calculate_delta_hedge_quantity(100, 20.0, 0.60)` must return `1200` shares, and
  passing `12.0` (the shares-per-bond form of the same delta) must raise `ValueError`.
- With par 1,000, a 4% annual coupon paid semi-annually, 3 years to maturity, a 4%
  risk-free rate and a 300bp credit spread, the bond floor must equal the closed-form
  annuity value `20 x (1 - 1.035^-6)/0.035 + 1000 x 1.035^-6 = 920.07`.
- With accrued 10 (full price 1,000), parity 900, delta 0.60, borrow 1%, dividend yield
  2%, repo 4.5% and 4% on short proceeds, net carry must be
  `40 + 21.6 - 45 - 5.4 - 10.8 = +0.40` per bond (`+4bp` on the full price).
- Run `python -m unittest discover -s skills/convertible-bond-arbitrage-data-requirements/scripts`.

## Related Skills

- `options-implied-volatility-surface-construction`
- `cross-asset-hedge-execution-synchronization`
- `short-selling-borrow-cost-and-availability-modeling`
- `us-reg-sho-short-sale-locate-requirements`
- `counterparty-credit-risk-for-otc-derivatives`
