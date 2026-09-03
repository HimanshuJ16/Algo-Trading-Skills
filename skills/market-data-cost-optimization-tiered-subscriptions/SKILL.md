---
name: market-data-cost-optimization-tiered-subscriptions
description: >-
  Market data subscription tiering engine that assigns each symbol to a direct-depth, real-time top-of-book, or delayed/EOD tier from its live position, signal, and trade-recency state, and prices the change against a caller-supplied fee schedule while keeping tradeable symbols off delayed data.
domain: Data Management Global
subdomain: Market Data Cost & Entitlement Governance
tags: ["market-data", "cost-optimization", "tiered-subscriptions", "bloomberg-bpipe", "refinitiv-dacs", "data-entitlements", "sip-vs-direct"]
brokers_frameworks: ["Bloomberg EMRS", "Refinitiv DACS", "TRG Screen", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a symbol universe is much larger than the set of symbols actually being traded, and the data bill has a component that genuinely scales with symbol count — a per-query or per-instrument vendor plan, a symbol-slot-limited API plan, or feed-handler/storage/egress capacity you provision per symbol. The engine classifies each symbol as `TIER1_DIRECT_L3` (full depth), `TIER2_SIP_L1` (real-time top-of-book), or `TIER3_DELAYED_EOD` from its position, signal, and trade-recency state, then prices the recommended changes against **your** contracted schedule.

The tier decision is a safety decision before it is a cost decision: delayed data cannot carry an order, so any symbol the strategy might trade before the next audit is held on a real-time tier regardless of what that costs.

## When NOT to Use

- **As a model of exchange or SIP pricing.** The dominant US equity market data charges are **per firm, per subscriber, and per non-display application**, and one entitlement covers the whole security universe. Nasdaq TotalView is $80.50 per subscriber/month (2025) with "Security Coverage" listed as *"Nasdaq, NYSE, and Other Regional Issues"*; UTP Real-Time Direct Access is $2,500/month **per firm** and Real-Time Non-Display Use $3,500/month **per firm**. Cutting 90% of your symbols moves none of that. See `market-data-entitlement-and-licensing-per-venue`.
- **With the shipped `TIER_COSTS` values.** They are illustrative placeholders with no market basis; the engine logs a warning when you leave them in place. Pass `tier_monthly_costs_usd` from your contract before quoting any savings figure.
- **To quote a headline "% data spend reduction" without declaring fixed cost.** Pass `fixed_monthly_platform_cost_usd` and report `total_savings_percentage_including_fixed`, not `savings_percentage`.
- **As an entitlement or licensing gate.** A promotion to TIER1 may require a non-display or professional-subscriber licence this engine knows nothing about.
- **As an actuator.** Output is a recommendation set. Applying it means a change in DACS/EMRS and a monthly usage report to the exchange — LSEG DACS is an entitlement enforcement and reporting system, not a billing engine.

## Prerequisites

- Per-symbol subscription state: `symbol`, `current_tier`, `has_active_position`, `has_active_signal`, `days_since_last_trade` (`None` = never traded), and `days_in_current_tier` if you use the dwell guard.
- **Your contracted symbol-metered rate per tier**, passed as `tier_monthly_costs_usd`.
- Your non-symbol-metered monthly data spend (per-firm access, per-subscriber entitlements, non-display licences, connectivity), passed as `fixed_monthly_platform_cost_usd`.
- Your billing period length, if you set `min_days_before_demotion`.

## Workflow

1. **Separate the metered bill from the fixed bill before anything else**:
   - Split the invoice into the portion that varies with symbol count and the portion that does not. Only the first can move. If it is small, the honest output of this audit is "no material saving available" — say that rather than reporting a large percentage of a small base.
   - Note where per-symbol charges **cap**: UTP Per Query is $0.0075 per query but caps at 3,200 quotes / $24 a month for a Professional Subscriber. Past the cap, dropping symbols saves nothing.
2. **Classify each symbol by trading relevance, safety-first**:
   - Position **and** live signal $\implies$ `TIER1_DIRECT_L3`.
   - Position **or** live signal $\implies$ at least `TIER2_SIP_L1`. A live signal with no position and no fill for a year still means an order may be sent before the next audit — it must not be demoted to delayed data.
   - Last fill within `demotion_inactivity_days_threshold` $\implies$ `TIER2_SIP_L1`. `None` (never traded) is stale, not recent.
   - Otherwise $\implies$ `TIER3_DELAYED_EOD`.
3. **Reject inputs rather than defaulting them**:
   - An unrecognised `current_tier` is an error, not a symbol to price at the top tier — defaulting a typo to the most expensive tier invents baseline spend and reports the phantom as a saving.
   - A duplicate symbol is an error: it double-counts that symbol's spend on both sides of the comparison.
4. **Apply the demotion dwell guard against a non-prorated billing period**:
   - UTP Data Policies: *"All fees are subject to change and fees will not be prorated."* A demotion applied mid-period saves nothing that period, and re-promoting next period costs a full period again. Set `min_days_before_demotion` to at least your billing period. The guard withholds demotions only — a promotion is never delayed for cost reasons.
5. **Report both denominators**:
   - `savings_percentage` is against symbol-metered spend only. `total_savings_percentage_including_fixed` is against total data spend. Quote the second one to a budget owner.
   - A net cost increase is reported as `NET_COST_INCREASE`, not as "already optimal" — promotions demanded by live positions are a correct outcome that costs more.
6. **Hand the decisions to the entitlement system**, and re-audit on the next billing cycle.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming direct feeds are billed per symbol.** They are not, on any venue cited here. Nasdaq Depth Non-Display is $396/subscriber for 1-39 subscribers and then a flat per-firm fee ($15,840 / $31,680 / $75,000 at 40 / 100 / 250+ subscribers). A savings model built on a per-symbol direct-feed rate produces a number that will not appear on the invoice.
- **Demoting a symbol that has a live signal but no open position.** It has not traded in months, so every recency rule marks it stale — and then the strategy fires and prices the order off 15-minute-delayed data. Position state alone is not a sufficient test of tradeability.
- **Treating "delayed" as a small degradation.** CME publishes website quotes delayed *at least 10 minutes*; MiFIR Article 13(2) sets the EU free-data delay at 15 minutes. That is not a slippage cost, it is an unusable price.
- **Quoting the symbol-metered savings percentage as the data spend reduction.** Cutting $89,550 out of $100,000 of metered spend is an 89.55% cut of that slice but a 44.8% cut of the bill if the firm also pays $100,000/month in fixed per-firm, per-subscriber and non-display fees — and less again as that fixed component grows.
- **Churning tiers inside a non-prorated billing period.** The demotion does not refund the current period and the re-promotion buys a fresh full period. Repeated monthly, this raises the bill while the report claims savings.
- **Forgetting the entitlement side of a promotion.** Adding a direct depth feed can trigger a non-display licence obligation and professional-subscriber reclassification whose cost dwarfs the per-symbol line the audit was optimising.
- **Auditing on stale activity inputs.** `has_active_signal` and `days_since_last_trade` are caller-supplied as of the audit date. Feeding yesterday's signal state is how a live name gets demoted.

## Verification

- Instantiate `MarketDataCostOptimizerEngine(demotion_inactivity_days_threshold=30, tier_monthly_costs_usd={...})`. Audit 100 symbols on TIER1 — 10 with position and signal, 90 dormant at 60 days. With the illustrative schedule (TIER1 $1,000, TIER2 $150, TIER3 $5 per symbol/month) verify 90 demotions, baseline $100,000, optimized $10,450, savings $89,550 and `savings_percentage` $= 89.55$, status `COST_OPTIMIZATION_SUCCESS`. **These figures are arithmetic on placeholder rates, not a market result.**
- Verify the safety rule: a symbol with `has_active_signal=True`, `has_active_position=False`, `days_since_last_trade=365` $\implies$ `determine_optimal_tier` must return `TIER2_SIP_L1`, never `TIER3_DELAYED_EOD`.
- Verify the fixed-cost denominator: same 10-symbol demotion with `fixed_monthly_platform_cost_usd=50000` $\implies$ `savings_percentage` $= 99.5$ but `total_savings_percentage_including_fixed` $= 16.58$.
- Verify input rejection: an unrecognised `current_tier` and a duplicate symbol must each raise `ValueError` rather than being priced.
- Verify the dwell guard: with `min_days_before_demotion=31`, a dormant symbol at `days_in_current_tier=5` $\implies$ action `HOLD_MIN_DWELL` and zero savings; a promotion at `days_in_current_tier=0` $\implies$ action `PROMOTE`, never held.
- Run `python -m unittest discover -s skills/market-data-cost-optimization-tiered-subscriptions/scripts`.

## Related Skills

- `market-data-entitlement-and-licensing-per-venue`
- `real-time-vs-delayed-data-entitlement-handling`
- `historical-data-backfill-rate-limit-management`
- `data-vendor-contractual-usage-restriction-tracking`
- `cost-monitoring-for-cloud-trading-infrastructure`
