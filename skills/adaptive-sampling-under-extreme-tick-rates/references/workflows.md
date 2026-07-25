# Deep Workflow Reference — adaptive-sampling-under-extreme-tick-rates

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Monitor Rolling Tick Frequency**:
   - Compute 1-second rolling tick arrival frequency $F_t$ per symbol.

2. **Evaluate Sampling Mode**:
   - $F_t \le F_{\text{target}}$: `PASSTHROUGH` mode ($100\%$ ticks emitted).
   - $F_t > F_{\text{target}}$: Set sampling factor $k = \lceil F_t / F_{\text{target}} \rceil$ (emit 1 out of every $k$ ticks).

3. **Accumulate Skipped Volume & VWAP**:
   - Accumulate volume $V_{\text{skipped}}$ of non-emitted ticks.
   - Attach $V_{\text{total}} = V_{\text{current}} + V_{\text{skipped}}$ on emitted sampled ticks.

4. **Flush Residual Volume**:
   - Flush remaining accumulated volume upon stream completion or quiet period.

## Production Implementation Reference

- Reference code: `scripts/tick_sampler.py` (`AdaptiveTickSamplerEngine`, `SamplingMode`, `SampledTick`).
- Automated unit tests: `scripts/test_tick_sampler.py`.
