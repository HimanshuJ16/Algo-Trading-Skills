# Pre-Flight / Sign-off Checklist — consumer-group-rebalance-safety

Use this before considering the skill's implementation complete.

- [ ] **Rebalance Hook Registration:** Confirm `on_partitions_revoked` and `on_partitions_assigned` callbacks are registered.
- [ ] **In-Flight Batch Flush:** Confirm in-flight records are processed before partition revocation completes.
- [ ] **Offset Commit Protocol:** Confirm partition offsets are committed before unassignment.
- [ ] **Unassigned Partition Veto:** Confirm records for unassigned partitions are rejected.
- [ ] **Automated Testing:** Run `python scripts/test_rebalance_guard.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
