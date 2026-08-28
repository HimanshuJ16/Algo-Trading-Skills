# Pre-Flight Checklist

## Before splitting
- [ ] Is `len(secret)` $\le$ `engine.max_secret_bytes` (64 for M_521, 15 for M_127)?
- [ ] Is the field wide enough for the *real* secret — a 256-bit key does **not** fit in $M_{127}$?
- [ ] If a custom modulus is used, is it prime (the engine Miller–Rabin tests it) and is the reason for not using the default written down?
- [ ] Is the split running on an air-gapped host, not a trading host — accepting that Python cannot wipe the key from memory?
- [ ] Is $K \ge 2$, so no single share is a full copy of the key?
- [ ] Is $N - K \ge 1$, so losing one custodian does not lose the key?
- [ ] Is $K$ greater than the number of shares any single person, site or jurisdiction will hold?

## Splitting
- [ ] Is `split_secret_bytes` used for key material, so a leading `00` byte is not silently dropped?
- [ ] Are coefficients from `secrets`, never `random`?
- [ ] Does each share carry `index`, `threshold_k` and `modulus` as recorded metadata?
- [ ] Have the `SSSResult` and the source key material been destroyed on the splitting host?

## Distribution
- [ ] Is each share delivered separately to its custodian (NIST SP 800-57 §8.1.5.2.2.1)?
- [ ] Is the share index recorded with the value — a value without its $x$ coordinate is unusable?
- [ ] Is it recorded which key, which split and which date the share belongs to?
- [ ] Are shares geographically separated from the primary key's usage location (CCSS 1.03.3 L2)?
- [ ] Does the custody roster leave every single location holding fewer than $K$ shares?

## Reconstruction
- [ ] Are $K+1$ shares collected, not exactly $K$ — the only integrity check plain Shamir offers?
- [ ] Did reconstruction complete without a `ShamirSecretSharingError`, rather than after retrying with fewer shares?
- [ ] Was a WARNING logged about "no integrity cross-check"? If so, a corrupt share would be undetected.
- [ ] Does the derived public key or address match the recorded one — checked *outside* the scheme?
- [ ] Is the byte length of the recovered material exactly what was split?

## After
- [ ] Has the reconstruction host been wiped, and treated as contaminated?
- [ ] If the reconstruction was not a supervised drill, has the compromise path in `recovery-plan-for-lost-or-compromised-keys` been run?
- [ ] Did this drill use a *different* $K+1$ subset than the last one, so every share gets exercised?

## Claims to not make
- [ ] These shares are **not** SLIP-0039 and no third-party wallet will read them.
- [ ] This is **not** verifiable secret sharing: a share forged by someone who knows the polynomial is undetectable.
- [ ] $K-1$ shares reveal nothing about the key's *value* — but the public field size still bounds its length.
