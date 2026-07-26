# Pre-Flight Checklist

- [ ] Are primary fill event callbacks linked to real-time hedge order generators?
- [ ] Is dynamic hedge ratio (Delta / Beta) updated continuously?
- [ ] Is hedge synchronization latency ($\Delta t$) monitored against SLA (100 ms)?
- [ ] Are aggressive repricing and emergency unwinding handlers configured for delayed fills?