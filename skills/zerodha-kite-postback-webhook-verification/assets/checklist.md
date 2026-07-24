# Pre-Flight / Sign-off Checklist — zerodha-kite-postback-webhook-verification

Use this before considering the skill's implementation complete.

- [ ] **Signature Verification:** Confirm `compute_checksum()` matches `sha256(order_id + timestamp + api_secret)` and uses `hmac.compare_digest()`.
- [ ] **Replay Attack Defense:** Confirm `verify_timestamp()` rejects postbacks older than 300 seconds.
- [ ] **Idempotent Event Handling:** Confirm duplicate postbacks are identified and prevented from mutating order state twice.
- [ ] **Security Alarm Logging:** Confirm invalid checksum signatures trigger security alerts.
- [ ] **Automated Testing:** Run `python scripts/test_postback_verifier.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
