---
name: liquidity-seeking-algorithm-across-lit-and-dark-venues
description: >-
  Lit/dark sequencing planner for institutional parent orders in US NMS stocks — sweeps dark ATS venues at the NBBO midpoint with IOC + MinQty before sweeping lit exchanges in strict price priority, flagging any child priced inferior to the protected NBBO as requiring ISO marking.
domain: Execution Algorithms
subdomain: Smart Order Routing (SOR) & Dark Pools
tags: ["liquidity-seeking", "dark-pools", "lit-venues", "nbbo-midpoint", "sor", "smart-order-router", "signal-leakage", "trade-through", "iso"]
brokers_frameworks: ["SEC Reg NMS Rule 611 (Order Protection)", "SEC Reg NMS Rule 612 (Minimum Pricing Increment)", "SEC Reg NMS Rule 610(e) (Locked/Crossed)", "FIX Protocol", "Python Dataclasses"]
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deciding **where and in what order** a large institutional parent order in US NMS stocks is worked across fragmented markets: displayed **Lit Exchanges** (NASDAQ, NYSE, Cboe) and non-displayed **Dark Venues (ATS)**. Routing size straight to the lit book signals intent and pays the spread; this module sweeps non-displayed liquidity at the **NBBO midpoint** with IOC + `MinQty` first, then sweeps the residual across lit exchanges in strict price priority.

The scope is the *sequencing* decision and the audit record it produces. Dark-side sizing (venue toxicity scoring, per-venue MinQty calibration) belongs to `dark-pool-routing-logic`; lit-side fee and rebate ranking belongs to `smart-order-routing-across-venues`.

## When NOT to Use

- **As a live order router.** This is a **pre-trade planner**. `historical_fill_rate` is applied as a deterministic expected-fill model (`FILL_MODEL_ID = "DETERMINISTIC_EXPECTED_FILL"`), so every quantity on the report is a *projection*, not a broker-reported fill. There is no venue connectivity, no order-state machine, no timeout or retry handling, and no idempotency key — see `order-placement-idempotency` before wiring any of this to a broker.
- **As a compliance determination.** Rule 611 binds "trading centers" (Rule 600(b): exchanges, ATSs, market makers, and broker-dealers that execute internally). A router that only sends orders elsewhere is not itself a trading center, though FINRA Rule 5310(a)(1) best execution applies to it regardless. `requires_iso_marking` is an engineering flag telling you a child *must* be marked and routed as an ISO; it is not evidence that it was. Audit with `us-reg-nms-order-protection-rule-compliance`.
- **Outside US NMS stocks.** Everything here — the NBBO, midpoint reference, trade-through logic, sub-penny handling — is US equity market structure. For EU venues, dark execution under the reference-price waiver additionally requires a single-volume-cap check (`esma-double-volume-cap-mechanism`), which this engine does not perform.
- **When you need a real NBBO.** The engine derives a *synthetic* NBBO from the venue books you hand it. The official NBBO is disseminated by the SIP under the CTA/UTP plans, and protected-quote status also depends on the quotation being automated and immediately accessible — neither is modelled.
- **When the residual is small.** `min_dark_fill_qty` (default 500 shares) blocks the dark stage once the routed quantity falls below the floor. That is deliberate; a 300-share ping into a deep pool leaks the parent's intent for negligible benefit. No US rule sets a minimum dark order size — the 500 is an engineering default, and venue minimums range from none to 25,000+ share block thresholds.

## Prerequisites

- Parent order payload (`symbol`, `side`: `BUY`/`SELL`, `target_quantity`, `limit_price`, `min_dark_fill_qty`). The limit price binds on **every** stage, dark midpoint included.
- Venue books (`venue_id`, `venue_type`: `DARK`/`LIT`, `bid_price`, `ask_price`, `bid_qty`, `ask_qty`, `historical_fill_rate`). One price level per venue; `historical_fill_rate` must be your own measured filled/sent ratio, in $[0, 1]$.
- Downstream ability to send FIX midpoint pegs (`ExecInst(18)='M'` / `PegPriceType(1094)=2`), `MinQty(110)`, and ISO marking (`ExecInst(18)='f'`) — the engine emits the instructions, it does not transmit them.

