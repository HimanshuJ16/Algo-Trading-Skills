# Deep Workflow Reference — graceful-shutdown-draining-in-flight-ticks

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **OS Signal Registration**:
   - Register handlers for `SIGTERM` (container termination) and `SIGINT` (keyboard interrupt).

2. **Transition State & Stop Ingress**:
   - Set state to `DRAINING`. Reject new incoming network market ticks.

3. **Drain In-Flight Queue & Flush Sinks**:
   - Flush remaining queue contents to downstream DB or log sinks until queue count = 0 or $T_{\text{max\_drain}}$ expires.

4. **Clean Exit Checkpoint**:
   - Confirm 0 items lost and exit process safely with status 0.

## Production Implementation Reference

- Reference code: `scripts/graceful_shutdown.py` (`GracefulShutdownManager`, `ShutdownState`, `ShutdownReport`).
- Automated unit tests: `scripts/test_graceful_shutdown.py`.
