# Standards for Segregation of Duties for Custody Operations

| Operational Control | Mandatory Requirement |
|---|---|
| Maker-Checker Dual Control | Initiators MUST NOT approve their own proposed custody transfers. |
| M-of-N Threshold | Large transfers ($\ge \$50,000$) MUST require at least 2 distinct approvals. |
| SOC 2 Audit Evidence | All approvals MUST record SHA-256 cryptographic signatures. |