## Workflow

1. **Synthetic NBBO & midpoint** — decision point: reject before pricing, don't repair.
   - $P_{\text{NBB}} = \max$ bid and $P_{\text{NBO}} = \min$ ask over lit venues **quoting size**. A zero-size quote is not a quotation and must not set the touch.
   - $P_{\text{mid}} = (P_{\text{NBB}} + P_{\text{NBO}}) / 2$.
   - If the book is **crossed** ($P_{\text{NBB}} > P_{\text{NBO}}$), raise. The midpoint of a crossed book is not a reference price; a crossed consolidated book is a market-data integrity failure, not a trading opportunity (cf. 17 CFR 242.610(e)). If it is **locked**, warn — the midpoint equals both touches and the dark stage buys nothing.
2. **Stage 1 — dark ATS midpoint sweep**, dark venues tried highest historical fill rate first:
   - **Gate on the parent limit first.** If the limit does not permit the midpoint, skip the whole stage with reason `LIMIT_PRICE_THROUGH_MIDPOINT` — on *both* sides. A SELL whose limit sits above the midpoint must not fill at the midpoint.
   - Route $Q_{\text{route}} = \min(Q_{\text{rem}}, \text{venue liquidity})$, but only if $Q_{\text{route}} \ge Q_{\text{min\_dark}}$. Gate on the **routed quantity**, not on the venue's available liquidity — a deep pool does not make a 300-share ping safe.
   - Project the fill as $\lfloor Q_{\text{route}} \times \text{FillRate} \rfloor$, capped at $Q_{\text{rem}}$. If the projection is below $Q_{\text{min\_dark}}$, treat it as **no fill**: IOC + `MinQty(110)` either executes at least MinQty or does not execute.
   - Emit the child as a **midpoint peg**, never as an explicit limit price (see the sub-penny pitfall below), with $\text{MinQty} = \min(Q_{\text{min\_dark}}, Q_{\text{route}})$.
3. **Stage 2 — lit sweep in strict price priority**:
   - Sort by price (ascending ask for a BUY, descending bid for a SELL) — **not** by the order the venues arrived in. This is what keeps the sweep off a trade-through, and it makes the ISO precondition hold structurally: an inferior venue is only reached once the full displayed size of every better-priced protected quotation has been taken.
   - Skip venues whose price breaches the parent limit; never reprice to reach them.
   - Flag any child priced inferior to the protected NBBO with `requires_iso_marking` — that child must carry `ExecInst(18)='f'` and satisfy Rule 611(b)(5)-(6) simultaneous routing.
