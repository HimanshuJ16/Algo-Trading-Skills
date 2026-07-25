# Pre-Flight / Sign-off Checklist — broker-margin-interest-accrual-tracking

Use this before considering the skill's implementation complete.

- [ ] **Rate Tier Registration:** Confirm APR rate tiers are configured per broker schedule.
- [ ] **Debit Balance Calculation:** Confirm cash debit balance and short market value are aggregated.
- [ ] **Weekend Compounding:** Confirm Friday holdings accrue 3 days of margin interest.
- [ ] **Net P&L Deduction:** Confirm total accrued interest is deducted from gross P&L.
- [ ] **Automated Testing:** Run `python scripts/test_margin_interest.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
