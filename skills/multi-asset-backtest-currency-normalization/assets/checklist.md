# Pre-Flight / Sign-off Checklist — multi-asset-backtest-currency-normalization

Use this before considering the skill's implementation complete.

- [ ] **Multi-Currency Cash Balances:** Confirm cash balances are tracked in local currencies.
- [ ] **Point-in-Time FX Conversion:** Confirm FX rates are queried by date $T$.
- [ ] **Position Valuation Conversion:** Confirm local asset valuations are multiplied by target FX rates.
- [ ] **NAV Calculation:** Confirm total NAV is computed accurately in reporting base currency.
- [ ] **Automated Testing:** Run `python scripts/test_currency_normalizer.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
