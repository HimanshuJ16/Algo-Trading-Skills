# Checklist — adjusted-vs-unadjusted-price-series-pitfalls

- [ ] Ensure historical Price AND Volume are passed to the Auditor.
- [ ] Verify that Split Adjustments correctly scale Volume inversely to Price.
- [ ] Confirm that Dividends are not backward-adjusting prices (to avoid look-ahead bias).
- [ ] Universe consistency validation enforced (all unadjusted or all properly split-adjusted).
- [ ] Tests pass: `python scripts/test_price_adjustment_auditor.py`

## Sign-off
- Reviewed by: ___________________________
- Date: ___________________________
