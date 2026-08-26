# Standards for LME Integration

All levels below are as published on the retrieval date shown. The LME revises
contract specifications, tick sizes and Daily Price Limits **by notice** — Lead
and Zinc moved from 15% to 12% on 8 June 2026. Re-derive them from the primary
sources rather than trusting a cached copy.

## Engineering standards

| Requirement | Standard |
|---|---|
| Lot sizing | Lot size is **per metal, in metric tonnes**. Exposure MUST be computed as `lots × lot_size_mt`. A single exchange-wide lot size MUST NOT be assumed. |
| Tick size | The outright tick is **per metal**. Nickel and Tin are $5.00/MT; the rest are $0.50/MT. A universal $0.50 constant is a defect, not a simplification. |
| Tick scope | Only the **outright** tick is enforced here. Carries, large-tick electronic calendar spreads and inter-office trades each use different ticks. |
| Order-entry price control | The **Daily Price Limit** is the control that refuses an order on price. It is measured from the previous Business Day's Closing Price for the **3-month** Contract and applies equally to all prompts on the curve. |
| Directionality | The LME band is **symmetric on price**: no bid above the upper limit and no offer below the lower one, and a deep passive order outside the band is refused too. ICE's directional Reasonability Limit logic MUST NOT be applied here. |
| Fail-closed | If the DPL reference price is unavailable, the order MUST NOT be reported as ready. The mid, the top of book and the LME Official Price are not substitutes. |
| Prompt dates | An explicit prompt date MUST be checked against the contract's furthest listed monthly prompt. Structural mismatches (non-Wednesday weeklies, non-third-Wednesday monthlies) MUST be flagged rather than hard-rejected, because the LME publishes substitute dates. |
| Arithmetic | Price, tick and notional arithmetic MUST use `Decimal`. Positivity MUST be checked separately from tick alignment — the remainder of a negative price against a positive tick is zero. |
| Provenance | Tick sizes and Daily Price Limits MUST be carried with their source and retrieval date. |

## Contract specifications

Source: LME contract specification pages, `lme.com/en/metals/non-ferrous/lme-<metal>/contract-specifications`.
Retrieved 2026-08-25. All contracts are quoted in **US dollars per tonne**.

| Code | Contract | Lot (MT) | Outright tick (Ring / LMEselect) | Carry tick | Furthest monthly prompt |
|---|---|---|---|---|---|
| `AH` | Primary Aluminium | 25 | $0.50 | $0.01 | 123 months |
| `AA` | Aluminium Alloy | 20 | $0.50 | $0.01 | 27 months |
| `NA` | NASAAC | 20 | $0.50 | $0.01 | 27 months |
| `CA` | Copper Grade A | 25 | $0.50 | $0.01 | 123 months |
| `PB` | Standard Lead | 25 | $0.50 | $0.01 | 63 months |
| `NI` | Primary Nickel | **6** | **$5.00** | $0.01 | 63 months |
| `SN` | Tin | **5** | **$5.00** | $0.01 | **15 months** |
| `ZS` | Special High Grade Zinc | 25 | $0.50 | $0.01 | 63 months |

Inter-office trades are $0.01 for both outrights and carries. Certain electronic
calendar spreads carry their own tick — see the tick sizes source below.

## Prompt date structure

**LME — Prompt date structure.**
<https://www.lme.com/en/sustainability-and-physical-markets/physical-market-benefits/prompt-date-structure>
Retrieved 2026-08-25.

> "Weekly prompts usually fall on a Wednesday while monthly prompt dates are
> normally the third Wednesday of the month. Where these are altered due to bank
> or public holidays or other non-tradable dates an LME notice will be issued to
> provide a substitute prompt date."

This is the basis for treating a weekday mismatch as a flag rather than a
rejection. The same page gives the structure — daily prompts out to three
months, weekly to six, monthly beyond — and states that trading in a contract
runs up to 12.30pm on the trading day before its prompt date.

Cash is the settlement business day after tomorrow (two business days forward);
3M is the business day three calendar months forward, with the LME confirming
substitute 3-month prompt dates when that date is not a prompt day. Resolving
either to a calendar date requires the LME trading calendar, which this skill
does not ship.

## Daily Price Limits

**LME — Volatility controls: price limits and price bands.**
<https://www.lme.com/en/trading/volatility-controls-price-limits-and-price-bands>
Retrieved 2026-08-25.

> "No orders (whether bids or offers) will be accepted above the upper Daily
> Price Limit or below the low Daily Price Limit."

This single sentence establishes two of this skill's rules: the DPL is an
order-entry control, and it is symmetric across both sides.

**LME Notice 26/138 — Changes to Daily Price Limits for Lead and Zinc Base Metal
Contracts.** Issued 18 May 2026, effective 8 June 2026.
<https://www.lme.com/-/media/files/news/notices/2026/05/trading-26-138-changes-to-daily-price-limits-for-lead-and-zinc-base-metal-contracts.pdf>
Retrieved 2026-08-25.

Restated limits, applied to the "previous Business Day's Closing Price for the
3-month Contract", on LMEselect, the Ring and inter-office, for **outrights
only**:

| Contract | Daily Price Limit |
|---|---|
| Aluminium, Copper, Lead, Zinc | 12% |
| Nickel, Tin | 15% |
| Aluminium Alloy, NASAAC | 15% |
| Cobalt (cash-settled) | 15% |

DPLs apply to all physically settled base metal contracts plus cash-settled
cobalt. The **DPL Multiple Day Framework** (Decision Notice 24/134, effective 28
June 2024) suspends a metal automatically after it closes at the limit in the
same direction on three consecutive business days.

## Tick sizes

**LME — Tick sizes** (Modernising the Market), reflecting Notice 24/240.
<https://www.lme.com/en/trading/initiatives/modernising-the-market/tick-sizes>
Retrieved 2026-08-25.

Revised electronic **spread** tick sizes took effect 20 January 2026. Large-tick
calendar spreads — all 3W–3W and 3M–3W instruments, plus any spread with a leg
beyond the 3-month prompt — trade at $0.25 for Aluminium, Copper, Lead and Zinc,
and $1.00 for Nickel and Tin. Small-tick spreads (both legs inside 3 months,
excluding 3W–3W and 3M–3W) remain $0.01. Outright tick sizes are unchanged by
this notice.

## Other LMEselect pre-trade controls (not modelled here)

**LME — Policies and Controls for the Prevention of Disorderly Trading**
(LMEselect 10).
<https://www.lme.com/-/media/Files/Trading/Systems/LMEselect/LMEselect10/Policies-and-Controls-for-the-Prevention-of-Disorderly-Trading.pdf>
Retrieved 2026-08-25.

- **Dynamic and Static Price Bands** — price collars that reject orders
  order-by-order. Dynamic bands move with the last traded price; static bands
  refresh on a timer around an anchor.
- **Exchange-set and Member-set maximum order size limits** — caps in both lots
  and notional value per order, split by contract type (outright, carry, Tom
  Next), configured via LMEptrm. "The most stringent limit will apply, and any
  order submitted in excess of a limit will be rejected."
- **Order throttle** — order entry/updates per second, per session and per
  member; excess submissions are rejected.

A gateway that models only tick size and the DPL will still see rejections from
all three.

## Platform

LMEselect is the LME's electronic order book. The LMEselect v10 platform went
live in March 2025, replacing the FIX 4.4 order entry API with a FIX 5.0 SP2
one; conformance testing is required before deployment. LMEsmart is the LME's
trade matching and registration system, and LMEsource the market data feed —
neither is an order-routing product.
