# Real-Time Architecture Standards — cross-datacenter-clock-sync-validation

| Health Tier | Max Inter-Region Drift ($\Delta \tau$) | Action |
|---|---|---|
| `EXCELLENT` | $\le 0.1\text{ms}$ | Full PTP cross-region arbitration |
| `ACCEPTABLE` | $\le 1.0\text{ms}$ | Permitted NTP arbitration |
| `DEGRADED` | $\le 5.0\text{ms}$ | Warning alert logged |
| `BREACH` | $> 5.0\text{ms}$ | `CLOCK_UNSYNC_VETO` blocks arbitration |

## Category

`real-time-architecture` — see top-level `mappings/` directory.
