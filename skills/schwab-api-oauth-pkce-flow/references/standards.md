# Standards for Schwab API OAuth PKCE Flow

| Parameter | Mandatory Value / Standard |
|---|---|
| PKCE RFC 7636 | `code_verifier` 64 characters, `code_challenge_method=S256`. |
| Access Token Lifespan | 30 minutes (refresh buffer: 300 seconds). |
| Refresh Token Lifespan | 7 days (warning trigger: 24 hours prior to expiry). |
