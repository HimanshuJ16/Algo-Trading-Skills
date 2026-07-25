# Real-Time Architecture Standards — adaptive-batch-size-tuning-under-load

| Load Level | Queue Fill Ratio | Batch Size Action | Flush Timeout Action |
|---|---|---|---|
| High Load | $> 70\%$ | Multiply by 1.5x (up to $B_{\text{max}}$) | Reduce by 20% |
| Low Load | $< 10\%$ | Divide by 1.2x (down to $B_{\text{min}}$) | Increase by 20% |
| DB Latency Spike | Latency $> 50\text{ms}$ | Multiply by 0.8x (Throttle) | Unchanged |

## Category

`real-time-architecture` — see top-level `mappings/` directory.
