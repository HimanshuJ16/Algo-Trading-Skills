# Deep Workflow Reference — backtest-audit-trail-for-regulatory-review

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Capture Lineage Metadata**: Extract git commit SHA, user ID, system timestamp, and OS platform metadata.
2. **Compute Input Data Checksums**: Generate SHA256 hashes of all input CSV/Parquet files.
3. **Assemble Audit Manifest**: Combine code SHA, data hashes, parameters, and resulting Sharpe/drawdown performance metrics.
4. **Generate Immutable Audit Cryptographic Hash**: Compute SHA256 signature of canonical manifest payload.

## Production Implementation Reference

- Reference code: `scripts/regulatory_audit_trail.py` (`RegulatoryAuditTrailEngine`, `BacktestAuditManifest`).
- Automated unit tests: `scripts/test_regulatory_audit_trail.py`.
