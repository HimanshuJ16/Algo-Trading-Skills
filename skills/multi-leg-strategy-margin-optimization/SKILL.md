---
name: multi-leg-strategy-margin-optimization
description: >-
  Use when sizing listed multi-leg option positions in a strategy-based margin account,
  computing the FINRA Rule 4210(f)(2) spread and maximum-potential-loss requirements so
  short legs are not margined naked.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: multi-asset-derivatives
  tags: options-margin, multi-leg-strategy, reg-t, finra-4210, vertical-spread, iron-condor, margin-optimization
  brokers_frameworks: "FINRA Rule 4210(f)(2); Cboe Rule 10.3 (strategy-based margin); Regulation T (12 CFR 220.12); Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when sizing or pre-trade-checking multi-leg listed option positions (vertical spreads, iron condors, iron butterflies, reverse iron condors) in a **strategy-based margin account**. Submitting the legs as uncombined single orders forces the broker to margin every short leg naked under FINRA Rule 4210(f)(2)(E) — roughly 20% of underlying value per short contract — while an exchange-recognised combination caps the short-leg margin at the position's *maximum potential loss*. On the worked AAPL iron condor below that is a $5,560 un-offset requirement against $660, and the size of that gap is the whole point of routing the legs as one combo order.

Use it also to answer "what happens if a leg doesn't fill" — run the partial leg set through the engine and read the un-offset number.

## When NOT to Use

- **As a portfolio-margin number.** Portfolio margin accounts are margined under FINRA Rule 4210(g) using the OCC's Theoretical Intermarket Margining System (TIMS), which revalues the whole portfolio across a scenario set rather than applying strategy templates. TIMS requirements are usually materially lower and are not derivable from anything here. See `options-margin-span-calculation-global`.
- **As the broker's number.** These are SRO minimums. Brokers routinely impose higher house requirements, and some decline to recognise combinations this rule would permit. Reconcile before committing freed capital.
- **For calendar or diagonal spreads.** The maximum-potential-loss computation assumes all legs expire together. Multi-expiry structures are detected and returned with no offset rather than mispriced.
- **For non-option legs.** Covered calls, collars against stock, and protective puts against a long equity position involve a stock leg this module does not model.
- **As a live margin-call or liquidation monitor.** The figure is a point-in-time estimate from the premiums you pass in. Use `margin-utilization-circuit-breaker` and the broker's live requirement for that.

## Prerequisites

- `MultiLegStrategyPayload`: `symbol`, `underlying_price`, `legs`, and `contract_multiplier` (default 100 — read it from the contract spec; adjusted contracts after a split, merger or special dividend deliver a different number).
- `OptionLeg` per leg: `option_type` (`CALL`/`PUT`), `action` (`BUY`/`SELL`), `strike`, `expiration` (ISO `YYYY-MM-DD` or `datetime.date`), `quantity` (positive contract count — direction lives in `action`), `premium` (per-share).
- Premiums on the right basis: the rule is stated on option **market value**, so pass current marks for a maintenance figure and trade prices for an initial figure.
- `underlying_pct` set for the option class: `0.20` for equity options (default), `0.15` for broad-based index options.

## Workflow

1. **Validate and reject, never coerce**: unrecognised `option_type`/`action`, non-positive or fractional quantity, negative or non-finite premium, non-positive strike or underlying, unparseable expiration all raise `MarginInputError`. Every available coercion here understates the requirement, and an understated margin number is the failure that empties the account.
2. **Un-offset requirement** — every leg margined independently:
   - Long option: paid for in full, $P \times M \times Q$.
   - Short call: $\max(0.20 S - \text{OTM} + P,\; 0.10 S + P) \times M \times Q$.
   - Short put: $\max(0.20 S - \text{OTM} + P,\; 0.10 K + P) \times M \times Q$ — note the floor is 10% of the **exercise price** for puts, 10% of the **underlying** for calls.
3. **Expiration gate**: if the legs span more than one expiration, apply **no offset** and stop — a diagonal's loss is not the strike-width payoff computed below. If a short leg expires *after* the longest long leg, flag it separately: 4210(f)(2)(H) requires the short to expire on or before the long, so that structure is not a spread and the short is naked once the long expires.
4. **Maximum potential loss** (4210(f)(2)(H), per Regulatory Notice 12-44): net the legs' intrinsic values at price points corresponding to every exercise price in the combination, plus spot 0 to bound the downside tail, and take the greatest loss. The payoff is piecewise linear with breakpoints only at strikes, so extrema can only sit at those points or in the tails.
   - **First check the upper tail**: if the net call position above the highest strike is short (a ratio spread, an uncovered strangle), loss is unbounded — apply no offset and margin the shorts naked. Do not classify by leg shape: four naked shorts have the leg shape of an iron condor and none of its defined risk.
