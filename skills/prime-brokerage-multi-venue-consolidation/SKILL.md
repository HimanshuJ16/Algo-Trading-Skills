---
name: prime-brokerage-multi-venue-consolidation
description: Use when a strategy executes across several brokers, ECNs and dark pools
  and the fills must be consolidated into one prime-brokerage account — netting each
  instrument, capturing both fee legs, and emitting idempotent give-up instructions
  the prime broker can claim before the affirmation cut-off.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- prime-brokerage
- give-up-trades
- multi-venue
- clearing-consolidation
- post-trade
- settlement
brokers_frameworks:
- Prime Brokerage (SEC 1994 no-action letter framework)
- DTC ID / Institutional Trade Processing
- FIA Tech EGUS (futures give-ups)
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when execution is deliberately fragmented across executing brokers and venues but clearing and financing are meant to sit in one prime-brokerage (PB) account. It nets each instrument across venues, keeps every monetary total in the currency it was traded in, accounts for both fee legs (the third-party execution commission *and* the PB clearing fee), and emits one give-up instruction per fill with duplicate submission blocked at the boundary.

It is the **post-trade consolidation layer**. Everything it produces is derived from the fills you hand it: it does not talk to a broker, does not hold positions between runs beyond an idempotency ledger, and does not decide whether the PB will accept the give-up.

## When NOT to Use

- **To estimate margin savings or capital relief.** The engine deliberately publishes no margin number. Whether a consolidated book actually attracts less margin depends on the PB's approved methodology — Regulation T strategy-based margin (12 CFR 220), FINRA Rule 4210(g) portfolio margin, or a clearing-house cross-margin arrangement — and on which positions are *eligible* to offset in one account. A ratio computed from traded quantities is not that number. Use `cross-margining-across-asset-classes` and `broker-account-margin-call-handling` for margin, and treat `notional_offset_pct_by_currency` as an operational netting statistic only.
- **As the position book of record.** The PB's statement is authoritative for settlement, financing and margin. A netted figure here that disagrees with the PB means *investigate*, never *overwrite the PB*.
- **For base-currency exposure.** Nothing is FX-converted; totals are kept per currency and never summed across currencies. `multi-broker-consolidated-position-view` and `multi-currency-pnl-and-fx-conversion` own that conversion.
- **To net opposing orders before they are sent.** This consolidates fills that already printed. Suppressing the offsetting order in the first place belongs to `multi-order-netting-before-routing`.
- **As a give-up transport.** It builds the payload; submitting it to the PB, DTC ID, EGUS or a FIX give-up session, and handling the PB's affirm/DK response, is the caller's integration.
- **For non-US-equity deadlines by default.** The timeliness check compares against cut-offs *you* supply. Listed-futures give-up and allocation deadlines come from the clearing-house rulebook and are not the equity affirmation timeline.

## Prerequisites

