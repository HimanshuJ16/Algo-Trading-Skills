# Broker & Framework Coverage — token-lifecycle-live-probing

| Broker / Framework | Probe Endpoint & Token Expiry Behavior |
|---|---|
| Fyers API v3 | GET `/profile` — Token invalidates daily around 2:00-3:00 AM IST regardless of TTL. |
| Zerodha Kite Connect | GET `/user/margins` — Token invalidates daily at 6:00 AM IST. |
| ICICI Breeze API | GET `/customerdetails` — Session token valid for single trading day session. |

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with 2FA token security standards, session management compliance, and broker API authentication rules.
