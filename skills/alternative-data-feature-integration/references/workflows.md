# Workflows for Alternative Data Integration

## Feature Engineering Pipeline

1. **Vendor Ingestion**: Download alternative data files (CSV, JSON, Parquet) from the data vendor.
2. **Lag Auditing**: Explicitly identify the vendor's publication SLA (e.g., $T+2$ days).
3. **PIT Transformation**: Map all `event_dates` to `knowledge_dates` using `AltDataIntegrator.ingest_events()`.
4. **Simulation Alignment**: During backtesting, the simulation engine generates a list of exact trading timestamps. Pass these to `align_to_trading_schedule()` to generate the precise state of alternative data features at those exact moments in time.
5. **Machine Learning Inference**: Pass the aligned, lag-safe features into the predictive model.
