# Pre-Flight Checklist

- [ ] Are reallocation intervals sufficiently long (e.g., weekly/monthly) to measure statistically significant edge rather than random variance?
- [ ] Have you implemented a Half-Kelly or Quarter-Kelly dampener to prevent over-leverage?
- [ ] Is there a hard-coded maximum capital constraint on every strategy to prevent it from outgrowing its liquidity capacity?
- [ ] Does the OMS gracefully handle capital reduction (e.g., by preventing new entries rather than force-liquidating existing positions)?