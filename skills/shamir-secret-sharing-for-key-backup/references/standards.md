# Standards — shamir-secret-sharing-for-key-backup

## Scope and status

Key backup is governed by *security standards and certification schemes*, not by a
market regulator's rulebook. No securities or derivatives regulator surveyed
prescribes a threshold, a field size or a share count. Where this skill fixes a
number, it is an engineering choice and is labelled as one below.

## Shamir (1979) — the scheme itself

> "Our goal is to divide $D$ into $n$ pieces $D_1, \dots, D_n$ in such a way that:
> (1) knowledge of any $k$ or more $D_i$ pieces makes $D$ easily computable;
> (2) knowledge of any $k-1$ or fewer $D_i$ pieces leaves $D$ completely
> undetermined (in the sense that all its possible values are equally likely)."
> — §1, p. 612

The construction is a random degree-$(k-1)$ polynomial over a finite field with the
secret as its constant term, reconstructed by Lagrange interpolation at $x=0$. Two
consequences drive this implementation:

1. **The modulus must exceed the secret.** The secret is a field element. A key
   larger than $P$ cannot be represented, and reducing it mod $P$ silently
   destroys it. This is why the default field is $M_{521}$, not $M_{127}$.
2. **Secrecy is perfect for the value, not for the size.** The field is public, so
   a shareholder learns the secret is smaller than $P$. Shamir's "completely
   undetermined" is a statement about the value within the field.

