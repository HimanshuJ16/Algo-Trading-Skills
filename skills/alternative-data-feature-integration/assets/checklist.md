# Checklist for Alternative Data Integration

- [ ] Verify the exact publication lag for the data source directly with the vendor.
- [ ] Confirm `knowledge_timestamp` is calculated as `event_timestamp + publication_lag`.
- [ ] Ensure that missing data alignment (forward filling) is executed *as-of* the `knowledge_timestamp`.
- [ ] Run test suite: `python scripts/test_alt_data_integrator.py`.

## Sign-off
- Lead Quantitative Researcher: ___________________________
- Date: ___________________________
