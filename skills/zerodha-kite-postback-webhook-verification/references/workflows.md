# Deep Workflow Reference — zerodha-kite-postback-webhook-verification

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Extract Postback Signature & Payload:**
   - Intercept HTTP POST requests on the webhook receiver endpoint.
   - Extract `order_id`, `timestamp`, `checksum`, `status`, and execution parameters.

2. **Replay Attack & Timestamp Drift Validation:**
   - Compare postback timestamp against current UTC server time via `verify_timestamp()`.
   - Reject any postback older than 300 seconds to prevent replay of captured webhooks.

3. **Constant-Time SHA-256 Signature Verification:**
   - Calculate expected signature: `sha256(order_id + timestamp + api_secret)`.
   - Compare calculated checksum against received `checksum` using `hmac.compare_digest()` to prevent timing side-channel attacks.

4. **Idempotent Order Ledger Synchronization:**
   - Check composite key `order_id:status` against `processed_postbacks` set.
   - If un-processed, route verified order update to local order ledger (`order-placement-idempotency`).

5. **Security Alarm Logging:**
   - On checksum validation failure, log critical alert containing origin IP, timestamp, and payload to detect webhook injection attacks.

## Failure Modes Observed in Production

- **Unauthenticated Webhook Receiver:** Processing HTTP POST requests without signature verification, enabling order fill spoofing attacks.
- **Non-Constant Time Comparison:** Using standard string equality (`==`) for checksum comparison, exposing the endpoint to timing side-channel attacks.
- **Replay Attack Vulnerability:** Accepting old postbacks without timestamp drift validation.
- **Duplicate Order Ledger Mutations:** Applying duplicate postback HTTP retries to position state multiple times.

## Production Implementation Reference

- Reference code: `scripts/postback_verifier.py` (`KitePostbackVerifier`, `PostbackVerificationResult`).
- Automated unit tests: `scripts/test_postback_verifier.py`.
