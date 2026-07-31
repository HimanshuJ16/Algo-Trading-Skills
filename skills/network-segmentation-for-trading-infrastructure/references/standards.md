# Standards for Network Segmentation for Trading Infrastructure

| Metric | Engineering Standard |
|---|---|
| Public to Execution Isolation | Direct ALLOW rules from `PUBLIC_DMZ` to `TRADING_EXECUTION` MUST be blocked. |
| Key Custody Isolation | Ingress to `KEY_CUSTODY` MUST be restricted to strictly whitelisted MPC/HSM nodes. |
| Admin Port Exposure | SSH (22) and RDP (3389) MUST NEVER be exposed to `PUBLIC_DMZ`. |