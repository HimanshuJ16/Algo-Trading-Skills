# Pre-Flight Checklist

- [ ] Are CPU core affinities (`taskset`) configured for high-priority trading threads?
- [ ] Are CPU, Memory, and FIX Gateway rate telemetry monitored in real-time?
- [ ] Is dynamic preemption active to pause `LOW_BATCH` tasks when CPU utilization breaches $85\%$?
- [ ] Are token-bucket rate limiters configured to protect shared FIX gateway sessions?