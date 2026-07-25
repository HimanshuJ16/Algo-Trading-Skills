# Deep Workflow Reference — point-in-time-database-for-ml-training-data

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Format Feature & Label Tables**: Include `symbol`, `timestamp`, `available_at`, and `value`.
2. **Execute As-Of Join**: Join feature records to label timestamps enforcing $T_{\text{feature.available\_at}} \le T_{\text{label.timestamp}}$.
3. **Audit Data Availability Gap**: Verify no feature row has `available_at > timestamp`.
4. **Generate Point-In-Time Dataset**: Output clean training matrix ready for model fitting.

## Production Implementation Reference

- Reference code: `scripts/pit_ml_database.py` (`PointInTimeMLDatabase`, `FeatureRecord`, `LabelRecord`, `PITJoinRow`).
- Automated unit tests: `scripts/test_pit_ml_database.py`.