5. **Combination requirement**: longs paid in full, plus **the lesser of** the naked (E) requirement and the maximum potential loss. On a very wide credit spread the naked figure is the lesser one and the combination frees nothing — the report says so via `binding_constraint`.
6. **Savings audit**: compare un-offset against combination on the same gross basis. Report `net_capital_required_usd` separately — the combination requirement less the credit received, i.e. the buying-power effect, which equals *max risk less net credit* for a credit spread and the *net debit* for a debit spread.
7. **Reconcile before deploying freed capital** against the broker's own requirement.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Charging a debit spread the strike width.** A long 150 call / short 155 call can never lose intrinsic value: the netted intrinsic is $\geq 0$ at every price point, so the maximum potential loss is $0$ and the position costs its $300 net debit. Cboe's rule is "pay for the net debit in full", not the $500 width. The same error inflates every reverse iron condor and long butterfly.
- **Classifying by leg shape rather than by payoff.** Two short puts plus two short calls have an iron condor's leg count and type mix. Pricing that on wing-width-minus-credit reports a near-zero requirement for four uncovered short options.
- **Taking the quantity from the first leg.** A long 1 / short 5 ratio spread margined as a 1-lot vertical leaves four uncovered short calls entirely unmargined. Derive risk from the whole leg set, not from `legs[0].quantity`.
- **Unlinked execution legging risk.** Even a correctly-priced combination gets naked treatment if the legs are submitted as separate orders — the broker margins what it has received. Between the first and last fill you hold uncovered shorts at the un-offset requirement; that window is what step 2's number sizes.
- **Assuming the long leg outlives the short.** A short expiring after its long is not a spread under 4210(f)(2)(H). It receives spread treatment from nobody and becomes naked the moment the long expires.
- **Silently returning zero margin for an unrecognised option type.** A typo'd `option_type` on a short leg must raise, not fall through to $0$.
- **Reading a strategy-based number as a portfolio-margin number.** The two methodologies do not agree and neither is derivable from the other.
- **Ignoring early assignment on near-the-money short legs.** American-style shorts can be assigned before expiry, converting a defined-risk combination into a stock position with an entirely different requirement. See `early-exercise-assignment-risk-management`.

## Verification

- Instantiate `MultiLegStrategyMarginOptimizerEngine()`. Audit an AAPL iron condor at $S = \$150$ (long 140P @ \$0.80, short 145P @ \$2.00, short 155C @ \$2.00, long 160C @ \$0.80, one expiration): verify `uncombined_requirement_usd == 5560.0` (\$160 of long premium plus \$2,700 for each short leg), `max_potential_loss_usd == 500.0` (one \$5 wing, not both), `combined_requirement_usd == 660.0`, and `net_capital_required_usd == 260.0` (\$500 max risk less the \$240 credit) — an 88.1% reduction against legging in.
- Verify a debit vertical (long 150C @ \$5.00 / short 155C @ \$2.00) returns `max_potential_loss_usd == 0.0` and `net_capital_required_usd == 300.0`, **not** \$500.
- Verify four short legs (140P/145P/155C/160C, all `SELL`) return `strategy_type == "UNDEFINED_RISK_COMBINATION"`, `max_potential_loss_usd is None`, and the full \$9,560 naked sum with zero savings.
- Verify a long 1 / short 5 ratio spread returns the full \$14,000 naked requirement, and that legs on two expirations return `STATUS_NO_OFFSET_MULTI_EXPIRY`.
- Run `python -m unittest discover -s skills/multi-leg-strategy-margin-optimization/scripts` and confirm a 100% pass rate.

## Related Skills

- `margin-utilization-circuit-breaker`
- `leverage-limit-enforcement-across-instruments`
- `options-margin-span-calculation-global`
- `cross-margining-across-asset-classes`
- `calendar-spread-and-multi-leg-order-atomicity`
- `early-exercise-assignment-risk-management`
