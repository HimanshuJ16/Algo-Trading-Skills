# Pre-Flight / Sign-off Checklist — backtest-audit-trail-for-regulatory-review

Use this before considering the skill's implementation complete.

## Key Management (blocking — nothing below matters without this)
- [ ] Signing key is ≥32 random bytes (`secrets.token_bytes(32)`), not a passphrase
- [ ] Key loaded from a secrets manager / environment variable, **not** from the repo
- [ ] Key is **not** stored in the same bucket, repo, or backup set as the manifests
- [ ] `signing_key_id` recorded, and retired keys retained as long as the manifests they signed
- [ ] Key rotation procedure documented

## Prerequisites
- [ ] Git working tree clean — verified with `git status --porcelain` (empty output)
- [ ] All input data files identified and accessible
- [ ] Strategy hyperparameters documented

## Manifest Generation
- [ ] **Git commit SHA** recorded (from `git rev-parse HEAD`, against a clean tree)
- [ ] **Data files hashed** via SHA256, one checksum per file, streamed
- [ ] **No aggregate/concatenated checksum** used in place of per-file hashes
- [ ] **Platform metadata** captured (OS, release, architecture)
- [ ] **Python version** captured
- [ ] **Execution timestamp** captured as integer nanoseconds UTC
- [ ] **Organization identifier** and **strategy ID** set correctly
- [ ] **Hyperparameters** fully captured (no omitted config)
- [ ] **Performance metrics** complete and all finite (no NaN/Infinity)
- [ ] **Data sources** listed (vendors, exchanges, date ranges)
- [ ] Library versions / CI build ID placed in `parameters` or `notes` if required — they are not captured automatically

## Integrity & Authenticity
- [ ] **Canonical JSON**: sorted keys, no whitespace, `allow_nan=False`
- [ ] **`content_digest_sha256`** computed (corruption detection)
- [ ] **`manifest_hmac_sha256`** computed (authenticity)
- [ ] Verification checks the **HMAC**, not only the digest
- [ ] Comparisons use `hmac.compare_digest`, not `==`
- [ ] **Forgery test passed**: edit a metric, recompute the digest, confirm verification still fails
- [ ] Manifest signed with a different key, or different `signing_key_id`, fails verification
- [ ] Round-trip verified: build → export → load → verify
- [ ] Mutating caller-owned dicts after `build_manifest` does not alter the manifest

## Regulatory Alignment (confirm applicability before ticking)
- [ ] Firm's registration status determined — 17 CFR 240.17a-4 applies to registered broker-dealers, not to unregistered funds
- [ ] Applicable retention tier determined from the record's classification under 17a-3 — **not** assumed to be 6 years
- [ ] Storage meets 17a-4(f)(2)(i): WORM **or** audit-trail system able to recreate modified/deleted records
- [ ] No claim made that this manifest satisfies SEC Rule 613 / CAT — CAT covers NMS order events, not research records
- [ ] No claim made that RTS 25 clock accuracy applies to research artifacts — it governs business clocks for reportable trading events
- [ ] Documentation does **not** describe the output as immutable, tamper-proof, or non-repudiable
- [ ] If assurance against the issuing firm is required, an external trust anchor is in place (asymmetric signing with independently held key, RFC 3161 TSA, or third-party WORM)

## Automated Testing
- [ ] Run `python -m unittest discover -s skills/backtest-audit-trail-for-regulatory-review/scripts` — 100% pass rate (32 tests)
- [ ] Coverage confirmed: forgery resistance, per-field tamper detection, manifest isolation, canonicalisation, input validation, checksum correctness, round-trip

## Sign-off
- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (dev/CI/prod): ___________________________
- Compliance review of regulatory claims by: ___________________________
