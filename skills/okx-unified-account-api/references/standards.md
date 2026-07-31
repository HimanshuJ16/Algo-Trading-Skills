# Standards for OKX Unified Account API

| Metric | Engineering Standard |
|---|---|
| Signature Algorithm | HMAC-SHA256 Base64 string. |
| Timestamp Format | ISO 8601 UTC `YYYY-MM-DDTHH:MM:SS.sssZ`. |
| Liquidation Threshold | $mrr \le 100\%$ triggers margin call alert. |
