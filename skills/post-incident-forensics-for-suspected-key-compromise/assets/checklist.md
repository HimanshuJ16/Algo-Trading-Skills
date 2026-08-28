# Pre-Flight Checklist

## Containment (do not wait for the analysis)

- [ ] Has use of the key to apply cryptographic protection already ceased, and has revocation started? (NIST SP 800-57 Pt.1 Rev.5 §5.5 — "shall cease … shall be revoked"; §8.3.5 permits emergency revocation on reason to believe disclosure.)
- [ ] Is it understood that no standard prescribes a numeric revocation SLA? §8.3.5 says "as soon as feasible"; any second-count target is your own operational objective, not a regulatory requirement.
- [ ] Does the revocation scope cover every key sharing a seed or HSM module, and is the re-keying monitored so no affected key is missed? (§5.5.2)
- [ ] Does the revocation notice identify the key **excluding the key itself**? (§8.3.5)

## Evidence acquisition

- [ ] Were raw log artifacts hashed **at acquisition**, before parsing, and are those digests passed as `source_artifact_digests`?
- [ ] Are `custodian` and `collected_at` recorded for every acquisition? (RFC 3227 §4.1)
- [ ] Was the source system's clock measured against UTC and passed as `clock_offset_seconds`? (RFC 3227 §3.2)
- [ ] Was collection ordered by volatility — live signer/memory state before disk, disk before archival stores? (RFC 3227 §2.1)
- [ ] Is the analysis running on copies, with the source artifacts untouched?

## Policy inputs

- [ ] Is `authorized_networks` built from the infrastructure source of truth, **not** from the logs under audit?
- [ ] Are CIDR blocks used where the real allowlist is a range, rather than enumerating single IPs?
- [ ] Is the allowlist neither empty nor an all-addresses block? (Both are rejected: one flags every access, the other flags none.)
- [ ] Does `authorized_destinations` list every known-good withdrawal address (cold storage, settlement, fees), so routine treasury movement is not reported as exfiltration?
- [ ] Does `privileged_actions` use **this signer's** action names, and does it correctly exclude routine `SIGN_TRANSACTION`-style key *use*?

## Data quality

- [ ] Is every timestamp ISO-8601 and timezone-aware? (Naive timestamps are rejected — ordering against the leak time would be a guess.)
- [ ] Is every `amount` a decimal string, int, or `Decimal` — never a float?
- [ ] Does every transfer carry its `asset_symbol`, given amounts are aggregated per asset and never summed across assets?
- [ ] Is `KeyForensicsError` handled as a data incident that halts the analysis, never swallowed into a "no findings, therefore clean" path?

## Reading the report

- [ ] Is `INSUFFICIENT_EVIDENCE` treated as an unresolved incident rather than a clean result? Absence of logs is not absence of access — "the worst form of key compromise is one that is not detected" (§5.5.2).
- [ ] Does `evidence_window_start`/`_end` actually span the suspected leak time, and if not, has the missing log range been pulled?
- [ ] Are `unauthorized_attempt_count` (rejected probes) and `unauthorized_successful_access_count` (evidence of disclosure) read as different things? Under 23 NYCRR 500.1(f) an unsuccessful attempt is still a cybersecurity *event*.
- [ ] Is `privileged_authorized_ip_access_count` investigated rather than dismissed because the source IP was allowlisted? Stolen sessions and insiders come from inside the allowlist.
- [ ] If `DUPLICATE_TRANSFER_RECORDS` fired, has the indexer query been checked for overlapping pagination windows before the loss figure is quoted?
- [ ] Is `exfiltrated_by_asset` read per asset, and is `pre_incident_transfer_count` excluded from the loss figure quoted to insurers?
- [ ] Is `DERIVED_KEY_EXPOSURE` understood as exposure radius rather than evidence — it widens containment scope but does not by itself indicate compromise?

## Sealing and handoff

- [ ] Is `evidence_sha256` recorded alongside the report, and can it be recomputed by an independent party via `compute_evidence_digest(build_evidence_manifest(...))`?
- [ ] Is `analysis_time` supplied by the caller (never the system clock), so the evidence set replays to an identical digest?
- [ ] Are reports persisted for clean outcomes too, as the record of what was examined and over what window?
- [ ] Has the incident been routed to compliance for the notification assessment — given this engine computes no deadlines, and NYDFS §500.17(a) (72 hours from determining an incident occurred) and SEC Form 8-K Item 1.05 (four business days from a materiality determination) apply only to specific entity types?
- [ ] Are `blocklist_addresses` reported to exchanges and analytics providers as **best-effort recovery**, not relied on as a control?
