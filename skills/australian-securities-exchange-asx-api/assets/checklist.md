# Checklist for ASX Integration

- [ ] Confirm `AsxProtocol.OUCH` and `AsxProtocol.ITCH` are strictly blocked if `is_alc_colocated` is false.
- [ ] Confirm FIX 5.0 SP2 is used for standard routing.
- [ ] Verify connection endpoints point to the CDE (Customer Development Environment) prior to production release.
- [ ] Run test suite: `python scripts/test_australian_securities_exchange_asx_api.py`.

## Sign-off
- Connectivity Engineer: ___________________________
- Date: ___________________________
