# Pre-Flight / Sign-off Checklist — multi-currency-pnl-and-fx-conversion

Use this before considering the skill's implementation complete.

- [ ] **Currency Tagging:** Confirm all trade and PnL records store native currency tags (`CurrencyAmount`).
- [ ] **Point-In-Time FX Rate Lookup:** Confirm historical conversions query timestamped FX rates via `PointInTimeFXResolver`.
- [ ] **PnL Decomposition:** Confirm `calculate_decomposed_pnl()` separates native price return PnL from FX translation gain/loss.
- [ ] **Currency Decimal Precision:** Confirm rounding rules respect currency conventions via `round_amount()`.
- [ ] **Automated Testing:** Run `python scripts/test_fx_convert.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
