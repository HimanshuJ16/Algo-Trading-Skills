# Pre-Flight Checklist

US federal securities lot matching. Sign off before a run feeds a Form 8949.

## Scope

- [ ] Is this a **US federal** filing? (UK matches same-day / 30-day / S.104 pool;
      India mandates FIFO for demat with a 12-month long-term threshold — neither
      is modelled here.)
- [ ] Has the taxpayer made a §475(f) mark-to-market election? If so, there is no
      capital lot matching to do.
- [ ] Are these ordinary securities, not RIC/DRP shares under average basis and
      not §1256 contracts?

## Inventory

- [ ] Are all lots for a **single symbol**, with unique `lot_id`s?
- [ ] Does `cost_basis_per_share` already include acquisition commissions and any
      prior wash-sale or corporate-action adjustment?
- [ ] Is every `acquisition_date_iso` parseable and on or before the sale date?
- [ ] Does open inventory actually cover the sale quantity — with no shortfall
      closed by inventing basis?

## Method

- [ ] Is the matching strategy chosen **before** the sale, not at filing time?
- [ ] For LIFO, HIFO or SPECIFIC_LOT: does an `identification_reference` exist —
      a broker confirmation or standing instruction dated no later than the
      earlier of the settlement date or the Rule 15c6-1 settlement time (T+1
      since 2024-05-28)?
- [ ] For SPECIFIC_LOT: do the designated lots cover the full sale quantity, so
      nothing spills into undesignated shares?

## Classification

- [ ] Is `sale_date` supplied for every sale?
- [ ] Is the term derived by **calendar anniversary** — sale strictly after the
      one-year anniversary is LTCG, sale on the anniversary is STCG — rather than
      by `days_held > 365`?
- [ ] Is the leap-year case verified (bought 2024-01-01, sold 2025-01-01 $\implies$
      STCG despite 366 elapsed days)?
- [ ] Is no `holding_period_days` value being stored on lots and reused across
      sales?

## Output

- [ ] Is `is_mixed_term` checked, with mixed-term sales split across Form 8949
      Part I and Part II rather than reported as one row?
- [ ] Does the sum of matched quantities equal the sale quantity exactly?
- [ ] Are per-lot rows (`matched_lots`), not the aggregates, used for filing?
- [ ] Is the `identification_reference` retained with the trade record for audit?
- [ ] Have totals been reconciled against the broker's Form 1099-B?
