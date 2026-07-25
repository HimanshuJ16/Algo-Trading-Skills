# Real-Time Architecture Standards — circuit-breaker-for-downstream-service-calls

| Parameter | Specification | Description |
|---|---|---|
| Failure Threshold | 3 consecutive errors | Cutoff for tripping breaker OPEN |
| Cooldown Period | 5.0 seconds | Window before testing HALF_OPEN recovery |
| Half-Open Trials | 2 successful calls | Required trial passes to reset CLOSED |
| Failure Action | Fail-fast or Fallback | Prevents main thread loop starvation |

## Category

`real-time-architecture` — see top-level `mappings/` directory.
