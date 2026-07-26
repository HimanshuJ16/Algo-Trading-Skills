# Pre-Flight Checklist

- [ ] Does the chaos test framework support injecting latency/jitter at the application or network level?
- [ ] Are packet drops handled gracefully by the downstream systems (e.g., triggering a REST recovery of missing sequence numbers)?
- [ ] Is the random number generator seeded to allow exact reproduction of failed chaos tests?
- [ ] Has "grey failure" (slow connection, no disconnect) been tested to ensure the main thread isn't blocked?
