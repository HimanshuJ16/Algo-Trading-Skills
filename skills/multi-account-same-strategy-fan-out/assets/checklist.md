# Pre-Flight / Sign-off Checklist — multi-account-same-strategy-fan-out

Use this before considering the skill's implementation complete.

- [ ] **Sub-Account NAV Registration:** Confirm sub-account IDs and individual NAV balances registered.
- [ ] **Pro-Rata Quantity Calculation:** Confirm master signal quantity is sized proportionally to sub-account NAVs.
- [ ] **Collision-Free Client Order IDs:** Confirm `client_order_id` values are unique across all sub-accounts.
- [ ] **Minimum Order Floor:** Confirm small accounts do not receive 0 share orders.
- [ ] **Automated Testing:** Run `python scripts/test_fanout_engine.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
