# Broker Integration Standards — questrade-api-rate-limit-and-account-types

| Parameter | Specification | Description |
|---|---|---|
| OAuth Endpoint | `https://login.questrade.com/oauth2/token` | Auth Token Refresh Server |
| API Server | Dynamic (`api01.iq.questrade.com`, etc.) | Per-session endpoint returned on OAuth exchange |
| Rate Limit Limit | 30 requests / second | Maximum allowed API calls before HTTP 429 |
| Account Types | `Margin`, `TFSA`, `RRSP`, `FHSA` | Canadian taxable and tax-advantaged account types |
| Registered Account Rules | No Short Selling / Naked Options | Restrictions on TFSA / RRSP tax-sheltered accounts |

## Category

`broker-integration` — see top-level `mappings/` directory.
