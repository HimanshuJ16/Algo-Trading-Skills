# Checklist for Illiquid Auction Execution

- [ ] Confirm severe illiquidity (>=5% of ADV) routes 100% of the volume to the Closing Auction.
- [ ] Confirm the engine mandates LOC (Limit-on-Close) rather than MOC for illiquid names to enforce price protection.
- [ ] Confirm `suggested_limit_price` is populated when `reference_price` is supplied; if it is `None` and `auction_qty > 0`, set a limit price before submission (LOC requires one — NYSE Rule 7.35(B)).
- [ ] Confirm inputs are validated: `total_qty > 0`, `average_daily_volume > 0`, non-empty `symbol`, `side` in {BUY, SELL}.
- [ ] Confirm `validate_submission_window` rejects submissions at or past 3:50 p.m. ET (NYSE/Nasdaq cancel-modify freeze; NYSE entry cutoff).
- [ ] Ensure continuous slicing (VWAP/TWAP) finishes by 3:45 p.m. ET so the auction leg can be submitted before the cutoff.
- [ ] Run test suite: `python -m unittest discover -s skills/auction-only-order-types-for-illiquid-names/scripts`.

## Sign-off
- Execution Quant: ___________________________
- Date: ___________________________
