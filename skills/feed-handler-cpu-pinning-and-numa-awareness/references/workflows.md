# Deep Workflow Reference — feed-handler-cpu-pinning-and-numa-awareness

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Topology Discovery**:
   - Inspect physical CPU cores, hyper-thread pairings, and NUMA socket nodes.

2. **Core Binding**:
   - Assign dedicated physical cores to latency-critical feed handler processes (`p.cpu_affinity([core_id])`).

3. **NUMA Memory Locality**:
   - Ensure worker thread memory allocations stay within local NUMA socket node memory banks.

4. **Affinity Verification**:
   - Verify assigned core list matches process affinity masks.

## Production Implementation Reference

- Reference code: `scripts/affinity_manager.py` (`CPUAffinityNUMAManager`, `CPUTopologyInfo`, `AffinityBindingReport`).
- Automated unit tests: `scripts/test_affinity_manager.py`.
