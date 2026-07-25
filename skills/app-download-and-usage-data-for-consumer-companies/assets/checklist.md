# Checklist for App Usage Data Signals

- [ ] Confirm DAU/MAU ratios are calculated cleanly, capping anomalies at 100%.
- [ ] Confirm the engine identifies high-churn "leaky buckets" by comparing download velocity to stickiness.
- [ ] Run test suite: `python scripts/test_app_download_and_usage_data_for_consumer_companies.py`.

## Sign-off
- Quantitative Researcher: ___________________________
- Date: ___________________________