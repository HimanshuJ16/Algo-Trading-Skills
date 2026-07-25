# Real-Time Architecture Standards — graceful-shutdown-draining-in-flight-ticks

| Shutdown Trigger | Ingress Policy | Drain Timeout | Exit Code |
|---|---|---|---|
| `SIGTERM` (K8s/Docker) | Reject new ticks | 5.0 seconds | 0 (Clean) |
| `SIGINT` (Ctrl+C) | Reject new ticks | 5.0 seconds | 0 (Clean) |
| Max Drain Timeout Breach | Stop drain loop | Forced exit | 0 / 1 (Warn) |

## Category

`real-time-architecture` — see top-level `mappings/` directory.
