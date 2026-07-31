# Pre-Flight Checklist

- [ ] Are consumer groups registered via XGROUP CREATE before consumption begins?
- [ ] Is XADD publishing with MAXLEN cap to prevent unbounded memory growth?
- [ ] Are workers XACK-ing every consumed tick to drain the PEL?
- [ ] Is XCLAIM configured to reclaim stale entries from crashed workers?
