# Pre-Flight Checklist

- [ ] Does the monitor use absolute values for offsets to catch both positive and negative clock drift?
- [ ] Is the critical threshold set exactly to the regulatory requirement (e.g., 100µs for MiFID II)?
- [ ] Is there an automated integration between the monitor's critical alert and the trading engine's halt function?
- [ ] Are PTP daemon states (e.g., `HOLDOVER`, `UNLOCKED`) monitored in addition to the raw offset?
