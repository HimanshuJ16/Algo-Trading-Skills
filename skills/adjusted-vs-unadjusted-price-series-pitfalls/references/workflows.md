# Deep Workflow Reference — adjusted-vs-unadjusted-price-series-pitfalls

## Full Procedure
1. **Detect Discontinuities**: Scan for overnight price jumps $\ge 30\%$.
2. **Classify Adjustment Type**: Map discontinuities to known corporate actions.
3. **Apply Backward Adjustment**: Divide pre-split prices by split ratio.
4. **Validate Universe Consistency**: Assert all symbols use same adjustment type.

## Production Implementation Reference
- Reference code: `scripts/price_adjustment_auditor.py`
- Automated unit tests: `scripts/test_price_adjustment_auditor.py`
