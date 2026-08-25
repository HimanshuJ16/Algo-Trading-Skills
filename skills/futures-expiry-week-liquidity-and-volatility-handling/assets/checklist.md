# Pre-Flight Checklist

## Snapshot data
- [ ] Are bid-ask spread and top-of-book depth monitored live for the **expiring** contract, not the deferred one?
- [ ] Is `bid_ask_spread_ticks` divided by *this product's* tick size (ES quotes 0.25-point ticks; CME Single Stock futures 0.01-point ticks), rather than being a raw price difference?
- [ ] Is `days_to_expiration` a **business**-day count to Last Trading Day, from the same reference session?
- [ ] Do `top_of_book_depth_qty` and `baseline_average_depth_qty` use the same depth convention (both near-side, or both bid+ask), and is the baseline the expiring contract's own normal-market average?

## Fail-closed behaviour
- [ ] Does a `NaN`, infinite, negative, or missing spread/depth value raise rather than produce a report? (`NaN` loses every threshold comparison, so an unvalidated engine permits market orders and cancels the haircut exactly when the feed breaks.)
- [ ] Is `baseline_average_depth_qty` required to be positive rather than clamped to 1?
- [ ] Is a crossed (negative) spread escalated as a data or venue-state problem rather than read as a tight market?

## Threshold calibration
- [ ] Is `mandatory_roll_dbe_cutoff` calibrated for this product rather than left at the default 2 business days? (CME's designated Equity Index roll date is the Monday before the third Friday — roughly four business days out.)
- [ ] Are `max_spread_ticks_threshold`, `min_depth_ratio_threshold`, and `size_haircut_factor` set deliberately, with the rationale recorded? None of them is mandated by any exchange or regulator.
- [ ] Is the boundary behaviour understood — spread and depth tests are strict, the DBE cutoff is inclusive?

## Consuming the report
- [ ] Is the report actually wired into the order path? (It is advisory; it enforces nothing on its own.)
- [ ] Is `adjusted_max_order_qty` treated as a cap on an *already permitted* order, so a mandatory-roll or escalation report cannot be read as authorising a new entry?
- [ ] Is `is_order_size_suppressed` handled as "do not send the order" rather than sending quantity 0?
- [ ] Is `EXPIRED_ESCALATE` routed to a human, given that the contract has stopped trading and cannot be rolled out of?
- [ ] Are `restriction_reasons` and `depth_ratio` persisted with the decision, not just the status string?

## Expiry-day specifics
- [ ] Is the per-instrument cut-off taken from that product's own specification? On a quarterly third Friday the expiring E-mini S&P 500 future stops trading at 9:30 a.m. ET into the Special Opening Quotation, while CME Single Stock futures run to 3:00 p.m. CT and settle to the cash close.
- [ ] Is the roll itself owned by a separate path (`futures-contract-roll-automation`), and is full-book impact sized elsewhere (`liquidity-adjusted-position-sizing`)?
