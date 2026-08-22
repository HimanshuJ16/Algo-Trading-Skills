# Pre-Flight Checklist

## Input measurement
- [ ] Have you modeled using *peak* microburst tick rates rather than daily averages?
- [ ] Was the peak measured over a sub-second window (10ms/100ms) and converted with `peak_rate_per_sec_from_burst`, rather than read off a one-second average?
- [ ] Is `cpu_msgs_per_sec_per_core` a measured figure for your actual parser, not an estimate?
- [ ] If per-symbol rates are heavily skewed, have you modeled liquidity tiers separately instead of applying one uniform rate to every symbol?

## Wire model
- [ ] Is packet framing charged **per packet** (`packet_overhead_bytes` + `msgs_per_packet`) rather than per message — or, if folded into `bytes_per_msg`, is the feed genuinely unbatched?
- [ ] Is `redundant_feeds=2` set if you consume both sides of an A/B multicast pair?
- [ ] Is `retransmission_overhead_fraction` set for gap-fill traffic (OPRA guidance: +10%)?
- [ ] Have you verified that your broker/exchange allows subscribing to this many symbols on a single WebSocket/FIX connection? (If not, you must model multiple connections).

## Headroom
- [ ] Is `max_network_utilization` set to a deliberate value, and are you comparing against `max_safe_network_mbps` rather than raw NIC line rate?
- [ ] If you also set a `safety_margin`, have you checked you are not double-counting it against the utilization ceiling?

## Hardware
- [ ] Does `available_cpu_cores` reflect cores you can actually dedicate, excluding OS and strategy threads?
- [ ] Is the resulting CPU core count strictly dedicated (pinned) rather than shared with OS processes?
- [ ] Have you checked `single_symbol_exceeds_core`? If set, no amount of additional cores makes this design work under symbol partitioning.
- [ ] Is RAM headroom left for the OS, page cache and runtime beyond `required_ram_gb`?

## Validation
- [ ] Have you replayed a real capture at target rate to confirm the model, rather than trusting the forecast alone?
