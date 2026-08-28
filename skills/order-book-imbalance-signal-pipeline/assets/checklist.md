# Pre-Flight Checklist — Order Book Imbalance Signal Pipeline

## Configuration
- [ ] Is `imbalance_threshold` inside $(0.0, 1.0]$ — never `0.0`, which fires `HIGH_BUY_PRESSURE` on every balanced book?
- [ ] Was the threshold **calibrated on this instrument**, rather than copied from another tick-size regime or from a paper?
- [ ] If the threshold came from published research, was it converted from the $[0, 1]$ convention ($I = 2I' - 1$) before use?
- [ ] Is `depth_levels` fixed for the run, so the signal means the same thing on every tick?
- [ ] Was `strict` chosen deliberately — raise on a validated production feed, count and continue on an imperfect replay?
- [ ] Is `allow_locked_book` set only for venues where a locked market is legal?

## Input integrity
- [ ] Are `NaN` and `inf` volumes rejected *before* any arithmetic (a `NaN` survives a `total <= 0` guard and classifies as `NEUTRAL`)?
- [ ] Are negative volumes rejected, rather than allowed to drive $|I|$ outside $[-1, +1]$?
- [ ] Are zero and negative prices rejected, rather than yielding a weighted mid of `0.0`?
- [ ] Is a crossed book ($P_{\text{bid}} > P_{\text{ask}}$) refused rather than measured?
- [ ] Is a non-integer or `NaN` `timestamp_ns` refused, rather than accepted and left to disable the ordering guard?
- [ ] Is an out-of-order `timestamp_ns` refused, and does a rejected update leave the per-symbol clock unmoved?
- [ ] Do `bid_depth` / `ask_depth` **exclude** level 1, so the best queue is not counted twice?
- [ ] Is an update with less depth than `depth_levels` rejected rather than silently downgraded?

## Signal semantics
- [ ] Is $I$ computed as $(V_{\text{bid}} - V_{\text{ask}}) / (V_{\text{bid}} + V_{\text{ask}})$ and confirmed to stay in $[-1, +1]$?
- [ ] Does a heavy bid queue pull the weighted mid **toward the ask** (a sign flip here inverts every signal)?
- [ ] Does $W = M + I_{\text{top}} \cdot s / 2$ hold on accepted books?
- [ ] Is weighted-mid divergence from the mid kept **out** of the signal set, given it equals $I \cdot s / 2$ identically?
- [ ] Is the reported imbalance the same float that was classified — no rounding on one side only?
- [ ] Is an empty book reported as `UNRELIABLE` rather than `NEUTRAL`?
- [ ] Do `UNRELIABLE` results carry `None` numerics, never `0.0` or `NaN`?

## Dispatch and operations
- [ ] Does a raising strategy callback leave the feed loop running, with the failure counted and logged?
- [ ] Is the callback free of blocking work — no DB write, no JSON serialisation, no synchronous REST call?
- [ ] Is `generate_report().status` checked, and treated as `OBI_PIPELINE_DEGRADED` blocking any aggregate statistic?
- [ ] Is a persistence or quote-flicker filter applied before acting, given that displayed depth is what layering manipulates?
- [ ] Is the latency claim in any downstream document consistent with a measured figure, not with "sub-microsecond"?

## Testing
- [ ] `python -m unittest discover -s skills/order-book-imbalance-signal-pipeline/scripts` — 61/61 pass.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
