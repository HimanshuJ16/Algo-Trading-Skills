# Standards — execution-venue-fee-tier-optimization

## Jurisdiction

The "Regulatory constraints" section below is **US-specific**, applying to NMS stocks on US
national securities exchanges. It does not apply to crypto venues, to non-US exchanges, or to
off-exchange US venues, which remain free to qualify tiers on a rolling current window. Do not
universalize it. Everything under "Engineering standards" is repo engineering policy, not law.

## Regulatory constraints (verified against primary sources)

| Requirement | Rule | Status | What it means for allocation |
|---|---|---|---|
| An exchange may not impose a fee or provide a rebate for the execution of an order in an NMS stock "that cannot be determined at the time of execution." | Reg NMS **Rule 610(d)** | **In force.** Compliance date the first business day of February 2026 (2 February 2026), extended from 3 November 2025 by [temporary exemptive relief granted 31 October 2025](https://www.sec.gov/newsroom/press-releases/2025-130-sec-issues-exemptive-order-regarding-compliance-certain-rules-under-regulation-nms). | Volume tiers on US equity venues are qualified on a **completed prior period**. Routing volume during a period cannot improve that period's rates. Model with `TierQualificationBasis.PRIOR_PERIOD`; any tier gain is billed `NEXT_PERIOD`. |
| Access (taker) fee cap: **$0.0030/share** for NMS stocks priced ≥ $1.00; 0.3% of quotation price below $1.00. | Reg NMS **Rule 610(c)** | **In force** — the operative cap today. | An upper bound on the taker rates in any US equity schedule transcribed into `VenueFeeTier`. Enforcement lives in `exchange-fee-tier-and-rebate-structure-analysis`, not here. |
| Amended access fee cap: **$0.0010/share** ≥ $1.00; 0.1% of quotation price below $1.00. | Reg NMS **Rule 610(c)** as amended | **Adopted, not yet in force.** Compliance deferred to the first business day of **November 2027** by [SEC exemptive order of 11 June 2026 (Rel. 34-105656)](https://www.sec.gov/files/rules/exorders/2026/34-105656.pdf), having previously been November 2026. | Materially compresses the taker side of every schedule when it lands. Re-verify before relying on the date — it has already been extended twice. |

Nothing in this module hard-codes a compliance date or a per-share rate.

### How exchanges implemented Rule 610(d)

Both major US equity venue families now state the prior-month basis on the fee schedule itself:

- Cboe [EDGX](https://www.cboe.com/us/equities/membership/fee_schedule/edgx/) and
  [EDGA](https://www.cboe.com/us/equities/membership/fee_schedule/edga/): "In compliance with
  Regulation NMS Rule 610(d), effective February 9, 2026, unless otherwise indicated, all volume
  figures will be derived from quoting or trading activity in the prior month."
- Nasdaq Equity 7 carries the equivalent note, and defines Consolidated Volume ("TCV") as
  volume reported to the consolidated transaction reporting plans **during a month**.

**Consequence for this skill:** the period-end "push volume to hit the tier" play does not exist
on US equity venues. Allocation still matters — it sets *next* period's tier standing, and it
still decides which already-qualified rate each share pays today.

## Venue pricing model — verify, do not remember

Venue orientation changes. **Cboe EDGA replaced its inverted (taker-maker) model with maker-taker
effective 1 November 2024**; its published schedule now shows an add-liquidity rebate
(`($0.0027)`) against a remove-liquidity fee (`$0.0030`). The "BATS" venues have been branded
Cboe BZX and Cboe BYX since 2017. Any material citing "BATS" or calling EDGA the canonical
inverted venue is stale. Confirm orientation against the venue's own published schedule before
routing; specific per-share rates are deliberately not reproduced here, because they change by
rule filing and a stale rate in a reference file is worse than no rate.

## Engineering standards

| Metric | Engineering standard |
|---|---|
| Sign convention | Every rate and every reported amount is signed: negative = credited to the member, positive = charged. Net cost is one signed sum, correct on both venue orientations without branching. |
| Expected-value pricing | Passive volume MUST be priced at `posted × passive_fill_probability`, never at posted size. Unfilled passive volume MUST be explicitly disposed of — swept at the taker rate or charged an opportunity cost. |
| Fill-probability floor | A desk routing policy, **not** a regulatory threshold. The 0.80 default is a configurable starting point with no external authority behind it. It MUST bind on posted passive volume only, and MUST NOT be silently relaxed: an infeasible constraint raises. |
| Constraint auditability | Every rejected candidate MUST be retained with the reason for its rejection. A silently dropped candidate is an unreviewable routing decision. |
| Allocation integrity | Every candidate MUST allocate exactly the volume budget. Integer splits use the largest-remainder method; floor division silently drops shares and understates weighted fill probability. |
| Determinism | Candidate generation and ranking MUST NOT depend on the order venues are passed in. |
| Tier qualification basis | MUST be explicit. There is no safe default, so the engine requires it rather than assuming one. |
| Base tier | A schedule MUST define a tier at threshold 0, so no volume can be assigned a tier it does not qualify for. |
| Savings baseline | Savings MUST be measured against a caller-supplied incumbent allocation, unclamped. Comparing the winner to the worst self-generated candidate produces a number bounded only by the strawman's badness. |
| Numerical handling | Non-finite rates, probabilities, and ratios are rejected at construction rather than propagating a NaN that reads as a valid float downstream. |

## Known limitations

- **Absolute, single-criterion share thresholds only.** Real US equity tiers commonly qualify on a
  percentage of Total Consolidated Volume, on ADAV/ADV (average *daily* volume), or on several
  simultaneous criteria. Converting those is the caller's responsibility and requires forecasts
  and a trading-day count this module does not hold.
- **No market impact, adverse selection, queue position, or spread.** Fee is one term of execution
  cost and often the smaller one. A rebate-maximising allocation can be net-negative once adverse
  selection on the filled passive volume is priced.
- **`passive_fill_probability` is an exogenous input.** It is not estimated, not validated against
  realized fills, and not conditioned on symbol, size, or horizon. Garbage in, confidently
  optimized garbage out.
- **Single-period, static.** There is no intra-period re-optimization and no path dependence
  between the allocation and the fill probability it assumes.
- **Per-share rates only.** Sub-$1.00 US securities are capped as a percentage of quotation price
  and are out of scope.
- **Candidate enumeration is heuristic, not an optimum over the simplex.** The engine scores a
  fixed family of allocations (concentrated, equal, liquidity-weighted, threshold-seeking); it does
  not prove that no other split is cheaper.

## Category

`Venue Integration & Microstructure`
