# Workflows for Smart Order Routing Across Venues

Scope: US NMS stocks. See `references/standards.md` for the regulatory basis and
`SKILL.md` for when this skill does *not* apply.

## 1. Snapshot validation (fail closed)

Reject the snapshot rather than filtering it. Every check names the offending
venue and field so the failure is diagnosable from the exception alone.

| Check | Why it fails closed |
|---|---|
| All prices and sizes finite | `min()`/`max()` over a list containing `NaN` returns `NaN`. The plan then carries a `NaN` NBBO and a `NaN` child limit price, which a FIX adapter will serialize without complaint. |
| Sizes ≥ 0 | A negative displayed size is meaningless and would flip the `avail_qty` slicing arithmetic. |
| A quoting side has price > 0 | A side with `qty=0` may carry a `0.0` placeholder; a side that is actually quoting may not. |
| No venue locked/crossed against its own book | A single venue cannot display `bid >= ask`; if it does, the feed handler has mixed up sides or timestamps. |
| `venue_id` unique | Two rows for one venue would double-count its displayed size and plan a sweep for liquidity that exists once. |
| `side ∈ {BUY, SELL}` | Anything else previously fell through to the sell path and quoted the bid — silently inverting the order's intent. |
| `quantity` finite and > 0 | A negative quantity produced a negative `unrouted_quantity` and no error. |
| Numerics are numbers, not strings | `float("300")` would validate and then raise an opaque `TypeError` at the first `"300" > 0` comparison, far from the field at fault. Strings are rejected by name. |

Validated quotes are returned as **copies with every numeric field coerced to
`float`** (the caller's objects are never mutated). `Decimal` and numpy scalars
are therefore accepted, and the tick quantizer never meets a `Decimal / float`
`TypeError` mid-route.

## 2. Best-accessible-price consolidation

```
quoted        = [q for q in venues if side_price(q) > 0]
best_quoted   = min(ask) / max(bid)  over quoted        # what the tape shows
accessible    = [q for q in venues if side_qty(q) > 0]
nbbo_price    = min(ask) / max(bid)  over accessible    # what can be routed against
```

If `best_quoted != nbbo_price` on the tick grid, a better-priced quote had **no
displayed size**. The engine logs a warning and routes at `nbbo_price`. Both
values are returned on the plan (`best_quoted_price`, `nbbo_price`) so the gap is
auditable.

Treat that warning as a data-quality alarm first and a market condition second.
A real protected quotation has size; a size-0 quote at a better price usually
means the snapshot is stale. Re-fetch before routing.

If **no** venue shows size on the relevant side, the engine returns a plan with
zero routes, the full quantity unrouted, and `iso_required_for_remainder=True`.

## 3. Tick-grid price comparison

All price equality and limit-price comparisons run on integer ticks:

$$\text{ticks}(P) = \operatorname{round}\!\left(\frac{P}{\Delta}\right), \qquad \Delta = \texttt{price\_increment}$$

Rationale: venue feed handlers reconstruct the same quoted price along different
float paths. `10007 / 100.0` and `10007 * 0.01` are both "\$100.07" and are not
equal as floats. Across the penny grid from \$100.00 to \$200.00, 1,334 of the
10,001 quotable prices are affected.

The consequence of getting this wrong is not cosmetic: the second venue's
displayed size falls out of the eligible set and is reported as unrouted, so the
caller works that balance at an inferior price — trading through the very
protected quotation the router just discarded.

$\Delta$ must match the instrument (Rule 612: \$0.01 at ≥\$1.00, \$0.0001 below
\$1.00). Too coarse and distinct levels merge and ordinary books read as locked;
too fine and identical quotes split.

## 4. Venue ranking at the price level

$$\text{score}_{\text{buy}} = P + f + \lambda \cdot t, \qquad \text{score}_{\text{sell}} = -\left(P - f - \lambda \cdot t\right)$$

where $P$ is the quoted price, $f$ the per-share taker fee (zero when
`fee_aware=False`), $t$ the venue latency in ms, and $\lambda$ =
`config.latency_penalty_per_ms` (default $10^{-5}$). Venues sort ascending by
`(score, venue_id)`; the `venue_id` tiebreaker makes the plan reproducible
regardless of the order quotes arrived in.

Two properties worth stating plainly:

- $\lambda t$ is a **sub-tick tiebreaker**, not a cost model. At the default, 1.5 ms
  is worth \$0.000015/share — enough to order two otherwise-identical venues and
  nothing more. Do not report it as a latency cost.
- `fee_aware=False` removes $f$ from the **ranking key only**. `effective_net_price`,
  `taker_fee_usd` and `net_expected_cost_usd` are always fee-inclusive, because
  the fee is paid regardless of whether the router optimized for it.

`maker_rebate_per_share` is deliberately absent from the score. This engine plans
liquidity-*taking* sweeps; the rebate is earned by resting passively, which is a
different decision (`post-only-and-maker-taker-fee-optimization`).

## 5. Slicing and the remainder

Walk the ranked venues, taking `min(remaining, displayed_size)` at each, until
the parent quantity is exhausted or the price level is. Per child order the plan
records the venue limit price, the fee-inclusive net price, and the dollar taker
fee.

The engine **stops at the price level**. Residual quantity is returned as
`unrouted_quantity` with `iso_required_for_remainder=True`, and the obligation is
written into `audit_notes`.

Why stop there: concurrent child orders all priced at the same protected quote
trade through nothing and are ordinary limit orders. The moment the remainder is
filled at an inferior price, that execution trades through a protected quotation
and must be an ISO — which per 17 CFR 242.600(b)(47) requires *simultaneous*
additional limit orders against the **full displayed size of every protected
quotation with a superior price**. That decision needs live protected-quote state
at the moment of the second wave, not the stale snapshot this plan was built
from. The engine surfaces the obligation instead of guessing at it.

## 6. Price bounding

`limit_price` is a hard bound compared on the tick grid: no route is planned above
it for a BUY or below it for a SELL. A limit exactly equal to the best accessible
price still routes. When the bound blocks routing, the plan returns zero routes
and the full quantity unrouted, with the reason in `audit_notes`.

`limit_price=None` means the sweep is unbounded and will route at whatever the
best accessible price happens to be — including a dislocated one. That is a
deliberate choice to make explicitly, not a default to inherit.

## 7. Concurrent dispatch

The plan is a set of child orders intended for **concurrent** dispatch. Serial
dispatch both degrades execution quality (the market reacts to the first child
before the rest land) and breaks the simultaneity an ISO requires. The engine
does not dispatch; guaranteeing simultaneity is the transport layer's job.

## 8. Execution audit

Persist, per parent order: the input quote snapshot with its capture timestamp,
`best_quoted_price` vs `nbbo_price`, the `price_increment` used, every child
route with venue/price/size/fee, `unrouted_quantity`,
`iso_required_for_remainder`, `locked_or_crossed`, `total_taker_fee_usd`, and
`audit_notes`. The plan is a pure function of its inputs, so a stored snapshot
plus these parameters reproduces the routing decision exactly — which is what a
best-execution or CAT enquiry will ask for.
