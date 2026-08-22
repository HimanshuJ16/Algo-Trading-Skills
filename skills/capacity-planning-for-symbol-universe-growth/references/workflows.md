# Workflows for Universe Capacity Planning

1. **Profiling Existing Load**:
   - Capture the feed and measure messages per **sub-second window** (10ms and 100ms), not
     per second. Convert with `peak_rate_per_sec_from_burst(messages, window_ms)`. A
     one-second average understates the burst your feed handler must actually absorb.
   - Measure the peak for a highly liquid symbol (e.g. SPY or TSLA) *and* for a median
     name. If they differ by orders of magnitude — they will — model the universe in
     liquidity tiers and sum the tiers rather than applying one uniform rate to every
     symbol.
   - Measure payload bytes per message and the observed batching factor (messages per
     packet) separately. Do not fold framing into the payload figure on a batched feed.
   - Measure `cpu_msgs_per_sec_per_core` for your actual parser on your actual hardware.
     A JSON/REST path and a binary ITCH/SBE path differ by more than an order of
     magnitude, and this parameter drives the entire CPU result.
2. **Modeling the Target State**:
   - Feed these metrics into the `CapacityPlanner`.
   - Set `num_symbols` to the planned future state.
   - Set `redundant_feeds=2` if you take both A and B lines, and
     `retransmission_overhead_fraction` for gap-fill traffic (OPRA guidance: +10%).
   - Set `available_cpu_cores` to the cores you can dedicate. Leaving it at the default
     assumes 64.
3. **Hardware Assessment**:
   - Compare `required_network_mbps` against `max_safe_network_mbps` (the report carries
     the ceiling actually applied), not against the raw NIC line rate.
   - Compare `required_cpu_cores` against your dedicated core budget.
   - Check `single_symbol_exceeds_core` before anything else: when it is set, the design
     cannot be rescued by adding cores under symbol partitioning.
4. **Architectural Adjustments**:
   - If CPU cores required > 16, consider moving from JSON/REST to binary protocols
     (ITCH, SBE) to increase `cpu_msgs_per_sec_per_core`, then re-measure — do not assume
     a speedup factor.
   - If a single symbol exceeds one core, partition that symbol's stream by message type
     or shard the book itself; adding cores will not help.
   - If bandwidth is the binding constraint, check whether you need every message type
     before buying a bigger link — dropping unneeded feed components or taking only the A
     line (accepting the resilience trade-off) may be cheaper than upgrading.
   - If memory is high, consider zero-copy ring buffers.
5. **Validate Against Reality**: The model is first-order. Before committing spend, replay
   a real capture at the target rate and confirm the measured throughput and drop rate
   match the forecast.
