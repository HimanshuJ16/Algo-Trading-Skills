# Pre-Flight / Sign-off Checklist — crypto-exchange-api-integration

Use this before considering the skill's implementation complete.

- [ ] **Weight-Based Rate Limiting:** Confirm endpoint calls track weight units rather than simple request counts and synchronize with exchange response headers (`x-mbx-used-weight-1m`).
- [ ] **Namespace Separation:** Confirm Spot, Futures, and Margin API calls use separated rate limit pools via `CryptoExchangeRateLimiter`.
- [ ] **Self-Trade Prevention (STP):** Confirm order payloads specify explicit `stp_mode` matching strategy design.
- [ ] **24/7 Rolling P&L Reset:** Confirm risk controls utilize `Rolling24hPnLTracker` for 24-hour sliding reset boundaries.
- [ ] **Maintenance Backoff:** Confirm websocket reconnect logic handles extended 502/503 maintenance windows with exponential jittered backoff.
- [ ] **Automated Testing:** Run `python scripts/test_weight_rate_limiter.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
