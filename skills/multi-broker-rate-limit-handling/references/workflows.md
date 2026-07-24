# Deep Workflow Reference — multi-broker-rate-limit-handling

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Per-Broker, Per-Endpoint Token Buckets:**
   - Initialize distinct `TokenBucket` instances per broker and endpoint category via `MultiBrokerRateLimiter.register_endpoint_bucket()` (e.g. `fyers:order`, `fyers:quote`).

2. **Priority Tier Classification:**
   - **Tier 0 (`TIER_0_KILL`):** Risk breaches, kill switches, order cancellations. **Bypasses** standard queue delay.
   - **Tier 1 (`TIER_1_ORDER`):** New order routing and order modifications.
   - **Tier 2 (`TIER_2_STATUS`):** Order status polling and margin/account checks.
   - **Tier 3 (`TIER_3_DATA`):** Historical data backfills and ticker quote polling.

3. **Jittered Exponential Backoff Processor:**
   - Catch HTTP 429 Rate Limit exceptions on Tiers 1–3 and apply exponential backoff ($T = 0.2 \times 2^{\text{attempt}} + \text{jitter}$).

4. **Tier 0 Risk Escalation Alerting:**
   - If a Tier 0 emergency call encounters a depleted token bucket or HTTP 429 response, trigger instant high-priority alerts (`alert_fn`) for operator intervention.

## Failure Modes Observed in Production

- **Conflated Global Rate Limiters:** Sharing a single token bucket across quote polling and order execution, allowing market data bursts to exhaust order placement bandwidth.
- **Silent Tier 0 Backoffs:** Retrying emergency kill-switch orders with long backoff delays during a flash crash.
- **Shared API Credential Collisions:** Running multiple bot processes under the same broker API key without shared rate budget accounting.
- **Uncapped Backoff Delays:** Allowing order status polling retries to back off for minutes, breaking execution reconciliation pipelines.

## Production Implementation Reference

- Reference code: `scripts/rate_limiter.py` (`MultiBrokerRateLimiter`, `TokenBucket`, `CallTier`, `RateLimiterMetrics`).
- Automated unit tests: `scripts/test_rate_limiter.py`.
