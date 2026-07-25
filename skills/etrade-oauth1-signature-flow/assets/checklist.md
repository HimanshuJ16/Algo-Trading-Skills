# Pre-Flight Checklist — etrade-oauth1-signature-flow

- [ ] Consumer key and secret stored securely in environment/vault.
- [ ] OAuth1 three-legged authorization flow tested and verified.
- [ ] HMAC-SHA1 signature base string construction matches RFC 5849 specification.
- [ ] Per-request Authorization header generation with unique nonce and timestamp.
- [ ] Daily token renewal procedure configured before market open.
- [ ] Run `python scripts/test_etrade_auth.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
