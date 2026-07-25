# Deep Workflow Reference — feed-handler-canary-deployment

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Configure Canary Allocation**:
   - Set canary percentage (e.g. $10\%$) or explicit symbol whitelist (`AAPL`, `MSFT`).

2. **Route Symbols & Execute Comparative Audit**:
   - Determine routing decision for symbol ($V_{\text{canary}}$ vs $V_{\text{stable}}$).
   - Audit tick price output diffs ($|P_{\text{canary}} - P_{\text{stable}}| / P < 0.001$).

3. **Monitor Error Rates**:
   - Track price mismatch count and feed handler exception logs.

4. **Auto-Rollback Execution**:
   - If error rate exceeds safety limit (e.g. $1.0\%$), trip auto-rollback breaker and revert 100% symbol traffic to $V_{\text{stable}}$.

## Production Implementation Reference

- Reference code: `scripts/canary_router.py` (`FeedHandlerCanaryRouter`, `CanaryStatus`, `CanaryRoutingDecision`).
- Automated unit tests: `scripts/test_canary_router.py`.
