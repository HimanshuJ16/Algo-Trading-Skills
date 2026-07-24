# Pre-Flight / Sign-off Checklist — tick-buffering-burst-handling

Use this before considering the skill's implementation complete.

- [ ] **Empirical Capacity Sizing:** Confirm buffer capacity is calculated using `calculate_empirical_capacity()` based on measured peak tick rates.
- [ ] **Bounded Memory Plateaus:** Confirm memory usage under sustained peak-rate replay plateaus rather than growing unbounded.
- [ ] **Structured Drop Audit Logging:** Confirm every overflow drop event records `TickDropRecord` containing timestamp and dropped tick details.
- [ ] **High-Water Mark Monitoring:** Confirm `get_occupancy_report()` tracks peak occupancy percentages.
- [ ] **Automated Testing:** Run `python scripts/test_burst_buffer.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
