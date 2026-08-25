# Pre-Flight Checklist — Execution Venue Fee Tier Optimization

## Schedule ingestion

- [ ] Every candidate venue's fee schedule transcribed from the venue's **own current published
      schedule**, not from memory or a cached copy (US equity schedules change by rule filing).
- [ ] Rebates entered as **negative** rates and fees as **positive** (Cboe's `($0.0027)` is `-0.0027`).
- [ ] Each schedule has an explicit tier at threshold `0`.
- [ ] Percentage-of-TCV and ADAV/ADV criteria converted to absolute share thresholds, with the
      forecast or trading-day count used recorded alongside the result.
- [ ] Venue orientation confirmed against the published schedule (EDGA is maker-taker since
      1 November 2024; "BATS" is Cboe BZX / BYX).

## Regulatory basis

- [ ] `TierQualificationBasis` set explicitly — `PRIOR_PERIOD` for US NMS stocks (Reg NMS Rule
      610(d)), `ROLLING_CURRENT` for crypto and some non-US venues.
- [ ] Under `PRIOR_PERIOD`, each venue's `qualifying_volume_shares` supplied from the completed
      prior period.
- [ ] `tier_benefit_period` read: under `PRIOR_PERIOD` any tier gain lands **next** period, and
      today's fills are billed at today's already-fixed rate.

## Inputs

- [ ] `passive_fill_probability` estimated per venue from realized fill data for the relevant
      symbol liquidity tier and horizon — not a global guess.
- [ ] Maker ratio (fraction posted passively) in $[0, 1]$ and matching the strategy's actual mix.
- [ ] Unfilled-passive policy chosen deliberately; under `ABANDON`, a **non-zero** opportunity
      cost per share set.
- [ ] `max_allocatable_shares` set for any venue that cannot absorb a concentrated allocation at
      the modelled fill rate.
- [ ] `baseline_allocation` supplied as the desk's live routing table, summing to the same budget.

## Results review

- [ ] `warnings` read in full before any routing weight is changed.
- [ ] `rejected_strategies` reviewed — an empty accepted set raises, but a heavily rejected set
      means the constraint or the venue list needs attention.
- [ ] `net_savings_vs_baseline_usd` is not `None`, and is positive by a margin that survives the
      costs this module does not price (market impact, adverse selection, spread).
- [ ] Per-venue `expected_passive_fills` vs `posted_passive_shares` sanity-checked against
      realized fill rates.

## Post-deployment

- [ ] Optimal weights applied to the SOR routing table and version-recorded.
- [ ] Realized fill rates monitored against the modelled `passive_fill_probability`; re-run on drift.
- [ ] Re-run at each period boundary as prior-period qualifying volumes roll, and whenever a venue
      republishes its schedule.
