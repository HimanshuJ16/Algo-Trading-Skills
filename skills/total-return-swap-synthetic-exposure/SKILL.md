---
name: total-return-swap-synthetic-exposure
description: >-
  Use when pricing or risk-managing a share-locked equity total return swap: the total
  return leg with manufactured dividends filtered by the ISDA dividend period, and the
  funding leg on a benchmark rate plus spread.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: multi-asset-derivatives
  tags: total-return-swap, trs, synthetic-exposure, derivatives, sofr-funding, manufactured-dividends, isda-margin, prime-brokerage
  brokers_frameworks: "2002 ISDA Equity Derivatives Definitions; 2006 ISDA Definitions (day count); BCBS-IOSCO Uncleared Margin Requirements; Python Standard Library (dataclasses, datetime)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when engineering, pricing, risk-managing, or accounting for a **share-locked
equity Total Return Swap (TRS)** — synthetic economic exposure to a stock, ETF or basket
without holding the physical asset.

It gives you:

- **Total Return Leg** — capital return plus manufactured dividends, with dividend
  eligibility filtered by the ISDA Dividend Period and the Record / Ex / Paid basis
  actually specified in the Confirmation.
- **Funding Leg** — benchmark (SOFR, €STR, SONIA, EURIBOR, TONA, Fed Funds) plus prime
  broker spread, accrued on the **period-reset notional** over an `ACT/360`, `ACT/365`
  or genuine `30/360` Bond Basis day count fraction.
- **Periodic net settlement** signed for whichever side you are on, plus a
  side-signed mark-to-market and synthetic share delta ($\Delta$).
- **ISDA CSA margin requirements** — initial, maintenance and variation margin reported
  as three separate numbers, because initial margin is segregated and cannot fund a
  variation margin call.

## When NOT to Use

- **Fixed-notional TRS.** This engine keeps `quantity_shares` fixed and resets the
  notional each period. In a fixed-notional structure the share count changes at each
  reset instead; the funding accrual and the delta both differ. Do not use this engine
  for one.
- **Path-dependent or intraday MtM.** `process_reset_period` values one complete reset
  period from two prices. It does not path-simulate, does not accrue daily, and does not
  model intra-period collateral movements.
- **A legal or tax determination.** The engine applies whatever withholding haircut you
  give it. Whether Section 871(m) applies, at what rate, and under which treaty is a
  counterparty- and transaction-specific determination — see Common Pitfalls.
- **Regulatory margin calculation of record.** The `initial_margin_pct` default reflects
  the BCBS-IOSCO standardised schedule for equity (15% of notional). If your CSA prices
  IM with ISDA SIMM or a bilateral house grid, that number governs, not this one.
- **Non-equity underlyings.** Bond and commodity TRS carry accrued-interest, coupon and
  roll mechanics this engine does not model.

## Prerequisites

- Python 3.9+ (standard library only).
- The executed Confirmation — specifically: share count vs. notional reset basis, the
  Dividend Amount basis (Record / Ex / Paid), the dividend pass-through percentage, the
  day count convention, and the CSA's VM threshold and Minimum Transfer Amount.
- Benchmark rate fixings (SOFR, €STR, SONIA) and a corporate action feed carrying
  ex-date, record date, payment date, gross amount and withholding rate.

## Workflow

1. **Configure the contract.** Instantiate `TRSContractConfig` with the swap ID, symbol,
   booked notional, initial reference price, fixed share quantity, benchmark, spread in
   bps, day count, margin percentages, dividend basis, VM threshold and MTA.
   `config.validate()` rejects non-positive quantities and prices, percentages outside
   `[0, 100]`, and NaN/Inf. `config.consistency_warnings()` reports the *non-fatal*
   booking contradictions — a booked notional that disagrees with
   `quantity_shares × initial_reference_price`, or a day count that is not the market
   convention for the chosen benchmark. Read those warnings; they are the difference
   between a mis-booked trade and a correct one, and they never block pricing.
2. **Define the reset period and dividends.** Build a `TRSResetPeriod` with the period
   start/end dates, start/end reference prices, the average benchmark fixing, and every
   `DividendEvent` you have — including ones you are unsure belong to the period. The
   engine filters them; you do not have to pre-filter. Supply `record_date` whenever the
   Confirmation specifies the Record Amount basis.
3. **Calculate the total return leg.** `calculate_total_return_leg()` returns
   `(capital_return, net_manufactured_dividends)`. Only dividends whose relevant date
   falls in `(start_date, end_date]` accrue. If the net dividend is smaller than you
   expected, inspect `TRSSettlement.excluded_dividend_ids` before assuming a bug — the
   dividend probably belongs to the adjacent period.
