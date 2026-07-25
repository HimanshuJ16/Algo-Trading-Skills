# Deep Workflow Reference — adaptive-batch-size-tuning-under-load

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Monitor Queue Pressure**:
   - Compute fill ratio $R = \frac{\text{queue\_len}}{\text{capacity}}$.

2. **Adapt Batch Parameters**:
   - $R > 0.70$: Expand batch size $B_{t+1} = \min(B_{\text{max}}, \lfloor B_t \times 1.5 \rfloor)$ and reduce flush timeout.
   - $R < 0.10$: Shrink batch size $B_{t+1} = \max(B_{\text{min}}, \lfloor B_t / 1.2 \rfloor)$ to lower latency.

3. **Latency Feedback Throttling**:
   - If downstream DB write latency $> 50\text{ms}$, throttle batch size $B_{t+1} = \lfloor B_t \times 0.8 \rfloor$ to prevent lock contention.

4. **Flush Batch**:
   - Flush when accumulated records $\ge B_t$ or elapsed time $\ge T_{\text{flush}}$.

## Production Implementation Reference

- Reference code: `scripts/batch_tuner.py` (`AdaptiveBatchTunerEngine`, `BatchTunerStatus`).
- Automated unit tests: `scripts/test_batch_tuner.py`.
