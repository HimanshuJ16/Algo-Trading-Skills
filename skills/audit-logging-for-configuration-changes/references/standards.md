# Standards for Audit Logging

Regulatory applicability is jurisdiction- and entity-dependent. Encode only
what applies to your firm, and keep compliance decisions auditable.

| Regulatory Standard | Scope / Applicability | Requirement | Implementation |
|---|---|---|---|
| **FINRA Rule 3110** (Supervision) | FINRA member broker-dealers | Written supervisory procedures and supervisory records; retention of designation and inspection records for >=3 years. | Every config change carries an authenticated `user_id` and `justification`; rejected attempts are also retained. |
| **FINRA Regulatory Notice 15-09** | Firms engaging in algorithmic trading | Documented change-management process for trading code and risk-control parameter settings; archive code versions; track significant system problems. | `ConfigChangeRecord` captures `old_value`/`new_value`, principal, reasoning, timestamp, and sequence for forensic reconstruction. |
| **SEC Rule 17a-4** | SEC-registered broker-dealers | Books-and-records retention with WORM-style preservation for prescribed periods. | Emitted JSON is forwarded to a WORM-compliant sink (e.g., S3 Object Lock); the hash chain does not replace WORM retention. |
| **SEC Regulation SCI** (Rules 1000-1007) | "SCI entities": SROs, certain ATSs, plan processors, certain exempt clearing agencies (a 2023 proposal would add certain large broker-dealers and SBSDRs) | Resiliency, systems integrity, and forensic reconstruction of "SCI systems" changes; recordkeeping under Rules 1005-1007. | High-precision UTC timestamps, strict `old_value`->`new_value` mapping, sequence numbers, and SHA-256 hash chaining for tamper detection. Firms not mandated by SCI may adopt these controls voluntarily. |
| **NIST SP 800-92** (Guide to Computer Security Log Management) | Advisory best practice | Log integrity checking via message digests; protect digests via read-only media or encryption; normalize timestamps. | Per-record SHA-256 `record_hash` chained via `prev_hash`; canonical (sorted-key) JSON; UTC ISO-8601 timestamps. |

## Integrity model

Tamper *detection* is provided in-process by the hash chain; tamper
*prevention* is provided by the downstream WORM/SIEM sink. The two layers are
complementary: the hash chain reveals modification or deletion of any record
(and gaps in the sequence), while WORM storage makes such modification
infeasible at rest.

## Category
`deployment-ops` / `regulatory`
