# Pre-Flight Checklist

- [ ] Is FIX SenderCompID and TargetCompID configured for SGX TITAN?
- [ ] Are contract-specific tick sizes (2.5 for CN, 5.0 for NK) validated before routing?
- [ ] Are contract value multipliers ($500\text{ JPY}$ for Nikkei 225) applied in margin calculations?
- [ ] Is FIX session state logged and monitored for heartbeats?
