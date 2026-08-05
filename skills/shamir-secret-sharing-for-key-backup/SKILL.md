---
name: shamir-secret-sharing-for-key-backup
description: >-
  Production-grade Shamir's Secret Sharing (SSS) engine splitting private signing keys and master seed phrases into (k, n) threshold shares using finite field polynomial evaluation over Mersenne Prime M_127 and reconstructing secrets via Lagrange interpolation.
domain: Crypto Custody & Security
subdomain: Key Backup & Threshold Cryptography
tags: ["shamir-secret-sharing", "sss", "threshold-cryptography", "key-backup", "lagrange-interpolation", "slip-0039"]
brokers_frameworks: ["Shamir's Secret Sharing Scheme (RFC / SLIP-0039)", "Mersenne Prime M_127", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when backing up institutional private keys, master seed phrases, or exchange API secrets to prevent single points of failure. Storing a master private key in a single location creates extreme vulnerability to physical theft, hardware failure, or personnel compromise. Shamir's Secret Sharing (SSS) splits the secret into $N$ unique shares such that any $K$ shares (threshold $K \le N$) can reconstruct the original secret via Lagrange interpolation over a finite field ($GF(P)$ or $M_{127}$), while any combination of fewer than $K$ shares yields zero information.

## Prerequisites

- Secret integer or 256-bit private key integer (`secret_int`).
- Threshold scheme parameters ($K$ required shares, $N$ total shares, $1 \le K \le N$).

## Workflow

1. **Random Polynomial Construction**:
   - Construct random polynomial $f(x) = S + a_1 x + a_2 x^2 + \dots + a_{K-1} x^{K-1} \pmod P$, where $S = \text{secret}$ and $a_i \in [0, P-1]$.
2. **Share Generation**:
   - Evaluate $f(x)$ at $x \in \{1, 2, \dots, N\}$ to generate shares $(x_i, y_i)$.
3. **Threshold Collection**:
   - Collect any subset of $K$ valid shares.
4. **Lagrange Interpolation Reconstruction**:
   - Compute $f(0) = \sum_{j=1}^{K} y_j \prod_{m \neq j} \frac{-x_m}{x_j - x_m} \pmod P$ to recover secret $S$.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reconstructing Secrets Over Standard Floating-Point Arithmetic**: Using standard division instead of modular multiplicative inverse ($a^{-1} \pmod P$), causing precision loss and invalid key recovery.
- **Inadequate Randomness in Coefficients**: Generating polynomial coefficients using weak pseudorandom number generators instead of `secrets.randbelow()`.
- **Storing Shares In the Same Geographic Location**: Distributing $N$ shares among individuals or vaults located in the same facility.

## Verification

- Instantiate `ShamirSecretSharingForKeyBackupEngine`. Perform $(3, 5)$ split of secret $S = 123456789012345678901234567890$ $\implies$ verify 5 shares generated. Reconstruct using shares #1, #3, #5 $\implies$ verify exact secret recovered. Reconstruct using 2 shares $\implies$ verify reconstructed value does not match secret.
- Run `python scripts/test_shamir_secret_sharing_for_key_backup.py`.

## Related Skills

- `hardware-security-module-hsm-for-signing-keys`
- `multi-party-computation-mpc-custody-solutions`
---
