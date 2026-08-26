# Workflows for LME Integration

Deep procedure for the client-side checks in
`scripts/lme_london_metal_exchange_integration.py`. Published levels and their
sources are in `references/standards.md`.

## 1. Validate the payload before it reaches any exchange rule

Structural faults are programming errors, not exchange verdicts, and are raised
rather than reported:

- `lots` must be a positive `int`. Not `0`, not negative, not `1.5`, not
  `"10"`, not `True` (`bool` is an `int` subclass, and never a lot count).
  A negative lot count that survives this step produces a negative tonnage and
  a negative notional that will be summed into a position somewhere downstream.
- `side` must be `BUY` or `SELL` after trimming and upper-casing. An
  unrecognised side must never come back attached to an approved order.
- `price_usd_per_mt` must be positive and finite. `NaN` and `Inf` propagate
  silently through comparisons; reject them at the boundary.
- `prompt_date` must be a `date`, an ISO `YYYY-MM-DD` string, or one of the
  rolling designators `CASH`, `TOM`, `3M`.

An unknown `metal_code` is different: it is a lookup failure against a
refreshable catalog, so it returns `INVALID_METAL_CODE` with zero tonnage and
zero notional rather than raising.

## 2. Resolve the contract and compute tonnage

Look the metal up by its LME contract code and take the lot size from the spec —
never from a default.

```
total_tonnage_mt = lots × lot_size_mt
```

The codes are not mnemonic and the tonnages do not follow the names:

- `AH` Primary Aluminium is 25 MT, but `AA` Aluminium Alloy and `NA` NASAAC are
  **20 MT**.
- `NI` Primary Nickel is **6 MT**. Ten lots is 60 MT, not 250 MT.
- `SN` Tin is **5 MT**.

Tonnage, not the lot count, is what a risk system should be limiting.

## 3. Check the outright tick, in decimal arithmetic

```
tick_ok = (price % outright_tick_usd) == 0
```

Two things this step must not do:

- **Use a single tick constant.** Nickel and Tin outrights are $5.00/MT on
  LMEselect and in the Ring. Every other listed base metal is $0.50/MT.
- **Rely on the tick check for positivity.** The remainder of a negative price
  against a positive tick is zero, so `-9,250.50 % 0.50` is `0` and a negative
  price passes a tick test unaided. Positivity is checked in step 1 for exactly
  this reason.

Use `Decimal`, not `float`. Binary float misreads almost every cent-denominated
price as off-tick — `0.03 % 0.01` is not zero in float — and the catalog is
injectable, so a refreshed spec may carry a $0.01 tick.

Only the outright tick is enforced. If you extend this to carries, note that
they use $0.01, that large-tick electronic calendar spreads have used a separate
tick since 20 January 2026, and that inter-office is $0.01 throughout.

## 4. Check the prompt date

Rolling designators (`CASH`, `TOM`, `3M`) pass as-is: which calendar date each
resolves to depends on the LME business-day calendar, and asserting a date here
would be a guess.

For an explicit date:

1. **Reject a date on or before the trade date.** Nothing further to check.
2. **Reject a date beyond the contract's furthest listed monthly prompt.** Tin
   lists 15 months; Aluminium Alloy and NASAAC 27; Lead, Zinc and Nickel 63;
   Copper and Aluminium 123. A 60-month tin prompt does not exist.
3. **Classify by tenor.** Under 3 months is a daily prompt; 3 to 6 months is
   weekly; beyond 6 months is monthly.
4. **Flag, do not reject, a structural mismatch.** Weekly prompts normally fall
   on a Wednesday and monthly prompts on the third Wednesday, but the LME issues
   notices with **substitute prompt dates** around bank holidays. A strict
   weekday rule refuses tradeable dates. Record the mismatch as a warning and
   leave `prompt_date_confirmed` False.
5. **Let a supplied calendar decide.** When `valid_prompt_dates` is passed,
   membership is authoritative: a date outside the set is rejected, and a date
   inside it is confirmed with no structural warning.

The tenor comparison is a calendar-month delta, not a business-day count. It is
precise enough to catch a curve-length violation and is not a substitute for the
LME calendar.

## 5. Check the Daily Price Limit

```
band  = previous_close_3m_usd × daily_price_limit_pct
upper = previous_close_3m_usd + band
lower = previous_close_3m_usd − band
dpl_ok = lower ≤ price ≤ upper
```

Three properties of this check matter more than the arithmetic:

- **The reference is the previous Business Day's Closing Price for the 3-month
  contract**, applied equally to every prompt on the curve. Not the mid, not the
  top of book, not the LME Official Price, and not the live 3M.
- **The band is symmetric across sides.** The LME accepts no bid above the upper
  limit and no offer below the lower one — and it refuses a deep passive bid
  below the lower limit as well. ICE's Reasonability Limit is directional and
  accepts far-side passive orders; that logic is wrong here.
- **A missing reference is not a pass.** Return `NO_DPL_REFERENCE_PRICE` with
  `ready_to_send` False. The DPL is a genuine order-entry rejection, so an
  unchecked one leaves the order unvalidated.

The percentages are 12% for Aluminium, Copper, Lead and Zinc, and 15% for
Nickel, Tin, Aluminium Alloy and NASAAC — as restated in LME Notice 26/138,
effective 8 June 2026. They are revised by notice; carry the source and
retrieval date with them.

The band edges here are computed from the published percentage and rounded to
cents. The LME's own published limit prices are authoritative; treat these as a
close bound, not as the exchange's exact figures.

## 6. Emit the report

```
total_notional_usd = total_tonnage_mt × price_usd_per_mt   (quantized to cents)
```

Order the verdict most-blocking first: prompt date, then tick, then the DPL
(missing reference before breach). Report `ready_to_send` only when every
modelled check passed, and keep the warnings — an unconfirmed prompt date is the
difference between "we checked" and "we could not check".

`ready_to_send` is a statement about this module, not about the LME. Dynamic and
Static Price Bands, Exchange- and Member-set maximum order size limits, and the
order throttle can all still reject the order, and the DPL Multiple Day
Framework can suspend the metal outright.
