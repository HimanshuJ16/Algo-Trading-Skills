# Broker & Framework Coverage — upstox-oauth-refresh-token-rotation

| Broker / API | Refresh Token Policy |
|---|---|
| Upstox API v2 | Single-use refresh token rotation; new refresh token issued per exchange call. |
| Zerodha Kite Connect v3 | Daily session token expiration (no refresh token). |
| Fyers API v3 | Daily token invalidation (requires daily re-auth). |
| Alpaca Trading API | OAuth2 refresh token rotation & long-lived API keys. |

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with OAuth 2.0 Security Best Current Practice (RFC 6749 / RFC 6819) and token storage security standards.
