# Pre-Flight / Sign-off Checklist — websocket-reconnection-with-state-recovery

Use this before considering the skill's implementation complete.

- [ ] **State Machine Definition:** Confirm connection states (`DISCONNECTED`, `CONNECTING`, `SUBSCRIBED`, `RECOVERING_GAP`, `STREAMING`) transition correctly.
- [ ] **Exponential Backoff & Jitter:** Confirm retry delays increase exponentially with randomized jitter.
- [ ] **Symbol Channel Re-subscription:** Confirm active symbols re-subscribe automatically on connection establishment.
- [ ] **REST Gap Recovery:** Confirm missing sequence numbers trigger REST gap filling before streaming resumes.
- [ ] **Automated Testing:** Run `python scripts/test_ws_recovery.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
