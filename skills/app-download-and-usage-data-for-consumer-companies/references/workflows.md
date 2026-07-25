# Workflows for App Usage Alternative Data

## Quantitative Pipeline

1. **Vendor Ingestion**: Download daily panel data from alt-data providers (e.g., Apptopia, SensorTower) containing Downloads, DAU, and MAU estimates per ticker.
2. **Point-In-Time Alignment**: Pass the data through the `alternative-data-feature-integration` skill to shift the event dates by the vendor's publication lag, ensuring zero look-ahead bias.
3. **Signal Generation**: Process the PIT-aligned data through `AppUsageSignalEngine.process()` to calculate fundamental engagement features.
4. **Portfolio Construction**: 
   - Overweight equities flagged as `is_world_class`.
   - Short/Underweight equities flagged with `churn_risk_warning`.