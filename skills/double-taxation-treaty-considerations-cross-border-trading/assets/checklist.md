# Pre-Flight Checklist

## Rate data

- [ ] Is a treaty rate registered for every (residence country, source country, **income type**) triple in use — not just for dividends?
- [ ] Are statutory rates registered per source country rather than assumed to be 30%?
- [ ] Are all rates entered as decimal fractions (`0.15`), not percentages (`15`)?
- [ ] Has each registered rate been checked against the treaty article and any protocol or MLI amendment?

## Entitlement

- [ ] Is beneficial ownership established, and does the entity satisfy any limitation-on-benefits article?
- [ ] Are Tax Residency Certificates / Forms W-8BEN-E on file and unexpired — valid to the **last day of the third succeeding calendar year**, not three years from signature?
- [ ] Is there a process to report a change in circumstances within 30 days?

## Credit and leakage

- [ ] Is tax withheld above an available treaty rate excluded from the credit and routed to a source-country refund claim instead?
- [ ] Is the credit ceiling a real limitation figure, or is it the per-payment gross approximation (and is that understood)?
- [ ] For zero-tax residence entities, is source withholding recognised as a permanent cost rather than a recoverable credit?
- [ ] Is `non_creditable_wht_usd` monitored as real leakage?

## Derivatives

- [ ] Are US equity derivatives assessed for Section 871(m) dividend equivalents (delta-one in scope; covered non-delta-one from 1 January 2027)?

## Validation

- [ ] Do `REVIEW_REQUIRED` payments block downstream tax booking rather than defaulting to zero?
- [ ] Automated testing: `python -m unittest discover -s skills/double-taxation-treaty-considerations-cross-border-trading/scripts`
