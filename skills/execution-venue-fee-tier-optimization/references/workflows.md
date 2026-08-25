# Workflows — execution-venue-fee-tier-optimization

## 0. Sign convention

Fix this before anything else, because every downstream figure depends on it:

```
rate < 0  ->  rebate: the venue CREDITS the member
rate > 0  ->  fee:    the venue CHARGES the member
```

A signed cost is always `shares * rate`. Net cost positive means money leaving the desk; negative
means net rebate capture. Identical to `exchange-fee-tier-and-rebate-structure-analysis`, so
schedules transcribed for one work in the other.

## 1. Venue schedule ingestion

Transcribe each venue's published schedule into `VenueFeeTier` rows inside an `ExecutionVenueSpec`.

- Include an explicit tier at threshold `0`; the engine rejects a schedule without one.
- Convert the venue's notation into the sign convention above. Cboe publishes rebates in
  parentheses — `($0.0027)` is a **rebate**, i.e. `-0.0027` here.
- Thresholds must be absolute share counts on a single criterion. Percentage-of-TCV and ADAV/ADV
  criteria must be converted by the caller, using their own consolidated-volume forecast or
  trading-day count, and the result recorded as conditional on it.
- Duplicate thresholds and non-finite rates are rejected at construction.
- Tiers are sorted internally, so input order does not matter.

## 2. Choose the tier qualification basis

A correctness decision, not a configuration preference. The engine requires it explicitly.

| Basis | Use for | Tier fixed by |
|---|---|---|
| `PRIOR_PERIOD` | **US NMS stocks** (mandatory, Reg NMS Rule 610(d), compliance date first business day of February 2026) | The completed prior period, supplied per venue as `qualifying_volume_shares` |
| `ROLLING_CURRENT` | Crypto venues (rolling 30-day volume), some non-US venues | The volume actually executed at the venue under the allocation being priced |

Under `PRIOR_PERIOD` the engine refuses to fall back to routed volume. That is the whole point of
Rule 610(d): the volume being priced is exactly the volume that may not determine its own rate.

Note that under `ROLLING_CURRENT` the qualifying volume is **executed**, not allocated. Passive
shares that never fill and are abandoned do not qualify you for anything.

## 3. Estimate passive fill probability

`passive_fill_probability` is the probability that a passive share posted at the venue fills within
the execution horizon. It is an input this module does not estimate, and it decides the answer:

```
expected_fills = posted_passive * p
unfilled       = posted_passive - expected_fills
```

Estimate it per venue, per symbol liquidity tier, and per horizon from your own fill records. A
single global constant makes the optimizer's output a restatement of the rebate schedule.

## 4. Choose the disposition of unfilled passive volume

| Policy | Residual treatment | Executed volume |
|---|---|---|
| `CONVERT_TO_TAKER` (default) | Swept aggressively at the venue's taker rate | Full allocation |
| `ABANDON` | Left unexecuted, charged `unfilled_passive_opportunity_cost_per_share` | Fills + aggressive only |

`ABANDON` with a zero opportunity cost prices a missed trade as free. That is almost never true and
it systematically favours low-fill high-rebate venues, so the engine warns when it is configured.

## 5. Candidate generation

Deterministic and independent of the order venues are passed in. Every candidate allocates exactly
the volume budget; integer splits use the largest-remainder method.

| Candidate | Allocation |
|---|---|
| `CONCENTRATED_<venue_id>` | Whole budget to one venue, one candidate per venue |
| `EQUAL_SPLIT_BALANCED` | Even split across all venues |
| `LIQUIDITY_WEIGHTED` | Proportional to `passive_fill_probability` |
| `THRESHOLD_SEEK_<venue>_<tier>[_REM_<venue>]` | Exactly the tier threshold on one venue, remainder on each other venue in turn |

Allocations identical after removing zero-share entries are deduplicated, so a threshold-seeking
candidate that happens to equal an even split appears once.

