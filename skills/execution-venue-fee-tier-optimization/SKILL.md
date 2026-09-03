---
name: execution-venue-fee-tier-optimization
description: >-
  Use when allocating a period's flow across venues to minimise net cost after
  volume-tiered fees and rebates, pricing passive volume at its expected fill rate. One
  venue's own schedule is exchange-fee-tier-and-rebate-structure-analysis.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: execution-algorithms
  tags: fee-tier-optimization, sor-routing, net-price-routing, maker-taker, fill-probability, volume-allocation, exchange-fees, reg-nms-610d
  brokers_frameworks: "SEC Reg NMS Rule 610(d); Nasdaq Equities Price List; Cboe US Equities Fee Schedules; SOR Net Cost Engine; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill in Smart Order Routers (SOR), market making desks, and multi-venue execution algorithms when deciding **how much of a period's flow to send to each venue** given volume-tiered fee schedules. Venues price liquidity asymmetrically and layer volume tiers on top, so the cheapest venue for the first share is often not the cheapest for the millionth. This module enumerates candidate allocations, prices each at its *expected* outcome — passive shares earn a rebate only on the fraction that actually fills — enforces a fill-probability floor, and reports the lowest-cost allocation against the routing table you are actually running today.

It is the allocation counterpart to `exchange-fee-tier-and-rebate-structure-analysis`, which prices a single venue's schedule and evaluates one tier jump. Use that one to understand a venue; use this one to divide flow between several.

## When NOT to Use

- **As a routing decision on its own.** Fee is one term of execution cost and usually the smaller one. This module carries no market impact, no adverse selection, no queue-position model, and no spread. A venue that pays the largest rebate is frequently the venue that fills you only when you are wrong — pair with `transaction-cost-analysis-tca-integration` and `adverse-selection-measurement-for-passive-orders`.
- **On schedules whose tiers qualify on a percentage of consolidated volume or on ADAV/ADV.** Most real US equity tiers do: Cboe EDGX carries criteria such as "ADAV ≥ 0.15% of the TCV" alongside "ADV ≥ 20,000,000", and Nasdaq defines Consolidated Volume monthly. This module takes **absolute share thresholds** on a single criterion. Converting a percentage-of-TCV criterion needs a consolidated-volume forecast it does not make, and converting an average-*daily* criterion needs a trading-day count you must supply yourself.
- **On schedules requiring several simultaneous criteria** (add % *and* remove %, or a cross-asset condition). One scalar threshold cannot express those.
- **To justify month-end volume pushing on a US equity venue.** Rule 610(d) removed that mechanic entirely — see step 1 of the Workflow.
- **As a substitute for a fill-probability model.** `passive_fill_probability` is an input, not an output. Feeding it a guess produces a confidently wrong allocation; estimate it from your own fill data per venue, per symbol liquidity tier, and per horizon.
- **On sub-$1.00 US securities.** Access fees for those are capped as a percentage of quotation price, not per share; this module is per-share only.

## Prerequisites

- Candidate venue definitions: fee schedule tiers (absolute share threshold, taker rate, maker rate), with an explicit tier at threshold `0`.
- **Sign convention**: a negative rate is a rebate the venue *credits*; a positive rate is a fee it *charges*. Every USD figure returned follows the same convention — positive is money out. This matches `exchange-fee-tier-and-rebate-structure-analysis`.
- **`passive_fill_probability` per venue** — the probability a posted passive share fills within your execution horizon. It multiplies money; it is not a cosmetic rating.
- **Tier qualification basis** — `PRIOR_PERIOD` or `ROLLING_CURRENT`. Required, no default; see step 1.
- Under `PRIOR_PERIOD`, each venue's `qualifying_volume_shares` from the *completed prior period*.
- Period order-flow budget in shares, and the maker ratio (fraction posted passively), in $[0, 1]$.
- The desk's **incumbent routing allocation**, if you want a savings figure. Without it the engine reports `None` rather than inventing a reference.

## Workflow

