# Pre-Flight Checklist

- [ ] Are joins performed using SEC EDGAR `filing_date` (not `period_end_date`)?
- [ ] Are historical restatements isolated and queryable by date?
- [ ] Is restatement lookahead leakage audited?
- [ ] Are unfiled/future earnings reports excluded from historical query dates?