- Fills across every executing broker (`execution_id` unique per fill, `executing_broker`, `venue_id`, `symbol`, `side` exactly `'BUY'`/`'SELL'`, unsigned `quantity`, `price`, ISO 8601 `trade_date`, ISO 4217 `currency`, `contract_multiplier` for anything that is not cash equity or spot, and the third-party `executing_broker_commission`).
- A `PrimeBrokerSpec`: PB name, the account give-ups are claimed into, the PB clearing fee per share/contract, and the currency that fee is charged in. There is no default spec — the account id is written onto every instruction.
- An executed give-up agreement with each executing broker (SIFMA Form 150-style for US equities under the SEC's 1994 prime brokerage no-action letter; the FIA International Uniform Give-Up Agreement, executed via FIA Tech EGUS, for listed futures) and a PB account meeting the letter's minimum net equity.
- For the timeliness check: a timezone-aware submission cut-off per trade date, sourced from the venue/clearing-house rulebook or the PB's own operational SLA.

## Workflow

1. **Ingest Multi-Venue Fills**:
   - Build a `VenueExecution` per fill. Validation happens on construction, so a NaN price, a zero quantity or a malformed date raises at the boundary rather than inside a payload the PB will act on.
   - **Decision point — is `side` trustworthy?** Anything that is not exactly `BUY`/`SELL` raises. A parser that maps every non-`BUY` value to a sell turns one typo into a position of the wrong sign, and the give-up transmits that sign to the PB.
   - **Decision point — is this a derivative?** `contract_multiplier` is mandatory and cannot be inferred from the ticker. It is part of the netting key: an adjusted option contract delivering a non-standard number of shares is a *different* instrument from the standard one and must not be netted with it.

2. **Enforce Give-Up Idempotency**:
   - The engine records every `execution_id` it has emitted an instruction for. A repeat within a batch always raises; a repeat across calls raises while `enforce_cross_batch_idempotency` is on (the default).
   - **Decision point — the give-up queue reconnected and you are unsure what landed.** Do not resubmit blindly: the PB claims each instruction independently, so a replayed batch double-claims every fill. Reconcile against the PB's claimed list, then `reset_submitted_execution_ids()` only for what genuinely needs re-sending.
   - Registration is atomic. A batch rejected for any reason registers nothing, so the corrected batch is accepted.

3. **Net Per Instrument**:
   - Netting is keyed on `(symbol, currency)` with a single multiplier per key. Per key: net and gross quantity, gross notional, VWAP, residual notional at VWAP, and the signed breakdown by executing broker and by venue.
   - **Decision point — which figure does the downstream limit read?** `gross_quantity` and `gross_notional` are what actually crossed the tape and what the fee legs price off. `net_quantity` is what remains to be financed. A book that is long 1,000 at one broker and short 1,000 at another nets flat while both legs still generated commission, clearing fees and market impact.
   - A ticker submitted under two currencies, or one symbol under two multipliers, raises rather than being blended into a meaningless aggregate.

4. **Price Both Fee Legs**:
   - PB clearing fee is `quantity × clearing_fee_per_unit`, accumulated in `fee_currency`. Executing-broker commissions accumulate in the currency of each fill, separately. Nothing is summed across currencies anywhere.

5. **Build Give-Up Payload and Check Timeliness**:
   - One instruction per execution, carrying the PB account, executing broker, venue, side, quantity, price, multiplier, currency, notional, both fee legs and the trade date.
   - **Decision point — supply cut-offs or not?** If you pass `giveup_cutoffs`, every trade date in the batch must be covered; an uncovered date raises rather than being reported as on time. Passing `submitted_at` without cut-offs (or the reverse) also raises, so a half-configured check can never look like a passing one. Late instructions are flagged per row, listed in `late_giveup_execution_ids`, logged at WARNING, and set the report status to `CONSOLIDATION_SUCCESSFUL_LATE_GIVEUP` — the batch is still produced, because operations needs the payload *and* the exception.

6. **Audit Report**: consume `PBConsolidationReport` — per-currency notional and fee totals, netted positions, trade dates covered, the late list, and `audit_notes` for the operations log.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reporting a traded-quantity ratio as "margin savings"**: `1 - |net|/gross` over raw share counts is not a capital figure and is not even dimensionally valid across instruments. On a book of 1,000 shares bought and 1,000 sold in a $0.10 stock plus one $700,000 BRK.A share, that ratio reads 99.95% "savings" while the only residual position is the $700,000 long that offsets against nothing. Notional-weighted, the same book offsets 0.03%.
- **Assuming netting at the PB is netting for margin**: offsets require the positions to be eligible and held in one account under an approved methodology. Consolidation is a precondition for margin efficiency, not evidence of it.
- **Defaulting an unrecognised side to SELL**: a one-character typo becomes a short position in the give-up instruction and a break at the PB.
- **Replaying the give-up queue after a reconnect**: the PB claims each instruction independently, so a resend double-claims. An `execution_id` is the idempotency key and must be unique per fill, not per order.
- **Netting a ticker across currencies**: the same symbol priced in USD at one venue and EUR at another is two instruments. Summing them produces a net quantity that corresponds to no real position.
- **Summing notional across currencies into one "USD" total**: a EUR fill added to a USD total is wrong by the exchange rate with no error anywhere. Keep totals per currency until an explicit, timestamped FX conversion.
- **Charging only the PB clearing fee**: a give-up costs the executing broker's commission *and* the PB's clearing fee. Modelling one leg understates the all-in cost of fragmenting execution — often the whole reason the routing decision looked good.
- **Dropping the contract multiplier**: 20 option contracts at a $7.50 premium are $15,000 of give-up notional, not $150.
- **Treating a give-up as settled once transmitted**: under the SIFMA Form 150 framework the PB may DK/disaffirm, and a disaffirmed trade stays a customer trade on the *executing* broker's books. Transmission is not acceptance; reconcile against the PB's claimed list.
- **Hard-coding a submission cut-off**: deadlines are venue- and clearing-house-specific, and the US equity timeline compressed when T+1 took effect on 28 May 2024. A constant in code is stale the moment a rulebook changes.
- **Comparing a cut-off against a naive timestamp**: 01:30 UTC on the day after trade date is 21:30 ET on trade date — already late. Both the cut-off and the submission time must be timezone-aware, and the engine raises if either is not.

## Verification

- Consolidate two AAPL fills (BUY 1,000 @ $150 via Broker A on NASDAQ; SELL 400 @ $151 via Broker B on BATS): verify net quantity 600, gross quantity 1,400, gross notional $210,400, VWAP $150.285714, residual notional $90,171.43, `offset_ratio_pct` 57.1429, and a two-row give-up payload with `broker_breakdown` `{BROKER_A: +1000, BROKER_B: -400}`.
- Regression on the offset metric: consolidate BUY 1,000 / SELL 1,000 of a $0.10 stock plus BUY 1 BRK.A @ $700,000. `notional_offset_pct_by_currency["USD"]` must be ≈0.0286 — a quantity-weighted ratio reports 99.95 on the same input. Confirm the report exposes no field containing "margin".
- Value 20 option contracts at a $7.50 premium with `contract_multiplier=100`: verify $15,000 of notional and a VWAP of $7.50 (per underlying unit, not per contract).
- Fee legs: 1,400 shares at `clearing_fee_per_unit=0.0005` gives $0.70 of PB clearing fee, with $3.50 + $1.25 of executing-broker commission tracked separately in its own currency bucket.
- Negative checks, each of which must raise: a `side` of `'BUYY'`; a zero or negative quantity; a NaN or infinite quantity/price; a negative price or commission; `trade_date` of `'31-07-2026'` or `'2026-13-01'`; a `currency` of `'US$'`; a non-positive multiplier; a blank identifier; a non-`VenueExecution` row; a missing `PrimeBrokerSpec`; one symbol under two currencies or two multipliers; a duplicate `execution_id` within a batch; the same batch replayed on the same engine; a naive `submitted_at` or cut-off; `submitted_at` without `giveup_cutoffs` (or the reverse); a trade date with no cut-off configured.
- Verify a batch rejected mid-validation registers no execution ids, and that the corrected batch is then accepted.
- Verify 21:01 ET on trade date and 01:30 UTC on T+1 both flag against a 21:00 ET cut-off, while 20:59 ET does not.
- Run `python -m unittest discover -s skills/prime-brokerage-multi-venue-consolidation/scripts` and confirm 100% pass rate.

## Related Skills

- `multi-broker-consolidated-position-view`
- `order-placement-idempotency`
- `cross-margining-across-asset-classes`
- `broker-account-margin-call-handling`
- `multi-order-netting-before-routing`
- `multi-currency-pnl-and-fx-conversion`
- `counterparty-and-broker-concentration-risk`
