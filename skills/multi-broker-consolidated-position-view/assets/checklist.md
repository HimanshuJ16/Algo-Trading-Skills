# Pre-Flight / Sign-off Checklist — multi-broker-consolidated-position-view

Use this before considering the skill's implementation complete.

- [ ] **Symbol Translation Mapping:** Confirm broker-specific tickers map to canonical symbols.
- [ ] **FX Conversion:** Confirm non-USD currencies convert accurately to base currency USD.
- [ ] **Net & Gross Accounting:** Confirm net quantity and gross quantity calculate correctly.
- [ ] **Reconciliation Audit:** Confirm drift between internal target ledger and broker holdings is flagged.
- [ ] **Automated Testing:** Run `python scripts/test_consolidated_ledger.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
