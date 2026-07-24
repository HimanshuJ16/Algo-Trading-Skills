# Deep Workflow Reference — ibkr-tws-gateway-headless-launch

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Port & Trading Mode Verification:**
   - Verify environment mode: `is_paper=True` requires API port `4002` (Gateway) or `7497` (TWS).
   - `is_paper=False` requires API port `4001` (Gateway) or `7496` (TWS).
   - Veto startup via `IBGatewayHeadlessManager` on port mismatch.

2. **Headless Xvfb / Container Execution:**
   - Launch IB Gateway using Virtual Framebuffer (`Xvfb`) or Docker container (`ghcr.io/gnzrb/ib-gateway`).
   - Configure IBC (`config.ini`) to handle automated login and bypass dialog prompts.

3. **Socket Readiness Probe:**
   - Execute `IBGatewayHeadlessManager.wait_for_gateway_ready()` before initializing `ibapi` or `ib_insync` client connection.

4. **Daily Reset Recovery:**
   - IBKR forces a daily server reset every night (~23:45 EST / 04:45 UTC).
   - Catch socket disconnect exceptions during this window and re-probe until IB Gateway completes reset.

## Failure Modes Observed in Production

- **Port Misconfiguration:** Connecting a paper bot to port 4001, executing trades against live capital.
- **Premature Socket Connection:** Attempting API connection before IB Gateway port probe returns ready.
- **Unmonitored Daily Reset:** Failing to handle IBKR's nightly 23:45 EST reset, hanging the strategy process.

## Production Implementation Reference

- Reference code: `scripts/ib_headless_manager.py` (`IBGatewayHeadlessManager`, `IBGatewayConfig`).
- Automated unit tests: `scripts/test_ib_headless_manager.py`.
