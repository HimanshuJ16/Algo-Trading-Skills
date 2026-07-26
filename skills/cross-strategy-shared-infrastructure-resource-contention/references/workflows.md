# Workflows for Shared Infrastructure Resource Contention

1. **Telemetry Ingestion**:
   - Collect CPU %, RAM %, and FIX msg rate.
2. **State Evaluation**:
   - Determine contention state (`NORMAL`, `ELEVATED`, `CRITICAL`).
3. **Throttling Action**:
   - If `CRITICAL`, pause `LOW_BATCH` tasks and scale back `MEDIUM_ARB` rate limits.
4. **CPU Core Affinity Isolation**:
   - Verify `HIGH_HFT` tasks remain pinned to dedicated CPU cores (`taskset`).