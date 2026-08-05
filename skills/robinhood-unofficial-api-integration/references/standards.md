# Standards for Robinhood Unofficial API Integration

| Metric | Engineering Standard |
|---|---|
| Device Token | Device UUID MUST be cached and reused across login sessions. |
| Polling Frequency | Position polling MUST NOT exceed 1 request per 2 seconds. |
| MFA Handling | System MUST support programmatic MFA code injection. |
