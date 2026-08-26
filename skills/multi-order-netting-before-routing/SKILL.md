---
name: multi-order-netting-before-routing
description: >-
  Use when several strategies, sub-accounts or desks generate opposing orders in
  the same symbol inside one batching window and the firm wants to cross them
  internally at the mid before routing a single net residual — covering
  beneficial-ownership classification (book transfer vs reportable cross),
  pro-rata fill allocation, limit-price eligibility, stale and crossed quote
  guards, and a cost-saving estimate that subtracts the fees internalisation
  does not avoid.
domain: Execution Algorithms
subdomain: Pre-Routing Internal Order Netting & Cost Optimization
tags:
- multi-order-netting
- pre-routing
- internal-crossing
- midpoint-cross
- beneficial-ownership
- pro-rata-allocation
- order-routing
- wash-trade-prevention
brokers_frameworks:
- FINRA Rule 5210 and Supplementary Material .02 (self-trades)
- FINRA Rule 6380A (OTC transaction reporting, 10 seconds)
- FINRA Rule 5310 and Supplementary Material .09 (best execution)
- SEC Regulation NMS Rules 611 and 612 (17 CFR 242.611, 242.612)
- Section 31 regulatory transaction fee / FINRA Trading Activity Fee
- Advisers Act Section 206(3) and Rule 206(3)-2 (agency cross)
- MiFIR Article 4(1)(a) reference price waiver / Article 5 volume cap
- Smart Order Routing (SOR) / internal crossing engine
- Python Dataclasses + decimal
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill at the point where a batch of internal orders has been assembled and is about to be handed to a router, when that batch contains **opposing** interest in the same symbol. Two strategies that both want AAPL — one buying 500, one selling 300 — will, if routed raw, pay the venue's access fee twice and cross the spread twice to trade 300 shares against each other through the public book. Netting first replaces that with a mid-price internal match on 300 shares and a single external order for the 200-share residual.

It answers four questions, and the arithmetic is the easiest of them:

- **How much crosses internally?** The minimum of the *eligible* buy and sell quantity — not of the submitted quantity, because an order whose limit is not marketable at the mid is not eligible.
- **Who gets the mid-price fill?** Whoever the allocation policy says, and that choice is a transfer of money between books, not a formatting detail.
- **Is the cross a trade at all?** Only if it moves stock between beneficial owners. Inside one owner it is a book transfer; across owners it is an execution with reporting, best-execution and fee consequences.
- **What did it actually save?** The avoided access fee and spread, less the costs that stay attached to the internalised print.

Typical callers: a multi-strategy book with a batching interval, a portfolio-rebalance engine fanning trades across sub-accounts, a desk consolidating child orders before a SOR.

## When NOT to Use

- **As the only self-match control.** Netting covers the orders present in *this* batch, in *this* symbol. Orders already resting at a venue, orders in a later batch, and orders on a second venue are outside it — that is native exchange Self-Match Prevention's job (`exchange-self-match-prevention-configuration`) and, after the fact, `wash-trade-and-spoofing-self-detection`.
- **To decide whether an internal cross is *permitted*.** The engine classifies ownership and tells you a print may be owed; it does not know your registration status, your jurisdiction, or your clients' consents. Crossing between two advisory clients is a regulated act — see the regulatory notes below and `best-execution-record-keeping-global`.
- **As a trade-reporting or booking system.** `requires_execution_report` is a flag, not a report. Nothing here talks to a TRF, a clearing system or a position keeper.
- **For fractional or continuously-divisible quantities.** Quantities are whole shares/contracts. A crypto venue's step size belongs to `minimum-fill-size-and-lot-rounding-logic`.
- **For multi-symbol or multi-leg baskets.** One batch, one symbol, one quote. An order in another symbol raises rather than being netted. Leg-level atomicity belongs to `calendar-spread-and-multi-leg-order-atomicity`.
- **As a risk or exposure check.** Netting reduces the routed quantity; it does not decide whether the resulting position is acceptable.

