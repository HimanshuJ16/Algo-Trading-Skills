# Workflows for OKX Unified Account API

1. **HMAC-SHA256 Signing**:
   - Build prehash `timestamp + method + requestPath + body`, sign with secret_key, and encode in Base64.
2. **Multi-Currency Equity Calculation**:
   - Compute USD equivalent adjusted equity using token discount factors.
3. **Margin Ratio Audit**:
   - Compute $mrr = (AdjEquity / MaintenanceMargin) \times 100\%$ and assign risk status.
4. **Audit Report Generation**:
   - Output structured OKX account report.
