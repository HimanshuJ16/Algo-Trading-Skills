# Pre-Flight Checklist

- [ ] Does the condition engine support atomic Price, Volume, Time, and Cross-Asset nodes?
- [ ] Are logical operators (`AND`, `OR`, `NOT`) supported in composite condition trees?
- [ ] Is single-fire state transition (`DORMANT` -> `TRIGGERED`) atomic to prevent duplicate orders?
- [ ] Are stale market data feeds handled safely by withholding trigger evaluation?
