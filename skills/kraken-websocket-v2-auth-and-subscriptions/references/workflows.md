# Workflows for Kraken WS v2 Integration

1. **REST HMAC Signature Generation**:
   - Compute HMAC-SHA512 signature to fetch `GetWebSocketsToken`.
2. **v2 Subscription Frame Construction**:
   - Construct public (`book`/`ticker`) and authenticated (`executions`) subscription JSON.
3. **Token Refresh Lifecycle Management**:
   - Monitor 15-minute token age and refresh at 12 minutes.
4. **Audit Report Generation**:
   - Output structured Kraken WS v2 report.
