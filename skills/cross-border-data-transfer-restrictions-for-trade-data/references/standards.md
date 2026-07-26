# Standards for Cross-Border Data Transfer Restrictions

| Metric | Engineering Standard |
|---|---|
| Zero Unmasked Cross-Border PII | PII fields MUST NOT cross international borders without cryptographic tokenization or anonymization. |
| Cryptographic Hashing | Trader IDs MUST be hashed using SHA-256 with a salt prior to cross-border egress. |
| Audit Logging | 100% of intercepted cross-border data transfers MUST produce immutable compliance audit logs. |
