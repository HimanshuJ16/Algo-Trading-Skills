# Pre-Flight Checklist

- [ ] Has the vendor's data delivery SLA been verified?
- [ ] Is `default_lag_days` set equal to or greater than the maximum SLA delay?
- [ ] Are we using `publication_date` as the primary Point-in-Time filter when available?
- [ ] Does the `get_point_in_time_data` correctly return an empty DataFrame if no data has been published yet?
- [ ] Have we verified that the dataset does not overwrite historical values (revision bias)? If so, are we using daily snapshots?