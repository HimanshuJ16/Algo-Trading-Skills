# Workflows for Latency Budget Accounting

1. **Hot Path Instrumentation**:
   - Store timestamps $T_0 \dots T_5$ in a fixed-size lock-free array during order processing.
2. **Asynchronous Ring Buffer Offload**:
   - Push completed trace records to a background worker thread for accounting analysis.
3. **Phase & SLA Evaluation**:
   - Compute phase durations: `ingress_to_decode`, `decode_to_signal`, `signal_to_risk`, `risk_to_encode`, `encode_to_egress`.
   - Compare total T2T time ($T_5 - T_0$) against target SLA.
4. **Bottleneck Diagnostics**:
   - For breached records, compute $\text{Excess}_k = \Delta_k - \text{SLA}_k$.
   - Flag the phase with $\max(\text{Excess}_k)$ as the primary bottleneck.
5. **Jitter Reporting**:
   - Generate hourly/daily percentile distributions ($P_{50}, P_{95}, P_{99}, P_{99.9}$).
