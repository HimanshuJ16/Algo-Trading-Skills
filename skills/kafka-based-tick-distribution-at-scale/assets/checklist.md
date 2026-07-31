# Pre-Flight Checklist

- [ ] Is `Key = Symbol` enforced for deterministic partition routing?
- [ ] Is producer batching configured (`batch.size = 128KB`, `linger.ms = 5`)?
- [ ] Is consumer lag monitored across all partition consumer groups?
- [ ] Are max lag threshold alerts configured ($> 10,000$ ticks)?
