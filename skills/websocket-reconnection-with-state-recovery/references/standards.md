# Real-Time Architecture Standards — websocket-reconnection-with-state-recovery

| Parameter | Default Specification | Description |
|---|---|---|
| Base Backoff Delay | 1.0 second | Initial retry delay |
| Max Backoff Delay | 30.0 seconds | Ceiling for exponential retry delay |
| Jitter Factor | 0.5 (Full Jitter) | Randomized noise multiplier to prevent thundering herds |
| Sequence Gap Action | REST Gap Fetch | Query missing sequence range before resuming WS feed |

## Category

`real-time-architecture` — see top-level `mappings/` directory.
