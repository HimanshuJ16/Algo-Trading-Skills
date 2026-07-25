# Pre-Flight / Sign-off Checklist — feed-handler-cpu-pinning-and-numa-awareness

Use this before considering the skill's implementation complete.

- [ ] **Topology Discovery:** Confirm physical CPU cores and NUMA nodes are discovered.
- [ ] **Affinity Assignment:** Confirm worker process PID is bound to isolated physical CPU cores.
- [ ] **Hyper-Threading Avoidance:** Confirm worker cores do not collide with HT sibling threads.
- [ ] **NUMA Node Locality:** Confirm memory allocations remain within the local NUMA socket node.
- [ ] **Automated Testing:** Run `python scripts/test_affinity_manager.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
