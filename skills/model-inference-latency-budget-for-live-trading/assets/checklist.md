# Pre-Flight Checklist

- [ ] Is maximum inference budget ($\tau_{\text{max\_budget\_ms}}$) defined?
- [ ] Are $P_{50}, P_{90}, P_{95}, P_{99}, P_{99.9}$ percentiles calculated?
- [ ] Is latency jitter ($\sigma_{\text{latency}}$) logged?
- [ ] Is automated model fallback action configured upon $P_{99}$ SLA breach?
