# Standards for Post-Incident Forensics for Suspected Key Compromise

| Metric | Engineering Standard |
|---|---|
| Evidence Integrity | SHA-256 evidence hashing MUST be generated for all forensic log artifacts. |
| Key Revocation SLA | Immediate key revocation MUST execute within $\le 60$ seconds of confirmed breach. |
| IP Whitelist Standard | Any request from a non-whitelisted IP MUST be flagged as a critical security anomaly. |