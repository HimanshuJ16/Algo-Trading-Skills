# Pre-Flight Checklist

- [ ] Are instrument trading status feeds (LULD, news halts) connected to the execution engine?
- [ ] Are cancellation requests issued for all active resting child limit orders during halts?
- [ ] Are TWAP/VWAP slice timers paused during trading halt duration?
- [ ] Is remaining quantity smoothed over remaining time horizon upon trading resumption?