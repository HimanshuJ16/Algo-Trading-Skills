# Pre-Flight Checklist

- [ ] Are consecutive FIX/API error counters active for all connected venues?
- [ ] Are venue circuit breakers tripped after $\ge 3$ consecutive errors?
- [ ] Is automatic failover configured to reroute orders to secondary healthy venues?
- [ ] Are secondary backup FIX connections pre-warmed to eliminate failover latency spikes?
