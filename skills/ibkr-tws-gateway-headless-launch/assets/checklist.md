# Pre-Flight / Sign-off Checklist — ibkr-tws-gateway-headless-launch

Use this before considering the skill's implementation complete.

- [ ] **Port & Mode Matching:** Confirm port `4002` (Paper) vs `4001` (Live) matches configuration mode.
- [ ] **Socket Readiness Probe:** Confirm `wait_for_gateway_ready()` probes port before client connection.
- [ ] **Headless Automation:** Confirm IBC configuration handles auto-login without unhandled GUI modals.
- [ ] **Daily Reset Resiliency:** Confirm reconnect logic handles IBKR's 23:45 EST reset window.
- [ ] **Automated Testing:** Run `python scripts/test_ib_headless_manager.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
