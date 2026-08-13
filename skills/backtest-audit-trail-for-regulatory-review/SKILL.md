---
name: backtest-audit-trail-for-regulatory-review
description: Use when conducting production backtests to record an HMAC-authenticated
  audit trail capturing code git commit SHA, per-file data checksums (SHA256),
  hyperparameter manifest, and execution environment metadata for internal governance
  and regulatory review (SEC Rule 17a-4 recordkeeping context).
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- regulatory-compliance
- audit-trail
- reproducibility
- sec-compliance
- data-lineage
- hmac-integrity
brokers_frameworks:
- Regulatory Audit Trail Engine
- Python hashlib/hmac (standard library)
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when performing strategy validation prior to live deployment or institutional client presentation, and you need a durable record of exactly what code, data, and parameters produced a set of backtested numbers. It captures git commit SHA, per-file input data checksums, hyperparameters, performance metrics, and execution environment, then authenticates the record with HMAC-SHA256 so later modification by anyone without the signing key is detectable.

## When NOT to Use

- **As proof to a regulator that your firm did not alter its own results.** HMAC is symmetric: whoever can sign can also forge. This gives integrity against outsiders, not non-repudiation against the issuer. See the trust-boundary note below.
- **As a substitute for compliant recordkeeping storage.** Under 17 CFR 240.17a-4(f) the durability obligation is met by the storage system (WORM or the audit-trail alternative), not by a hash embedded in a file.
- **For live order/trade event capture.** Order and execution event reporting is a different obligation with different systems — see the CAT and RTS 25 notes in `references/standards.md`.
- **When you have not yet eliminated lookahead bias.** An impeccably signed manifest for a biased backtest is a precisely documented wrong answer.

## Trust Boundary — read before relying on this

An unkeyed hash over a manifest is **not** tamper-evidence. Anyone can edit the file, recompute the hash, and every check passes. Earlier versions of this skill made exactly that mistake. This version emits two separate values:

| Value | What it proves | What it does not prove |
|---|---|---|
| `content_digest_sha256` | The bytes were not corrupted in transit or at rest. Anyone can recompute it. | Nothing about authenticity. |
| `manifest_hmac_sha256` | The record was produced by a holder of the signing key and has not been altered since. | Nothing against the key holder themselves. |

For assurance against the issuing firm, anchor trust outside it: asymmetric signatures with the private key held independently, an RFC 3161 timestamp authority, or third-party-administered write-once storage.

## Prerequisites

- Git repository with clean working tree — a commit SHA taken against uncommitted changes documents code that never existed.
- Input data files accessible for checksumming.
- Strategy parameter configuration (all tunable hyperparameters).
- A signing key of ≥32 random bytes from a secrets manager or environment variable — **never** committed to the repository and never stored alongside the manifests it authenticates.
- Standard library only: `hashlib`, `hmac`, `json`, `platform`.

## Workflow

1. **Capture Lineage Metadata**: Record git commit SHA (`git rev-parse HEAD`), UTC timestamp as integer nanoseconds, OS/architecture, and Python version. Confirm the working tree is clean first — if `git status --porcelain` is non-empty, stop; the SHA does not describe the code that ran.
2. **Compute Per-File Data Checksums**: Stream each input file to compute SHA256 individually. Never hash a concatenation of files: a single aggregate digest tells you something changed but not which file, which is exactly what an auditor asks.
3. **Assemble Manifest**: Organization, strategy ID, git SHA, per-file checksum map, all hyperparameters, performance metrics, execution metadata, data sources, notes. Any field left out of the signed payload is a field an attacker can change undetected — including the key ID.
4. **Digest and Authenticate**: Serialize as canonical JSON (sorted keys, no whitespace, `allow_nan=False`), then compute both the unkeyed digest and the keyed HMAC. Reject non-finite metrics rather than serializing them — Python writes bare `NaN`, which is invalid JSON and unparseable by strict verifiers in other languages.
5. **Verify on Load**: Check the HMAC, not just the digest. A manifest whose digest matches but whose HMAC does not has been re-digested by someone without the key — treat it as forged, not as corrupted.
6. **Store & Archive**: Write to storage whose retention controls match your regulatory status. If you are a registered broker-dealer, 17 CFR 240.17a-4(f) requires either WORM or an audit-trail system that can recreate a modified or deleted record. Determine the applicable retention tier from the record's classification under 17a-3 — do not assume six years applies (see `references/standards.md`).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Calling an Unkeyed Hash a Signature**: A plain SHA256 embedded in the record it describes is a checksum. Recomputing it after editing takes three lines. Use a keyed MAC or an asymmetric signature.
- **Testing Tamper Detection Without Re-Signing**: Mutating a field and asserting verification fails only proves the digest covers that field. The real adversary recomputes the digest. A tamper test that never re-signs tests nothing about forgery.
- **Storing the Signing Key With the Manifests**: A key in the same bucket, repo, or backup set as the records it authenticates provides no protection against anyone who can reach the records.
- **Untracked Code Modifications**: Running a backtest against a dirty working tree — the SHA is meaningless for audit.
- **Single Aggregate Checksum**: Hashing concatenated data instead of per-file checksums masks which input changed.
- **Non-Canonical JSON Serialization**: Unsorted keys, incidental whitespace, or `NaN`/`Infinity` values break verification across implementations.
- **Aliasing Caller State**: Holding a reference to the caller's parameter dict rather than a copy means the "immutable" manifest silently changes after signing.
- **Float Formatting Across Languages**: Metrics stored as JSON floats may re-serialize differently in another runtime and break byte-level canonical comparison. Where cross-toolchain verification matters, encode metrics as decimal strings.
- **Assuming a Retention Period**: 17a-4 sets different tiers for different record classes and applies to registered broker-dealers. Asserting "six years" for a research artifact is unsupported.

## Verification

- Run `python scripts/test_regulatory_audit_trail.py` — 100% pass rate (32 tests).
- **Forgery test (the one that matters)**: modify a metric, recompute `content_digest_sha256`, then verify. The digest will match and verification must still fail on the HMAC.
- Confirm a manifest signed with a different key, or a different `signing_key_id`, fails verification.
- Confirm mutating the caller's parameter dict after `build_manifest` does not change the manifest.
- Confirm non-finite metrics are rejected at build time rather than serialized.

## Related Skills

- `backtest-determinism-and-reproducibility` — ensures bit-identical runs; this skill records what was run
- `paper-to-live-promotion-checklist` — final gate before live capital; requires a valid audit trail
- `lookahead-bias-elimination` — must pass before an audit trail is meaningful
- `walk-forward-validation-setup` — validation methodology whose results belong in the manifest
- `centralized-secrets-management-vault-integration` — where the signing key should live
- `record-retention-periods-by-jurisdiction` — determining the applicable retention tier
---
