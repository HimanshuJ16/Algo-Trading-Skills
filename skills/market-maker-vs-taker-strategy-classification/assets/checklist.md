# Pre-Flight Checklist — Maker vs Taker Strategy Classification

## Scope
- [ ] Fills come from a **single venue**? (Per-share and percentage-of-notional schedules do not blend into one bps figure.)
- [ ] Reporting period aligned to the period the venue bills on?
- [ ] Venue actually prices by liquidity flag? (Per-contract membership schedules such as CME Group's futures fees are out of scope.)
- [ ] The question being answered is "which side of the book is my flow on, and what did it cost" — not "am I a market maker" and not "is my passive strategy working"?

## Fill log normalisation
- [ ] `is_maker` parsed to a **real boolean**? (JSON `"false"` is truthy in Python and would book every taker fill as a maker fill.)
- [ ] Liquidity indicator mapped from the venue's code, not assumed binary? FIX tag 851: `1 -> ADDED`, `2 -> REMOVED`, `3 -> ROUTED_OUT`, `4 -> AUCTION`.
- [ ] Auction and routed-out fills tagged with `liquidity_category` rather than collapsed into `is_maker=False`?
- [ ] Any venue-specific code with no category here (midpoint, hidden, price-improvement) mapped deliberately, with the decision recorded?
- [ ] Prices and quantities positive and finite, with side encoded separately from quantity?
- [ ] `fee_paid_usd` carries the **signed amount actually billed** — positive charged, negative credited?
- [ ] Log **deduplicated** before submission? Overlapping paginated fetches double-count volume and fees; the engine rejects a repeated `trade_id`.

## Basis and thresholds
- [ ] `ClassificationBasis` chosen to match the venue's pricing unit (`QUANTITY` for per-share, `NOTIONAL` for percentage-of-value)?
- [ ] Multi-symbol log run on `NOTIONAL` (or split by symbol) rather than forced through `QUANTITY`?
- [ ] Thresholds understood as **this repository's convention**, not a regulatory or exchange standard, and overridden if your desk means something else?
- [ ] Classification read from the full-precision ratio, not from a rounded display value?

## Reading the report
- [ ] `warnings` read in full and each entry actioned or explicitly accepted?
- [ ] `classification_ratio` checked against the thresholds — is the label near a cut-off?
- [ ] `excluded_trades_count` / `excluded_gross_notional_usd` checked — how much of the day is outside the ratio?
- [ ] `maker_volume_ratio` vs `maker_notional_ratio` compared, and any wide gap explained?
- [ ] `UNCLASSIFIED_NO_MAKER_TAKER_VOLUME` treated as "no continuous-book flow", not as a taker result?

## Fee attribution
- [ ] `maker_fees_paid_usd` checked for sign — was the passive side actually credited, or charged?
- [ ] Negative `effective_fee_rate_bps` understood as net rebate capture, and do downstream consumers tolerate a negative cost?
- [ ] Excluded fills' fees recognised as still being in the net figure?
- [ ] Fee drag compared against realized alpha before any routing or strategy change is made on the back of it?

## Boundaries
- [ ] No regulatory market-maker or dealer conclusion drawn from the maker ratio? (MiFID II/RTS 8 tests quoting presence; the US dealer rules were vacated in November 2024.)
- [ ] Passive execution quality assessed separately via adverse selection and queue position, not inferred from the maker ratio?
