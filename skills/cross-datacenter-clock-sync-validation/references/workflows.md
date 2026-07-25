# Deep Workflow Reference — cross-datacenter-clock-sync-validation

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Probe Multi-Region Datacenter Clocks**:
   - Query PTP/NTP daemon metrics (`chronyc tracking`) or issue high-precision UDP NTP/PTP offset probes across region nodes.

2. **Compute Pairwise Clock Drift**:
   - Calculate drift $\Delta \tau_{AB} = |(T_A + \text{offset}_A) - (T_B + \text{offset}_B)|$.

3. **Evaluate Health Tier & Veto**:
   - $\Delta \tau_{AB} \le 1.0\text{ms}$: Permitted (`ACCEPTABLE`).
   - $\Delta \tau_{AB} > 1.0\text{ms}$: Veto cross-region arbitration (`CLOCK_UNSYNC_VETO`).

## Production Implementation Reference

- Reference code: `scripts/clock_sync_validator.py` (`CrossDatacenterClockSyncValidator`, `ClockSyncHealth`, `DatacenterClockProbe`).
- Automated unit tests: `scripts/test_clock_sync_validator.py`.
