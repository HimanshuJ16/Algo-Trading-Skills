# Lot Rounding & Minimum Fill Size Pre-Flight Checklist

## Reference data

- [ ] Are `lot_size` and `min_order_quantity` sourced **per security**, not per venue?
- [ ] Is the provenance recorded (`lot_size_source` / `lot_size_as_of`), so no report carries `LOT_SIZE_REFERENCE_DATA_UNSOURCED` unnoticed?
- [ ] Is there a refresh path for venues that re-tier on a schedule (US round lots semiannually; SGX-ST quarterly from October 2026)?
- [ ] Are `max_order_quantity` and `min_notional` populated where the venue publishes them?

## Quantity arithmetic

- [ ] Is every quantity comparison, remainder, and multiplication done in an exact decimal type — no binary floats anywhere on this path?
- [ ] Are NaN, Infinity, zero, negative, and non-numeric quantities rejected at the input boundary rather than deeper in the arithmetic?
- [ ] Is the rounding mode chosen deliberately per use (`FLOOR` for entries, `CEIL` only where over-filling is acceptable)?
- [ ] Is the language's default `round()` kept out of nearest-lot rounding (banker's rounding breaks ties asymmetrically)?
- [ ] Is `quantity_delta` surfaced, and is a positive delta routed past the risk layer before dispatch?

## Odd-lot policy

- [ ] Does `allow_odd_lots` change the routed **quantity**, not just the audit note?
- [ ] Is the flag set from this venue's and this instrument's actual odd-lot handling?
- [ ] Where odd lots are not auto-matched (e.g. HKEX main board), is the sub-lot residual routed to the odd-lot mechanism or merged into the previous slice rather than sent to the main book?

## Venue floors

- [ ] Is the minimum-notional check evaluated **after** rounding?
- [ ] Is a market order's unchecked notional reported as unchecked rather than as passed?
- [ ] Is `rounded_quantity` zeroed on every rejection?
- [ ] Does the caller branch on `is_compliant` rather than on `rounded_quantity` alone?
- [ ] Is a below-minimum rejection handled by resizing or dropping the child order — never by an unchanged retry?

## FIX execution constraints

- [ ] Are Tag 110 `MinQty` and Tag 1089 `MatchIncrement` left absent unless the caller explicitly asked for a minimum-execution constraint?
- [ ] Is it confirmed that Tag 110 is **not** being populated from the venue's minimum order size?
- [ ] Has the display consequence been accepted — under Nasdaq Rule 4703(e) a Minimum Quantity order may not be displayed, and a Display instruction forces IOC?
- [ ] Is the minimum quantity a whole multiple of the round lot, so the venue does not silently round a mixed-lot condition down?
- [ ] Is a `MinQty`/`MatchIncrement` above the routed quantity rejected before dispatch?

## Reporting

- [ ] Is `available_liquidity_depth` left unset when it was not measured, rather than defaulted?
- [ ] Do advisory findings accumulate in `warnings` independently of the terminal `status`?
- [ ] Does the report retain enough state (raw, routed, delta, lot, tags, notional) to reconstruct the sizing decision post-trade?
- [ ] Is exposure-limit enforcement handled by the risk layer rather than by this rounder?
