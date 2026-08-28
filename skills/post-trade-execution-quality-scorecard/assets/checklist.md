# Pre-Flight Checklist

## Input data

- [ ] One row per **parent** order, not per fill?
- [ ] `arrival_midquote` and `arrival_quoted_spread` stamped at **time of order receipt**, not time of execution?
- [ ] `market_vwap` covers the order's own trading interval, not the whole session?
- [ ] `end_price` supplied wherever implementation shortfall is expected? (Without it, IS is `None` by design.)

## Validation

- [ ] Unrecognised `side` raises rather than defaulting to SELL?
- [ ] Non-finite or non-positive prices raise rather than being floored into a denominator?
- [ ] `arrival_quoted_spread` $\le 0$ (locked/crossed book) excludes the order rather than being floored?
- [ ] `parent_qty > 0`, and `0 \le executed_qty \le parent_qty` enforced?
- [ ] Fill rate divides by the **actual** `parent_qty` — so a fully filled $0.5$-unit fractional or crypto order reports $100\%$, not $50\%$?
- [ ] The whole batch validates before any metric is computed?

## Metrics

- [ ] Side sign inverts for SELL on arrival slippage, VWAP slippage, effective spread **and** opportunity cost?
- [ ] Effective spread computed as $2 \cdot \text{SideSign} \cdot (\text{AvgFill} - \text{ArrivalMid})$?
- [ ] Wholly unfilled orders excluded from every price-based metric, yet still counted in fill rate and opportunity cost?
- [ ] Implementation shortfall includes the **opportunity cost of unfilled shares** (Perold 1988), not just the filled-share cost?
- [ ] IS reported as `None` — never as the filled-share cost — when `end_price` is absent?

## Aggregation

- [ ] Aggregates notional-weighted, not a plain mean over orders?
- [ ] Overall fill rate computed as $\sum\text{ExecutedQty} / \sum\text{ParentQty}$?
- [ ] Both $E/Q$ forms reported and labelled — ratio-of-averages (Rule-605-comparable) vs mean-of-ratios?
- [ ] Venues below the notional floor reported as `NR` rather than given a letter grade?

## Reporting hygiene

- [ ] `orders_missing_end_price` and `unfilled_orders` surfaced, so a reader knows how much of the batch the headline IS covers?
- [ ] Metrics labelled as gross of commissions, fees, taxes and borrow?
- [ ] `eqr_penalty_per_unit` recalibrated (or zeroed) if the book is dominated by large worked parent orders, where $E/Q \gg 1$ is structural rather than a broker failing?
- [ ] The composite grade presented as a **house heuristic**, never as a regulatory measure?
- [ ] No claim made that this output satisfies SEC Rule 605 filing, or MiFID II RTS 27/28 (both deleted)?
