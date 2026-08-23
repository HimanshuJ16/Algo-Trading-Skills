# Workflows for Shared Infrastructure Resource Contention

1. **Registration**:
   - Register every co-located strategy with a valid priority class
     (`HIGH_HFT`, `MEDIUM_ARB`, `LOW_BATCH`). An unrecognised class is rejected,
     not ignored — a silently unclassified process would be neither throttled
     nor protected.
2. **Telemetry Ingestion**:
   - Collect host-normalised CPU %, RAM %, and the aggregate outbound FIX rate
     (msgs/sec) for the shared session.
   - Reject non-finite, negative, or un-normalised readings at the boundary.
     A NaN compares False against every threshold and would report `NORMAL` on
     a saturated host.
3. **State Evaluation**:
   - Score on the single most-loaded resource:
     `max(CPU %, RAM %, FIX rate / negotiated limit × 100)`.
   - `NORMAL` < 75%, `ELEVATED` in [75%, 85%), `CRITICAL_CONTENTION` ≥ 85%.
   - Record the binding resource so the escalation is auditable after the fact.
4. **Preemption (CRITICAL_CONTENTION only)**:
   - `LOW_BATCH` → pause, *after* cancelling or handing off any working orders.
   - `MEDIUM_ARB` → throttle to the concrete msgs/sec cap in
     `throttle_caps_msg_per_sec`.
   - `HIGH_HFT` → protect; report the pinned core for verification.
5. **De-escalation with hysteresis**:
   - `ELEVATED` holds existing suppression rather than lifting it.
   - Release only after `resume_clear_samples` consecutive samples strictly
     below `resume_threshold_pct`. One clear sample is not a recovery.
6. **CPU Core Affinity Isolation** (enforced outside this module):
   - Verify `HIGH_HFT` threads are pinned *onto* cores that are themselves
     isolated (cgroup cpusets or `isolcpus`/`nohz_full`) with IRQs steered away.
     `taskset` alone restricts where a thread may run; it does not reserve the
     core.
   - Bind memory to the same NUMA node as the pinned core
     (`numactl --cpunodebind --membind`).
7. **Enforcement and follow-up**:
   - Apply every directive in the report, then confirm the effect on the next
     telemetry sample. The module reports; it does not enforce.
