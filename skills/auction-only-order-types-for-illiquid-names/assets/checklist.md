# Checklist for Illiquid Auction Execution

- [ ] Confirm severe illiquidity (>=5% of ADV) routes 100% of the volume to the Closing Auction.
- [ ] Confirm the engine mandates LOC (Limit-on-Close) rather than MOC for illiquid names to enforce price protection.
- [ ] Confirm `suggested_limit_price` is populated when `reference_price` is supplied; if it is `None` and `auction_qty > 0`, set a limit price before submission (LOC requires one — NYSE Rule 7.31(c)(2)(A)).
- [ ] Confirm the limit price is a whole multiple of the instrument's minimum price variation (`DEFAULT_TICK_SIZE` $0.01 at or above $1.00, `SUB_DOLLAR_TICK_SIZE` $0.0001 below — SEC Rule 612) and is rounded away from the aggressive side, so the slippage tolerance is never breached.
- [ ] Confirm inputs are validated: `total_qty` a positive integer, `average_daily_volume` finite and positive, non-empty `symbol`, `side` in {BUY, SELL}, `tick_size` finite and positive.
- [ ] Confirm the **scheduled** session close for the trading date is resolved and passed as `market_close_et` — on a 1:00 p.m. early close the MOC/LOC deadline is 12:50 p.m., not 3:50 p.m. (NYSE Rule 7.35(a)(8)).
- [ ] Confirm `validate_submission_window` is fed a timezone-aware timestamp and converts it to `America/New_York` before comparing; naive datetimes must be rejected.
- [ ] Confirm the venue-specific deadline is used where it matters: `entry_cutoff_for` gives NYSE 15:50 for MOC and LOC, Nasdaq 15:55 for MOC and 15:58 for LOC on a regular close.
- [ ] If relying on a post-freeze entry window, confirm the qualifying condition holds — a published NYSE Significant Closing Imbalance on the contra side, or a Nasdaq First/Second Reference Price with the reprice-vs-reject instruction set.
- [ ] Ensure continuous slicing (VWAP/TWAP) finishes at least five minutes before the entry cutoff so the auction leg can still be sized and submitted.
- [ ] Confirm the auction leg is sized as committed capital: it cannot be freely cancelled or reduced after `cancel_modify_freeze_for(market_close_et)`.
- [ ] Run test suite: `python -m unittest discover -s skills/auction-only-order-types-for-illiquid-names/scripts`.

## Sign-off
- Execution Quant: ___________________________
- Date: ___________________________
