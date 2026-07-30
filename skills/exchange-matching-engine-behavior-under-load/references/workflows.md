# Workflows for Exchange Matching Engine Behavior Under Load

1. **Message Rate Monitoring**:
   - Track incoming message arrival rate ($\lambda$) against engine service capacity ($C$).
2. **Queuing Delay Model**:
   - Compute non-linear latency degradation using M/M/1 queuing equations.
3. **Adverse Selection Assessment**:
   - Evaluate stale quote exposure risk during queue congestion.
4. **Strategy Adaptation Emission**:
   - Issue directives to widen quotes or pause passive market making.