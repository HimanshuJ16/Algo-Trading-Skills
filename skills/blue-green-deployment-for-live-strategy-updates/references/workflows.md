# Deep Workflow Reference — blue-green-deployment-for-live-strategy-updates

## Full Procedure

1. Deploy new version to inactive slot.
2. Run health checks on inactive slot.
3. Drain active slot (read-only mode).
4. Atomic cutover to new slot.
5. Monitor for stabilization period.
6. Rollback if errors detected.

## Production Implementation Reference

- Code: `scripts/blue_green_deployer.py` (`BlueGreenDeployer`).
- Tests: `scripts/test_blue_green_deployer.py`.
