# Pre-Flight Checklist

- [ ] Does the algorithm filter for minimum delay points before fitting the linear drift line?
- [ ] Is monotonicity strictly enforced on the output timestamp series?
- [ ] Are time units handled consistently (e.g., converting all timestamps to float seconds or integer nanoseconds)?
- [ ] Has the corrector been validated against synthetic drift and jitter to verify accuracy?
