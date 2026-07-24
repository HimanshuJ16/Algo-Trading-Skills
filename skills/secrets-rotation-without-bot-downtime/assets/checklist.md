# Pre-Flight Checklist — secrets-rotation-without-bot-downtime

- [ ] New credentials validated before switchover.
- [ ] Hot-swap is atomic (no restart required).
- [ ] Fallback to old credentials works on validation failure.
- [ ] Old credentials revoked only after new ones confirmed.
- [ ] Run `python scripts/test_secrets_rotator.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
