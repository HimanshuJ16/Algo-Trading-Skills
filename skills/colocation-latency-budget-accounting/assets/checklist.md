# Pre-Flight Checklist

- [ ] Are NIC hardware timestamps used for packet ingress and egress?
- [ ] Is timestamp recording in the trading loop non-blocking and zero-allocation?
- [ ] Are SLAs defined per processing phase (`decode`, `signal`, `risk`, `encode`)?
- [ ] Does the telemetry framework output $P_{50}, P_{95}, P_{99}, P_{99.9}$ metrics?
