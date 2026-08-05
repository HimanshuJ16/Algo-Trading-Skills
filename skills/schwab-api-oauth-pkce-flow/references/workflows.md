# Workflows for Schwab API OAuth PKCE Flow

1. **PKCE Key Pair Generation**:
   - Generate `code_verifier` and derive SHA-256 `code_challenge` (S256).
2. **User Authorization & Code Capture**:
   - Direct user to authorization URL; capture `code` from redirect URI callback.
3. **Token Exchange & Atomic Persistence**:
   - Submit `code` and `code_verifier` to token endpoint; save token JSON atomically.
4. **Lifecycle Monitoring & Token Renewal**:
   - Refresh 30-minute access token preemptively; warn when 7-day refresh token approaches expiry.
