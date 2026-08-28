# Workflows for Post-Incident Forensics for Suspected Key Compromise

## 0. Contain First, Analyse Alongside

Forensics does not gate containment. NIST SP 800-57 Pt.1 Rev.5 §5.5 requires that all use of a
compromised key to apply cryptographic protection *shall* cease and the key *shall* be revoked;
§8.3.5 allows emergency revocation on reason to believe disclosure occurred. Start containment as
soon as the key is suspected — this engine reconstructs what happened, it does not decide whether
to act.

## 1. Evidence Acquisition and Custody

1. Collect in order of volatility (RFC 3227 §2.1) — memory and live signer state before disk, disk
   before archival log stores.
2. **Hash each raw artifact at acquisition**, before any parsing, and record the digests as
   `source_artifact_digests` (name → 64-hex SHA-256). This is the link the manifest digest cannot
   make for you: the engine only ever sees parsed records.
3. Record `custodian` (who collected it) and `collected_at` (when), per RFC 3227 §4.1. Both are
   mandatory — an analysis nobody can attest to is not evidence.
4. Measure the source system's clock against UTC and record the difference as
   `clock_offset_seconds`. A skewed signer clock silently reclassifies exfiltration as routine
   activity.
5. Work on copies. Nothing in this module writes to the source artifacts, but the surrounding
   pipeline must not either.

## 2. Access-Control Policy Construction

1. Build `AccessControlPolicy` from your **infrastructure source of truth**, not from the logs
   under audit. An allowlist read out of the audited records is written by whoever can write those
   records.
2. `authorized_networks` accepts single IPs and CIDR blocks, IPv4 and IPv6 mixed. Traffic arriving
   as IPv4-mapped IPv6 (`::ffff:10.0.0.5`) is normalized to its IPv4 form before matching, so an
   allowlist written in either notation matches.
3. `authorized_destinations` are the known-good withdrawal addresses (cold storage, settlement,
   fee accounts). Everything else is a candidate exfiltration destination.
4. `privileged_actions` must be **this signer's** action names for operations that expose key
   material. The defaults (`EXPORT_KEY`, `WRAP_KEY`, `UNWRAP_KEY`, `PUT_KEY_POLICY`,
   `SCHEDULE_KEY_DELETION`) exclude `SIGN_TRANSACTION` on purpose: signing is key *use*, and
   flagging it would raise a HIGH finding on every routine bot signature and pin the status at
   `COMPROMISE_SUSPECTED` permanently.
5. An empty `authorized_networks` is rejected: it would mark every access unauthorized and drown
   the real finding. An all-addresses block (`0.0.0.0/0`, `::/0`) is rejected too, for the mirror
   reason — it authorizes the attacker and makes the access audit vacuous while still reporting
   a clean result.

## 3. Evidence Ingestion

Construct `KeyCompromiseIncident`, `KeyAccessLogEntry` and `OnChainTransfer`. Validation is
fail-closed and raises `KeyForensicsError`:

| Input | Rejected | Why |
|---|---|---|
| `key_id`, `derived_key_ids` | 64/128 hex characters | Looks like raw key material; a revocation notice identifies the key "excluding the key itself" (§8.3.5) |
| any timestamp | naive (no UTC offset), non-ISO-8601 | Cannot be ordered against the leak time without guessing |
| `amount` | `float`, negative, `NaN`, `Infinity` | Binary floats cannot represent decimal asset amounts; `NaN` defeats every comparison and propagates through a sum |
| `ip_address` | not a valid IP literal | A CIDR block or hostname in a record field is a parsing bug, not evidence |
| `status_code` | outside 100–599, non-`int` | Outcome classification depends on it |
| `source_artifact_digests` | not 64-hex SHA-256 | A malformed digest proves nothing |
| identifiers | blank, or containing whitespace/newlines | A newline inside a hash forges line breaks into the audit record |

Treat `KeyForensicsError` as a data incident that halts the analysis. Never fall through to
"no findings raised, therefore the key is clean".

## 4. Access-Log Audit

| Finding | Trigger | Severity |
|---|---|---|
| `UNAUTHORIZED_SUCCESSFUL_ACCESS` | source IP outside every allowlisted network **and** `200 ≤ status < 300` | CRITICAL |
| `UNAUTHORIZED_ACCESS_ATTEMPT` | source IP outside the allowlist, request not successful | HIGH |
| `PRIVILEGED_ACTION_FROM_AUTHORIZED_IP` | successful `privileged_actions` call from an allowlisted IP | HIGH |

The three are counted separately and never merged. A rejected probe is a cybersecurity *event*
(23 NYCRR 500.1(f) — "any act or attempt, successful or unsuccessful"); only the successful access
evidences disclosure. An allowlisted source IP does not clear a key-material action: stolen
sessions and insiders operate from authorized hosts.

