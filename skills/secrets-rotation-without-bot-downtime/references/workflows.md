# Workflows for Secrets Rotation Without Bot Downtime

1. **Pre-Validation**:
   - Test new API key against broker endpoint prior to hot-swap.
2. **In-Memory Hot-Swap**:
   - Promote new key to active while storing previous key as fallback.
3. **Health Monitor & Emergency Rollback**:
   - Revert to previous key if HTTP 401/403 errors occur post-swap.
4. **Revocation**:
   - Mark previous key as invalid after successful rotation validation window.
