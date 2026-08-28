# Pre-Flight Checklist

## Capture
- [ ] Were the send and receive timestamps read from **clocks synchronised to each other**, not from a monotonic clock on one host?
- [ ] Is the combined divergence of those two clocks from UTC known, and is it **smaller than the jitter budget** being enforced?
- [ ] Are the timestamps taken at the NIC (`SO_TIMESTAMPING`) rather than after the kernel receive path, so kernel queueing is not being counted as network jitter?
- [ ] Was the capture taken from a **single** tap, so no packet is observed twice?
- [ ] Does the captured flow actually carry the traffic the strategy depends on?

## Sample sufficiency
- [ ] Does the window hold at least **100 packets**, so P99 resolves to something other than the maximum?
- [ ] Is `JITTER_INSUFFICIENT_SAMPLES` treated as "not measured" rather than as a pass?
- [ ] Is `is_p99_resolvable` checked before quoting the tail figure anywhere?

## Input integrity
- [ ] Are NaN/Inf timestamps rejected rather than filtered?
- [ ] Is a negative one-way delay treated as invalidating the **whole window**, not just that packet?
- [ ] Were any duplicate-`packet_id` warnings investigated before the percentiles were used?

## Percentiles
- [ ] Are percentiles nearest rank (`ceil(p/100 × N)`), so every reported delay was actually observed?
- [ ] If migrating from v1.0.0, is it understood that **every percentile moves down one rank** — and that the old P50 over the repo's own fixture was 9 ms where the median is 1 ms?
- [ ] Are budget comparisons made on unrounded values?

## Variation metrics
- [ ] Are **all three** of σ, IQR and RFC 5481 PDV read, rather than σ alone?
- [ ] If σ is large while the IQR is near zero, has this been diagnosed as a **stall** rather than as link jitter?
- [ ] Is it clear which definition of "jitter" any external budget was written against — σ, RFC 5481 PDV, RFC 5481 IPDV, or RFC 3550 interarrival jitter?

## Degradation model
- [ ] Was `jitter_penalty_coeff` (γ) **regressed from this strategy's own realized Sharpe**, not inherited from another strategy, venue, instrument or this skill's defaults?
- [ ] Is the measured jitter inside the σ range γ was fitted over, rather than an extrapolation?
- [ ] Is it understood that **no regulator, exchange, vendor or paper publishes a jitter-to-Sharpe coefficient**?
- [ ] Is `simulated_degraded_sharpe` reported downstream as a **model output** (with `sharpe_model`), never as measured PnL?

## Budgets
- [ ] Are `base_sharpe`, `target_sharpe_min` and `max_acceptable_jitter_ms` calibrated for this strategy, not left at the shipped placeholders?
- [ ] For a strategy competing in latency races, are the budgets in **microseconds**? A 3 ms ceiling is three orders of magnitude past the 5–10 µs modal race margin.
- [ ] Is `max_p99_latency_ms` set, given that the damage arrives through the tail rather than through σ?

## Scope
- [ ] Is this audit understood as **windowed and after-the-fact**, with live halting delegated to a dedicated risk control?
- [ ] Is it clear that this module reads no clock and measures nothing itself?
