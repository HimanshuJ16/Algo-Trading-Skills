# Pre-Flight Checklist: Borrow Cost Modeling

- [ ] Ensure GC rate matches broker's fee schedule.
- [ ] Confirm utilization data is point-in-time and avoids look-ahead bias.
- [ ] Verify that HTB rates are applied daily (divided by 365 or 360).
- [ ] Check that short sale requests exceeding available shares are completely rejected or partially filled.
- [ ] Validate that extreme HTB rates correctly draw down portfolio equity in backtest.
