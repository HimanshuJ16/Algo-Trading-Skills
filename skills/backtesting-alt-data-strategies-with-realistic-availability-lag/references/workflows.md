# Reporting Workflow

1. Ingest alternative data from the vendor into a DataFrame. Ensure it contains the date the event actually happened (`event_date`).
2. If the vendor supplies it, map the timestamp the data file was physically made available for download to `publication_date`.
3. If no `publication_date` is available, establish a conservative `default_lag_days` based on the vendor's SLA.
4. Pass the DataFrame to the `AltDataLagEnforcer`.
5. Inside your daily backtest loop, call `enforcer.get_point_in_time_data(current_simulated_date)`. Use only the returned data to generate trading signals for the next open.