This is a heuristic family, not an optimum over the allocation simplex. It does not prove no other
split is cheaper — it scores the splits a desk would realistically consider.

## 6. Per-venue cost calculation

```
posted_passive = round(allocation * maker_ratio)          # capped at the allocation
aggressive     = allocation - posted_passive
expected_fills = posted_passive * p
unfilled       = posted_passive - expected_fills
swept          = unfilled  (CONVERT_TO_TAKER)  else 0
executed       = expected_fills + aggressive + swept

qualifying     = qualifying_volume_shares   (PRIOR_PERIOD)
               = floor(executed)            (ROLLING_CURRENT)
tier           = highest tier whose threshold <= qualifying     # inclusive

maker_side     = expected_fills * tier.maker_rate_per_share
taker_side     = (aggressive + swept) * tier.taker_rate_per_share
opportunity    = (unfilled if ABANDON else 0) * opportunity_cost_per_share
net_cost       = maker_side + taker_side + opportunity
```

`gross_maker_rebates_usd` and `gross_taker_fees_usd` are retained for maker-taker reporting and are
0.0 when the corresponding rate is not a credit / not a charge. Read `maker_side_cost_usd` and
`taker_side_cost_usd` for the signed truth.

Under `PRIOR_PERIOD`, `projected_next_period_tier_name` reports the tier this venue's *executed*
volume would qualify for next period. That, not the current tier, is where a tier improvement shows
up.

## 7. Constraint application

Two hard constraints, applied per candidate:

1. **Capacity** — an allocation above a venue's `max_allocatable_shares` is rejected.
2. **Fill-probability floor** — a candidate whose passive-volume-weighted fill probability falls
   below `min_weighted_passive_fill_probability` is rejected.

```
weighted_passive_fill = sum(posted_passive_k * p_k) / sum(posted_passive_k)
```

Weighted over **posted passive shares**, not total volume: fill probability is a property of
passive orders, so an all-aggressive allocation is not constrained by it and reports `1.0`.

Rejected candidates are retained in `rejected_strategies` with a stated reason. If no candidate
survives, the engine **raises**. It does not fall back to the best of the rejected set — that would
return an allocation violating the desk's own routing policy while reporting success.

The 0.80 default is a starting point, not a standard. Set it from your own opportunity-cost
tolerance.

## 8. Ranking and savings

Accepted candidates are ranked by net cost ascending, tie-broken by name for determinism. The
cheapest is `optimal_strategy`.

```
net_savings_vs_baseline_usd = baseline.total_net_cost_usd - optimal.total_net_cost_usd
```

`baseline_allocation` is the desk's **live routing table**, and must sum to the same budget — a
baseline priced over a different volume is not comparable. Savings are **not clamped**: zero or
negative means the incumbent is already at or better than the optimum, and the report says "Do not
re-route".

With no baseline supplied the field is `None` and a warning explains why. The engine will not
substitute the worst candidate it generated itself: that number measures how bad a strawman was
enumerated, not what the desk stands to save.

## 9. Report

`VenueFeeOptimizationReport` carries the budget and maker mix, the qualification basis and benefit
period, the unfilled-passive policy, the optimal and baseline strategies with full per-venue
breakdowns, every accepted candidate ranked, every rejected candidate with its reason, and
`warnings`.

**Read `warnings`.** Constraint exclusions, the `PRIOR_PERIOD` benefit-period caveat, the zero
opportunity-cost caveat, and "no improvement, do not re-route" are reported there rather than by
raising, so an unread list is a silently ignored finding.

## 10. SOR routing update

Apply `optimal_strategy.volume_allocations_shares` as venue weights only after checking the
warnings and confirming the fill-probability inputs are current. Re-run when a venue republishes
its schedule (US equity schedules change by rule filing, often monthly), when realized fill rates
drift from the modelled `passive_fill_probability`, and at each period boundary when the
prior-period qualifying volumes roll.
