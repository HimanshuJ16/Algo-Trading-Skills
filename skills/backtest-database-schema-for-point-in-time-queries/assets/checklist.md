# Pre-Flight / Sign-off Checklist — backtest-database-schema-for-point-in-time-queries

- [ ] `known_at` column included in all fundamental / reference tables.
- [ ] As-of query filtering enforced (`known_at <= as_of_date`).
- [ ] Restated earnings/metrics insert new rows with updated `known_at`.
- [ ] Automated Testing: Run `python scripts/test_pit_schema.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
