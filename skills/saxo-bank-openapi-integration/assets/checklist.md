# Pre-Flight / Sign-off Checklist — saxo-bank-openapi-integration

Use this before considering the skill's implementation complete.

- [ ] **OAuth2 Bearer Auth:** Confirm valid Bearer token in request headers.
- [ ] **UIC Instrument Search:** Confirm search resolves symbol keywords to numeric `Uic`.
- [ ] **Multi-Asset Order Routing:** Confirm `AssetType` and `AccountKey` formatted properly in `/trade/v1/orders`.
- [ ] **Portfolio Position Tracking:** Confirm multi-asset position snapshot returned from `/port/v1/positions`.
- [ ] **Automated Testing:** Run `python scripts/test_saxo_client.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
