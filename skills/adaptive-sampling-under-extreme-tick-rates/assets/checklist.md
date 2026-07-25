# Pre-Flight / Sign-off Checklist — adaptive-sampling-under-extreme-tick-rates

Use this before considering the skill's implementation complete.

- [ ] **Rolling Frequency Monitor:** Confirm 1-second rolling tick frequency $F_t$ is tracked per symbol.
- [ ] **Dynamic Sampling Factor:** Confirm sampling ratio $k = \lceil F_t / F_{\text{target}} \rceil$ scales dynamically.
- [ ] **Volume Preservation:** Confirm volume of skipped ticks is accumulated and attached to emitted ticks.
- [ ] **Residual Volume Flush:** Confirm un-emitted residual volume is flushed cleanly on stream end.
- [ ] **Automated Testing:** Run `python scripts/test_tick_sampler.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
