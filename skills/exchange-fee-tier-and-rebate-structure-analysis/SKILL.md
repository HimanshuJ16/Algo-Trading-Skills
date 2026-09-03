---
name: exchange-fee-tier-and-rebate-structure-analysis
description: >-
  Use when computing the signed net cost of a fill mix at one venue under its
  maker-taker or inverted schedule and deciding whether the next volume tier is worth
  chasing. Allocating flow across venues is execution-venue-fee-tier-optimization.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: market-microstructure-latency
  tags: exchange-fees, maker-taker, taker-maker, rebate-analysis, volume-tiers, order-routing, market-microstructure, reg-nms-610
  brokers_frameworks: "SEC Reg NMS Rule 610; Nasdaq Equities Price List; Cboe US Equities Fee Schedules; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill in market making, Smart Order Routing (SOR), and venue fee optimization when you need the *signed net* cost of a fill mix at a venue, not its headline rate. Exchanges price liquidity asymmetrically — **maker-taker** venues credit the passive side and charge the aggressive side, **inverted (taker-maker)** venues do the reverse — and layer volume tiers on top. This module classifies the active tier from the correct qualifying period, computes net cost after rebates with an explicit sign convention, and evaluates whether the volume gap to the next tier is worth closing.

## When NOT to Use

- **As a routing decision on its own.** Fee is one term of execution cost and usually the smaller one. An inverted venue's taker rebate is routinely swamped by adverse selection, and a maker rebate is worthless on an order that never fills. Pair with `transaction-cost-analysis-tca-integration` and a fill-probability model.
- **On a schedule whose tiers are percentages of consolidated volume.** Many US equity tiers qualify on a share of Total Consolidated Volume or of consolidated ADV (Nasdaq's price list carries criteria such as "Add 0.65% or greater of TCV or 70M shares ADV"). This module takes absolute share thresholds; converting a percentage criterion requires a consolidated-volume forecast it does not make.
- **On a schedule requiring several simultaneous criteria** (add % *and* remove %, or a cross-asset condition). One scalar threshold cannot express those.
- **On sub-$1.00 securities.** US access fees for those are capped as a percentage of quotation price, not per share; this module is per-share only.
- **To model current-month tier chasing on a US equity venue.** Rule 610(d) removed that mechanic — see the Workflow.

## Prerequisites

- Venue fee schedule as tiers of (threshold, maker rate, taker rate), with an explicit tier at threshold `0`.
- **Sign convention**: a negative rate is a rebate the venue *credits*; a positive rate is a fee it *charges*. Every USD figure the engine returns follows the same convention — positive is money out.
- Pricing model: `MAKER_TAKER` or `TAKER_MAKER`.
- **Tier qualification basis** — `PRIOR_PERIOD` or `ROLLING_CURRENT`. Required, no default; see step 1.
- Under `PRIOR_PERIOD`, the qualifying volume from the *completed prior period*, separate from the volume being priced.

## Workflow

1. **Establish the tier qualification basis first — it decides whether everything downstream is right.**
   - `PRIOR_PERIOD`: the tier is fixed by a completed prior period, and volume traded now cannot change the rate applied now. **This is mandatory for US NMS stocks.** Reg NMS Rule 610(d) provides that an exchange "shall not impose ... any fee ... or provide ... any rebate ... for the execution of an order in an NMS stock that cannot be determined at the time of execution"; exchanges implemented it by deriving tier volumes from the prior month.
   - `ROLLING_CURRENT`: the tier is fixed by a rolling window that includes the volume being priced, so crossing a threshold reprices the window retroactively. This is the crypto-venue model (rolling 30-day volume) and some non-US venues. It is **not lawful for US NMS stocks** on or after 2 February 2026.
   - **Decision point:** under `PRIOR_PERIOD` the engine *refuses* to substitute the priced volume for the prior period's. Supply `qualifying_volume_shares` explicitly — the volume being priced is precisely the volume that may not set its own rate.

2. **Validate the schedule against the declared pricing model before pricing anything.**
   - The engine raises when a schedule contradicts its declared model (a "maker-taker" venue whose maker rate is worse than its taker rate is an inverted schedule).
   - **Decision point:** a maker-taker tier with a *positive* maker rate is legal but is not a rebate tier. That surfaces as a warning, not an error — read it before routing passive flow there expecting to be paid.
   - A schedule with no tier at threshold `0` is rejected rather than silently assigning volume to a tier it does not qualify for.

3. **Compute signed net execution cost.**
   - $\text{MakerSide} = Q_{\text{maker}} \times r_{\text{maker}}$, $\quad \text{TakerSide} = Q_{\text{taker}} \times r_{\text{taker}}$
   - $\text{NetCost} = \text{MakerSide} + \text{TakerSide}$ — one signed sum, valid on both venue orientations. A negative net cost is net rebate capture.
   - **Decision point:** on an inverted venue, read `maker_side_cost_usd` / `taker_side_cost_usd`. The maker-taker-oriented `gross_taker_fees_usd` and `gross_maker_rebates_usd` fields are 0.0 there by construction, and the report says so.

4. **Evaluate the tier jump against what it costs to get there.**
   - Gap: $\Delta V = \max(0,\ V_{\text{next}} - V_{\text{qualifying}})$.
   - Gross savings: the priced mix repriced at the next tier's rates. **Not clamped at zero** — a higher tier can be worse for a maker-heavy desk (smaller rebate), and hiding that inverts the decision.
   - Incremental cost: $\Delta V$ priced at whichever tier actually bills it. Under `ROLLING_CURRENT` that is the *next* tier (crossing reprices the window); under `PRIOR_PERIOD` it is the *current* tier, because today's volume cannot reprice today's fills.
   - **Act on `net_tier_jump_benefit_usd` = gross savings − incremental cost.** The gross figure alone always overstates the case, and even the net figure excludes adverse selection and market impact on the forced volume.
   - **Decision point:** `tier_jump_benefit_period` tells you *when* the benefit lands. Under `PRIOR_PERIOD` it is `NEXT_PERIOD` — trading more today buys a better rate next month, nothing today.

5. **Check the access fee cap where applicable.** `check_reg_nms_access_fee_cap()` flags taker rates above the Rule 610(c) cap for US NMS stocks priced at or above $1.00.

6. **Audit Report Generation**: output the structured `FeeTierAnalysisReport`, including `warnings`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Chasing a volume tier at month-end on a US equity venue.** Since 2 February 2026 (Rule 610(d)), tiers are set from the *prior* month's volume. Extra volume pushed today cannot improve today's rate — it only moves next month's tier. Any model that reprices the current month on current-month volume is describing a market structure that no longer exists, and the trading it motivates is unpaid.
- **Assuming a named venue is still inverted.** Cboe **EDGA replaced its inverted model with maker-taker effective 1 November 2024**; routing passive flow there expecting to be charged, or aggressive flow expecting a rebate, has the sign backwards. Re-read the venue's published fee schedule rather than trusting a remembered classification. Cboe BYX and Nasdaq BX are current inverted examples.
- **Treating a taker rebate as a negative taker fee.** On an inverted venue the credited side is the taker. Reporting that as `gross_taker_fees = -$10,800` produces a field whose name contradicts its sign, and any downstream sum that assumes fees are non-negative silently breaks.
- **Reporting gross tier-jump savings as the benefit.** Pricing today's volume at tomorrow's rates ignores the cost of the ΔV shares you must trade to qualify. It is not an opportunity-cost calculation, it is the upper bound of one.
- **Clamping savings at zero.** A higher tier is not automatically a better tier. A schedule that trades a smaller rebate for a smaller fee is worse for a maker-heavy desk, and a `max(0, savings)` clamp reports "no downside" on a strictly losing move.
- **Conflating gross fees with net capture.** Evaluating taker fees without netting the maker rebates earned on passive fills overstates cost and mis-ranks venues.
- **Letting a schedule contradict its declared pricing model.** A mislabelled venue is exactly how passive flow ends up paying a maker fee it expected to earn.

## Verification

- Instantiate `ExchangeFeeTierAnalyzerEngine("NASDAQ_EQUITIES", "MAKER_TAKER", tiers, TierQualificationBasis.ROLLING_CURRENT)` with Tier 1 (≥0 sh, maker $-0.0020$, taker $+0.0030$) and Tier 2 VIP (≥10M sh, maker $-0.0024$, taker $+0.0025$). Submit 5,000,000 maker / 3,000,000 taker: verify Tier 1, `maker_side_cost_usd` $= -\$10{,}000$, `taker_side_cost_usd` $= +\$9{,}000$, `net_transaction_cost_usd` $= -\$1{,}000$, `effective_cost_per_share` $= -\$0.000125$, and a 2,000,000-share gap.
- **Rule 610(d) check**: with `PRIOR_PERIOD` and `qualifying_volume_shares=12_000_000` but only 1M maker / 1M taker priced, verify Tier 2 VIP applies and net cost is $+\$100$ — the tier must come from the prior period, not from the volume being priced. Verify the mirror case (20M traded now, 500k prior) stays in Tier 1.
- **Tier jump**: verify `tier_jump_benefit_period` is `NEXT_PERIOD` under `PRIOR_PERIOD` and `CURRENT_PERIOD` under `ROLLING_CURRENT`, and that the incremental 2,000,000 shares are billed at $-\$250$ (current tier) versus $-\$1{,}125$ (next tier) respectively.
- **Inverted venue**: 4M maker @ $+0.0020$ / 6M taker @ $-0.0018$ gives net $-\$2{,}800$, with the maker-taker-named fields at 0.0 and an inverted-venue warning present.
- **Negative checks**: an empty schedule, a schedule with no tier at 0, duplicate thresholds, a NaN or infinite rate, a negative volume, an unknown pricing model or qualification basis, a schedule contradicting its declared model, and `PRIOR_PERIOD` without a qualifying volume must each raise `FeeScheduleError`.
- Run `python -m unittest discover -s skills/exchange-fee-tier-and-rebate-structure-analysis/scripts` and confirm 100% pass rate.

## Related Skills

- `execution-venue-fee-tier-optimization`
- `order-to-trade-ratio-fee-penalty-avoidance`
- `post-only-and-maker-taker-fee-optimization`
- `transaction-cost-analysis-tca-integration`
- `smart-order-routing-across-venues`
- `us-reg-nms-order-protection-rule-compliance`