## Prerequisites

- **Internal order batch** — per order: `order_id` (unique within the batch), `strategy_id`, `symbol`, `side` (`BUY`/`SELL`), integer `quantity`, optional `limit_price`, optional `beneficial_owner_id`.
- **`beneficial_owner_id` wherever it is knowable.** Without it the engine cannot tell a book transfer from a reportable cross and fails safe by assuming the latter.
- **Market quote** — `symbol`, `bid_price`, `ask_price`, `fee_per_share_usd` (the venue access/taker fee crossing avoids), and `as_of` so the quote can be aged. Without `as_of` the report carries `QUOTE_AGE_UNVERIFIED`; the engine will not pretend an undated quote is fresh.
- **`retained_internalization_cost_per_share_usd`** where the cross may be reportable — the per-matched-share cost that survives internalisation. `references/standards.md` shows the conversion from the published Section 31 and TAF rates.
- Prices as `str`, `int`, or `Decimal`. `float` is accepted and converted via `Decimal(str(value))`, which recovers the decimal literal but not precision already lost upstream.

## Workflow

1. **Validate the batch before any arithmetic — and fail, do not filter.**
   - Reject an order whose `symbol` differs from the quote's, a repeated `order_id`, an unrecognised `side`, and a non-positive or non-integer quantity.
   - **Decision point — a side filter is not validation.** Selecting `side == 'BUY'` and `side == 'SELL'` into two lists sends anything else (`'SHORT'`, `'buy_to_cover'`, an empty string) to neither list. The order is then not crossed, not routed, and not reported anywhere: it is simply gone, and the batch totals still look consistent. Raise on it.
   - **Decision point — a duplicated `order_id` is a replay, not extra size.** A retried batch that arrives twice doubles the quantity on one side and produces a residual order for stock no strategy asked to trade.

2. **Guard the reference price, and refuse to cross rather than cross badly.**
   - Age the quote against `max_quote_age_seconds`. On a stale quote the engine crosses nothing, emits no residual, and returns every order to the caller (`NETTING_SKIPPED_STALE_QUOTE`).
   - **Decision point — do not emit a net residual from a quote you have just rejected.** The netting decision and the residual size both came from that quote; keeping the residual while discarding the cross commits the batch to a position sized off data the engine declared untrustworthy.
   - **Decision point — a crossed book (bid > ask) is not a bargain.** It is a dislocated or corrupt quote. Its "spread saving" is negative and its mid is not a defensible fill price. Skip. A *locked* book (bid == ask) is legitimate: cross at the touch and record a zero spread saving.

3. **Compute the mid — and leave it alone.**
   - $P_{\text{mid}} = (P_{\text{bid}} + P_{\text{ask}}) / 2$, in exact decimal arithmetic.
   - **Decision point — do not round the mid to a whole penny.** A one-cent spread has a half-cent mid; rounding it hands the side it moves toward a systematic half-cent per share on every cross. SEC Rule 612 restricts the increments in which orders and quotations may be *displayed, ranked or accepted* — it does not restrict the price at which an execution may occur, and the report flags a sub-penny mid (`SUB_PENNY_INTERNAL_MATCH_PRICE`) so the *order-entry* side of that rule gets checked separately.

4. **Determine eligibility from each order's own limit price.**
   - A buy crosses only where $P_{\text{limit}} \ge P_{\text{mid}}$; a sell only where $P_{\text{limit}} \le P_{\text{mid}}$. Anything else is returned in `excluded_orders` with `LIMIT_PRICE_NOT_MARKETABLE_AT_MID`.
   - **Decision point — an ineligible order leaves the netting entirely, not just the cross.** If a buy limited at $149.00 cannot cross at $150.05, it also cannot be netted against an opposing sell: netting it would remove a sell that must still reach the market. The excluded order goes back to the caller intact.

