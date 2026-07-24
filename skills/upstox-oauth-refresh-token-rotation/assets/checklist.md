# Pre-Flight / Sign-off Checklist — upstox-oauth-refresh-token-rotation

Use this before considering the skill's implementation complete.

- [ ] **Preemptive Refreshing:** Confirm access token is refreshed 15 minutes before expiration ($T_{\text{expires}} - 900\text{s}$).
- [ ] **Single-Use Rotation Handling:** Confirm newly issued `refresh_token` replaces old refresh token in local state.
- [ ] **Atomic File Persistence:** Confirm new tokens are written via temporary file write + atomic rename (`os.replace`).
- [ ] **Thread Safety:** Confirm token rotation executes within thread/async lock.
- [ ] **Automated Testing:** Run `python scripts/test_upstox_auth.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
