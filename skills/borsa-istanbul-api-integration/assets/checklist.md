# BIST Integration Readiness Checklist

- [ ] Network connectivity to BIST Simulator/Production IP and Port verified.
- [ ] FIX credentials (SenderCompID, TargetCompID, Passwords) securely injected.
- [ ] Heartbeat mechanism confirmed stable at 30 seconds.
- [ ] Sequence number persistence and recovery logic tested.
- [ ] NewOrderSingle validation covers Price, Qty, Side, and TIF.
- [ ] Execution Report handling correctly computes partial fills and average price.
- [ ] Order cancellation rejects are gracefully managed.
- [ ] BISTECH FIX Certification scenarios passed (if preparing for PROD).
- [ ] Latency metrics logging enabled for all network boundaries.
