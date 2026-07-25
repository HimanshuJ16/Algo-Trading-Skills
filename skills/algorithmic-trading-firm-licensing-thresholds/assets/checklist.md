# Checklist for Firm Licensing Thresholds

- [ ] Ensure dark pool and ATS executions are accurately mapped to `off_exchange_volume_usd` for US compliance.
- [ ] Implement a rolling 1-second window to calculate accurate `peak_orders_per_second` for EU/IN compliance.
- [ ] Confirm no client funds are comingled with proprietary capital (triggers `has_customers`).
- [ ] Run test suite: `python scripts/test_algorithmic_trading_firm_licensing_thresholds.py`

## Sign-off
- Chief Compliance Officer (CCO): ___________________________
- Date: ___________________________