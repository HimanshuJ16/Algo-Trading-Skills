# Pre-Flight / Sign-off Checklist — alpaca-paper-live-key-separation

Use this before considering the skill's implementation complete.

- [ ] **Credential Validation:** Confirm `key_id` and `secret_key` are non-empty and non-whitespace.
- [ ] **Credential Prefix Verification:** Confirm key ID prefix (`PK...` for paper, `AK...` for live) matches configured environment.
- [ ] **Base URL Matching:** Confirm paper configuration uses `https://paper-api.alpaca.markets` and live uses `https://api.alpaca.markets`. Any other URL must be rejected.
- [ ] **Live Execution Gate:** Confirm live trading is blocked unless `ALLOW_LIVE_TRADING=true` environment flag is set.
- [ ] **Account API Probe:** Confirm `probe_account()` verifies `status` is `ACTIVE` and `is_paper` boolean against account data.
- [ ] **is_paper Fail-Safe:** Confirm a missing `is_paper` field in the API response is treated as live (`False`), not paper.
- [ ] **Config Immutability:** Confirm `AlpacaConfig` is frozen and cannot be mutated after validation.
- [ ] **Automated Testing:** Run `python scripts/test_alpaca_env_guard.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
