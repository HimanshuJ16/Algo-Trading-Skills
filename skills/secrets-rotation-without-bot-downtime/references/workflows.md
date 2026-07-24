# Deep Workflow Reference — secrets-rotation-without-bot-downtime

## Full Procedure

1. Generate new credentials at broker/secrets store.
2. Validate new credentials with a read-only API call.
3. Atomically hot-swap active credential reference.
4. Verify live traffic works with new credentials.
5. Revoke old credentials only after confirmation.
6. If validation fails, keep old credentials and alert.

## Production Implementation Reference

- Code: `scripts/secrets_rotator.py` (`SecretsRotator`).
- Tests: `scripts/test_secrets_rotator.py`.