5. **Allocate the matched quantity, deliberately.**
   - $Q_{\text{matched}} = \min(Q_{\text{buy}}^{\text{eligible}}, Q_{\text{sell}}^{\text{eligible}})$, split across each side by the configured policy.
   - **Decision point — arrival order is not a neutral default.** Filling in batch order gives whichever strategy happens to be first the mid-price fill and leaves the rest to cross the spread at the venue. Over a day that is a steady, invisible transfer between books. `PRO_RATA` (floor plus largest remainder, ties broken by `order_id`) allocates exactly the matched quantity and is the default; `TIME_PRIORITY` remains available where that priority is a disclosed policy rather than an accident of list order.

6. **Classify the cross by beneficial ownership before treating it as an execution.**
   - All matched participants under one `beneficial_owner_id` → `SAME_BENEFICIAL_OWNER_TRANSFER`. Two or more → `REPORTABLE_CROSS`. Any unknown → `BENEFICIAL_OWNERSHIP_UNCLASSIFIED`, treated as reportable.
   - **Decision point — a book transfer is not an execution to report.** Moving stock between two strategy books of the same owner changes no beneficial ownership and touched no market. Book it internally if your P&L attribution needs it; do not manufacture a transaction record for it. (Read FINRA Rule 5210 Supplementary Material .02 carefully in this direction: it treats *unintentional* self-trades as generally bona fide and requires controls against a **pattern or practice** of them arising from related algorithms or desks — pre-routing netting is one of those controls.)
   - **Decision point — a cross between owners is a real execution and starts a clock.** For a FINRA member in an NMS stock it must reach a Trade Reporting Facility "as soon as practicable, but no later than 10 seconds after execution" (Rule 6380A). The netting engine is not that path; wire one.

7. **Size and price the single residual order.**
   - Residual = eligible dominant-side quantity less matched. Only one side can carry it.
   - **Decision point — the residual inherits its contributors' limits.** Where residual quantity comes from limit orders, the external order is a LIMIT priced at the most conservative contributor limit (lowest for a buy, highest for a sell), so nobody is filled through their own price. A `MARKET` residual is emitted only when no contributor constrained the price, and it is flagged (`RESIDUAL_ROUTED_AS_MARKET_ORDER`) because unpriced residual size is how a netting engine turns a spread saving into a slippage loss.
   - **Decision point — a residual bunching several owners needs an allocation policy.** `RESIDUAL_BUNCHES_MULTIPLE_ACCOUNTS` says the external fill will have to be split back across accounts; that split must follow a written, pre-disclosed policy, not the order the fills happen to arrive in.

8. **Read the savings as an estimate with a stated counterfactual.**
   - Gross fee saving $= 2 \cdot Q_{\text{matched}} \cdot \text{fee}$; spread saving $= Q_{\text{matched}} \cdot \text{spread}$ (each side saves half the spread). Net fee saving subtracts the retained per-share cost on a reportable cross.
   - **Decision point — the number assumes both sides would have crossed the spread.** For orders that would have rested passively the saving is overstated, and on a maker-rebate venue crossing internally *forgoes* the rebate. Where the retained cost is not supplied on a reportable cross, the report says `INTERNALIZATION_COST_UNMODELLED` rather than quietly reporting the gross figure as net.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring the limit price on an order you are crossing.** A buy limited at $149.00 crossed at a $150.05 mid is filled $1.05 through its own limit by the firm's own engine. The limit field existing on the order object is not the same as the engine reading it.