4. **Calculate the funding leg.** `calculate_funding_leg()` accrues
   `period_notional × (benchmark% + spread_bps/100)/100 × day_count_fraction`, where
   `period_notional = quantity_shares × start_price`. A negative all-in rate produces a
   negative accrual — that is correct, not a sign bug, and the engine warns when it
   happens.
5. **Process the settlement.** `process_reset_period(config, reset, side,
   collateral_already_posted_usd=0.0)` returns a `TRSSettlement` whose `net_cashflow_usd`
   and `current_mtm_usd` are signed **for the side you passed**. Positive is an inflow to
   that party. Do not read the receiver's numbers and negate them mentally — pass
   `PAYER_TOTAL_RETURN` and let the engine do it.
6. **Act on margin as three separate numbers.** `variation_margin_due_usd` is the
   incremental transfer after the VM threshold, the already-posted collateral and the
   MTA. `initial_margin_requirement_usd` is a segregated requirement that stands
   alongside it. Never subtract one from the other to decide what to post.

## Common Pitfalls

- **Netting initial margin against a variation margin call.** Under the BCBS-IOSCO
  uncleared margin framework initial margin is exchanged on a gross basis, held so as to
  protect the posting party, and cannot be re-hypothecated — so it cannot fund a VM call.
  A `$113,750` mark-to-market loss against `$150,000` of posted IM is still a `$113,750`
  variation margin call, not a zero call. Variation margin fully collateralises the MtM
  at a **zero threshold**, subject only to the Minimum Transfer Amount (capped at
  €500,000 across IM and VM combined in the EU implementation).
- **Computing 30/360 from a day count.** `actual_days / 360` is Act/360, not 30/360. The
  Bond Basis fraction is `[360(Y2−Y1) + 30(M2−M1) + (D2−D1)] / 360` with D1 = 31 mapped
  to 30 and D2 = 31 mapped to 30 when D1 ∈ {30, 31}. Over 1 Jan → 1 Jul the two
  conventions differ by a full day of interest on the whole notional. Use
  `day_count_fraction_for_dates(start, end, convention)`; the day-count-only helper
  raises for 30/360 rather than returning a plausible wrong number.
- **Assuming 15% Section 871(m) withholding.** The statutory rate on a dividend
  equivalent paid to a non-US person is **30%** (IRC §871(a)/§881); 15% is a *treaty*
  rate that applies only where a treaty and the counterparty's documentation support it.
  Separately, IRS **Notice 2024-44** extended the phase-in so that for transactions
  issued before 1 January 2027 only **delta-one** transactions are §871(m) transactions —
  a TRS tracking its underlying one-for-one *is* delta-one and is in scope today. Confirm
  no later notice has moved that date again before relying on it.
- **Accruing every dividend in the list.** A dividend accrues to the period only if its
  relevant date falls inside the Dividend Period. Which date is "relevant" is a
  Confirmation term: Record Amount, Ex Amount or Paid Amount (2002 ISDA EDD §10.1). A
  dividend with an ex-date in March and a payment date in April lands in different
  periods under the EX and PAID bases — booking the wrong basis silently shifts cash
  between quarters.
- **Mismatched day-count conventions.** USD SOFR funding accrues `ACT/360`; SONIA and
  TONA accrue `ACT/365`. Booking a SONIA leg `ACT/360` over-accrues interest by roughly
  1.4%. The engine warns on any benchmark/day-count mismatch instead of silently
  correcting it, because a bespoke Confirmation may genuinely depart from convention.
- **Confusing notional reset with fixed share reset.** In a share-locked TRS the share
  count is fixed and the notional resets to `shares × start_price` each period — so
  funding interest and margin both scale with the reference price. In a fixed-notional
  TRS the share count moves instead. This engine implements the former only.
- **Reading a payer's position off the receiver's numbers.** The mark-to-market of the
  two sides is a mirror image; only the funding accrual is a shared gross figure. A model
  that reports one MtM for both sides tells the short synthetic party it lost money on a
  day the underlying fell.
- **Neglecting financing drag in backtests.** Backtesting a synthetic long without the
  funding leg (benchmark + spread on the resetting notional) overstates net return and
  Sharpe — the drag compounds with the reference price, not with the trade-date notional.

## Verification

Run the test suite. It covers the ISDA Bond Basis end-of-month rules, dividend period
boundary inclusion, the Record/Ex/Paid bases, negative funding rates, side-signed MtM,
IM/VM separation, MTA suppression, and input validation:

```bash
python -m unittest discover -s skills/total-return-swap-synthetic-exposure/scripts
```

Check a settlement's `warnings` and `excluded_dividend_ids` before signing off a reset —
an empty `warnings` list is part of the expected result, not an optional extra.

## Related Skills

- `cross-margining-across-asset-classes`
- `dividend-futures-and-forward-modeling`
- `capital-efficiency-across-cross-margined-strategies`
- `multi-leg-strategy-margin-optimization`
- `counterparty-credit-risk-for-otc-derivatives`
