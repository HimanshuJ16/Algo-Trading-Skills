# Deep Workflow Reference — backtest-audit-trail-for-regulatory-review

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## 0. Key Management (do this first)

The HMAC key is the entire basis of tamper detection. Everything below is
theatre without it.

- Generate with `secrets.token_bytes(32)` or longer. Not a passphrase.
- Store in a secrets manager or injected environment variable. Never in the
  repository, never in the manifest, never in the same bucket or backup set as
  the manifests it authenticates.
- Record a `signing_key_id` so keys can be rotated without invalidating history.
  The key ID is part of the signed payload, so it cannot be swapped after the
  fact to point verification at a different key.
- On rotation, retain retired keys for as long as the manifests they signed are
  retained — otherwise old records become unverifiable.

## 1. Capture Lineage Metadata

- **Git commit SHA**: from `git rev-parse HEAD`. Before trusting it, check
  `git status --porcelain`; if output is non-empty the tree is dirty and the SHA
  describes code that never ran. The engine does not check this for you.
- **Execution timestamp**: integer nanoseconds since epoch (`time.time_ns()`).
  Integers are used deliberately — a float epoch re-serialises differently
  across language runtimes and would break canonical byte comparison.
- **Platform metadata**: OS, release, architecture.
- **Python version**: captured via `platform.python_version()`.

Library versions and CI build identifiers are *not* captured automatically. If
your review process requires them, put them in `parameters` or `notes` so they
fall inside the signed payload.

## 2. Compute Input Data Checksums

- Stream each input file and compute SHA256 per file. Streaming matters for
  multi-GB Parquet; the default chunk size is 64 KiB.
- Record a filepath → checksum map. Duplicate paths are rejected rather than
  silently collapsed, which would understate the input set.
- **Never** hash a concatenation of files. A single aggregate digest tells an
  auditor that something changed but not which file.

## 3. Assemble Manifest

Everything an auditor cares about must be inside the signed payload:
organization, strategy ID, git SHA, checksum map, hyperparameters, metrics,
timestamp, platform, Python version, signing key ID, data sources, notes.
A field outside the payload is a field an attacker can change for free.

Caller-supplied dicts are deep-copied on build. Without that, the manifest
aliases live caller state and can change after signing.

## 4. Digest and Authenticate

- Serialise canonically: `sort_keys=True`, `separators=(",", ":")`,
  `ensure_ascii=True`, `allow_nan=False`.
- `allow_nan=False` is a correctness control, not a style choice. Python emits
  bare `NaN` / `Infinity`, which RFC 8259 does not permit; a strict parser in
  another language rejects the file outright, so the manifest could never be
  independently verified.
- Compute **both**:
  - `content_digest_sha256` — unkeyed SHA256. Corruption detection only.
  - `manifest_hmac_sha256` — HMAC-SHA256 over the same bytes. Authenticity.

## 5. Verify

Verification requires **both** values to match, compared in constant time via
`hmac.compare_digest`.

Interpret the failure modes differently:

| Digest | HMAC | Meaning |
|---|---|---|
| match | match | Record is authentic. |
| mismatch | mismatch | Corrupted or naively edited. |
| **match** | **mismatch** | **Re-digested by a party without the key. Treat as forged.** |
| mismatch | match | Not reachable in practice; indicates a code or storage bug. |

The third row is the case an unkeyed-hash design cannot see at all.

## 6. Store & Archive

- Write to storage whose controls match your regulatory status. For registered
  broker-dealers, 17 CFR 240.17a-4(f)(2)(i) permits either WORM **or** an
  audit-trail system able to recreate a modified or deleted record.
- Determine the retention tier from the record's classification under 17a-3.
  Do not assume six years — see `references/standards.md`.
- Keep the verification tooling and retired signing keys archived alongside the
  records, or the archive becomes unverifiable before it becomes unneeded.

## Worked Example

```python
import os
from regulatory_audit_trail import RegulatoryAuditTrailEngine

engine = RegulatoryAuditTrailEngine(
    signing_key=bytes.fromhex(os.environ["AUDIT_SIGNING_KEY_HEX"]),
    organization="AlphaHedge LLC",
    signing_key_id="2024-q1",
)

manifest = engine.build_manifest(
    strategy_id="stat_arb_v3",
    git_commit_sha="41c1d05a8b792e01",
    data_checksums=engine.compute_data_checksums(["data/train.parquet"]),
    parameters={"lookback": 20, "z_entry": 2.0},
    metrics_summary={"sharpe_ratio": 2.15, "max_drawdown_pct": 8.4},
    data_sources=["NYSE", "NASDAQ"],
)

engine.verify_manifest(manifest)          # True
archived = engine.export_manifest_json(manifest)
engine.load_manifest_from_json(archived)  # raises ManifestVerificationError if altered
```

## Production Implementation Reference

- Reference code: `scripts/regulatory_audit_trail.py`
  (`RegulatoryAuditTrailEngine`, `BacktestAuditManifest`, `AuditManifestError`,
  `ManifestVerificationError`).
- Automated unit tests: `scripts/test_regulatory_audit_trail.py`.
- Regulatory scope and limitations: `references/standards.md`.

## Failure Modes Observed in Production

- **Unkeyed hash treated as a signature**: the manifest is edited and re-hashed;
  every check passes. This was the defect fixed in 2.0.0.
- **Signing key stored beside the manifests**: anyone who can reach the records
  can re-sign them.
- **Dirty working tree**: SHA reflects code that never ran.
- **Missing data hashes**: no record of which dataset version was used.
- **Non-canonical JSON**: sorting, whitespace, or `NaN` differences break
  verification across implementations.
- **Partial manifests**: omitting metrics or parameters leaves gaps an auditor
  will ask about.

## Notes for Agent Implementers

- Do not describe the output as "immutable" or "tamper-proof". It is
  tamper-*evident to parties without the key*. Overstating this in a compliance
  context is worse than omitting it.
- When a verification fails, report *which* check failed. Digest-match with
  HMAC-mismatch is a forgery signal and should escalate differently from a
  plain corruption failure.
- Verify round-trip (build → export → load → verify) before considering an
  integration complete.
