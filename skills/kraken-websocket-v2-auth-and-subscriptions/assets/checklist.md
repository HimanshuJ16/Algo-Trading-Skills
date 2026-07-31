# Pre-Flight Checklist

- [ ] Is HMAC-SHA512 signature verified for `/0/private/GetWebSocketsToken`?
- [ ] Is `token` included in `params` object for private WS v2 channels (`executions`)?
- [ ] Is token age monitored for auto-refresh prior to 15-minute expiry?
- [ ] Is connection pointed to `wss://ws-auth.kraken.com/v2`?
