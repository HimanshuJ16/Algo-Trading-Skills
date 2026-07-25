# Pre-Flight / Sign-off Checklist — graceful-shutdown-draining-in-flight-ticks

Use this before considering the skill's implementation complete.

- [ ] **Signal Registration:** Confirm `SIGTERM` and `SIGINT` traps are registered.
- [ ] **Ingress Block:** Confirm external network ingestion stops immediately upon signal receipt.
- [ ] **Queue Drain Verification:** Confirm in-flight queue items are processed to completion before termination.
- [ ] **Timeout Safeguard:** Confirm $T_{\text{max\_drain}}$ caps waiting time to avoid container kill locks.
- [ ] **Automated Testing:** Run `python scripts/test_graceful_shutdown.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
