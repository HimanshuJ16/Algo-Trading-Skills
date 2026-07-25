# API Migration Operations Checklist

Use this checklist during live deployment of an API version migration.

## Pre-Migration
- [ ] Ensure V1 baseline metrics (latency, error rates) are captured and saved.
- [ ] Verify test suite for `BrokerAPIVersionMigrator` passes 100%.
- [ ] Confirm emergency kill switch is mapped to `MigrationPhase.ROLLBACK_V1`.
- [ ] Notify execution traders of impending shadow mode activation.

## Shadow Mode Deployment
- [ ] Transition phase to `SHADOW_MODE`.
- [ ] Let run for minimum 2 full trading sessions (e.g., 48 hours).
- [ ] Review `audit_log` output. 
- [ ] Confirm `is_equivalent == True` for all critical endpoints.
- [ ] Verify V2 latency tracker stats meet tolerance standards.

## Canary Cutover
- [ ] Transition phase to `CANARY_CUTOVER` with `canary_percentage = 0.01` (1%).
- [ ] Monitor order fill rates and reject rates for 1 hour.
- [ ] Increase to 5%, monitor through one market close.
- [ ] Increase to 25%, monitor through one market open.
- [ ] Increase to 50%, then 100%.

## Finalization
- [ ] Transition phase to `V2_ONLY`.
- [ ] Confirm 0 traffic on V1 read and write paths.
- [ ] Create Jira ticket to remove V1 boilerplate in next sprint.
