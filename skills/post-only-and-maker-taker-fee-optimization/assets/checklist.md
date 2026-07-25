# Pre-Flight / Sign-off Checklist — post-only-and-maker-taker-fee-optimization

Use this before considering the skill's implementation complete.

- [ ] **Fee Schedule Configuration:** Confirm Maker and Taker fee rates are configured for target exchange.
- [ ] **Spread-Crossing Detection:** Confirm limit prices that cross bid/ask spread are detected prior to submission.
- [ ] **Passive Repricing Option:** Confirm crossing orders reprice to passive side of order book when enabled.
- [ ] **Post-Only Payload Injection:** Confirm exchange-specific post-only flags are attached to order payload.
- [ ] **Automated Testing:** Run `python scripts/test_fee_optimizer.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
