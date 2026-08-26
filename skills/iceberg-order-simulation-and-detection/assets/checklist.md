# Pre-Flight Checklist — Iceberg Detection Screen

## Data plumbing
- [ ] Are trade prints and Level 2 depth ingested from the **same clock**, with nanosecond timestamps?
- [ ] Is `tick_size` set, so prices are binned to integer ticks rather than used as raw float keys?
- [ ] Are non-finite prices rejected, and negative prices *accepted* (WTI, spreads)?
- [ ] Is a stable `trade_id` present on every print, and are already-seen IDs suppressed across reconnects?
- [ ] Are depth snapshots older than the last processed for a level dropped rather than applied?

## Detection logic
- [ ] Does $V_{\text{cum}}$ accumulate **only** volume whose aggressor consumes the tracked side (SELL→BID, BUY→ASK)?
- [ ] Is contra-side volume recorded separately instead of inflating $\hat{Q}_{\text{hidden}}$?
- [ ] Is the volume ratio threshold ($V_{\text{cum}} \ge$ `min_volume_ratio` $\times Q_0$) enforced **together with** the refill minimum, so a single sweep cannot flag?
- [ ] Are levels with $Q_0 \le 0$ skipped rather than divided by a substituted 1?
- [ ] Is the signal classified from the tracked **book side**, never from an individual print's aggressor side?
- [ ] Are levels re-baselined on a side flip and after dwelling empty, and is a momentary zero between refills still counted as a refill?
- [ ] Is the tracker table bounded (LRU), so a long session cannot leak memory?

## Calibration
- [ ] Have `min_volume_ratio` and `min_refill_count` been calibrated for **this** instrument and liquidity tier, rather than inherited from the defaults?
- [ ] Is `level_reset_dwell_nanos` matched to the feed's latency profile?

## Interpretation and governance
- [ ] Is every output treated as a **candidate**, given that price-level depth cannot attribute a refill to an order?
- [ ] Has MBO / L3 data been ruled out as unavailable before relying on this screen?
- [ ] Is `confidence_score` documented downstream as an uncalibrated ordinal heuristic, never as a probability, and never used to size a position?
- [ ] Is $\hat{Q}_{\text{hidden}}$ consumed as a **lower bound**, conditional on all refills coming from one resting order?
- [ ] Is it understood that a positive screen is **not** evidence of spoofing, layering, or abuse, and is not a surveillance artifact?
