# Checklist for Algo Trading Disclosure

- [ ] Verify FIX gateway is configured to pass `algo_id` into the correct exchange tag.
- [ ] Ensure the compliance registry is synchronized with actual exchange approval statuses.
- [ ] Confirm all child orders spawned by smart order routers inherit the parent's `algo_id`.
- [ ] Run test suite: `python scripts/test_algo_trading_disclosure_to_exchange_membership.py`.

## Sign-off
- Chief Compliance Officer (CCO): ___________________________
- Date: ___________________________