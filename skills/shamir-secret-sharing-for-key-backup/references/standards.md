# Standards for Shamir Secret Sharing for Key Backup

| Parameter | Standard Requirement |
|---|---|
| Modulus Field | Prime Field $M_{127} = 2^{127} - 1$ or $GF(2^8)$. |
| Coefficient Randomness | Cryptographically secure random number generator (`secrets.randbelow`). |
| Threshold Constraint | Minimum $K \ge 2$, Maximum $N \le 16$. |