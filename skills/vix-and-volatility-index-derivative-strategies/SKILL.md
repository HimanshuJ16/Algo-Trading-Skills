---
name: vix-and-volatility-index-derivative-strategies
description: >-
  Use when classifying the front-two VIX futures curve as contango, backwardation or
  flat and sizing a position off it, annualising the front-month basis as a static-spot
  carry estimate rather than a forecast.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: multi-asset-derivatives
  tags: vix, volatility-derivatives, vix-futures, term-structure, contango, backwardation, roll-yield, tail-hedging, black-76, position-sizing
  brokers_frameworks: "Cboe Futures Exchange (VX); Cboe VIX Options; Python Standard Library (math, datetime); Python Dataclasses; Black (1976)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

# VIX and Volatility Index Derivative Strategies

Turns a spot VIX quote and the front two VX futures into a classified curve state,
a carry number with its assumption stated, and a contract count that respects the
budget it was given.

This is a classification and sizing tool. It does not forecast the curve, model
margin, calibrate a volatility surface, or route orders.

## When to Use

- When deciding whether today's front-two VX curve is steep enough to justify a
  short-volatility carry position, or inverted enough to justify buying protection.
- When converting a stated exposure budget into a VX contract count that does not
  quietly exceed it.
- When you need the front-month basis annualized, with the static-spot assumption
  attached rather than presented as an expected return.
- When pricing a 1x1 VIX call spread off the front future and you need the honest
  max profit, max loss and breakeven — not the gross spread width.
- When you need the protective buy-stop on a short VX position expressed as a
  price and a dollar loss, so it can be entered as a resting order.

## When NOT to Use

- **As a reason to be short volatility.** A steep curve is a description of the
  present, not a prediction. A short VX future has unbounded loss: on 2018-02-05
  the intraday indicative value of the XIV inverse-VIX ETN fell to at or below 20%
  of the prior close ($108.3681 on 2018-02-02), triggering the acceleration clause
  that terminated the note (Credit Suisse 6-K, 2018-02-06). Carry accrues in cents
  and reverses in dollars. Pair this with
  `kill-switch-and-drawdown-circuit-breakers` before any short-vol position goes
  live.
- **As a margin or capital model.** `notional_exposure_usd` is `contracts x F1 x
  1000`. FCM initial margin on short VX is set by the clearing member, moves with
  volatility, and is not modelled here. A notional budget is not a margin budget
  and is not a loss bound.
- **With an at-the-money implied volatility.** The VIX option smile slopes *upward*
  in strike — a call skew, the mirror image of the equity index put skew — so
  reusing an ATM quote for a far OTM call misprices it. `black76_call` has no
  default IV for that reason. Get per-strike vols from
  `options-implied-volatility-surface-construction`.
- **As a distributional model of VIX.** Black-76 assumes the futures price is
  lognormal. VIX is mean-reverting and positively skewed; Black-76 is the market's
  *quoting* convention for VIX option IVs, and this module uses it as an
  interpolator for quoted vols, nothing more.
- **For VXX, UVXY, SVXY or any volatility ETP.** Those track a constant-maturity
  index and rebalance daily, so their return is a path-dependent compounding of
  daily index returns, not the front-month basis this module computes. Their
  leverage factors have also changed: ProShares cut UVXY from 2x to 1.5x and SVXY
  from -1x to -0.5x effective 2018-02-28. Read the current prospectus; do not
  assume a factor.
- **On the settlement morning.** VX and VIX options both settle to the Special
  Opening Quotation (SOQ) of the VIX Index, computed from the opening prices of
  the constituent SPX options. There is no continuous market to price against
  during that auction, and `days_to_expiry < 1` raises rather than guessing.
- **For weekly VX contracts without checking the calendar.** Cboe lists VIX
  Weeklys futures alongside monthlies, generally listed Thursdays and expiring
  Wednesdays, up to six consecutive weekly expirations. "Front month" and "front
  contract" are then different things, and picking the wrong one changes the sign
  of the slope.

## Prerequisites

- Python 3.9+. Standard library only.
- Spot VIX and the front two VX futures: price, final settlement date, and
  calendar days to expiry for each.
- For the call-spread pricer: **either** the implied volatility of each of the two
  strikes, **or** an observed net debit in index points. There is no default and
  no fallback.
- Portfolio equity in USD, and a house policy for the exposure budget, the premium
  budget and the stop distance. The module's defaults (5% notional, 2% premium,
  30% adverse move) are illustrative starting points, not published standards.

## Workflow

1. **Build the two contracts.** `VIXFuturesContract` validates on construction:
   positive finite price, whole-number `days_to_expiry >= 1`. A settled or
   same-morning contract raises rather than being clamped to one day, because a
   contract with no basis left must be rolled, not carried.
2. **Classify the curve.** `analyze_term_structure(spot_vix, f1, f2)` returns the
   slope in points and percent, the state, the front basis `F1 - S`, and the
   annualized basis. It **raises** if `f2` does not expire strictly after `f1` —
   reversed contracts invert the slope sign and silently turn a short-volatility
   recommendation into a long one, which is the failure mode most worth catching
   at the boundary rather than in the fill.
3. **Read the carry number for what it is.** `annualized_roll_yield_pct` is the
   front-month basis annualized: the return a short-F1 position earns *if spot VIX
   is unchanged at settlement*. It is not a forecast, and it is not the `F1 -> F2`
   curve roll that constant-maturity ETPs harvest. Slope and basis can disagree in
   sign — a backwardated curve with F1 below spot is both BACKWARDATION and a
   negative basis — so never infer one from the other.
