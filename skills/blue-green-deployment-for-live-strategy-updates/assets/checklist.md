# Pre-Flight Checklist — blue-green-deployment-for-live-strategy-updates

- [ ] Both slots (blue/green) are configured and accessible.
- [ ] Health check function validates connectivity and readiness.
- [ ] Cutover is atomic with zero-gap coverage.
- [ ] Rollback restores previous version instantly.
- [ ] Run `python scripts/test_blue_green_deployer.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
