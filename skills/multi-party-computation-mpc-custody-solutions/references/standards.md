# Standards for MPC Custody Solutions

| Metric | Engineering Standard |
|---|---|
| Threshold Quorum | Minimum threshold $t \ge 2$ out of $N \ge 3$ MUST be enforced. |
| Private Key Security | Private key MUST NEVER be assembled in memory during signing. |
| Protocol Selection | Fast CMP 1-round/3-round protocol PREFERRED over legacy 9-round GG18. |