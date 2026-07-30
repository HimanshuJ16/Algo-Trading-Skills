# Pre-Flight Checklist

- [ ] Are raw vendor timestamps parsed into 64-bit nanosecond UTC epoch integers?
- [ ] Is raw timestamp stored alongside normalized nanosecond timestamp for auditability?
- [ ] Are out-of-order tick sequence arrivals ($\Delta t < 0$) detected and logged?
- [ ] Are coarse millisecond timestamps flagged when sub-microsecond resolution is required?