4. **For a contango signal, size and place the stop together.**
   `generate_strategy_signal(term_struct, equity)` floors the contract count
   against the notional budget and returns `stop_loss_trigger_price` and
   `loss_at_stop_usd` alongside it. Compare those two numbers before trading:
   at the module's own reference case the stop loss is roughly 96 days of carry.
   If the count comes back 0 with `NONE_INSUFFICIENT_CAPITAL`, the account cannot
   carry one lot inside its own limit — do not round up.
5. **For a backwardation signal, price the spread first.** The tail-hedge branch
   needs a `VIXCallSpreadQuote`; without one it returns 0 contracts and
   `LONG_VIX_CALL_SPREAD_PENDING_QUOTE` rather than inventing a premium. Call
   `price_vix_call_spread(f1, K1, K2, dte, implied_vol_lower=..., implied_vol_upper=...)`
   with the two strikes' own vols, or pass `net_debit=` from the market.
6. **Size the hedge on premium, not notional.** Premium is the entire loss bound
   of a long debit spread, so the premium budget is a true risk budget in a way
   the futures notional budget is not. `tail_hedge_protection_usd` is already net
   of the premium paid.
7. **Roll before settlement week.** Track `days_to_expiry` and roll into F2 while
   there is still a continuous two-sided market. Holding into the SOQ auction
   exchanges a quoted price for an auction print. See
   `futures-contract-roll-automation`.

## Common Pitfalls

- **Using the $1,000 multiplier on the options leg.** VX futures settle at $1,000
  per index point; VIX **options** settle at $100 — the exercise-settlement amount
  is the difference between the settlement value and the strike, multiplied by
  $100. Transposing them overstates every option premium, payoff and budget
  consumption by exactly 10x. This module keeps `VIX_FUTURES_MULTIPLIER` and
  `VIX_OPTIONS_MULTIPLIER` as separate named constants for that reason.
- **Reporting the gross spread width as max profit.** A long 15-point spread
  bought for 3.00 points is worth $1,200 at best, not $1,500: the debit is already
  spent and cannot also be won. Quoting the width makes every spread look like a
  4:1 payoff regardless of what it cost, which is precisely the number a sizing
  decision must not be built on.
- **Assuming a debit instead of pricing one.** A "typical" debit as a fraction of
  width is not a market observable. It moves with the level of the curve, the
  time to settlement and the call skew between the two strikes. Price it or quote
  it; the pricer raises rather than guessing.
- **Rounding a small account up to one lot.** A 5% notional budget on a $50,000
  account is $2,500; one VX contract at F1=16 is $16,000 of notional — a 6.4x
  breach, on the one strategy whose loss is unbounded. `max(1, ...)` in a sizing
  function is a capital-protection defect, not a convenience.
- **Pricing VIX options off spot VIX.** VIX options and the VX future settle to
  the same SOQ, so the *future* is the tradeable forward. In a spike, spot VIX
  moves proportionally more than F1, because F1 prices the expected VIX at
  settlement rather than today's level. Buying calls on a spot spike without
  checking F1 buys a move the forward has already declined to follow.
- **Setting a spot-referenced stop on a futures position.** For the same reason, a
  "+30% on spot VIX" stop and a "+30% on F1" stop are different triggers, and only
  the second can be entered as a resting buy-stop against the position you hold.
  This module computes the stop on F1 and says so.
- **Reading a NaN as a passed check.** Every comparison against NaN is False, so
  `if spot_vix <= 0` lets NaN through and the failure surfaces later as a NaN
  contract count. Inputs are checked for finiteness at the boundary here.
- **Trusting an exactly-on-threshold classification.** F1=20.00 with F2=20.40 is a
  2.00% slope on paper and 1.999999999999993 in binary floating point, which
  classifies FLAT against a 2.0% threshold and suppresses the trade. The slope is
  rounded before comparison so the boundary behaves as documented.

## Verification

Run the test suite from the skill's `scripts/` directory:

```bash
python -m unittest discover -s skills/vix-and-volatility-index-derivative-strategies/scripts -v
```

42 tests. Option prices are checked independently of the implementation — by
put-call parity against a Black-76 put written in the test file, by no-arbitrage
bounds, by monotonicity in strike and volatility, and by the zero-vol limit — and
spread economics are checked against a terminal payoff function written in the
tests rather than by restating the module's algebra. Eight suites are explicit
regressions and fail against the pre-2.0.0 behaviour: the options multiplier, the
gross-width max profit, the fabricated 25%-of-width debit, the `max(1, ...)` sizing
floor, notional-based option sizing, NaN spot VIX, clamped expiry, and reversed
contract order.

Sign-off gates are in `assets/checklist.md`; contract specifications, formulas and
their sources are in `references/standards.md`; the operational sequences are in
`references/workflows.md`.

## Related Skills

- `options-implied-volatility-surface-construction` — produces the per-strike IVs the spread pricer requires.
- `variance-swap-and-volatility-derivative-pricing` — the variance-space counterpart to these futures-space trades.
- `tail-risk-hedging-with-options` — the same budget-versus-protection problem in SPX puts rather than VIX calls.
- `kill-switch-and-drawdown-circuit-breakers` — the out-of-band control a short-vol position must sit behind.
- `futures-contract-roll-automation` — the roll mechanics this skill only signals the timing for.
- `synthetic-continuous-futures-contract-construction` — for backtesting a rolled VX series without splicing artefacts.
- `options-greeks-real-time-portfolio-aggregation` — consumes the spread's exposure at portfolio level.
- `physical-vs-cash-settlement-handling` — VX and VIX options are cash-settled to the SOQ; this covers the general case.
- `cboe-options-exchange-api-integration` — market data and order entry for the venue these contracts trade on.
