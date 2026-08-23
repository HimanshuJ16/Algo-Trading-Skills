---
name: dark-pool-routing-logic
description: Quantitative Smart Order Router (SOR) module for routing block orders
  across dark pools / ATS, enforcing anti-pinging MinQty (FIX tag 110) thresholds,
  and filtering venues by self-measured post-trade markout toxicity.
domain: Execution Algorithms
subdomain: Smart Order Routing & ATS
tags:
- dark-pool
- ats-routing
- smart-order-router
- min-qty
- adverse-selection
- toxic-flow-filtering
- midpoint-execution
brokers_frameworks:
- FIX Protocol
- Python Dataclasses
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing institutional Smart Order Routers (SOR) or execution algorithms that slice parent block orders into non-displayed Alternative Trading Systems (ATS / dark pools). Dark pools offer midpoint executions without pre-trade market impact, but a counterparty can probe a resting block with small "pinging" orders, detect it, and trade ahead of it on lit venues. This module scores dark venues by fill rate discounted by post-trade markout toxicity, excludes venues above a toxicity ceiling, allocates the parent proportionally, and attaches a Minimum Quantity (`MinQty`, FIX tag 110) instruction to every child order.

Empirical support for the premise: dark venues with more access restrictions show measurably less order-flow information leakage and adverse selection, causally identified from exogenous venue closures (Brugler & Comerton-Forde, *Journal of Financial Economics*, 2025 — Australian venue setting; the mechanism generalises, the magnitudes do not).

## When NOT to Use

- **As a complete SOR.** This module sizes dark child orders. It does not price them (attach `ExecInst(18)='M'` or `PegPriceType(1094)=2` midpoint pegging yourself), does not sweep the lit book, and has no venue capacity or ADV model. For the lit/dark sequencing decision use `liquidity-seeking-algorithm-across-lit-and-dark-venues`.
- **With venue-published toxicity scores.** `toxicity_score_bps` must come from *your own* post-trade markouts (see `adverse-selection-measurement-for-passive-orders`). Venue-published toxicity and "liquidity profiling" metrics have been the subject of SEC enforcement for misrepresentation (SEC Press Release 2016-16, 31 Jan 2016 — Barclays LX and Credit Suisse Crossfinder, $154M combined with the NYAG).
- **Without a waiver/suspension gate in EU markets.** Under MiFIR as amended by Regulation (EU) 2024/791 a name suspended under the single volume cap (7% Union-wide, reference-price waiver only; first suspension file published 9 Oct 2025, quarterly thereafter) cannot trade dark under that waiver. Run `esma-double-volume-cap-mechanism` first; this engine has no waiver logic.
- **Before the thresholds are calibrated.** Every default here (5.0 bps ceiling, 0.05 fill-rate floor, 50.0 bps toxicity decay, 20% MinQty fraction, 200-share MinQty floor) is an engineering default, not a regulatory or venue-imposed constant.
- **For a parent smaller than the venues' minimums.** MinQty and block-size dark pools are for institutional size; a 500-share order routed at a 25,000-share minimum-quantity pool simply never trades.

## Prerequisites