4. **Audit report** — output `LiquiditySeekingReport`, holding `total_executed_qty + unfilled_qty == target_quantity` as an invariant and carrying `nbbo_bid`/`nbbo_ask`, `fill_model`, `requires_iso_marking`, and every `dark_skip_reasons` entry.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Sweeping lit venues in list order**: iterating the venue array as given executes against whichever venue happens to be first. With NASDAQ offering at \$100.02 and a wide venue at \$100.05, taking the wide venue first is a trade-through of a protected quotation under 17 CFR 242.611(a) — and it is invisible in the resulting fills, because every child looks like it was inside its own limit. Sort by price.
- **Marking ISO without meeting its conditions**: `ExecInst(18)='f'` is not a label that makes an inferior print legal. The Rule 611(b)(5)-(6) exception requires simultaneous routing against the **full displayed size** of every better-priced protected quotation. If your sweep took only part of the better venue's size, the exception is not available.
- **Guarding the limit price on one side only**: a check like `if side == "BUY" and limit < midpoint` leaves the SELL side unguarded, and the dark stage then fills at the midpoint *below* the client's limit. Every price gate in an SOR has two sides.
- **Sending the midpoint as a limit price**: a one-cent spread midpoints to a half cent. Rule 612 bars display/rank/**accept** of sub-penny-priced orders; it does *not* bar sub-penny executions — the Reg NMS Adopting Release permits a sub-penny midpoint execution "so long as the execution did not result from an impermissible sub-penny order or quotation". Peg the child (`ExecInst(18)='M'` / `PegPriceType(1094)=2`); do not transmit \$100.005 as a limit.
- **Pinging without minimum quantity limits**: sending a small residual ping to a dark pool exposes institutional intent to latency arbitrageurs. Gating on the *venue's* liquidity instead of the *routed* quantity looks like the same control and is not — the venue is deep, the ping is still 300 shares.
- **Trusting an unvalidated fill rate**: a `historical_fill_rate` above 1.0 from a broken markout job projects a fill larger than the quantity routed, drives the residual negative, and reports the parent as over-filled. Validate at ingest, and reconcile `executed + unfilled == requested` on every report.
- **Defaulting an unrecognised side**: `if side == "BUY": ... else: ...` silently treats `"B"`, `"Buy "`, or an empty string as a SELL and hits the bid on what was meant as a purchase. Reject unknown sides.
- **Pricing off a crossed book**: during a data glitch or a fast market the consolidated bid can exceed the offer. The "midpoint" is then outside both touches and the dark stage prices against a fiction. Reject and re-snapshot.
- **Reading projections as fills**: `dark_executed_qty` is a model output, not a broker report. Ignoring dark pool fill-rate decay — continuing to allocate to a venue whose measured fill rate has collapsed — is invisible if you never reconcile the projection against realised executions.

## Verification

All figures below are derived independently of the implementation and are asserted in the test suite.

- **Two-stage sweep**: BUY 20,000 @ \$100.05 limit across NASDAQ/NYSE (both \$100.00 × \$100.02, 5,000 × 5,000) and two dark ATSs (Alpha 15,000 @ 80% fill rate, Beta 5,000 @ 50%). Midpoint \$100.01. Alpha projects $\lfloor 15{,}000 \times 0.8 \rfloor = 12{,}000$; Beta projects $\lfloor 5{,}000 \times 0.5 \rfloor = 2{,}500$; residual 5,500 sweeps lit (NASDAQ 5,000 + NYSE 500) at \$100.02. Expect dark 14,500 / lit 5,500 / unfilled 0, improvement $14{,}500 \times \$0.01 = \$145.00$, VWAP $(14{,}500 \times 100.01 + 5{,}500 \times 100.02)/20{,}000 = \$100.01275$, and `LIQUIDITY_SEEKING_COMPLETE`.
- **Trade-through regression**: BUY 6,000 with a wide venue (\$100.05, 5,000) listed **before** NASDAQ (\$100.02, 5,000). Expect the first child at NASDAQ for the full 5,000 displayed, the second at the wide venue for 1,000 with `requires_iso_marking=True`, and a signed improvement of $1{,}000 \times (\$100.02 - \$100.05) = -\$30.00$.
- **Limit-price regression**: SELL 10,000 with a \$100.02 limit against a \$100.00 × \$100.02 NBBO. Expect **zero** dark fills, `LIMIT_PRICE_THROUGH_MIDPOINT` in `dark_skip_reasons`, and `INSUFFICIENT_LIQUIDITY` — not an 8,000-share fill at the \$100.01 midpoint.
- **Anti-pinging regression**: a 300-share residual against a 5,000-share-deep pool with a 500-share floor must produce no dark child and a `PING_BELOW_MIN_DARK_FILL_QTY` skip reason.
- **Sub-penny**: a \$100.00 × \$100.01 NBBO gives a \$100.005 midpoint; the dark child must carry `price_instruction="MIDPOINT_PEG"`.
- **NBBO integrity**: a zero-size lit quote at \$100.00 must not set the NBO (expect \$100.02, midpoint \$100.01); a crossed book must raise `LiquiditySeekingError`.
- **Conservation**: across BUY/SELL and over/under-sized parents, `total_executed_qty + unfilled_qty == total_requested_qty` and the child fills sum to the total.
- Run `python -m unittest discover -s skills/liquidity-seeking-algorithm-across-lit-and-dark-venues/scripts`.

## Related Skills

- `dark-pool-routing-logic`
- `smart-order-routing-across-venues`
- `us-reg-nms-order-protection-rule-compliance`
- `smart-order-router-failover-on-venue-outage`
- `cross-venue-latency-arbitrage-defensive-design`
- `minimum-fill-size-and-lot-rounding-logic`
- `adverse-selection-measurement-for-passive-orders`
- `post-trade-execution-quality-scorecard`
