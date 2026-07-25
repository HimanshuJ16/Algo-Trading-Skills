# Checklist for Illiquid Auction Execution

- [ ] Confirm severe illiquidity (>5% of ADV) routes 100% of the volume to the Closing Auction.
- [ ] Confirm the engine mandates LOC (Limit-on-Close) rather than MOC for illiquid names to enforce price protection.
- [ ] Ensure execution systems respect the strict MOC/LOC exchange cutoff times (e.g., 3:50 PM).
- [ ] Run test suite: `python scripts/test_auction_only_order_types_for_illiquid_names.py`.

## Sign-off
- Execution Quant: ___________________________
- Date: ___________________________