## 5. On-Chain Outflow Tracing

1. Ignore anything whose `from_address` is not the affected wallet. Hex addresses match
   case-insensitively (EIP-55); Base58/Bech32 addresses match byte-exactly.
2. Classify each outflow, in order:
   - destination in `authorized_destinations` → `authorized_transfer_count`;
   - else `leak_time − tx_time > |clock_offset_seconds|` → `pre_incident_transfer_count`;
   - else → unauthorized, aggregated into `exfiltrated_by_asset` and its destination added to
     `blocklist_addresses`.
3. The boundary is strict: a transfer *at* the leak time is attributed, and one inside the clock
   offset margin is attributed. Attribution errs toward investigating.
4. Aggregation is per `asset_symbol` and uses exact `Decimal`. There is deliberately no single
   "total exfiltrated" figure — it would be dimensionally meaningless across assets and is exactly
   the number an insurer and a materiality assessment will scrutinise.

## 6. Evidence-Quality Audit

| Finding | Trigger | Severity |
|---|---|---|
| `EVIDENCE_GAP` | no access logs, or the log window does not span `suspected_leak_time` | CRITICAL |
| `NO_SOURCE_ARTIFACT_DIGESTS` | `source_artifact_digests` empty | MEDIUM |
| `DERIVED_KEY_EXPOSURE` | `derived_key_ids` non-empty | HIGH |
| `CLOCK_OFFSET_RECORDED` | `clock_offset_seconds ≠ 0` | MEDIUM |
| `DUPLICATE_TRANSFER_RECORDS` | byte-identical transfer records repeat | MEDIUM |

`DUPLICATE_TRANSFER_RECORDS` fires when identical transfer records repeat — the signature of a
paginated indexer query with overlapping windows, which double-counts the loss. Records are
reported, not deduplicated: one transaction can legitimately emit several transfer events, so
collapsing them would understate a real loss. Check the query before quoting the figure.

`DERIVED_KEY_EXPOSURE` is exposure radius, not evidence, and deliberately does **not** move the
status — otherwise a key with siblings could never be cleared. It does widen the containment
scope when containment is mandated.

## 7. Sealing the Evidence

`evidence_sha256` = SHA-256 (FIPS 180-4) over `json.dumps(manifest, sort_keys=True,
separators=(",",":"), ensure_ascii=True)`. The manifest carries every field of every access log
entry and transfer, in collection order, plus the incident, custody and policy metadata and
`analysis_time`.

- Mutating any single field of any record, or reordering records, changes the digest. Log sequence
  is itself evidence, so order is sealed.
- Canonical JSON is not forgeable by an identifier containing a delimiter, unlike a concatenated
  `KEY:…|LOGS:…` string.
- `build_evidence_manifest()` and `compute_evidence_digest()` are exported so a third party can
  recompute the digest from the same records and confirm it.
- The engine reads no system clock: `analysis_time` is supplied by the caller, so a stored evidence
  set replays to a byte-identical report and digest.

## 8. Status and Containment Dispatch

1. `KEY_COMPROMISE_CONFIRMED` — an unauthorized successful access or an unauthorized outflow.
2. `COMPROMISE_SUSPECTED` — rejected attempts, or a privileged action from an allowlisted IP.
3. `INSUFFICIENT_EVIDENCE` — an evidence gap with nothing else found. **Not** a clean result.
4. `NO_EVIDENCE_OF_COMPROMISE` — clean evidence whose window spans the suspected leak time.

`ContainmentMandate` is emitted for 1–3 and is empty only for 4. It carries `revoke_key_ids` and
`rekey_key_ids` (the key plus every derived key), `cease_cryptographic_protection`,
`blocklist_addresses`, `revocation_reason`, `determined_at`, and the ordered `actions` list keyed
to NIST SP 800-57 §5.5 / §5.5.2 / §8.3.5.

## 9. Handoff

1. Persist the report **and** its digest before remediation touches the affected systems.
2. Persist clean reports too: `NO_EVIDENCE_OF_COMPROMISE` with a stated evidence window is the
   record that the examination happened and what it covered.
3. Route to compliance for the notification assessment. The engine computes no deadlines — NYDFS
   §500.17(a) runs 72 hours from determining an incident occurred, SEC Form 8-K Item 1.05 runs four
   business days from a materiality determination, and applicability depends on the entity. Use
   `analysis_time` and the findings as the evidence for that determination, not as the
   determination.
4. Report `blocklist_addresses` to the relevant exchanges and chain-analytics providers as
   best-effort recovery. Freezing is discretionary for those venues and is not a control.