Source: A. Shamir, "How to Share a Secret", *Communications of the ACM* 22(11),
612–613, November 1979,
[doi:10.1145/359168.359176](https://dl.acm.org/doi/10.1145/359168.359176).

## NIST SP 800-57 Part 1 Rev. 5 — split knowledge, backup, distribution

| Location | What it says | Effect here |
|---|---|---|
| §3 Glossary, "Split knowledge" | A key is split into $n$ shares, "each of which provides no knowledge of the key … If knowledge of $k$ … shares is required to construct the key, then knowledge of any $k-1$ key shares provides **no information about the key other than, possibly, its length**." | The confidentiality claim in `SKILL.md` is worded to include the length caveat rather than claiming "zero information". |
| §8.1.5.2.2.1 Manual Key Distribution | "If split-knowledge procedures are used for key distribution … **each key share shall be distributed separately to its intended recipient**", and shares "shall either be wrapped … or distributed using appropriate physical security procedures." | Workflow step 4. Hand-delivering two shares to one custodian defeats the split before it is stored. |
| §8.2.2.1 Backup Storage | "The backup of keying material on an independent, secure storage media provides a source for key recovery." | Why this skill exists at all: shares *are* the backup medium. |

Mandatory language (`shall`) in SP 800-57 binds US federal systems and anyone
contractually held to it; for a private trading firm it is best practice, not law.

Source: [NIST SP 800-57 Part 1 Rev. 5](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-57pt1r5.pdf)
(May 2020, current final revision).

## CCSS (C4) — crypto-native backup requirements

The CryptoCurrency Security Standard is a three-level certification scheme run by
the CryptoCurrency Certification Consortium. It is voluntary, not law.

| Requirement | Level | Wording | Bearing on a Shamir split |
|---|---|---|---|
| 1.03.2 Key material backup(s) | I / II | A backup of the key/seed must exist (L1); a backup must exist for at least as many keys as are required to spend funds (L2) | The share set *is* the backup; $N-K$ is the loss tolerance |
| 1.03.3 Environmental protection for backup(s) | II | "The backup key/seed must be stored in a location that is geographically separate from the usage location of the primary key/seed" | Shares in one facility satisfy the arithmetic and defeat the purpose |
| 1.02.3 Geographic distribution of keys | II | "Any keys that have signing authority on a single wallet must be stored in different locations" | Same rule applied to a multi-signer roster |

Requirement numbering verified against the C4 CCSS requirement details page; see
also `mappings/regulatory-coverage.md`, which maps CCSS 1.03.2/1.03.3 to this
skill and to `recovery-plan-for-lost-or-compromised-keys`.

Source: [C4 — CCSS details](https://cryptoconsortium.org/cryptocurrency-security-standard-documentation/details/).

## SLIP-0039 — contrast, not compliance

This engine is **not** SLIP-0039 and produces nothing a SLIP-0039 wallet can read.
The differences are load-bearing, so they are recorded rather than glossed over:

| Aspect | SLIP-0039 | This engine |
|---|---|---|
| Field | GF(256), byte-wise, Rijndael polynomial | GF(p), $p = 2^{521}-1$ by default |
| Share encoding | Mnemonic words + RS1024 checksum | `(index, value)` integers |
| Index/threshold width | 4 bits each ⇒ $N_i \le 16$ members per group, $G \le 16$ groups | Bounded only by the field |
| Integrity | RS1024 checksum per share, plus a digest share at index 254 giving $2^{-32}$ acceptance of an invalid set | Cross-check against a surplus share; no checksum, no digest share |
| Threshold floor | "If the member threshold $T_i$ of a group is 1, then the size $N_i$ … SHOULD also be 1" | $K \ge 2$ enforced |
| Minimum secret | ≥ 128 bits, length a multiple of 16 bits | Any 1..64 bytes (M_521) |
| Groups | Two-level group/member structure | Single level |

SLIP-0039 is a SatoshiLabs Improvement Proposal (status: Final). It is **not** an
RFC, and there is no IETF RFC for Shamir's Secret Sharing — an earlier version of
this skill cited one, and it does not exist.

The $N \le 16$ figure that appeared in this file as a universal "standard
requirement" is a SLIP-0039 encoding limit and does not apply to this engine.

Source: [SLIP-0039](https://github.com/satoshilabs/slips/blob/master/slip-0039.md).

## Engineering choices (not external requirements)

| Choice | Value | Basis |
|---|---|---|
| Default modulus | $M_{521} = 2^{521}-1$ | Smallest well-known Mersenne prime that holds a 32-byte key *and* a 64-byte BIP-39 seed with the length tag; also the NIST P-521 field prime. Primality independently confirmed by Lucas–Lehmer in the test suite |
| `MIN_THRESHOLD` | 2 | $K=1$ makes every share a full key copy. Matches SLIP-0039's SHOULD and the `min_shamir_threshold` default in `recovery-plan-for-lost-or-compromised-keys` |
| `LENGTH_TAG` | `0x01` | Preserves leading zero bytes across the integer round trip. A shape check, **not** an integrity check: a corrupt set passes it ~1 time in 256 |
| Miller–Rabin rounds | 32, random bases | Guards a caller-supplied modulus. Random bases (from `secrets`) so a crafted pseudoprime cannot be tuned against a fixed base set. Costs ~25 ms once for an unknown 600-bit modulus; known moduli skip it |
| Collect $K+1$ shares | recommendation | The only integrity signal plain Shamir offers. Mirrors `min_shamir_surplus_shards = 1` in `recovery-plan-for-lost-or-compromised-keys` |

## Not covered

- **Verifiable secret sharing.** No Feldman/Pedersen commitment, so a malicious
  shareholder's forged share is indistinguishable from a good one.
- **Proactive resharing.** Refreshing shares without changing the secret is a
  separate protocol; see `multi-party-computation-mpc-custody-solutions`.
- **Memory hygiene.** Python cannot wipe an immutable int. Assume the secret is
  recoverable from process memory and a core dump for the lifetime of the process.
- **Share storage, custody rosters and share geography.** See
  `cold-storage-geographic-distribution-strategy` and
  `segregation-of-duties-for-custody-operations`.

## Category

`crypto-custody-security` — see the top-level `mappings/` directory for how this
category rolls up across the full skill library.
