# Standards for Kraken WS v2 API

| Metric | Engineering Standard |
|---|---|
| REST HMAC Algorithm | HMAC-SHA512 with Base64 decoded secret MUST be used for REST token retrieval. |
| WS Endpoint | Kraken WS v2 MUST connect to `wss://ws-auth.kraken.com/v2` for private channels. |
| Token Refresh | Token MUST be refreshed before 15-minute expiry (recommended at 12 minutes). |
