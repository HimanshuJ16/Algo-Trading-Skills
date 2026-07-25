# Blue-Green Deployment Checklist

## Pre-Deployment (Staging)
- [ ] Verify target binary / strategy version passes offline backtest reconciliation.
- [ ] Confirm sufficient hardware capacity on target trading servers (NUMA cores, memory).
- [ ] Lock risk parameters for the new deployment.

## Shadow Deployment (Green)
- [ ] Spin up Green instance in shadow mode.
- [ ] Confirm Green is successfully consuming market data feeds.
- [ ] Verify zero outgoing FIX/binary messages from Green.
- [ ] Wait for Green JIT warmup and initial signal generation cycles.
- [ ] Green instance passes real-time risk checks.

## Synchronization & Cutover
- [ ] Trigger atomic portfolio and state sync from Blue to Green.
- [ ] Validate Green's internalized portfolio matches Blue's exactly.
- [ ] Execute execution gateway pointer swap (Blue -> Green).
- [ ] Monitor first 5 minutes of Green's order submissions.

## Post-Cutover
- [ ] If issues detected: TRIGGER EMERGENCY ROLLBACK.
- [ ] If stable for 30 minutes: Decommission Blue instance.
- [ ] Document deployment artifacts and performance metrics.
