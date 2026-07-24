# Pre-Flight / Sign-off Checklist — mifid-ii-algo-trading-compliance-eu

Use this before considering the skill's implementation complete.

- [ ] **Pre-Trade Risk Controls:** Confirm `validate_pretrade_order()` checks price collar, max order value, max volume, and message rate limits.
- [ ] **RTS 6 Kill Switch & Order Purge:** Confirm `trigger_rts6_kill_switch()` halts new orders AND cancels all active resting orders on the venue.
- [ ] **MiFID II Order Tagging:** Confirm order payloads include `MiFID2OrderTag` containing `algo_id` and `client_id`.
- [ ] **RTS 6 Annex I Audit Trail:** Confirm audit logs of pre-trade risk decisions are retained for compliance inspection.
- [ ] **Automated Testing:** Run `python scripts/test_pretrade_risk_checks.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