- ATS venue profiles: `venue_id`, `venue_name`, `historical_fill_rate` ∈ [0, 1] measured as filled/sent by your own router, `toxicity_score_bps` (post-trade markout, signed so **higher is worse**), `min_qty_threshold` (the venue's own minimum executable quantity), `is_active`.
- Parent block order details: `symbol`, `side` (`BUY`/`SELL`), `total_quantity` — already capped to the share of the parent you intend to place in the dark, since this engine places everything it is given.
- Venue behaviour taken from primary sources: for US NMS-stock ATSs, the operator's **Form ATS-N** filing (SEC Rule 304 of Regulation ATS, public on EDGAR) is the authoritative description of order types, MinQty support, and counterparty segmentation.
- An awareness of input staleness: FINRA's ATS transparency data (Rules 6110/6610) publishes weekly volume on a **two-week delay for Tier 1 NMS stocks and four weeks otherwise**, so any venue statistics derived from it are stale by construction.

## Workflow

1. **ATS Venue Screening** — each screen records an explicit exclusion reason in the report:
   - Drop inactive venues.
   - Drop venues where $\text{Toxicity}_v > \text{MaxToxicity}$ (strictly greater — a venue exactly at the ceiling stays in). Toxicity is your measured markout, refreshed on a cadence you control; a ceiling applied to a metric refreshed monthly is a monthly control, not a real-time one.
   - Drop venues below the fill-rate floor ($\text{FillRate}_v < 0.05$ by default).
2. **Allocation Scoring**:
   - $S_v = \text{FillRate}_v \times \max\left(0.0,\ 1.0 - \frac{\max(0,\ \text{Toxicity}_v)}{\text{ToxicityDecay}}\right)$, with $\text{ToxicityDecay} = 50.0$ bps by default.
   - The $\max(0, \text{Toxicity})$ clamp matters: a *favourable* (negative) markout would otherwise push the discount above 1.0 and hand that venue nearly the whole parent.
   - A venue scoring exactly 0 is excluded rather than divided by, so an all-zero candidate set cannot raise `ZeroDivisionError`.
3. **Child Order Slicing** — decision point: a venue's proportional share can land below the size that venue will actually trade.
   - Effective minimum per venue $= \max(1,\ \text{VenueMinQty},\ \text{DefaultMinQty})$.
   - If a venue's proportional share falls below its effective minimum, **drop that venue and renormalise the weights over the survivors**, repeating until every funded venue clears its minimum. Do not send the undersized child, and do not silently shrink the routed quantity.
   - Distribute the integer truncation residual by largest fractional remainder to already-funded venues, so the child quantities sum to the parent.
   - If no venue can be funded, return zero directives with reason `PARENT_TOO_SMALL_FOR_VENUE_MIN_QTY` — the caller must handle that path.
4. **MinQty Attachment**:
   - $\text{MinQty} = \min\left(\max(\text{EffectiveMinQty}_v,\ \lceil \text{ChildQty} \times 0.20 \rceil),\ \text{ChildQty}\right)$.
   - The outer $\min$ is the invariant that matters: **MinQty must never exceed the child quantity**, or the order can never trade.
5. **Audit Report Generation**: output a structured `DarkPoolRoutingReport` carrying the child directives, every exclusion reason, and `unallocated_reason` (`NO_ELIGIBLE_VENUE` / `PARENT_TOO_SMALL_FOR_VENUE_MIN_QTY` / `VENUE_CONCENTRATION_CAP` / empty).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **MinQty above the child quantity**: computing MinQty from an engine-wide floor (e.g. 200) while gating eligibility on the venue's own smaller minimum (e.g. 100) produces a 150-share child order carrying `MinQty=200`. On venues that publish their handling, each execution must be at least MinQty (IEX Minimum Execution Size), so the order rests unfilled or is rejected — an execution failure that looks like "no dark liquidity".
- **Omission of MinQty Constraints**: routing dark IOC orders with no `MinQty`, letting a counterparty ping one lot, infer the hidden block, and trade ahead of it on lit venues.
- **Trusting venue-published toxicity**: scoring venues on the operator's own "toxicity"/liquidity-profiling metric rather than your own markouts — precisely the representation the SEC charged as false in the 2016 Barclays LX and Credit Suisse Crossfinder settlements. Read Form ATS-N for mechanics; measure toxicity yourself.
- **Silent quantity leakage**: truncating each venue's share with `int()` and dropping under-minimum venues without renormalising, so a 10,000-share parent quietly routes 6,000 shares. Reconcile `allocated + unallocated == parent` on every report and treat a non-empty `unallocated_reason` as an event, not noise.
- **Unvalidated venue statistics**: a `historical_fill_rate` above 1.0 or a negative `toxicity_score_bps` from a broken markout job silently re-weights the whole allocation toward the corrupted venue. Validate at ingest.
- **Stale toxicity inputs**: FINRA ATS volume is published two to four weeks late, and internal markouts are typically batch-computed. A venue that turned toxic this morning is still scoring well this afternoon.
- **Over-allocating to Low-Liquidity Pools**: sending large child quantities to illiquid dark pools, causing high cancellation ratios and opportunity cost. This engine's `max_venue_allocation_pct` is `None` (no cap) by default — set it explicitly; concentration is not controlled unless you ask for it.
- **Assuming a sub-penny problem**: Reg NMS Rule 612 constrains quotations and orders, not execution prices, so a dark midpoint print in a half-cent is not a Rule 612 issue. (The 2024 amendments adding a $0.005 quoting increment remain under extended exemptive relief until the first business day of November 2027.)

## Verification

- Register three placeholder venues — A (FillRate 0.40, Toxicity 1.0 bps), B (0.50, 8.0 bps), C (0.30, 0.5 bps), all `min_qty_threshold=100` — and route 10,000 shares with `MaxToxicity=5.0` bps. Independently derived scores: $0.40 \times (1 - 1/50) = 0.392$ and $0.30 \times (1 - 0.5/50) = 0.297$; B is excluded as toxic. Expect A = 5,689 and C = 4,311 shares (the residual share goes to the larger fractional remainder), summing to exactly 10,000.
- **MinQty regression**: two identical venues with `min_qty_threshold=100`, engine `default_min_qty=200`, parent 300 shares. Expect **one** directive of 300 shares with `MinQty=200` — not two 150-share children carrying an unsatisfiable `MinQty=200`.
- **Conservation**: three identical venues, parent 10,000 → child quantities 3,333 / 3,333 / 3,334, `unallocated_quantity == 0`.
- **Clamp**: a venue with `toxicity_score_bps=-500` must not out-score a clean venue with a higher fill rate.
- Run `python -m unittest discover -s skills/dark-pool-routing-logic/scripts`.

## Related Skills

- `adverse-selection-measurement-for-passive-orders`
- `liquidity-seeking-algorithm-across-lit-and-dark-venues`
- `esma-double-volume-cap-mechanism`
- `minimum-fill-size-and-lot-rounding-logic`
- `post-trade-execution-quality-scorecard`
- `smart-order-router-failover-on-venue-outage`
