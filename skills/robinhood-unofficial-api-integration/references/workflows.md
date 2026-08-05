# Workflows for Robinhood Unofficial API Integration

1. **OAuth2 Device Token Authentication**:
   - Authenticate via `/oauth2/token/` with persistent `device_token`.
2. **MFA Handling**:
   - Handle 400 `mfa_required` by submitting SMS/app MFA code.
3. **Order Placement**:
   - Submit market or limit orders via `/orders/`.
4. **Position Polling**:
   - Poll `/positions/` and parse non-zero positions.
