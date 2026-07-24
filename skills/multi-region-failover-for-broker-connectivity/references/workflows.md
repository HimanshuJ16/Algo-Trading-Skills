# Deep Workflow Reference — multi-region-failover-for-broker-connectivity

## Full Procedure

1. Register primary and backup endpoints in different regions.
2. Continuously probe active endpoint health.
3. After N consecutive failures, trigger failover to healthy backup.
4. Monitor primary recovery; failback after cooldown period.

## Production Implementation Reference

- Code: `scripts/region_failover.py` (`RegionFailoverManager`).
- Tests: `scripts/test_region_failover.py`.
