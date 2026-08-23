# Workflows for Dark Pool Routing Logic

## 0. Inputs and their provenance

- `historical_fill_rate` — filled/sent, measured by your own router, in $[0, 1]$. Reject values outside the range at ingest; a rate above 1.0 is a broken measurement that would over-weight the venue.
- `toxicity_score_bps` — your own post-trade markout, signed so **higher is worse**. Never the venue's published toxicity score (SEC PR 2016-16).
- `min_qty_threshold` — the venue's own minimum executable quantity, from its Form ATS-N filing or FIX spec, not a guess.
- Refresh cadence — record when each statistic was computed. FINRA-derived venue volume is 2–4 weeks delayed by rule.

## 1. Venue toxicity and fill-rate screen

1. Drop inactive venues.
2. Drop venues where $\text{Toxicity}_v > \text{MaxToxicity}$ (strict inequality: a venue exactly at the ceiling stays in).
3. Drop venues where $\text{FillRate}_v < \text{MinFillRate}$.
4. Record a human-readable reason for every exclusion — the audit trail is the point of the screen, not a side effect.

## 2. Allocation weighting

$$S_v = \text{FillRate}_v \times \max\left(0.0,\ 1.0 - \frac{\max(0,\ \text{Toxicity}_v)}{\text{ToxicityDecay}}\right)$$

- $\text{ToxicityDecay} = 50.0$ bps by default — a shaping constant, not an empirical one. A venue at the decay point scores zero.
- Clamp toxicity at 0 *before* the discount. Without it, a favourable markout of $-500$ bps yields a discount of $11\times$ and captures ~89% of the parent.
- Exclude any venue scoring exactly 0 rather than carrying it into the weight denominator.
- Sort candidates by descending score, ties broken by `venue_id`, so the allocation does not depend on registration order.

## 3. Child slicing under venue minimums

1. Effective minimum: $\text{EffMin}_v = \max(1,\ \text{VenueMinQty}_v,\ \text{DefaultMinQty})$.
2. Target: $q_v = \text{Parent} \times S_v / \sum S$ (capped at `max_venue_allocation_pct` if configured).
3. **Feasibility loop**: while some venue has $\lfloor q_v \rfloor < \text{EffMin}_v$, drop the single worst-funded such venue, renormalise the weights over the survivors, and recompute. Dropping all under-funded venues at once discards venues the renormalisation would have funded.
4. Residual: distribute $\text{Parent} - \sum \lfloor q_v \rfloor$ one share at a time by largest fractional remainder, to already-funded venues only, so no child falls back below its minimum.
5. If nothing can be funded, emit zero directives with `unallocated_reason = PARENT_TOO_SMALL_FOR_VENUE_MIN_QTY`.

## 4. MinQty attachment

$$\text{MinQty}_v = \min\left(\max\left(\text{EffMin}_v,\ \lceil \text{ChildQty}_v \times \text{Fraction} \rceil\right),\ \text{ChildQty}_v\right)$$

The outer $\min$ enforces $\text{MinQty} \le \text{ChildQty}$. Without it the eligibility gate (venue minimum) and the instruction (engine floor) disagree, and the router emits child orders that can never trade.

Trade-off to state explicitly: a higher MinQty reduces adverse selection and raises the probability of no fill at all. Size it against the parent's urgency, not by default.

## 5. Execution routing (caller's responsibility)

- Price the child: `ExecInst(18)='M'` or `PegPriceType(1094)=2` (mid-price peg). This engine emits size and `MinQty(110)` only.
- Choose the time-in-force deliberately: IOC pings surrender queue position; resting midpoint orders accept exposure over time.
- **EU**: confirm the name is not suspended under the MiFIR single volume cap (7%, reference-price waiver only) before routing dark under that waiver.
- **US**: midpoint execution inside the NBBO does not trade through, and Rule 612 governs quotations/orders rather than execution prices.
- Apply self-match prevention and check whether the pool is affiliated with a counterparty you are trying to avoid.

## 6. Post-trade feedback loop

1. Compute per-venue markouts on the fills this router produced, at your chosen horizons.
2. Feed the markouts back as `toxicity_score_bps`; record the as-of timestamp.
3. Reconcile every report: $\text{allocated} + \text{unallocated} = \text{parent}$, and every `unallocated_reason` is investigated rather than logged and forgotten.
4. Re-calibrate the ceiling, fill-rate floor and MinQty fraction on the realised distribution — not on the defaults shipped here.
