# Real-Time Architecture Standards — adaptive-sampling-under-extreme-tick-rates

| Tick Frequency ($F_t$) | Sampling Mode | Sampling Factor ($k$) | Volume Action |
|---|---|---|---|
| $F_t \le 5,000$ ticks/sec | `PASSTHROUGH` | $k = 1$ | Direct passthrough |
| $F_t > 5,000$ ticks/sec | `SYSTEMATIC_SAMPLING` | $k = \lceil F_t / 5000 \rceil$ | Accumulate skipped volume |

## Category

`real-time-architecture` — see top-level `mappings/` directory.