- **Folding a limit order into a market residual.** The netting arithmetic is indifferent to price; the router is not. Aggregating priced and unpriced interest into one `MARKET` order silently discards every limit in the batch.
- **Filtering unknown sides instead of rejecting them.** `[o for o in orders if o.side == 'BUY']` plus `[... == 'SELL']` drops `'SHORT'` into a gap where the order is neither netted nor routed, and no total looks wrong afterwards.
- **Netting across symbols because the symbol came from the quote.** Taking `symbol = quote.symbol` and never checking each order leaves a stray MSFT order netted against AAPL — creating an unintended position in both names.
- **Double-counting a replayed batch.** Without a duplicate `order_id` check, a retried submission inflates one side and routes a residual nobody ordered.
- **Allocating internal fills in list order and calling it fair.** The strategies at the front of the batch get the mid; the rest pay the spread at the venue. Pro-rata with an explicit remainder rule, or a disclosed priority policy — not whichever the loop happened to do.
- **Crossing at a stale mid.** A mid computed from a quote the market has moved away from can sit outside the current NBBO. A broker-dealer executing orders internally is a "trading center" under Regulation NMS and must have policies reasonably designed to prevent trade-throughs of protected quotations (Rule 611); a stale reference price is exactly how an internal cross becomes one.
- **Treating a crossed quote as a wide spread.** `ask - bid` going negative turns the spread saving negative and the audit trail nonsensical; the correct response is not to cross.
- **Rounding the mid to a penny.** Half a cent on every share of every cross, always in the same direction, is a persistent transfer between the two sides.
- **Manufacturing a print for an intra-owner netting.** Two strategy books under one beneficial owner produce no change of ownership and no market execution — there is nothing to report, and a fabricated transaction record engages Rule 5210's requirement that a member believe any transaction it reports was a bona fide purchase or sale. The netting is the *control* that keeps those orders from meeting in a matching engine, not a trade in its own right.
- **Assuming an inter-owner cross is fee-free because it never touched an exchange.** A reportable cross still carries the Section 31 regulatory transaction fee, the FINRA Trading Activity Fee and TRF/clearing charges. Counting only the avoided access fee overstates the saving — in the worked example in `references/standards.md`, by roughly half.
- **Assuming internalisation satisfies best execution because it saved a fee.** A member that internalises customer order flow must either review order-by-order or conduct regular and rigorous reviews comparing its internalisation against competing markets (FINRA Rule 5310.09). A mid-price cross is usually favourable — "usually" is not the standard, and the review is not optional.
- **Crossing two advisory clients without checking the consent regime.** Advisers Act Rule 206(3)-2 does not cover a transaction the adviser recommended to both sides — which is precisely the netting case — so the blanket agency-cross consent many firms rely on is unavailable here.

## Verification

- Bid $150.00 / Ask $150.10; Buy 500 (`FUND_A`), Sell 300 (`FUND_B`), Buy 200 limited at $150.02 (`FUND_A`). Expect: mid $150.05; the $150.02 buy excluded as not marketable; 300 matched; residual Buy 200; `cross_type == REPORTABLE_CROSS`; `requires_execution_report` true; gross fee saving $1.80, spread saving $30.00.
- Same quote, Buy 500 and Sell 200 both under `FUND_A`: expect `SAME_BENEFICIAL_OWNER_TRANSFER`, `requires_execution_report` false, and zero retained cost even when a retained rate is supplied.
- Bid $150.00 / Ask $150.01: expect an internal match price of exactly `Decimal('150.005')` and a `SUB_PENNY_INTERNAL_MATCH_PRICE` warning — not $150.00 or $150.01.
- A quote older than `max_quote_age_seconds`: expect `NETTING_SKIPPED_STALE_QUOTE`, no internal fills, no external order, and every order returned in `excluded_orders`.
- Run `python -m unittest discover -s skills/multi-order-netting-before-routing/scripts`.

## Related Skills

- `exchange-self-match-prevention-configuration`
- `wash-trade-and-spoofing-self-detection`
- `smart-order-routing-across-venues`
- `smart-order-router-failover-on-venue-outage`
- `minimum-fill-size-and-lot-rounding-logic`
- `cross-account-aggregate-risk-view`
- `best-execution-record-keeping-global`
- `esma-double-volume-cap-mechanism`