1. **Fix the tier qualification basis first — it decides whether the whole exercise pays off this period or next.**
   - `PRIOR_PERIOD`: the tier applied to this period's fills is fixed by a completed prior period. **Mandatory for US NMS stocks.** Reg NMS Rule 610(d) provides that an exchange may not impose a fee or provide a rebate for an NMS-stock execution "that cannot be determined at the time of execution"; exchanges implemented it by deriving tier volumes from the prior month. Cboe's and Nasdaq's schedules now carry that note explicitly.
   - `ROLLING_CURRENT`: the tier is fixed by a rolling window that includes the volume being priced — the crypto-venue model (rolling 30-day volume) and some non-US venues.
   - **Decision point:** under `PRIOR_PERIOD` the engine *refuses* to substitute routed volume for the prior period's. Supply `qualifying_volume_shares` per venue; the volume you are routing is precisely the volume that may not set its own rate.
   - **Decision point:** under `PRIOR_PERIOD` the report's `tier_benefit_period` is `NEXT_PERIOD` and every per-venue breakdown carries `projected_next_period_tier_name`. Read that field, not the current tier, when deciding whether an allocation is worth adopting for its tier effect.

2. **Estimate `passive_fill_probability` per venue before generating candidates.** This is the input that decides the answer. A venue's rebate is only earned on the shares that fill; the rest are swept aggressively (paying that venue's taker rate) or abandoned. Two venues differing by $0.0002$ in rebate but $0.10$ in fill probability are not close.

3. **Choose the disposition of unfilled passive volume.**
   - `CONVERT_TO_TAKER` (default): the residual is swept aggressively at the venue's taker rate. Correct for a desk that must acquire the exposure regardless.
   - `ABANDON`: the residual goes unexecuted and is charged `unfilled_passive_opportunity_cost_per_share`.
   - **Decision point:** `ABANDON` with a zero opportunity cost prices a missed trade as free, which systematically favours low-fill high-rebate venues — exactly the failure mode this skill exists to prevent. The engine warns; set a real number instead.

4. **Generate and price candidate allocations.** The engine enumerates, deterministically and independent of venue input order:
   - `CONCENTRATED_<venue_id>` — the whole budget to one venue, to reach its highest tier.
   - `EQUAL_SPLIT_BALANCED` — even split, largest-remainder so no share is dropped.
   - `LIQUIDITY_WEIGHTED` — proportional to `passive_fill_probability`.
   - `THRESHOLD_SEEK_<venue>_<tier>[_REM_<venue>]` — exactly enough volume to clear one venue's tier threshold, with the remainder on each other venue in turn.

   Per venue: posted passive $= V_k \times m$; expected fills $= \text{posted} \times p_k$; residual swept or abandoned; then
   $$\text{NetCost}_k = (\text{fills} \times r_k^{\text{maker}}) + (\text{aggressive} + \text{swept}) \times r_k^{\text{taker}} + \text{opportunity}_k$$
   One signed sum, so it is correct without branching on venue orientation.

5. **Apply the hard constraints — and let infeasibility surface.**
   - Candidates whose passive-volume-weighted fill probability falls below `min_weighted_passive_fill_probability` are **rejected**, with the reason recorded in `rejected_strategies`.
   - Candidates exceeding a venue's `max_allocatable_shares` are rejected the same way.
   - **Decision point:** if no candidate survives, the engine raises rather than returning the best of a rejected set. An infeasible constraint is a routing problem to solve, not a filter to quietly drop.
   - The floor binds on **posted passive** volume only. All-aggressive flow is not gated by it.

6. **Compare against the incumbent, not against a strawman.** Pass `baseline_allocation` (your live routing table, summing to the same budget). `net_savings_vs_baseline_usd` = baseline net cost − optimal net cost, unclamped, so a negative or zero result is visible. Without a baseline the field is `None` and a warning says so.

