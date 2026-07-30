# Pre-Flight Checklist

- [ ] Are TargetCompID, SenderCompID, and HeartBtInt (30s) configured?
- [ ] Is sequence gap detection (ResendRequest Tag 35=2) tested and operational?
- [ ] Is heartbeat liveness monitoring and TestRequest (Tag 35=1) active?
- [ ] Is graceful Logout handshake (Tag 35=5) implemented?
