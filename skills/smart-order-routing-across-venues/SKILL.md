---
name: smart-order-routing-across-venues
description: >-
  Use when splitting a parent order across lit US venues and the split must be
  defensible afterwards: consolidates the best accessible displayed price and ranks
  equally-priced venues by fee-inclusive net price. Options have their own linkage plan.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: execution-algorithms
  tags: smart-order-routing, sor, reg-nms, nbbo-consolidation, maker-taker-fee, order-book-sweep
  brokers_frameworks: "SEC Regulation NMS (Rules 610, 611, 612); US Equities Market Microstructure; Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when splitting a parent order in a **US NMS stock** across fragmented lit venues (NASDAQ, NYSE, Cboe BZX/BYX/EDGA/EDGX, IEX, MEMX) and you need the split to be defensible after the fact: which venues were at the best price, why one was ranked ahead of another at the same price, how much quantity the price level could not absorb, and what obligation that remainder carries.

The engine consolidates the best price *that actually has displayed size*, ranks the venues quoting it by taker-fee-inclusive net price, and emits a `SORRoutingPlan` of child orders plus an explicit unrouted balance. It plans routes only — it does not send orders, model fills, or track order state.

**Who owes what.** SEC Reg NMS Rule 611 (17 CFR 242.611) obliges **trading centers** — exchanges, ATSs, OTC market makers, and broker-dealers that execute internally — to maintain policies preventing executions at prices inferior to *protected quotations*. A routing broker-dealer that does not execute internally owes **best execution** (FINRA Rule 5310) rather than Rule 611 directly, but its routing decisions are what expose a trading center to a trade-through. Treat this engine as the routing-side control that keeps those decisions clean, not as a Rule 611 compliance surveillance system — that is `us-reg-nms-order-protection-rule-compliance`.

## When NOT to Use

- **Listed options.** Rule 611 applies only to NMS stocks. Options trade-through protection lives in the **Options Order Protection and Locked/Crossed Market Plan** (the options linkage plan), which has its own protected-quote definition, its own exemptions, and no NBBO fee-cap analogue. Nothing in this skill transfers. Earlier versions of this skill claimed options coverage; that claim was wrong.
- **Non-US venues.** MiFID II/RTS 1 best execution, SEBI, and JPX have no order protection rule of this shape. The tick-quantization and slicing mechanics generalize; every regulatory statement here does not.
- **As a Rule 611 compliance check on executions.** This engine reasons over a quote snapshot and produces intent. Deciding whether a *completed* execution was a trade-through, and which statutory exemption applied, requires execution timestamps, automated-quote flags, and self-help state — use `us-reg-nms-order-protection-rule-compliance`.
- **As the depth-of-book sweep.** The plan targets one price level and stops. It deliberately does not walk the book, because walking it is precisely where trade-throughs and ISO obligations arise (see Workflow step 5).
- **As a substitute for protected-quote data.** `VenueQuote` has no `is_automated` flag. A protected quotation must be an *automated* quotation of an exchange's BBO (17 CFR 242.600(b)(81)). If your feed carries manual/non-firm quotes, filter them before calling — the engine cannot tell the difference.
- **For venue-outage handling.** A venue that is quoting but unreachable is a different problem: `smart-order-router-failover-on-venue-outage`.

## Prerequisites

- Python 3.10+, standard library only.
- A same-instant top-of-book snapshot per venue (`VenueQuote`: `venue_id`, `bid_price`, `bid_qty`, `ask_price`, `ask_qty`, `taker_fee_per_share`, `maker_rebate_per_share`, `latency_ms`). One quote per venue — duplicates are rejected. A side with no displayed size is expressed as `qty=0`; its price may be a `0.0` placeholder.
- Parent order specification: `parent_order_id`, `symbol`, `side` (`'BUY'`/`'SELL'`, anything else raises), `quantity` (finite, > 0).
- The instrument's **tick size** for `price_increment`. The default is `$0.01` (Rule 612, NMS stocks ≥ $1.00). Sub-$1.00 stocks quote in `$0.0001` and must pass it explicitly — see the pitfall below.
- Per-venue taker fee schedules. Defaults on `VenueQuote` are illustrative placeholders, not any venue's published rate.

## Workflow

1. **Validate the snapshot before consolidating.**
   - Every price and size must be finite; sizes ≥ 0; a side that is quoting must have a price > 0; a venue may not be locked or crossed against its own book; `venue_id` must be unique.
   - **Decision point — reject, don't filter.** A single `NaN` ask propagates through `min()` and yields a plan with a `NaN` NBBO and a `NaN` child limit price, which a downstream FIX adapter will happily serialize. The engine raises `ValueError` naming the offending venue and field.

2. **Consolidate the best *accessible* price.**
   - `nbbo_price` is the best price among venues with **non-zero displayed size**; `best_quoted_price` is the best price across all supplied quotes.
   - **Decision point — a zero-size quote is not routable liquidity, and it is not a licence to route past it either.** When the two prices differ the engine logs a warning and routes at `nbbo_price`, because there is nothing to execute against a quote with no size. If that gap is not explained by a genuine size-0 quote, your snapshot is stale — stop and re-fetch rather than routing.

3. **Compare prices on the tick grid, never with `==`.**
   - Venue feeds reconstruct the same quoted price along different float paths (`10007/100.0` vs `10007*0.01`), which differ in the last bit. Prices are quantized to integer ticks via `round(price / price_increment)` before any comparison.
   - **Decision point — this is a trade-through control, not a tidiness fix.** Float-exact matching drops the second venue's displayed size out of the eligible set and reports it as unrouted; the caller then works that remainder at an inferior price, trading through the protected quotation it just discarded. Measured on the penny grid, 1,334 of the 10,001 prices between \$100.00 and \$200.00 are affected.