7. **Read `warnings` and `rejected_strategies` before changing any routing weight.** Constraint exclusions, the `PRIOR_PERIOD` benefit-period caveat, and "no improvement, do not re-route" are reported there rather than by raising.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Pricing posted passive volume as if it fills.** Multiplying the full passive allocation by the maker rebate is the single most expensive error here: it makes the venue with the deepest rebate and the worst queue look optimal by construction. Only `posted × fill_probability` earns the rebate; budget for what happens to the rest.
- **Treating fill probability as a filter rather than a cost term.** A binary gate lets a venue at 0.81 look identical to one at 0.99, and lets both look identical on price. It must enter the arithmetic.
- **Silently disabling the fill-probability floor when nothing passes it.** Falling back to "the best of the rejected candidates" returns an allocation that violates the stated routing policy while reporting success. If nothing is feasible, the answer is that nothing is feasible.
- **Chasing a volume tier at period-end on a US equity venue.** Since the Rule 610(d) compliance date (first business day of February 2026), tiers are set from the *prior* month. Volume pushed today cannot improve today's rate — it only moves next month's tier, and the fills bought to get there are billed at today's rate. Any model that reprices the current period on current-period volume describes a market structure that no longer exists.
- **Quoting savings against the worst candidate the optimizer generated itself.** That number is bounded by how bad a strawman you were willing to enumerate, not by anything the desk would have done. Measure against the live routing table or report nothing.
- **Assuming a named venue's orientation from memory.** Cboe **EDGA replaced its inverted model with maker-taker effective 1 November 2024**, and the "BATS" venues have been Cboe BZX and BYX since 2017. Re-read the venue's published schedule.
- **Feeding monthly totals against ADAV thresholds.** A "20,000,000" criterion on a US equity schedule is usually average *daily* added volume, not a monthly total. Mixing the two overstates tier standing by roughly the trading-day count.
- **Letting integer division drop shares.** Splitting $N$ shares across $k$ venues with `//` loses up to $k-1$ shares and understates the volume-weighted fill probability. Allocations must sum to the budget exactly.
- **Ignoring venue capacity.** Concentrating a month of flow on one venue to reach its top tier assumes that venue can absorb it at the modelled fill rate. Set `max_allocatable_shares` where that is not credible.

## Verification

- Instantiate `ExecutionVenueFeeTierOptimizerEngine(TierQualificationBasis.ROLLING_CURRENT)` with a NASDAQ-style schedule (Tier 1 ≥ 0 sh: maker $-0.0020$, taker $+0.0030$; Tier 2 VIP ≥ 15M sh: maker $-0.0028$, taker $+0.0025$).
  - All-aggressive 30,000,000 shares: verify Tier 2 VIP and net cost $= +\$75{,}000$.
  - All-passive 30,000,000 shares at $p = 1.0$: verify net cost $= -\$84{,}000$.
  - All-passive 30,000,000 shares at $p = 0.5$: verify 15,000,000 fills at $-\$42{,}000$, 15,000,000 swept at $+\$37{,}500$, net $= -\$4{,}500$.
- **Fill-probability regression**: a venue with maker $-\$0.0090$ at $p = 0.10$ prices 10,000,000 passive shares at $+\$18{,}000$ (1M filled at $-\$9{,}000$, 9M swept at $+\$27{,}000$), while a venue with maker $-\$0.0020$ at $p = 1.0$ prices the same flow at $-\$20{,}000$. Verify the optimizer selects the second. A model ignoring fill probability scores the first at $-\$90{,}000$ and picks it.
- **Rule 610(d) check**: under `PRIOR_PERIOD` with `qualifying_volume_shares = 20_000_000`, routing only 1,000,000 passive shares must still price at Tier 2 VIP ($-\$2{,}800$). With `qualifying_volume_shares = 5_000_000`, routing 30,000,000 must stay at Tier 1 ($-\$60{,}000$) while `projected_next_period_tier_name` reads `Tier 2 VIP`. Verify `tier_benefit_period` is `NEXT_PERIOD`.
- **Constraint enforcement**: a single venue at $p = 0.10$ with a 0.80 floor must raise, not return an allocation. With a viable venue alongside it, the low-fill candidates must appear in `rejected_strategies` with stated reasons.
- **Savings**: with no `baseline_allocation`, `net_savings_vs_baseline_usd` is `None` and a warning says so. With a baseline already equal to the optimum, savings are $0.0$ and the report says "Do not re-route".
- **Integrity**: every candidate allocates exactly the budget (check 10,000,000 across 3 venues), and reversing the venue input order yields an identical optimal allocation.
- **Negative checks**: `maker_ratio` of 1.7, −0.1, NaN or Inf; a zero or fractional volume; an empty or duplicated venue list; an empty schedule; a schedule with no tier at 0; duplicate tier thresholds; a non-finite rate; a fill probability outside $[0, 1]$; a negative opportunity cost; and a baseline that does not sum to the budget must each raise `VenueOptimizationError`.
- Run `python -m unittest discover -s skills/execution-venue-fee-tier-optimization/scripts` and confirm 100% pass rate.

## Related Skills

- `exchange-fee-tier-and-rebate-structure-analysis`
- `smart-order-routing-across-venues`
- `smart-order-router-failover-on-venue-outage`
- `adverse-selection-measurement-for-passive-orders`
- `post-only-and-maker-taker-fee-optimization`
- `transaction-cost-analysis-tca-integration`
