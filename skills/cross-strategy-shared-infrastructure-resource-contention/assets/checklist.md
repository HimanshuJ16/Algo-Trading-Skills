# Pre-Flight Checklist

- [ ] Are high-priority trading threads pinned (`taskset`/`sched_setaffinity`) **onto cores that are themselves isolated** via cgroup cpusets or `isolcpus`/`nohz_full`, with IRQs steered elsewhere? (Affinity alone restricts a thread; it does not reserve the core.)
- [ ] Is memory bound to the same NUMA node as the pinned core (`numactl --cpunodebind --membind`)?
- [ ] Are CPU, memory, and shared FIX-gateway rate telemetry sampled in real time, and are CPU/RAM values host-normalised to `[0, 100]` before ingestion?
- [ ] Are non-finite or out-of-range telemetry readings rejected rather than scored as `NORMAL`?
- [ ] Is `max_fix_gateway_rate_sec` set from the venue's or broker's actual per-session allocation, with headroom sized from a real capacity test (RTS 6 Art. 10 uses 2× the prior six months' peak)?
- [ ] Does every registered strategy carry a valid priority class, with registration failing loudly on a typo?
- [ ] Is dynamic preemption active to pause `LOW_BATCH` work when the binding resource breaches the critical threshold?
- [ ] Do `LOW_BATCH` jobs cancel or hand off working orders **before** being suspended, so no resting order is left unmanaged?
- [ ] Is a separate kill switch available for emergency order cancellation, distinct from this load-shedding path?
- [ ] Are pre-trade risk checks excluded from every load-shedding tier (SEC Rule 15c3-5(d): direct and exclusive control)?
- [ ] Are token-bucket rate limiters configured per shared FIX session, sized below the venue's Reject threshold rather than its Terminate threshold?
- [ ] Is resume gated by hysteresis (N consecutive clear samples below the resume threshold) rather than a single sub-critical reading?
- [ ] Does a supervisor actually enforce the emitted directives, and is enforcement verified on the following telemetry sample?
