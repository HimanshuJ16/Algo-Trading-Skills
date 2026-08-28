---
name: shamir-secret-sharing-for-key-backup
description: >-
  Shamir (k, n) threshold split and reconstruction of signing keys and BIP-39 seeds over the prime
  field M_521, with a field large enough for a 256-bit key, byte-length-preserving splitting,
  and reconstruction that refuses duplicate, out-of-field, sub-threshold or mutually inconsistent
  share sets instead of returning a plausible wrong key. Not SLIP-0039 compatible.
domain: Crypto Custody & Security
subdomain: Key Backup & Threshold Cryptography
tags: ["shamir-secret-sharing", "sss", "threshold-cryptography", "key-backup", "lagrange-interpolation", "crypto-custody"]
brokers_frameworks: ["Shamir (CACM 1979)", "NIST SP 800-57 Part 1 Rev. 5", "CCSS v9 (C4)", "SLIP-0039 (contrast only)", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when key material that unlocks funds — a cold-storage signing key, a BIP-39 seed,
an exchange withdrawal-API secret — must survive the loss of any single custodian, vault or
device without any single custodian, vault or device being able to spend on its own. Shamir's
scheme splits the secret into $N$ shares over a finite field so that any $K$ reconstruct it
exactly and any $K-1$ reveal nothing about it (Shamir, CACM 1979). NIST SP 800-57 Part 1 Rev. 5
calls this *split knowledge* and states the guarantee precisely: $K-1$ shares provide "no
information about the key other than, possibly, its length."

Use it also to reason about an existing split: whether the field is wide enough for the secret,
whether $K$ is high enough to stop one custodian acting alone, and whether the reconstruction
path can tell a good share set from a corrupt one.

## When NOT to Use

- **Do not use it as a SLIP-0039 implementation.** SLIP-0039 shares are GF(256) byte-wise shares
  with an RS1024 checksum, a 4-bit member index, group structure and a digest share, encoded as
  mnemonic words. Shares from this engine interoperate with nothing but this engine. A Trezor
  will not read them, and a SLIP-0039 mnemonic cannot be fed in.
- **Do not use it where a malicious shareholder is in the threat model.** This is plain Shamir,
  not verifiable secret sharing (VSS): nothing binds a share to the dealer's polynomial, so a
  deliberately forged share cannot be attributed. It detects *accidental* corruption only, and
  only when a surplus share is present.
- **Do not use it to sign.** Reconstruction puts the whole key in one process's memory, which is
  exactly what threshold signing avoids — see `multi-party-computation-mpc-custody-solutions`.
  Shamir is for *backup*, where the key is expected to be reassembled once, under supervision.
- **Do not run it on a trading host.** Python integers cannot be wiped; the secret, the
  polynomial coefficients and every share stay in memory until the GC decides otherwise. Split
  and reconstruct air-gapped (`air-gapped-signing-workflow-for-cold-storage`) or inside an HSM.
- **Do not use it to decide *where* shares live.** Share geography is audited by
  `cold-storage-geographic-distribution-strategy`; this skill only produces and consumes them.

## Prerequisites

- Key material as `bytes` (preferred — use `split_secret_bytes`, which preserves the exact byte
  length) or as a non-negative integer strictly below the field modulus.
- A field large enough for the secret. The default `PRIME_FIELD_MODULUS = MERSENNE_M521`
  ($2^{521}-1$, also the NIST P-521 field prime) holds a 32-byte key and a 64-byte BIP-39 seed;
  `MERSENNE_M127` holds 15 bytes and **cannot** hold a 256-bit key.
- Scheme parameters $K, N$ with $2 \le K \le N$. $K=1$ is rejected: every share would be a
  verbatim copy of the key.
- A custody plan for who holds which share, and a recorded share `index` — a share whose $x$
  coordinate is lost is unusable.

## Workflow

1. **Size the field before splitting.** Compare `len(secret)` against
   `engine.max_secret_bytes` (64 for M_521, 15 for M_127). Splitting is refused, not truncated,
   when the secret does not fit — a truncated key is a lost key.
2. **Choose $K$ against both failure directions.** $K$ too low and any $K$ colluding custodians
   spend unilaterally; $K$ too high and one unreachable custodian makes the key unrecoverable.
   $N - K$ is your loss tolerance, so make it at least 1, and hold $K \ge 2$ always.
3. **Split with the byte API.** `split_secret_bytes` tags the secret with a `0x01` byte before
   the integer conversion so a key beginning `00` does not come back one byte short. Coefficients
   come from `secrets.randbelow`; the leading coefficient is resampled if it lands on zero, which
   would silently reduce the effective threshold to $K-1$.
4. **Distribute the shares separately.** NIST SP 800-57 §8.1.5.2.2.1: "each key share **shall**
   be distributed separately to its intended recipient", wrapped or under physical controls.
   Record `index`, `threshold_k` and the modulus with each share — none of that leaks anything,
   and without them a reconstruction cannot tell a short share set from a complete one.
5. **Collect $K+1$ shares, not $K$.** The surplus share is what makes integrity checkable. With
   exactly $K$ shares, a mistyped digit produces a well-formed *wrong* key and the engine can
   only log a warning.
6. **Reconstruct and let it refuse.** `reconstruct_secret_bytes` raises rather than returning a
   wrong value on: duplicate indices, an $x=0$ share, a share from another field, fewer shares
   than the recorded threshold, conflicting declared thresholds, or a set that does not lie on a
   single degree-$(K-1)$ polynomial. Treat any raise as "fetch another share", never as
   "retry with fewer".
7. **Confirm against something outside the scheme.** Derive the public key or address from the
   reconstructed material and compare it with the recorded one. Interpolation returning *a*
   value is not evidence it returned *your* key.

> Full procedure: see `references/workflows.md`.
> Standards and sourcing: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **A field too small for the key.** $M_{127}$ is the textbook modulus and holds 127 bits; a
  256-bit private key does not fit. An implementation that reduces the secret modulo $P$ instead
  of rejecting it destroys the key silently. This engine raises.
- **Reconstructing with exactly $K$ shares and trusting the answer.** Any $K$ points define
  *some* degree-$(K-1)$ polynomial, so a corrupt share yields a clean-looking wrong key with no
  error anywhere. Only a $(K+1)$-th share makes the inconsistency detectable.
- **Padding a short share set by repeating a share.** Two copies of the same point are one point;
  the Lagrange denominator becomes zero. Under a Fermat-based inverse ($a^{P-2} \bmod P$) that
  returns 0 rather than raising, and the caller gets an unrelated integer that looks like a key.
- **A composite modulus.** Change the modulus to a non-prime and inverses stop existing; a
  perfectly good share set then reconstructs to the wrong value with no error. The modulus is
  primality-checked at construction.
- **Losing the leading zero byte.** `int.from_bytes` drops leading zeros, so a 32-byte key
  starting `00` reconstructs as 31 bytes — a different key. Use the bytes API, which length-tags.
- **$K=1$, or all shares in one facility.** Both collapse the scheme back to a single point of
  compromise; the second one silently, since the split *looks* correct. CCSS 1.03.3 Level II
  requires backups be held geographically separate from the primary key's usage location.
- **Treating the scheme as authenticated.** Plain Shamir has no dealer commitment. A shareholder
  who knows the polynomial can forge a consistent share; only VSS or a signed share envelope
  defends against that.

## Verification

- Instantiate `ShamirSecretSharingForKeyBackupEngine` (default field M_521). Split a real 32-byte
  key with `split_secret_bytes(key, threshold_k=3, total_shares_n=5)` $\implies$ 5 shares, each
  carrying `threshold_k=3` and the modulus. Reconstruct from shares #1, #3, #5 $\implies$ exact
  byte-for-byte key, same length. Flip one bit in share #3 and supply 4 shares $\implies$
  `ShamirSecretSharingError` (inconsistent set), not a wrong key. Supply only 2 shares
  $\implies$ `ShamirSecretSharingError` (sub-threshold). Duplicate share #1 to make three
  $\implies$ `ShamirSecretSharingError` (duplicate index).
- Reconstruct the hand-computed polynomial $f(x) = 7 + 3x + 5x^2$ from the points
  $(1,15), (2,33), (3,61)$ $\implies$ 7, matching Lagrange interpolation done by hand.
- Run `python -m unittest discover -s skills/shamir-secret-sharing-for-key-backup/scripts`.

## Related Skills

- `hardware-security-module-hsm-for-signing-keys`
- `multi-party-computation-mpc-custody-solutions`
- `recovery-plan-for-lost-or-compromised-keys`
- `cold-storage-geographic-distribution-strategy`
- `air-gapped-signing-workflow-for-cold-storage`
