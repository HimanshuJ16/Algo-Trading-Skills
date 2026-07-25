# Workflows for Universe Capacity Planning

1. **Profiling Existing Load**:
   - Measure the peak (99th percentile) messages per second (MPS) for a single highly liquid symbol (e.g., SPY or TSLA).
   - Measure the average bytes per message (including TCP/IP overhead).
2. **Modeling the Target State**:
   - Feed these metrics into the `CapacityPlanner`.
   - Set the `num_symbols` to the planned future state.
3. **Hardware Assessment**:
   - Compare the output `required_network_mbps` against your cross-connect or cloud instance limits.
   - Compare `required_cpu_cores` against available hardware.
4. **Architectural Adjustments**:
   - If CPU cores required > 16, consider moving from JSON/REST to binary protocols (ITCH, SBE) to increase `cpu_msgs_per_sec_per_core`.
   - If memory is high, consider zero-copy ring buffers.