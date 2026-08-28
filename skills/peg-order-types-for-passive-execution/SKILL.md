---
name: peg-order-types-for-passive-execution
description: >-
  Pegged limit-price engine for Primary, Midpoint and Market pegs against the NBBO, applying side-relative offsets and then clamping to every protective bound — passivity, LULD band, Reg SHO Rule 201 floor and limit cap — before quantizing to the minimum price variation.
domain: Algorithmic Execution & Order Routing
subdomain: Passive Liquidity Provision & Pegged Order Routing
tags: ["pegged-orders", "primary-peg", "midpoint-peg", "market-peg", "passive-execution", "nbbo", "tick-size", "reg-nms"]
brokers_frameworks: ["FIX 4.4 ExecInst(18) / FIX 5.0 PegInstructions", "Nasdaq Equity 4 Rule 4703(d)", "Python Decimal", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a passive strategy needs its resting limit price to track the NBBO automatically instead of being recalculated and resubmitted by hand on every quote change. Manual repricing in a moving market costs message budget, queue position and latency; a peg pushes the tracking into the venue's own price logic.

The engine resolves the three NBBO-referenced peg types — **Primary** (same side of the market), **Midpoint** (between the inside bid and offer), **Market** (opposite side) — applies a discretionary offset, and then produces a price you can actually submit: bounded, tick-aligned, and annotated with which constraint produced it.

Its second job is **refusing to price when pricing would be wrong**. A crossed or NaN quote does not raise and does not fall back to a default; it returns a report with `effective_limit_price = None` and a suspension reason, because a pegged order priced off bad market data is worse than no order at all.

## When NOT to Use

- **As a substitute for venue-native pegging.** If the venue supports the peg natively, send `OrdType=P` with a `PegInstructions` block and let the matching engine track the quote at its own latency. This module is for venues without native support, for pre-trade validation of a peg you are about to send, and for backtest/replay parity with a live peg.
- **For non-NBBO peg references.** Last-sale, opening, VWAP, trailing-stop and peg-to-limit references (FIX `PegPriceType` 1, 3, 7, 8, 9) are not implemented. They need a trade tape or a schedule, not a top-of-book quote.
- **For a market outside US NMS equities.** The Rule 612 tick lattice, the Rule 201 short-sale floor and the LULD bands are US equity constructs. Peg mechanics elsewhere (crypto perpetuals, LSE, futures) use different increments and no equivalent price test — reusing these defaults there produces confidently wrong prices.
- **As the order manager.** The engine computes a price and a reprice decision. Submission, cancel/replace sequencing, client order IDs and duplicate-fill protection live in `order-placement-idempotency`.
- **As an LULD or Rule 201 state source.** Bands and the short-sale restriction flag are *inputs*. This module clamps to what you give it; it does not derive bands or detect the circuit-breaker trigger.
- **On a co-located latency-critical path.** `Decimal` is chosen for exactness, not speed. At tick-to-trade latencies that matter, peg natively at the venue.

## Prerequisites

- A **consolidated NBBO** for the instrument being pegged, with `symbol` matching the order's. The engine refuses to peg an order to another instrument's quote rather than trusting the caller.
- The instrument's **minimum price variation**. Under Rule 612 that is `$0.01` for NMS stocks priced at or above `$1.00` and `$0.0001` below `$1.00`; the engine defaults to `$0.01` and sub-dollar names must set `tick_size` explicitly on the quote.
- Optional but strongly recommended: current **LULD price bands**, and the **Rule 201 short-sale restriction** flag for any short sale.
- A configured **logging handler**. The module attaches a `NullHandler`, so suspension warnings are silent until the host application configures logging.
- Python 3.9+. Standard library only.

## Workflow

1. **Validate the specification before the market state.** `PegOrder`, `NBBOQuote` and `PegPricingConfig` validate on construction and raise `PegSpecError`.
   - **Decision point — an unrecognised side is an error, not a default.** A `side` of `"B"` or `"Buy "` must never fall through to the sell branch; the resulting order is priced on the wrong side of the book with an inverted offset and an inverted cap. Reject it.

2. **Classify the market state, and return a report rather than raising.** A bad tick is an operational event, not a programming error — a replay or a live loop must be able to log it and continue.
   - **Decision point — locked is not crossed.** A locked book (`bid == ask`) is legal; a midpoint peg there is well defined and equals the locking price. A crossed consolidated quote (`bid > ask`) indicates stale or bad data, and the engine suspends. `NaN` is the trap worth naming: `nan <= 0` is `False` and `nan >= nan` is `False`, so a naive validity check passes NaN straight through into a NaN limit price.

3. **Resolve the reference price** from the peg type and the side: Primary → same-side inside quote, Market → opposite-side inside quote, Midpoint → `(bid + ask) / 2`.

4. **Apply the offset, side-relative and aggressive-positive.** A positive offset moves a BUY up and a SELL down.
   - **Decision point — this is not the FIX convention.** `PegOffsetValue(211)` is a *signed* amount added to the peg regardless of side, so a passive sell offset is negative in FIX and positive here. Negate the offset for SELL orders when translating into a `PegInstructions` block, or every sell peg goes out on the wrong side of its reference.

5. **Clamp to the tightest protective bound.** For a BUY the passivity limit, the LULD upper band and the limit cap are all *ceilings*; for a SELL the passivity limit, the Rule 201 floor, the LULD lower band and the limit cap are all *floors*. Two bounds can therefore never contradict each other — the tightest wins and a clamp always makes the order less aggressive.
   - **Decision point — a Market peg is not passive by construction.** It references the *contra* quote, so with no offset it prices at the touch and takes liquidity on arrival. `enforce_non_marketable` (default on) clamps it back inside the spread and records a `PASSIVITY` clamp. Turning it off is a deliberate choice to pay the taker fee.
   - **Decision point — a missing `limit_cap` is the only unbounded path.** Passivity bounds a peg to the spread, but if you disable it and supply no cap, a Primary peg follows a runaway quote as far as it goes.

6. **Quantize to the price lattice, last.** Passive rounding (FIX `PegRoundDirection=2`) floors a buy and ceils a sell, so it can never cross a bound. Aggressive rounding (`=1`) can, so bounds are re-applied afterwards on the lattice.
   - **Decision point — the non-displayed Midpoint peg is the sub-penny exception.** Rule 612 bars displaying, ranking or accepting a sub-penny order, but a non-displayed midpoint peg may price in sub-pennies to reach the midpoint of a one-tick spread. Set `is_displayed=False` and the price lands on the half-tick lattice; leave it `True` and a `$100.005` midpoint is pushed back to `$100.00`.

7. **Gate the replace on `should_reprice`.** A pegged order that chases every quote flicker burns the venue message budget and forfeits queue position on every cancel/replace. `should_reprice` authorises a replace only once the price has moved `reprice_threshold_ticks` full ticks, and never on a suspended report.

8. **Read the report as an audit record.** `status` is `PRICED`, `PRICED_CLAMPED` or `SUSPENDED`; `clamps` lists every bound that cut the order and `binding_constraint` names the one that set the final price, with regulatory bounds reported ahead of house limits on a tie.

> Full procedure: see `references/workflows.md`.
> Standards and citations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating a Market peg as passive.** A Market peg with no offset prices at the contra touch. Submitted post-only it is rejected; submitted plain it lifts the offer and pays the taker fee — in a strategy whose entire economics assume a maker rebate.
- **Pegging without a limit cap.** With passivity enforcement off and no `limit_cap`, a Primary peg buy follows the bid through a news spike with nothing to stop it. The cap is the only bound that survives every other setting.
- **NaN and crossed quotes producing a price.** `nan <= 0` is `False`, so a NaN bid passes a naive validity check and yields a NaN limit price that a broker API may serialise as `null` or `0`. Suspend on non-finite and crossed quotes; never substitute a last-known-good price silently.
- **Emitting a sub-penny price on a displayed order.** The midpoint of a one-cent spread is a half-cent. Sent as a displayed order it is a Rule 612 violation the venue will reject; the sub-penny allowance applies to the non-displayed midpoint peg.
- **Pegging with floats.** `0.1 + 0.2` is not `0.3`, and `round(x, 4)` does not produce a tick-valid price. Use `Decimal` and snap to the instrument's MPV as the final step.
- **Repricing on every tick.** Without a minimum-move threshold, a peg generates a cancel/replace per quote update, exhausting the order-to-trade budget and resetting queue priority each time — the two things a passive strategy is trying to preserve.
- **Ignoring the Rule 201 floor on short sales.** When the short-sale circuit breaker is active, a sell peg referencing the bid sits exactly at the NBB, which the price test forbids. The floor is one minimum increment above the NBB.
- **Carrying a stale peg through a halt.** Venues cancel or reject midpoint-pegged orders around trading halts. A peg computed from the pre-halt NBBO is not a valid price on resumption.

## Verification

- Instantiate `PegOrderTypesForPassiveExecutionEngine` with default config and `NBBOQuote("AAPL", Decimal("100.00"), Decimal("100.10"))`:
  - Primary peg BUY, offset `+$0.01` ⟹ `effective_limit_price == 100.01`, `status == PRICED`, no clamps.
  - Midpoint peg BUY ⟹ `reference_price == 100.05`, `effective_limit_price == 100.05`.
  - Market peg BUY, no offset ⟹ raw price `100.10` clamped to `100.09` with `clamps == ("PASSIVITY",)` — the peg is not left marketable.
  - Market peg BUY, offset `+$0.05`, `limit_cap=100.12` ⟹ raw `100.15`, `is_cap_active` true, and the passivity bound still tighter at `100.09`.
  - Short-sale Market peg SELL against `short_sale_restricted=True` ⟹ `100.01`, `binding_constraint == "SHORT_SALE_201"`.
  - Non-displayed Midpoint peg on a `100.00 / 100.01` book ⟹ `100.005` with `price_increment == 0.005`; the same peg displayed ⟹ `100.00`.
  - `NBBOQuote("AAPL", float("nan"), 100.10)` ⟹ `status == SUSPENDED`, `effective_limit_price is None`.
- Run `python -m unittest discover -s skills/peg-order-types-for-passive-execution/scripts`.

## Related Skills

- `post-only-limit-repricing-under-fast-markets`
- `queue-position-modeling-for-passive-orders`
- `adverse-selection-measurement-for-passive-orders`
- `post-only-and-maker-taker-fee-optimization`
- `exchange-tick-size-regime-tracking`
- `us-reg-sho-short-sale-locate-requirements`
- `iceberg-order-native-broker-support-vs-simulation`
