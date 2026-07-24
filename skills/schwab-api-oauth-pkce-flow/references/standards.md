# Broker & Framework Coverage — schwab-api-oauth-pkce-flow

| Broker / API Endpoint | OAuth Method | Access Token Expiry | Refresh Token Lifetime |
|---|---|---|---|
| Charles Schwab Developer API | OAuth 2.0 PKCE (RFC 7636) | 30 Minutes | 7 Days (Hard expiry) |
| E*TRADE API v1 | OAuth 1.0a (RSA-SHA1) | Midnight EST | Daily |
| Tradier Brokerage API | OAuth 2.0 Bearer | Long-lived / Custom | Configurable |

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with RFC 7636 (Proof Key for Code Exchange by OAuth Public Clients) and FINRA/SEC credential security guidelines.
