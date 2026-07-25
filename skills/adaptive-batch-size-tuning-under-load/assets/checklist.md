# Pre-Flight / Sign-off Checklist — adaptive-batch-size-tuning-under-load

Use this before considering the skill's implementation complete.

- [ ] **Queue Fill Monitoring:** Confirm queue fill ratio $R$ is calculated dynamically.
- [ ] **Batch Size Expansion:** Confirm high fill ratio ($R > 70\%$) increases batch size toward $B_{\text{max}}$.
- [ ] **Batch Size Contraction:** Confirm low fill ratio ($R < 10\%$) decreases batch size toward $B_{\text{min}}$.
- [ ] **DB Latency Throttling:** Confirm latency spikes $> 50\text{ms}$ throttle batch size.
- [ ] **Automated Testing:** Run `python scripts/test_batch_tuner.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
