# Pre-Flight Checklist

- [ ] Is the engine architected as a mandatory gateway (middleware) rather than an optional strategy-level check?
- [ ] Does the engine persist its state? (If the server restarts during a HALT, it must wake up in a HALTED state).
- [ ] Is the order frequency limit configured tightly enough to prevent runaway algorithms before exchange rate limits ban the firm's IP?
- [ ] Is the manual override/reset function secured and logged for audit?