# Workflows for Execution Algo Behavior Under Halted Instrument

1. **Instrument Status Monitoring**:
   - Ingest real-time instrument status feeds.
2. **Halt Reaction Protocol**:
   - Cancel all resting child orders and freeze algo timers.
3. **Resumption Protocol**:
   - Re-benchmark remaining quantity over remaining time.
4. **Schedule Resumption**:
   - Resume child slice dispatch under updated participation schedule.