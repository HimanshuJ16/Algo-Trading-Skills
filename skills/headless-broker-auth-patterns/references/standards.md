# Broker & Framework Coverage — headless-broker-auth-patterns

| Broker / Framework | Archetype & Authentication Standard |
|---|---|
| Fyers API v3 | Archetype A (REST API + TOTP + SHA-256 Checksum) |
| Zerodha Kite Connect | Archetype A (REST API + TOTP + SHA-256 Checksum) |
| ICICI Breeze API | Archetype B (Headless Browser Automation + Redirect Token Interception) |
| Upstox API v2 | Archetype A (REST API + TOTP OAuth Code Exchange) |
| Alpaca Trading API | Archetype A (API Key + Secret Header Auth) |
| IBKR TWS/Gateway API | Archetype A (Local Client Portal / IB Gateway REST Bridge) |

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Checksum Formula Reference

- **Fyers:** `sha256(app_id + ":" + secret_key)` — `auth_code` is sent as a separate
  `code` field in the token-exchange payload, never included in the hash.
- **Zerodha:** `sha256(api_key + request_token + api_secret)` — concatenated with no
  separators.

Both verified against current broker-side behavior/source as of this revision; broker
APIs change without notice, so re-verify against current docs before relying on this.

## Regulatory & Operational Notes

Intersects with 2FA mandatory authentication guidelines issued by financial regulators (SEBI 2FA mandating TOTP/OTP, FINRA multi-factor authentication rules).
