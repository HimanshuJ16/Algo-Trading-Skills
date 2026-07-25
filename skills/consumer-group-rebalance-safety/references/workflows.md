# Deep Workflow Reference — consumer-group-rebalance-safety

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Register Listener Hooks**:
   - Register callbacks for partition revocation (`on_partitions_revoked`) and partition assignment (`on_partitions_assigned`).

2. **Handle Partition Revocation**:
   - Transition state to `REVOKING`.
   - Process in-flight records to completion.
   - Commit partition offset checkpoint synchronously before partition is unassigned.

3. **Handle Partition Assignment**:
   - Transition state to `ASSIGNING` $\to$ `NORMAL`.
   - Initialize assigned partitions and resume message consumption from last committed offset checkpoint.

## Production Implementation Reference

- Reference code: `scripts/rebalance_guard.py` (`ConsumerGroupRebalanceGuard`, `RebalanceState`, `RebalanceEventReport`).
- Automated unit tests: `scripts/test_rebalance_guard.py`.
