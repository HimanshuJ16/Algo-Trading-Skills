# Real-Time Architecture Standards — feed-handler-canary-deployment

| Deployment Stage | Allocation | Observation Window | Rollback Condition |
|---|---|---|---|
| Phase 1: Whitelist Canary | Specific test symbols (`AAPL`, `MSFT`) | 15 minutes | Price mismatch $> 0.1\%$ or exception |
| Phase 2: Percentage Ramp | $10\% \to 50\%$ universe | 1 hour | Error rate $> 1.0\%$ |
| Phase 3: Full Promotion | $100\%$ universe | Continuous | Standard healthcheck |

## Category

`real-time-architecture` — see top-level `mappings/` directory.
