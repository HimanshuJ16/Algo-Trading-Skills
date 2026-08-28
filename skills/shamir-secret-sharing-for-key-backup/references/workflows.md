# Workflows — shamir-secret-sharing-for-key-backup

## 0. Decide the field before anything else

The secret is a field element, so the modulus caps what can be shared. Check
`engine.max_secret_bytes` against `len(secret)` **before** splitting:

| Field | Bits | `max_secret_bytes` | Holds a 32-byte key? | Holds a 64-byte BIP-39 seed? |
|---|---|---|---|---|
| `MERSENNE_M127` = $2^{127}-1$ | 127 | 15 | No | No |
| `MERSENNE_M521` = $2^{521}-1$ (default) | 521 | 64 | Yes | Yes |

A secret that does not fit is rejected, never reduced mod $P$ — reduction would
return a different key that reconstructs perfectly and unlocks nothing.

A custom modulus is Miller–Rabin tested at construction. This is not ceremony: a
composite modulus leaves Lagrange denominators non-invertible, so a *correct*
share set reconstructs to a wrong value with no error raised anywhere.

## 1. Choose $(K, N)$

Two opposing failure modes, and both are permanent:

- **$K$ too low** — any $K$ colluding custodians (or one thief who reaches $K$
  storage sites) reconstruct the key and spend. $K = 1$ is rejected outright:
  every share would be a verbatim copy.
- **$K$ too high relative to $N$** — one unreachable custodian, one flood, one
  bereavement and the key is gone. $N - K$ is the number of shares you can afford
  to lose; make it at least 1, and remember that step 5 wants a $(K+1)$-th share
  present at every reconstruction, not just held somewhere.

$3$-of-$5$ is the common institutional starting point: it tolerates two lost
shares and requires three simultaneous compromises. It is a starting point, not a
standard — no source surveyed prescribes a threshold.

## 2. Split

```python
engine = ShamirSecretSharingForKeyBackupEngine()          # M_521 by default
result = engine.split_secret_bytes(key_bytes, threshold_k=3, total_shares_n=5)
```

What happens inside, and why each part matters:

1. `0x01` is prepended to the secret before the integer conversion, so a key
   beginning `00` keeps its length. Without the tag, `int.from_bytes` drops
   leading zeros and a 32-byte key returns as 31 bytes.
2. Coefficients $a_1 \dots a_{K-1}$ come from `secrets.randbelow` (CSPRNG). A
   `random.Random` here would let anyone who recovers the PRNG state derive the
   polynomial from a single share.
3. The leading coefficient is resampled if it lands on zero. A zero $a_{K-1}$
   drops the polynomial to degree $K-2$, so $K-1$ shares would suffice — the
   threshold you documented would not be the threshold you have.
4. Each share carries `index`, `value`, `threshold_k` and `modulus`. The metadata
   leaks nothing (the field is public, and $K$ is operational, not secret) and is
   what lets reconstruction distinguish "not enough shares" from "enough shares".

Use `split_secret` (integer API) only for values that are genuinely integers. For
key material, the byte API is the correct entry point.

## 3. Distribute

- One share per custodian, delivered separately — NIST SP 800-57 §8.1.5.2.2.1:
  "each key share **shall** be distributed separately to its intended recipient."
- Record the `index` with the share. A `value` whose $x$ coordinate is lost is
  not a partial share; it is nothing.
- Record `threshold_k` and the modulus alongside it, plus which key it belongs to
  and the split date. A drawer full of untagged numbers is unusable in exactly the
  situation the backup exists for.
- Keep fewer than $K$ shares in any one facility, jurisdiction or key-holder
  group. Share *placement* is audited by
  `cold-storage-geographic-distribution-strategy`; CCSS 1.03.3 Level II requires
  geographic separation from the primary key's usage location.
- Destroy the `SSSResult` and the source key material on the splitting host. It is
  an air-gapped host, and the split is the only thing that ever ran on it.

## 4. Collect $K+1$ shares

The surplus share is the entire integrity story for plain Shamir. Any $K$ points
define *some* degree-$(K-1)$ polynomial, so with exactly $K$ shares a mistyped
digit yields a well-formed wrong key and nothing can tell. A $(K+1)$-th share is
checked against the polynomial the first $K$ define; a mismatch proves the set is
corrupt.

It proves the *set* is corrupt, not which share is at fault — and it cannot detect
a share forged by someone who knows the polynomial. That needs VSS, which this is
not.

## 5. Reconstruct

```python
key_bytes = engine.reconstruct_secret_bytes(shares)       # k+1 shares
```

`reconstruct_secret` / `reconstruct_secret_bytes` raise `ShamirSecretSharingError`
rather than return a value when:

| Condition | Why it is fatal rather than recoverable |
|---|---|
| Fewer shares than the recorded `threshold_k` | Interpolating anyway returns an unrelated field element that looks exactly like a key |
| Duplicate share index | The same point twice is one point; the Lagrange denominator is zero |
| Share index 0 | $x=0$ *is* the secret; accepting it lets a caller "recover" what they handed in |
| Share value outside $[0, P)$ | Not a field element — transcription error or wrong field |
| Share from another modulus | Silently wrong result otherwise |
| Shares declaring different thresholds | They are not from one split |
| $>K$ shares that disagree | At least one share is corrupt |
| Reconstructed value with no `0x01` tag (bytes API) | Integer-API shares, or a corrupt set |

Treat every one of these as "fetch another share". Retrying with *fewer* shares to
get past an error is the one response that turns a detected problem into an
undetected wrong key.

## 6. Confirm out-of-band

Interpolation always returns *a* value. Derive the public key or deposit address
from the reconstructed material and compare it against the recorded one before
acting on it. That comparison, not the absence of an exception, is what proves the
recovery worked.

## 7. After reconstruction

Reconstruction reassembles the whole key in one process. Treat the host as
contaminated: it should be air-gapped, wiped afterwards, and never a trading host.
If the key was reconstructed for any reason other than a rehearsed drill, run the
compromise path in `recovery-plan-for-lost-or-compromised-keys` — re-split at
minimum, and re-key if the reconstruction was not fully supervised.

## Drill cadence

Reconstruct from a *different* $K+1$ subset each drill, so every share is exercised
over time. A share nobody has read since it was written is an assumption, not a
backup. CCSS Level III sets an annual floor for testing the key compromise policy;
`recovery-plan-for-lost-or-compromised-keys` defaults to a stricter 90 days.
