---
name: post-trade-execution-quality-scorecard
description: >-
  Use when ranking brokers, algos or venues from your own executed-order records —
  computes arrival slippage, VWAP slippage, Rule-605-style effective spread and
  effective-over-quoted ratio, fill rate, and a Perold implementation shortfall that
  charges opportunity cost on the shares that never filled.
domain: Execution Algorithms
subdomain: Transaction Cost Analysis & Execution Quality
tags: ["tca", "execution-quality", "implementation-shortfall", "vwap-slippage", "effective-spread", "sec-rule-605", "opportunity-cost", "scorecard"]
brokers_frameworks: ["SEC Rule 605 (17 CFR 242.605)", "MiFID II Art. 27(7)", "Perold Implementation Shortfall", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill to build a *house* post-trade scorecard that ranks brokers, algo wheels and execution venues from your own executed-order records. It computes arrival-price slippage, VWAP slippage, effective spread and the effective-over-quoted ratio ($E/Q$), fill rate, a Perold implementation shortfall including opportunity cost, and a notional-weighted composite grade ($A$–$F$) per venue and overall.

Two of these statistics — effective spread and $E/Q$ — are defined the way SEC Rule 605 defines them, so your numbers are comparable *in kind* to a market centre's published Rule 605 report. That comparability is the point: it lets you check a venue's own claims against what you actually received.

The obligation this serves is ongoing execution-quality monitoring, not report filing: MiFID II Art. 27(7) and Art. 66 of Commission Delegated Regulation (EU) 2017/565 require an investment firm to monitor the effectiveness of its order execution arrangements and review its execution policy at least annually. Both remain in force. See `references/standards.md` for the jurisdictional detail.

## When NOT to Use

- **To produce a filable SEC Rule 605 report.** This engine cannot. Rule 605 is a *reporting obligation* on market centres, on broker-dealers introducing or carrying 100,000 or more customer accounts, and on single-dealer platforms. A filed report needs monthly aggregation, notional size categories, fractional/odd-lot/round-lot classification, price *and size* improvement, realized spreads at five post-execution horizons, sub-100-microsecond speed buckets, and a CSV+PDF summary. None of that is here.
- **To produce RTS 27 or RTS 28 reports.** Both obligations have been deleted from MiFID II — do not build them. See `references/standards.md`.
- **On a batch where most orders lack an `end_price`.** Without a mark for the unfilled residual the opportunity-cost term is unknowable and implementation shortfall is reported as `None`. A scorecard built only on price-based statistics systematically flatters a broker that missed half the order.
- **To compare a maker-rebate venue against a taker-fee venue.** Every metric here is gross of commissions, fees, taxes and borrow. On these numbers alone the comparison is meaningless.
- **To grade a book of large worked parent orders on the default weights.** The $E/Q$ penalty is calibrated for marketable orders; on worked parents it saturates every score to zero. Recalibrate `eqr_penalty_per_unit` first — see Common Pitfalls.
- **To attribute cost to timing versus sizing.** The record carries one arrival price, one average fill price and one interval VWAP per parent order — points, not paths. Use `execution-slippage-attribution-timing-vs-sizing`.
- **To measure adverse selection or reversion.** Realized spread needs post-trade marks this record does not carry; see `adverse-selection-measurement-for-passive-orders`.

## Prerequisites

- Executed parent-order records: `order_id`, `venue`, `symbol`, `side` (`'BUY'`/`'SELL'`), `parent_qty`, `executed_qty`, `avg_fill_price`, `arrival_price`, `market_vwap`, `arrival_midquote`, `arrival_quoted_spread`.
- `arrival_midquote` and `arrival_quoted_spread` stamped from the consolidated quote **at the time of order receipt** — Rule 605's reference point — not at the time of execution.
- Optional `end_price`: the price marking the unfilled residual, normally the last price of the trading horizon. **Required for implementation shortfall**; there is no safe default.
- Scorecard config: `benchmark_target_is_bps` (default $10.0$ bps) plus the penalty weights, all house-calibrated (see `references/standards.md`).

## Workflow

1. **Validate the whole batch before computing anything**:
   - Prices must be finite and $> 0$; `parent_qty` $> 0$; `0 \le` `executed_qty` $\le$ `parent_qty`; `side` in `{BUY, SELL}`.
   - **Decision point — an unrecognised `side` must raise, never default.** Falling through to SELL inverts the sign of every cost metric: a broker that paid $50$ bps is reported as having saved $50$.
   - **Decision point — a locked or crossed book has no $E/Q$ denominator.** A zero or negative `arrival_quoted_spread` must exclude the order, not be floored to a small positive number — flooring converts an unmeasurable ratio into an enormous fabricated one.
   - Validation runs over the entire batch first, so a malformed record can never contribute a partial result to an aggregate.

2. **Compute per-order metrics** with $\text{SideSign} = +1$ for BUY, $-1$ for SELL, so positive always means cost:
   $$\text{ArrivalSlippage}_{\text{bps}} = \text{SideSign} \cdot \frac{\text{AvgFill} - \text{Arrival}}{\text{Arrival}} \cdot 10^4$$
   $$\text{Slippage}_{\text{VWAP,bps}} = \text{SideSign} \cdot \frac{\text{AvgFill} - \text{VWAP}}{\text{VWAP}} \cdot 10^4$$
   $$\text{EffSpread} = 2 \cdot \text{SideSign} \cdot (\text{AvgFill} - \text{ArrivalMid}), \qquad E/Q = \frac{\text{EffSpread}}{\text{ArrivalQuotedSpread}}$$
   - **Decision point — a wholly unfilled order has no fill price.** Skip every price-based metric for it. Feeding a placeholder `avg_fill_price` of $0.0$ into the slippage formula produces a fictional $-10{,}000$ bps *saving* that then drags the whole aggregate down.

3. **Compute implementation shortfall per Perold (1988)** — execution cost on the shares that filled *plus* opportunity cost on the shares that did not:
   $$f = \frac{\text{ExecutedQty}}{\text{ParentQty}}, \qquad IS_{\text{bps}} = \text{ArrivalSlippage}_{\text{bps}} \cdot f + \text{SideSign} \cdot \frac{\text{End} - \text{Arrival}}{\text{Arrival}} \cdot 10^4 \cdot (1 - f)$$
   - **Decision point — no `end_price`, no IS.** Report `None`, not the filled-share cost. A broker that filled $500$ of $1{,}000$ shares at $5$ bps while the stock ran $200$ bps away delivered $102.5$ bps of shortfall, and the filled-share number shows $5$.

4. **Aggregate notional-weighted, never as a mean over orders**:
   - Price metrics weight by executed notional; shortfall and score weight by parent notional; overall fill rate is $\sum\text{ExecutedQty} / \sum\text{ParentQty}$.
   - **Decision point — $E/Q$ has two forms and they are not interchangeable.** Rule 605 publishes a *ratio of share-weighted averages*, $\overline{\text{EffSpread}} / \overline{\text{QuotedSpread}}$ (as a percentage). The mean of per-order ratios is a different number: on two equal-size orders with the same $0.10$ effective spread against $0.10$ and $0.02$ quoted spreads, the ratio-of-averages is $1.67$ and the mean of ratios is $3.00$. Compare like with like when benchmarking against a filing.

5. **Grade per venue and overall**, worst venue first. A venue below `min_venue_notional_for_grade` is reported but graded `NR` — a letter grade from two odd lots is noise wearing the costume of a measurement, and desks route on letter grades.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Calling the filled-share cost "implementation shortfall".** It is the implicit cost component only. Perold's IS also charges the opportunity cost of shares never traded; omitting it is the single most common way a scorecard rewards a broker for quietly not working the order.
- **Averaging unweighted across orders.** A $1$-share fill and a $1{,}000{,}000$-share fill carry equal weight in a plain mean, so one excellent odd lot can outrank a whole badly-executed programme. Weight by notional.
- **Averaging per-order ratios where the standard averages the numerator and denominator separately.** Small quoted spreads blow up per-order $E/Q$ and dominate a plain mean.
- **Flooring a denominator instead of rejecting the record.** `max(0.0001, price)` does not make a zero price safe; it turns it into a ~$10^9$ bps figure that enters the aggregate looking like a measurement.
- **A fill-rate denominator of `max(1.0, parent_qty)`.** For fractional shares, crypto or sub-unit FX lots this silently understates the fill rate — a fully filled $0.5$-unit order reports $50\%$.
- **Letting an unfilled order's placeholder fill price into the price metrics.** Exclude it from price statistics; count it in fill rate and opportunity cost.
- **Stamping the midquote at execution time rather than order receipt.** Rule 605 measures effective spread against the quote *when the order arrived*. Using the execution-time quote measures something else and makes a slow, drifting execution look tight.
- **Relying solely on VWAP.** VWAP is gameable: trade slowly into high-volume windows and the VWAP number flatters while implementation shortfall blows out.
- **Ignoring fill rates.** Low slippage on partial fills plus a high cancel rate is not good execution.
- **Uncorrected side sign.** For a SELL a *higher* fill price is a saving; the sign must invert on slippage, effective spread and opportunity cost alike.
- **Carrying $E/Q$ over from marketable orders to worked parent orders.** Rule 605 computes $E/Q$ for individual marketable orders against the receipt-time quote, where $pprox 1.0$ is normal. A parent order worked over minutes walks the book, so $E/Q$ of $5$–$15$ is routine in a tight-spread name — at the default `eqr_penalty_per_unit` of $20.0$ that pins every such order to a score of $0$ and the scorecard stops discriminating between brokers. Recalibrate the weight, or set it to $0.0$ and rank on slippage and fill rate.
- **Presenting the composite grade as a regulatory measure.** The weights and the $A$–$F$ boundaries are house convention. No regulator defines a grade, and no regulator mandates a $10$ bps IS target or a $95\%$ fill rate.

## Verification

- Instantiate `PostTradeExecutionQualityScorecard()`. Input a BUY of $1{,}000$ shares fully filled @ $\$100.05$ vs $\$100.00$ arrival, $\$100.10$ VWAP, $\$100.00$ midquote, $\$0.10$ quoted spread $\implies$ arrival slippage $= +5.00$ bps, VWAP slippage $= -5.00$ bps, effective spread $= \$0.10$, $E/Q = 1.00$, fill rate $= 100\%$, grade $A$.
- Mirror check for sign: SELL $1{,}000$ @ $\$99.95$ against the same $\$100.00$ arrival/midquote $\implies$ arrival slippage $= +5.00$ bps and effective spread $= \$0.10$ (both costs, not savings).
- Opportunity cost: BUY $1{,}000$, only $500$ filled @ $\$100.05$, `end_price` $= \$102.00$ $\implies$ opportunity cost $= +100.00$ bps and $IS = +102.50$ bps, against a filled-share cost of only $5.00$ bps. Omit `end_price` and $IS$ must be `None`, not $5.00$.
- Weighting: a $1$-share $0$ bps fill plus a $100{,}000$-share $100$ bps fill $\implies$ notional-weighted slippage $\approx 100.0$ bps, unweighted $50.0$ bps.
- Negative checks: `side='SHORT'`, `arrival_price=0`, `arrival_quoted_spread=0`, `parent_qty=0`, `executed_qty > parent_qty`, and a `NaN` price must each raise.
- Run `python -m unittest discover -s skills/post-trade-execution-quality-scorecard/scripts`.

## Related Skills

- `transaction-cost-analysis-tca-integration`
- `algo-wheel-broker-execution-quality-comparison`
- `execution-cost-model-recalibration-cadence`
- `implementation-shortfall-minimization`
- `execution-slippage-attribution-timing-vs-sizing`
- `adverse-selection-measurement-for-passive-orders`
- `best-execution-record-keeping-global`
- `execution-venue-fee-tier-optimization`
