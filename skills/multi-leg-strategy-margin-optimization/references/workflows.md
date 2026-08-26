# Workflows for Multi-Leg Strategy Margin Optimization

## 1. Validate the payload

Reject rather than coerce. `MarginInputError` is raised for an unrecognised
`option_type` or `action`, a non-positive or fractional `quantity`, a negative or
non-finite `premium`, a non-positive `strike`, `underlying_price` or
`contract_multiplier`, and an expiration that is neither an ISO `YYYY-MM-DD` string
nor a `datetime.date`.

Direction is carried by `action`, never by the sign of `quantity`. Accepting a signed
quantity and taking its absolute value turns a short leg into a long one and removes
its margin entirely.

## 2. Compute the un-offset requirement

Margin every leg as though it had been submitted alone (FINRA 4210(f)(2)(E) /
Cboe Rule 10.3), with $M$ the contract multiplier and $Q$ the contract count:

- Long option: $P \times M \times Q$ — paid for in full.
- Short call: $\max(0.20 S - \max(0, K - S) + P,\; 0.10 S + P) \times M \times Q$.
- Short put: $\max(0.20 S - \max(0, S - K) + P,\; 0.10 K + P) \times M \times Q$.

Set `underlying_pct` to 0.15 for broad-based index options. The put floor uses the
**exercise price**; reusing the call floor understates deep out-of-the-money short
puts and overstates deep in-the-money ones.

## 3. Gate on expiration

Group the legs by expiration date.

- **One expiration** → proceed to step 4.
- **More than one** → apply no offset, label the position `MULTI_EXPIRY_COMBINATION`,
  and return the un-offset requirement. The payoff computation in step 4 nets
  intrinsic values as if all legs settled together, which is not a calendar or
  diagonal's loss profile.
- Additionally, if any short leg expires **after** the longest long leg, emit the
  4210(f)(2)(H) warning: the short must expire on or before the long, so the
  structure is not a spread and the short leg stands naked once the long expires.

## 4. Bound the risk before pricing it

Compute the payoff slope above the highest strike as the net long-minus-short call
contract count. If it is negative the loss is unbounded (ratio spread, uncovered
strangle, four naked shorts) — apply no offset, label
`UNDEFINED_RISK_COMBINATION`, and margin the shorts naked.

This check must come **before** any pattern classification. Classifying by leg count
and type mix is what lets four uncovered short options be priced as an iron condor.

## 5. Maximum potential loss

Per FINRA Regulatory Notice 12-44, evaluate the netted intrinsic value of the
combination at price points corresponding to every exercise price present, and take
the greatest loss. Spot 0 is evaluated in addition to bound the downside tail below
the lowest strike. Because the payoff is piecewise linear with breakpoints only at
strikes, extrema can only occur at those points or in the tails, so this enumeration
is exhaustive.

This is what makes an iron condor's requirement the wider wing rather than the sum of
both: the underlying cannot finish below the put wing and above the call wing at the
same time, and the netting shows it without a special case.

## 6. Combination requirement

$$\text{combined} = \underbrace{\textstyle\sum_{\text{long}} P M Q}_{\text{paid in full}} + \min\left(\text{naked short requirement},\; \text{maximum potential loss}\right)$$

Record which term bound (`binding_constraint`). When the naked requirement is the
lesser one — a very wide credit spread, or a single short leg — recognising the
combination frees nothing, and the status says `NO_OFFSET_NAKED_REQUIREMENT_BINDS`
rather than reporting an "optimised" position that was never optimised.

## 7. Savings and net capital

- `margin_savings_usd` = un-offset requirement − combination requirement, both gross,
  so the comparison is apples-to-apples.
- `net_capital_required_usd` = combination requirement − short-sale proceeds. This is
  the buying-power effect: *maximum risk less net credit* for a credit spread,
  the *net debit* for a debit spread.

Worked check — AAPL iron condor, $S = 150$, long 140P @ 0.80 / short 145P @ 2.00 /
short 155C @ 2.00 / long 160C @ 0.80, one expiration, $M = 100$:

| Quantity | Value |
|---|---|
| Long premium paid in full | $160 |
| Naked short requirement (2 × $2,700) | $5,400 |
| Un-offset requirement | $5,560 |
| Maximum potential loss | $500 (one $5 wing) |
| Combination requirement | $160 + \min(5{,}400,\ 500) = \$660$ |
| Short-sale proceeds | $400 |
| Net credit | $240 |
| Net capital to deposit | $660 - 400 = \$260$ |
| Reduction | 88.1% |

(The short-sale proceeds are $400 — $2.00 on each of two short legs. The *net* credit
of $240 is those proceeds less the $160 of long premium, which the combination
requirement has already charged in full. Deposit \$260 = the \$500 maximum risk less
the \$240 net credit, the figure a broker quotes as the buying-power effect.)

## 8. Reconcile before redeploying

The figure is an SRO minimum. Compare it against the broker's own requirement — house
add-ons are common and some brokers decline combinations the rule would permit — and
release capital only up to the reconciled number.

## 9. Route the legs as one order

The requirement above is what the broker charges for a *recognised combination*. Legs
submitted as separate orders are margined as separate positions until all of them
arrive. Between the first and last fill the account carries the step-2 un-offset
requirement, and an account sized against the step-6 figure will breach. See
`calendar-spread-and-multi-leg-order-atomicity`.
