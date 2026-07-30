# Standards for Exchange Proof of Reserves Verification

| Metric | Engineering Standard |
|---|---|
| Minimum Reserve Ratio | On-chain reserves MUST meet or exceed 100.0% of total Merkle liabilities. |
| Negative Balance Ban | Merkle trees MUST NOT contain negative user balances ($u_i \ge 0$). |
| Cryptographic Proof Standard | User balance inclusion MUST be verifiable via SHA-256 Merkle Sum Paths. |