4. **Rank equally-priced venues and slice.**
   - Score = quoted price $\pm$ taker fee $\pm$ (latency $\times$ `latency_penalty_per_ms`), then `venue_id` as the final tiebreaker so the plan is reproducible regardless of input order.
   - Take the **full displayed size** at each venue before moving to the next.
   - **Decision point — `fee_aware=False` removes the fee from the *ranking key only*.** `effective_net_price`, `taker_fee_usd` and `net_expected_cost_usd` always include the taker fee, because the fee is paid whether or not the router optimized for it. A "fee-unaware" plan is not a cheaper plan.
   - **Decision point — the latency term is a sub-tick tiebreaker, not a cost model.** At the `1e-5`/ms default, 1.5 ms is worth \$0.000015/share. It can only reorder venues that are already tied on price and fee. Do not read it as a latency cost estimate.

5. **Stop at the price level; hand the remainder back with its obligation.**
   - Quantity the level cannot absorb is returned as `unrouted_quantity` with `iso_required_for_remainder=True`.
   - **Decision point — the remainder is the regulated moment, not the routed part.** Concurrent child orders all resting at the same protected price trade through nothing and are ordinary limit orders. The instant you fill the remainder at an inferior price, that execution trades through a protected quotation and must be marked an **Intermarket Sweep Order** — which under 17 CFR 242.600(b)(47) means simultaneously routing additional limit orders against the **full displayed size of every protected quotation with a superior price**. An ISO tag without those simultaneous orders is not an ISO; it is a mismarked trade-through.

6. **Bound the price.** Pass `limit_price` for any order you are not willing to fill at an arbitrary price. With `limit_price=None` the plan will route at whatever the best accessible price happens to be, including a dislocated one.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Comparing venue prices with `==`.** Two venues quoting the identical price can differ in the last float bit depending on how each feed handler built the number. Exact matching silently drops one venue's liquidity from the NBBO level and reports it unrouted — the failure mode most likely to *cause* the trade-through this skill exists to prevent.
- **Leaving `price_increment` at the default for a sub-\$1.00 stock.** Those quote in \$0.0001. At a \$0.01 increment, \$0.3401 and \$0.3405 collapse onto the same tick, so the router treats the worse price as tied with the better one and routes to both. Too coarse an increment also makes a normal book look locked.
- **Marking the remainder ISO without the simultaneous sweep orders.** The ISO tag is not a waiver you attach to an inferior-priced fill. It is a representation that you *already* routed full-displayed-size orders to every superior protected quotation. Tagging without doing that is a mismarked trade-through, not an exemption.
- **Treating a zero-size quote at a better price as permission to route past it.** It is not permission and it is not liquidity — it is almost always a stale snapshot. Re-fetch.
- **Assuming the taker fee cap is \$0.0030 indefinitely.** The 2024 Reg NMS amendments cut the Rule 610(c) cap to \$0.0010/share for NMS stocks ≥ \$1.00. The amendment was upheld on review in October 2025 and its compliance date has since been deferred; \$0.0030 is what applies today, but it is a moving number. It lives in `SmartOrderRoutingAcrossVenuesConfig.access_fee_cap_per_share`, not hard-coded. See `references/standards.md` for dates.
- **Reading `maker_rebate_per_share` as something the router uses.** It does not. This engine plans liquidity-*taking* sweeps, where the taker fee applies and the rebate does not. Rebate capture requires posting passively — `post-only-and-maker-taker-fee-optimization`.
- **Reading `net_expected_cost_usd` as a signed P&L figure.** It is always positive: cash paid on a BUY, cash received net of fees on a SELL.
- **Sending child orders sequentially.** Serial dispatch lets the market react to the first child before the rest arrive, and it breaks the simultaneity an ISO requires. Dispatch concurrently.
- **Applying any of this to options.** Rule 611 does not reach listed options; the options linkage plan governs instead.

## Verification

- Instantiate `SmartOrderRoutingAcrossVenuesEngine`. Route a 600-share BUY across NASDAQ (300 @ 150.00, taker fee 0.0030), BATS (400 @ 150.00, fee 0.0020), and NYSE (1000 @ 150.05) $\implies$ `nbbo_price == 150.00`, BATS first (400 shares, lower fee), NASDAQ second (200), nothing routed to NYSE, `iso_required_for_remainder == False`, and `net_expected_cost_usd == 90{,}001.40` ($400 \times 150.0020 + 200 \times 150.0030$).
- Route 2,000 shares against the same book $\implies$ 1,300 unrouted at the NBBO level, still no NYSE route, `iso_required_for_remainder == True`.
- Regression: two venues quoting `10007/100.0` and `10007*0.01` $\implies$ both routed to as one price level, zero unrouted.
- Safety: an unrecognized `side`, a non-positive or non-finite quantity, a `NaN` price, a negative size, or a duplicate `venue_id` $\implies$ `ValueError`.
- Run `python -m unittest discover -s skills/smart-order-routing-across-venues/scripts`.

## Related Skills

- `us-reg-nms-order-protection-rule-compliance`
- `smart-order-router-failover-on-venue-outage`
- `cross-venue-latency-arbitrage-defensive-design`
- `post-only-and-maker-taker-fee-optimization`
- `exchange-fee-tier-and-rebate-structure-analysis`
