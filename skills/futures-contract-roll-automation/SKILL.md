---
name: futures-contract-roll-automation
description: >-
  Quantitative execution engine for automating futures contract roll decisions via volume/open-interest crossover, days-to-expiration, and First Notice Day deadlines, describing the calendar spread order that performs the roll.
domain: Execution Algorithms
subdomain: Futures Derivatives & Roll Automation
tags: ["futures-roll", "calendar-spread", "volume-crossover", "open-interest", "first-notice-day", "contango", "backwardation", "derivatives"]
brokers_frameworks: ["CME Group", "Interactive Brokers", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in CTA momentum strategies, futures execution algorithms, and multi-asset derivative trading systems that must move an open position from one expiration to the next before the current contract becomes an obligation. The engine evaluates four triggers — **volume crossover** ($V_{\text{next}} > V_{\text{front}}$), **open-interest crossover** ($OI_{\text{next}} > OI_{\text{front}}$), **days-to-expiration** against Last Trading Day, and **days-to-First-Notice-Day** for physically delivered contracts — and describes the calendar spread order that performs the roll in a single transaction rather than two legs.

## When NOT to Use

- **As the delivery-avoidance control for a physically delivered contract you cannot supply First Notice Day data for.** The engine refuses that input rather than guessing. For CBOT grains FND is the *last business day of the month preceding the delivery month*, roughly ten business days before Last Trading Day — a days-to-expiration rule alone will fire after the position is already deliverable.
- **As an order router.** The engine emits a decision and a description of the spread, not an order. It never contacts a venue, never tracks fills, and holds no state between calls.
- **To generate a tradable spread instrument symbol.** `spread_symbol` is a label. The tradable combination instrument's ID must come from the venue's security definition; string-concatenated spread symbols are rejected or, worse, silently mismatched.
- **As the atomicity mechanism on a venue with no native combination instrument.** This skill assumes a listed spread exists. Where it does not, algorithmic legging is a different problem — see `calendar-spread-and-multi-leg-order-atomicity`.
- **To build a back-adjusted price history.** Roll *execution* and roll *series construction* use similar triggers but answer different questions; see `synthetic-continuous-futures-contract-construction`.

## Prerequisites

- Front-month `FuturesContractState`: `symbol`, `expiration_date_iso`, `days_to_expiration`, `daily_volume`, `open_interest`, `last_price`, and — for physically delivered products — `is_physically_delivered=True` with `days_to_first_notice`.
- Next-month `FuturesContractState` with the same `contract_multiplier` and a later expiration.
- All day counts expressed in **business days**, consistently. The engine cannot infer the unit; feeding calendar days silently halves the effective safety margin around a weekend.
- The venue's calendar-spread quoting convention for this product (see Workflow step 3).

## Workflow

1. **Delivery-deadline audit (runs first, and can override everything else)**:
   - If `days_to_expiration < 0` the front leg is no longer tradable. The engine returns `ROLL_TOO_LATE_ESCALATE` with **no spread order** and logs at CRITICAL — you cannot lift a leg that has stopped trading, and emitting an unexecutable order would hide that.
   - For physically delivered contracts, compare `days_to_first_notice` against `min_days_to_first_notice`. FND, not LTD, is the binding date for a long: from FND onwards a short may tender a delivery notice and the clearing house may assign it.
2. **Liquidity-migration audit**: evaluate volume and open-interest crossover independently. Both are enabled by default and either alone triggers a roll; disabling one is recorded in the audit note rather than silently assumed.
3. **Determine the spread side from the product's quoting convention** — this is the step most likely to send the wrong trade. There is no universal convention:
   - `NEARBY_MINUS_DEFERRED` (default; CME standard `SP` listing, CBOT Treasuries): buying the spread buys the nearby leg and sells the deferred one, so a **long position rolls by SELLING the spread**.
   - `DEFERRED_MINUS_NEARBY` (CME FX calendar spreads, Equity Index roll): buying the spread buys the deferred leg, so a **long position rolls by BUYING the spread**.
   - The leg actions (`SELL` front / `BUY` next for a long) are identical either way. Only `spread_side` and the sign of `quoted_spread_price` change. Confirm the convention per product before wiring the output to a venue.
4. **Compute basis and roll cost**:
   - Convention-independent basis: $\text{spread\_price\_diff} = P_{\text{next}} - P_{\text{front}}$; `CONTANGO` if positive, `BACKWARDATION` if negative, `FLAT` if exactly zero.
   - Roll basis cost in position currency: $(P_{\text{next}} - P_{\text{front}}) \times \text{qty} \times \text{multiplier}$, negated for a short. Positive means P&L drag. This is the basis only — commissions and the cost of crossing the spread's own bid/ask are not included.
5. **Audit report generation**: output a structured `FuturesRollAuditReport` carrying every trigger that fired (`trigger_reasons`) and the `delivery_risk_level`, not just the first match.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating days-to-expiration as the delivery deadline**: for physically delivered contracts First Notice Day usually comes *first*. A CBOT corn December contract has FND on the last business day of November and LTD on the business day before 15 December; "roll 5 days before expiry" leaves a long deliverable for two weeks. Cash-settled contracts (ES, NQ) have no FND — the two cases need different data, not the same threshold.
- **Assuming a single calendar-spread convention**: CME lists the standard `SP` spread nearby-first and differences nearby-minus-deferred, but quotes FX calendar spreads far-minus-near and the Equity Index roll deferred-over-nearby. Hard-coding one convention sends the *opposite* trade on the other product family and doubles the position instead of rolling it.
- **Legging out of roll orders**: executing two separate leg orders instead of the exchange's listed calendar spread, incurring directional market risk between fills.
- **Waiting until the last trading day**: delaying rolls until liquidity has drained from the front month, suffering extreme bid-ask spreads exactly when the roll is no longer optional.
- **Omitting the contract multiplier from roll-cost accounting**: a 15-point ES roll on 10 contracts is $15 \times 10 \times \$50 = \$7{,}500$, not $\$150$. Roll drag is a multiplier-scaled cost, and it flips sign for a short — a short in contango *earns* the basis.
- **Rolling on one session's volume**: single-day volume crosses back and forth around the roll window. The engine is stateless and evaluates exactly what it is given; if you want an $N$-session confirmation, smooth or confirm the crossover before calling it.
- **Reusing stale open interest**: exchanges publish OI with a one-session lag. Feeding a same-session estimate makes the OI trigger fire on a number the venue has not published.
- **Building a spread symbol by string concatenation**: `spread_symbol` is a human-readable label only. The tradable instrument ID comes from the venue security definition.

## Verification

- Instantiate `FuturesContractRollEngine()`. Input ESH6 (Vol 50k, OI 100k, DBE 10, $5{,}000.00$) vs ESM6 (Vol 120k, OI 150k, $5{,}015.00$), LONG 10: verify `trigger_reasons` contains both `VOLUME_CROSSOVER` and `OPEN_INTEREST_CROSSOVER`, legs are `SELL ESH6` / `BUY ESM6`, `term_structure == 'CONTANGO'`, `spread_price_diff == 15.0`, and `estimated_roll_cost == 7500.0` ($15 \times 10 \times \$50$). Under `NEARBY_MINUS_DEFERRED` verify `spread_side == 'SELL'` and `quoted_spread_price == -15.0`; under `DEFERRED_MINUS_NEARBY` verify `'BUY'` and $+15.0$.
- Delivery regression: a physically delivered ZCZ6 with `days_to_expiration=10` (above the threshold), higher volume *and* higher open interest than the next contract, and `days_to_first_notice=1` must still trigger, with `delivery_risk_level == 'APPROACHING_FIRST_NOTICE'`.
- Negative checks: rolling a contract into itself, swapped front/next arguments, mismatched multipliers, a physically delivered contract without `days_to_first_notice`, non-finite or non-positive prices, negative volume/OI, and a non-integer or non-positive quantity must each raise `ValueError`.
- Run `python -m unittest discover -s skills/futures-contract-roll-automation/scripts` and confirm a 100% pass rate.

## Related Skills

- `synthetic-continuous-futures-contract-construction`
- `calendar-spread-and-multi-leg-order-atomicity`
- `futures-expiry-week-liquidity-and-volatility-handling`
- `physical-vs-cash-settlement-handling`
