# Standards Reference — robinhood-unofficial-api-integration

| Parameter | Value | Description |
|---|---|---|
| Base URL | `https://api.robinhood.com` | Unofficial API base |
| Auth Endpoint | `/oauth2/token/` | Device-token + MFA auth |
| Token Lifetime | ~86400s (24h) | Bearer token expiry |
| API Status | **Unofficial** | No SLA, may break without notice |

## Category

`broker-integration`

## Risk Warning

Unofficial API usage may violate Robinhood's Terms of Service and result in account suspension.
