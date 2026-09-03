---
name: execution-slippage-attribution-timing-vs-sizing
description: >-
  Post-trade TCA engine that splits the executed leg of Implementation Shortfall into timing/delay slippage (decision to arrival) and sizing/market-impact slippage (arrival to completion), names the larger adverse component, and flags partial fills whose opportunity cost is not measured here.
domain: Execution Algorithms
subdomain: Post-Trade Transaction Cost Analysis (TCA)
tags: ["tca", "implementation-shortfall", "slippage-attribution", "timing-slippage", "sizing-slippage", "market-impact", "execution-benchmarking"]
brokers_frameworks: ["Perold (1988) Implementation Shortfall", "Delay / Trading Cost Decomposition", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in post-trade Transaction Cost Analysis (TCA) and algorithmic execution reviews, when you already know an order cost money and need to know **which half of the execution stack to fix**: the path between the PM's decision and the order reaching the venue, or the algorithm's behaviour once it was working the order.

It splits the executed leg of Implementation Shortfall into two additive components, both expressed as cost-signed basis points of the decision price $P_{\text{decision}}$ (positive = money lost, for buys *and* sells):

- **Timing / delay slippage** — what the price did between decision and arrival, before the algorithm had any influence.
- **Sizing / market-impact slippage** — what the price did between arrival and the final fill, while the order was being worked.

## When NOT to Use

- **As a complete Implementation Shortfall.** Perold (1988) decomposes IS into delay cost + trading cost + **opportunity cost** + **explicit fees**. This engine measures the first two — the price cost on shares that actually filled. It never sees an end-of-horizon price, so it cannot compute opportunity cost on the unfilled residual, and it takes no commissions or taxes. For the full four-component shortfall use `implementation-shortfall-minimization`.
- **On a materially underfilled order, as the headline cost number.** At a 40% fill the opportunity cost on the missing 60% can exceed everything reported here. The report sets `is_partial_fill` and excludes that term; do not present the remainder as the cost of the order.
- **As proof that the dispatch path is slow.** The timing component is *whatever the price did* during the delay. Over a short delay on a liquid name that is mostly drift and news, not latency. Correlate the bps figure with `delay_seconds` across many trades before re-engineering anything.
- **As a market-adjusted measure.** Neither component is decontaminated of index/beta movement, so a timing figure measured during a broad market move is partly beta. Pair with `benchmark-relative-performance-attribution` if you need the stock-specific part.
- **As an automatic control input.** `strategy_action_recommendation` is a single-trade triage hint, not a risk control. Retuning a live participation ceiling from one trade's attribution fits noise; see `execution-algo-parameter-optimization-via-backtest`.

## Prerequisites

- Trade execution details: `side` (`'BUY'` or `'SELL'` — nothing else is accepted), `order_qty`, optional `filled_qty` (defaults to a full fill), `decision_price`, `arrival_price`, `average_exec_price` (quantity-weighted).
- All three prices finite and strictly positive, on the same quotation basis and currency.
- Timestamps `decision_time_iso`, `arrival_time_iso`, `completion_time_iso` as **timezone-aware** ISO-8601 strings, in non-decreasing order. Naive timestamps are rejected.
- A materiality threshold in bps (default 1.0) below which a component counts as noise rather than a driver. This is a desk reporting convention, not a standard — set it from your own cost distribution.

## Workflow

1. **Validate before computing.** Reject non-finite or non-positive prices, unrecognised sides, non-positive or over-filled quantities, and naive or out-of-order timestamps.
   - **Decision point — never attribute unvalidated data.** A NaN price makes every comparison in the classifier false, which lands the trade in the "nothing material" branch and reports corrupt input as `OPTIMAL`. Fail loudly; a TCA engine that cannot compute a cost must not emit a clean bill of health.
   - **Decision point — an unrecognised `side` is a data error, not a sell.** Defaulting anything that is not `'BUY'` to $-1$ turns a $+70$ bps cost on a mistyped buy into a $-70$ bps gain of identical magnitude.

2. **Decompose, normalising every term on $P_{\text{decision}}$** (with $\text{Side} = +1$ for BUY, $-1$ for SELL):
   - $\text{IS}_{\text{total}} = \text{Side} \times \frac{\bar{P}_{\text{exec}} - P_{\text{decision}}}{P_{\text{decision}}} \times 10{,}000$
   - $\text{IS}_{\text{timing}} = \text{Side} \times \frac{P_{\text{arrival}} - P_{\text{decision}}}{P_{\text{decision}}} \times 10{,}000$
   - $\text{IS}_{\text{sizing}} = \text{Side} \times \frac{\bar{P}_{\text{exec}} - P_{\text{arrival}}}{P_{\text{decision}}} \times 10{,}000$
   - **Decision point — the sizing term is divided by $P_{\text{decision}}$, not $P_{\text{arrival}}$.** That single choice is what makes the decomposition additive. Normalising the impact leg on the arrival price is defensible in isolation but breaks $\text{IS}_{\text{total}} \equiv \text{IS}_{\text{timing}} + \text{IS}_{\text{sizing}}$.
   - Verify the identity **in full precision**, then round for reporting — and report the directly computed total, not the sum of the rounded components.

3. **Weight to the intended notional if the order underfilled.** Canonical IS divides by $Q_{\text{order}} \times P_{\text{decision}}$, so the per-share cost contributes only $Q_{\text{filled}} / Q_{\text{order}}$ of itself: `executed_is_contribution_bps` $= \text{IS}_{\text{total}} \times \text{fill ratio}$.
   - **Decision point — if `is_partial_fill` is true, this report is not the order's total cost.** Retrieve the opportunity cost separately before quoting a headline number.

4. **Rank by *adverse* cost, never by absolute magnitude.**
   - A component that made money is never a slippage driver. Ranking by $|\cdot|$ lets a $-50$ bps timing *gain* outrank a $+20$ bps sizing cost and recommend `ACCELERATE_ORDER_DISPATCH` — advice that would forfeit the gain and leave the only real cost untouched.
   - Neither component materially adverse $\implies$ `FAVORABLE_EXECUTION` (total materially negative) or `ZERO_SLIPPAGE`.
   - Both materially adverse and within the tie band $\implies$ `BOTH_DRIVERS_MATERIAL` (`REDUCE_DELAY_AND_PARTICIPATION`). An exact tie is *not* zero slippage: $+50/+50$ is 100 bps of real cost.
   - Otherwise the larger adverse component wins: `TIMING_DRIVEN_SLIPPAGE` (`ACCELERATE_ORDER_DISPATCH`) or `SIZING_DRIVEN_SLIPPAGE` (`REDUCE_PARTICIPATION_RATE_CEILING`), with `secondary_driver_material` set when the loser is *also* adverse — fixing one leg then leaves most of the cost in place.

5. **Report contribution shares against gross cost** $|\text{IS}_{\text{timing}}| + |\text{IS}_{\text{sizing}}|$, not against the net total, so offsetting legs cannot produce percentages in the thousands.

6. **Audit Report Generation**: output structured `SlippageAttributionAuditReport`, carrying the materiality threshold the verdict was judged against.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reporting corrupt data as `OPTIMAL`**: with NaN prices every bps figure is NaN, and both `abs(nan) > abs(nan)` comparisons evaluate false, so a naive classifier falls through to its "nothing material" branch. The worst input produces the most reassuring output. Validate prices before attributing.
- **Treating an exact tie as zero slippage**: a classifier built from two strict `>` comparisons has no branch for $|\text{timing}| = |\text{sizing}|$. A $+50/+50$ split — 100 bps of genuine cost — silently lands in the else-branch labelled "minimal slippage".
- **Ranking components by absolute value**: this promotes a favourable leg to "primary slippage driver" and inverts the recommendation. Only adverse components can drive remediation.
- **Reversing sign conventions for sell orders**: mapping "anything not BUY" to $-1$ means one typo (`'BUYY'`, `'B'`, `'LONG'`, an empty string) reports a cost as a gain of exactly the same size, and nothing in the output looks wrong.
- **Dividing contribution shares by the net total**: when the legs offset, $+500$ bps timing against $-499$ bps sizing yields $50{,}000\%$ and $-49{,}900\%$. Normalise on gross cost.
- **Claiming the identity is verified while substituting the sum for the total**: writing `total = timing + sizing` guarantees the printed numbers agree and checks nothing. Verify in full precision and report the independently computed total.
- **Conflating delay slippage with market impact**: blaming the execution algorithm when most of the cost accrued before the order ever reached the venue.
- **Quoting a partial fill's per-share cost as the order's cost**: it omits opportunity cost on the residual *and* overstates the contribution to IS by $Q_{\text{order}} / Q_{\text{filled}}$.
- **Measuring the delay from naive timestamps**: across a DST transition or between venues in different zones the duration is silently wrong, which makes an `ACCELERATE_ORDER_DISPATCH` recommendation unfalsifiable.
- **Warning on every trade**: logging each routine attribution at WARNING buries the material ones under thousands of lines in a batch run.

## Verification

- **Timing-driven BUY**: $P_{\text{decision}} = \$100.00$, $P_{\text{arrival}} = \$100.50$, $\bar{P}_{\text{exec}} = \$100.70$ $\Rightarrow$ total $= +70.0$ bps, timing $= +50.0$ bps, sizing $= +20.0$ bps, shares $71.4\% / 28.6\%$, driver `TIMING_DRIVEN_SLIPPAGE`, recommendation `ACCELERATE_ORDER_DISPATCH`, `secondary_driver_material` true.
- **Sign convention**: a SELL at $P_{\text{decision}} = \$100.00$, $P_{\text{arrival}} = \$99.90$, $\bar{P}_{\text{exec}} = \$99.20$ gives $+80.0$ bps of *cost* ($+10.0$ timing, $+70.0$ sizing); the same SELL filled at $\$100.50$ gives $-50.0$ bps and `FAVORABLE_EXECUTION`.
- **Tie**: $\$100.00 \to \$100.50 \to \$101.00$ must yield `BOTH_DRIVERS_MATERIAL` / `REDUCE_DELAY_AND_PARTICIPATION` at $+100.0$ bps — never `ZERO_SLIPPAGE`.
- **Favourable leg**: $\$100.00 \to \$99.50 \to \$99.70$ (timing $-50.0$, sizing $+20.0$) must yield `SIZING_DRIVEN_SLIPPAGE`, not `TIMING_DRIVEN_SLIPPAGE`.
- **Offsetting legs**: $\$100.00 \to \$105.00 \to \$100.01$ must keep both contribution shares within $[-100\%, 100\%]$.
- **Partial fill**: 4,000 of 10,000 filled at the timing-driven prices above gives `fill_ratio` $= 0.4$, `is_partial_fill` true, `executed_is_contribution_bps` $= 28.0$ while `total_is_slippage_bps` stays $+70.0$.
- **Rounding**: a SELL at $\$1234.56 \to \$1240.01 \to \$1231.77$ reports timing $-44.15$, sizing $+66.74$, total $+22.60$ — the directly computed total, one ulp above the $22.59$ sum of the rounded parts.
- **Negative checks**: non-finite or non-positive prices, an unrecognised `side`, `filled_qty` $\le 0$ or $>$ `order_qty`, naive/malformed/out-of-order timestamps, and a negative materiality threshold must each raise.
- Run `python -m unittest discover -s skills/execution-slippage-attribution-timing-vs-sizing/scripts` and confirm all tests pass.

## Related Skills

- `implementation-shortfall-minimization` — the full Perold decomposition including opportunity cost and explicit fees.
- `post-trade-execution-quality-scorecard`
- `execution-algo-parameter-optimization-via-backtest`
- `arrival-price-benchmark-execution-algo`
- `transaction-cost-analysis-tca-integration`
