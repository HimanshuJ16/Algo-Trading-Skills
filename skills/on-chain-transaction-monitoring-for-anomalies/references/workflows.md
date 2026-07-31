# Workflows for On-Chain Transaction Monitoring for Anomalies

1. **Transaction Payload Ingestion**:
   - Receive transaction parameters (`to_address`, `value_usd`, `gas_price_gwei`, `method_signature`).
2. **Multi-Vector Risk Audit**:
   - Audit against blacklist, value limit, gas limit, and method whitelist.
3. **Risk Scoring & Block Action**:
   - Calculate score ($0-100$) and block high-risk transactions ($\ge 70$).
4. **Audit Report Generation**:
   - Output structured monitoring report.