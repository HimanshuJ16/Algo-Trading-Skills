# Standards for Custodial vs Non-Custodial Trade-Off Assessment

| Metric | Engineering Standard |
|---|---|
| HFT Latency SLA | Sub-10ms trading strategies MUST use `CUSTODIAL_CEX` or `HYBRID_OFF_EXCHANGE` architectures. |
| Zero Counterparty Mandates | Strategies prohibiting exchange counterparty risk MUST use `NON_CUSTODIAL_DEX` or MPC self-custody. |
| Off-Exchange Settlement | Institutional funds trading $> \$10\text{M}$ monthly volume MUST evaluate Off-Exchange Settlement (Fireblocks/Copper). |