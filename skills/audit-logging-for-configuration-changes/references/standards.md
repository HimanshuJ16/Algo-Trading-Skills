# Standards for Audit Logging

| Regulatory Standard | Requirement | Implementation |
|---|---|---|
| **SEC Reg SCI** | Resiliency and forensic reconstruction of system changes. | High-precision UTC timestamps, strict `old_value` to `new_value` mapping. |
| **FINRA Rule 3110** | Supervisory oversight of algorithmic trading changes. | Mandatory `justification` field requiring human reasoning for the change. |
| **Data Integrity** | Logs must be tamper-proof. | Output to JSON for ingestion into an immutable SIEM (Security Information and Event Management) platform. |

## Category
`deployment-ops`