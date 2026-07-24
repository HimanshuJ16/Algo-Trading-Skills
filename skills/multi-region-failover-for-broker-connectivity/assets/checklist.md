# Pre-Flight Checklist — multi-region-failover-for-broker-connectivity

- [ ] Primary and backup endpoints registered in different regions.
- [ ] Health probing detects consecutive failures correctly.
- [ ] Automatic failover switches to healthy backup.
- [ ] Failback occurs after primary recovery and cooldown.
- [ ] Run `python scripts/test_region_failover.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
