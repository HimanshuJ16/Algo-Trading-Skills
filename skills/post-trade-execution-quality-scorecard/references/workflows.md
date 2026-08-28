# Workflows for Post-Trade Execution Quality Scorecard

## 0. Assemble the record set

One row per **parent** order, not per fill. Each row needs `order_id`, `venue`,
`symbol`, `side`, `parent_qty`, `executed_qty`, `avg_fill_price`, `arrival_price`,
`market_vwap`, `arrival_midquote`, `arrival_quoted_spread`, and — if implementation
shortfall is wanted — `end_price`.

Stamp `arrival_midquote` and `arrival_quoted_spread` from the consolidated quote at the
**time of order receipt**. This is Rule 605's reference point. Sourcing them from the
execution-time quote silently measures a different statistic and flatters slow
executions into a drifting market.

`market_vwap` must cover the order's own trading interval. A full-session VWAP applied
to an order that worked for ten minutes is not a benchmark, it is a coincidence.

## 1. Validate the whole batch before computing anything

Reject, do not repair:

| Condition | Why it must raise |
|---|---|
| `side` not in `{BUY, SELL}` | Defaulting to SELL inverts the sign of every cost metric on the order. |
| Any price non-finite or $\le 0$ | Flooring a denominator turns a zero price into a ~$10^9$ bps figure that enters the aggregate looking like data. |
| `arrival_quoted_spread` $\le 0$ | A locked or crossed book has no $E/Q$ denominator. Exclude the order. |
| `parent_qty` $\le 0$ | There is no fill rate to compute. |
| `executed_qty` $< 0$ or $>$ `parent_qty` | An over-fill is a reconciliation break, not a $>100\%$ fill rate. |
| Empty `order_id` | The row cannot be traced back for audit. |

Validation runs over the entire batch before any metric is computed, so a malformed
record can never contribute a partial result to an aggregate that is then reported as
complete.

`avg_fill_price` is exempt when `executed_qty == 0` — an unfilled order has no fill
price, and a `0.0` placeholder is legitimate there.

## 2. Per-order price metrics

With $\text{SideSign} = +1$ (BUY) / $-1$ (SELL), so **positive is always a cost**:

- $\text{ArrivalSlippage}_{\text{bps}} = \text{SideSign} \cdot \dfrac{\text{AvgFill} - \text{Arrival}}{\text{Arrival}} \cdot 10^4$
- $\text{Slippage}_{\text{VWAP,bps}} = \text{SideSign} \cdot \dfrac{\text{AvgFill} - \text{VWAP}}{\text{VWAP}} \cdot 10^4$
- $\text{EffSpread} = 2 \cdot \text{SideSign} \cdot (\text{AvgFill} - \text{ArrivalMid})$
- $E/Q = \text{EffSpread} / \text{ArrivalQuotedSpread}$

**Skip all four for a wholly unfilled order.** There is no execution to measure, and a
placeholder fill price produces a fictional saving that poisons the aggregate.

## 3. Implementation shortfall (Perold 1988)

$$f = \frac{\text{ExecutedQty}}{\text{ParentQty}}$$
$$\text{OpportunityCost}_{\text{bps}} = \text{SideSign} \cdot \frac{\text{End} - \text{Arrival}}{\text{Arrival}} \cdot 10^4 \cdot (1 - f)$$
$$IS_{\text{bps}} = \text{ArrivalSlippage}_{\text{bps}} \cdot f + \text{OpportunityCost}_{\text{bps}}$$

Both terms are expressed in bps of the **parent** notional, so they add.

If `end_price` is absent, report `IS` as `None`. Do not substitute the filled-share
cost: on a $1{,}000$-share BUY where only $500$ filled at $5$ bps while the stock ran to
$+200$ bps, the true shortfall is $102.5$ bps and the filled-share figure is $5$.

Sign check for a seller: failing to sell into a *falling* market is a cost, so
`end_price` below `arrival_price` yields a **positive** opportunity cost for a SELL.

Out of scope and to be added downstream before calling the number a full Perold
shortfall: commissions, fees, taxes, borrow, and decision-to-arrival delay cost.

## 4. Aggregation

| Aggregate | Weight | Rationale |
|---|---|---|
| Arrival slippage, VWAP slippage, per-order $E/Q$ | Executed notional | The cost was paid on the shares that traded. |
| Effective and quoted spread (for the Rule-605-style $E/Q$) | Executed shares | Rule 605 share-weights its spread averages. |
| Implementation shortfall, composite score | Parent notional | IS is a shortfall against the paper portfolio, whose value is the parent notional. |
| Overall fill rate | — | $\sum\text{ExecutedQty} / \sum\text{ParentQty}$, never the mean of per-order rates. |

Report the unweighted mean alongside for reference only. If the two diverge sharply,
the batch is dominated by a few large orders and the unweighted figure is misleading —
that divergence is itself worth surfacing to a broker review.

Publish **both** $E/Q$ forms and label them. `eqr_ratio_of_averages` is the
Rule-605-comparable figure; `avg_eqr_ratio` is the mean of per-order ratios. On two
equal-size orders with a $0.10$ effective spread against $0.10$ and $0.02$ quoted
spreads they read $1.67$ and $3.00$ respectively.

## 5. Scoring and grading

Per order, clamped to $[0, 100]$:

$$\text{Score} = 100 - \max(0, \text{ArrivalSlippage} - \text{Target}) \cdot w_{IS} - \max(0, E/Q - 1) \cdot w_{EQ} - (100 - \text{FillRate}) \cdot w_{F}$$

Scoring uses arrival slippage rather than IS so every order is scorable whether or not
`end_price` was supplied; the unfilled residual is already charged through the fill
term, and charging it again through IS would double-count it. A wholly unfilled order
scores on the fill term alone — crediting it a price outcome would reward not trading.

All four weights are house configuration. Recalibrate them against your own cost model
rather than treating the defaults as authoritative.

## 6. Venue rollup and review

Roll up notional-weighted per venue and sort worst score first. A venue below
`min_venue_notional_for_grade` is reported but graded `NR`: desks route on letter
grades, and a grade derived from two odd lots is noise wearing the costume of a
measurement.

## 7. Audit output

Retain the per-order metrics, the per-venue rollup, the counters
(`orders_missing_end_price`, `unfilled_orders`, notional totals) and the audit note.
The counters are the provenance record: they state how much of the batch the headline
IS figure actually covers, which is the first question a reviewer should ask.
