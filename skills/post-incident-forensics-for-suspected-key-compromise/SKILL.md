---
name: post-incident-forensics-for-suspected-key-compromise
description: >-
  Use when a signing key or KMS credential is suspected of having leaked and you need a
  defensible reconstruction of what it did, correlating access logs against a policy
  allowlist and separating success from rejected attempts.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: crypto-custody-security
  tags: key-compromise, crypto-custody, digital-forensics, on-chain-analysis, ip-allowlist, incident-response, chain-of-custody
  brokers_frameworks: "NIST SP 800-86; NIST SP 800-57 Pt.1 Rev.5; RFC 3227; FIPS 180-4 SHA-256; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a private key, signer credential, or KMS-held key used by a trading or custody system is *suspected* of having leaked, and you need a defensible reconstruction of what that key did. The engine correlates off-chain key-access evidence (KMS / signer / API-gateway logs) against an independently-held access-control policy, classifies on-chain outflows from the affected wallet relative to the suspected leak time, seals the analysed evidence under a canonical SHA-256 digest, and emits a containment mandate covering the key and every key in its exposure radius.

Crypto outflows are irreversible, so the containment path is deliberately asymmetric: the engine clears a key only on clean evidence that actually spans the suspected leak time, and mandates containment on everything else — including the case where evidence is missing.

## When NOT to Use

- **As a substitute for acquisition-time evidence preservation.** The engine sees *parsed records*, not the original artifact bytes. `evidence_sha256` seals what was analysed; it cannot prove the source log files were unmodified before parsing. Hash the raw artifacts at acquisition (RFC 3227 §3.2) and pass those digests as `source_artifact_digests`.
- **As a regulatory notification clock.** The engine computes no legal deadlines. NYDFS 23 NYCRR 500.17(a) (72 hours after determining a *cybersecurity incident* occurred), SEC Form 8-K Item 1.05 (four business days after a *materiality determination*, and only for SEC registrants), and every other regime are entity- and jurisdiction-specific. Route the report to compliance; see `references/standards.md`.
- **As a live containment actuator.** `ContainmentMandate` is an instruction, not an action. Nothing here revokes a key, pauses a signer, or contacts an exchange.
- **As proof that funds are recoverable.** `blocklist_addresses` are destinations to *report*. Freezing is discretionary for exchanges and analytics providers, so treat it as best-effort recovery and never as a control.
- **As an intrusion-detection system.** This is retrospective analysis of a bounded evidence set for one incident. It is stateless, has no baseline, and detects nothing in real time — see `on-chain-transaction-monitoring-for-anomalies`.
- **As an attribution or chain-tracing tool.** It identifies the first-hop destination of an outflow. Following funds through mixers, bridges, and peel chains is a chain-analytics function this engine does not perform.
- **On evidence whose custody you cannot state.** `custodian` and `collected_at` are mandatory (RFC 3227 §4.1). An analysis nobody can attest to is not evidence.

## Prerequisites

- **Incident metadata**: `key_id` (never the key itself — key-material-shaped identifiers are rejected), `wallet_address`, `suspected_leak_time`, `affected_systems`, and optionally `derived_key_ids` for keys sharing a seed or HSM.
- **Chain-of-custody record**: `custodian`, `collected_at`, and `source_artifact_digests` (SHA-256 of each raw log artifact as acquired). `clock_offset_seconds` records the source system's clock minus UTC.
- **`AccessControlPolicy`**, held *independently of the logs*: `authorized_networks` (IPs or CIDR blocks), `authorized_destinations` (known-good withdrawal addresses), and `privileged_actions` (the signer's own key-material-exposing action names).
- **Access log entries**: `timestamp` (ISO-8601, timezone-aware), `ip_address`, `action`, `status_code`, `principal`.
- **On-chain transfers**: `tx_hash`, `from_address`, `to_address`, `amount` (Decimal, int, or decimal string — **never a float**), `asset_symbol`, `timestamp`.
- **`analysis_time`**, supplied by the caller. The engine has no clock of its own, so a stored evidence set replays to an identical report and digest.

## Workflow

1. **Preserve before you analyse.** Hash each raw log artifact at acquisition and record who collected it and when. Every input is validated fail-closed: naive timestamps, float amounts, invalid IPs, out-of-range status codes and non-SHA-256 digests raise `KeyForensicsError` rather than being coerced. Handle that exception as a data incident — never fall through to "no findings, therefore safe".
2. **Access-log audit against the policy allowlist.**
   - **Decision point — the allowlist is policy, not log content.** It lives on `AccessControlPolicy`, not on each log record. An allowlist carried inside the record is written by whoever can write the log: an attacker who appends a record can append themselves into its allowlist and vanish from the report.
   - **Decision point — classify by outcome, not just by origin.** A rejected 403 probe from an unknown IP and a successful 200 `EXPORT_KEY` from the same IP are different findings. NYDFS 23 NYCRR 500.1(f) counts an unsuccessful attempt as a *cybersecurity event*; only the successful access evidences disclosure. Collapsing them into one counter inverts triage priority and overstates the incident.
   - **Decision point — an allowlisted IP does not clear a key-material action.** A successful privileged action (`EXPORT_KEY`, `WRAP_KEY`, …) from an allowlisted host is reported: a stolen session or an insider operates from an authorized host. `SIGN_TRANSACTION` is deliberately *not* a default privileged action — it is key use, not key exposure, and flagging it would raise a finding on every routine bot signature.
3. **On-chain outflow tracing.**
   - Only transfers *from* the affected wallet are considered; inbound transfers are ignored.
   - **Decision point — not every outflow is exfiltration.** Transfers to `authorized_destinations`, and transfers that completed before the suspected leak time, are counted separately and excluded from the exfiltration figure. Attributing every historical treasury movement to the breach inflates the number that feeds an insurance claim and a materiality assessment.
   - **Decision point — attribution is fail-closed against clock error.** A transfer counts as pre-incident only if it precedes the leak time by more than `clock_offset_seconds`. Inside that margin it is attributed as unauthorized (RFC 3227 §3.2 requires recording clock drift).
   - Amounts are exact `Decimal` and aggregated **per asset symbol**. 50 ETH and 1,200.5 USDC are not summable into one number.
4. **Evidence-quality audit.** An empty log set, or a log window that does not span the suspected leak time, is a CRITICAL `EVIDENCE_GAP`: absence of logs is not absence of access. Missing `source_artifact_digests` and a non-zero clock offset are reported as custody weaknesses. `derived_key_ids` are reported as exposure radius — deliberately *not* as evidence, or a key with siblings could never be cleared.
5. **Seal the evidence.** `evidence_sha256` is SHA-256 (FIPS 180-4) over a canonical JSON manifest containing **every field of every record**, plus incident, custody and policy metadata. Altering any single IP, timestamp, action, amount or record order changes the digest. `build_evidence_manifest()` / `compute_evidence_digest()` let an independent party recompute it.
6. **Containment mandate dispatch.** Emitted whenever the status is anything other than `NO_EVIDENCE_OF_COMPROMISE` — including `INSUFFICIENT_EVIDENCE`. It carries `revoke_key_ids` and `rekey_key_ids` covering the key *and* its derived keys, `blocklist_addresses`, the revocation reason, and the determination time.
7. **Status.** `KEY_COMPROMISE_CONFIRMED` (unauthorized successful access or unauthorized outflow) → `COMPROMISE_SUSPECTED` (rejected attempts, or privileged action from an allowlisted IP) → `INSUFFICIENT_EVIDENCE` (evidence gap, nothing else found) → `NO_EVIDENCE_OF_COMPROMISE` (clean evidence that spans the leak time).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **An "integrity hash" that hashes a summary.** Digesting record *counts* and transaction hashes leaves every IP, timestamp, action and amount silently mutable under an unchanged hash — the artifact is then evidence of nothing. The digest must cover the records themselves, in a canonical encoding.
- **Delimiter-concatenated evidence strings.** `f"KEY:{key_id}|LOGS:{n}"` is forgeable by an identifier containing the delimiter. Canonical JSON with sorted keys is not.
- **Reading an empty log set as a clean bill of health.** The most likely reason a compromised key shows no unauthorized access is that the logs were never collected, were rotated away, or were deleted by the intruder. NIST SP 800-57 Pt.1 Rev.5 §5.5.2: "The worst form of key compromise is one that is not detected."
- **Trusting an allowlist that lives inside the audited log.** See Workflow step 2.
- **Counting rejected probes as breaches.** It converts a blocked scan into a "confirmed compromise" and buries the one record that matters.
- **Clearing a key because the source IP was allowlisted.** Credential theft and insider misuse both come from inside the allowlist.
- **Attributing every historical outflow to the incident.** Without a leak-time cut-off and a known-good destination list, routine treasury rebalancing is reported as exfiltration.
- **Summing different assets into one "total exfiltrated".** Dimensionally meaningless, and it is exactly the figure an insurer and a materiality assessment will scrutinise.
- **Float amounts.** 0.1 ETH has no exact binary representation, and rounding to a fixed number of decimals discards wei and satoshis. Pass the decimal string from the RPC response.
- **Naive timestamps.** Ordering an outflow against the suspected leak time requires a known offset; guessing wrong reclassifies exfiltration as routine activity.
- **Lower-casing every address.** Correct for EVM hex (EIP-55 checksummed and lowercase spell the same account), wrong for Base58 (BTC, TRON) and Bech32, which are case-sensitive.
- **Exact-string IP comparison.** It misses CIDR-block allowlists and IPv4-mapped IPv6 (`::ffff:10.0.0.5`), producing false "unauthorized access" findings against your own hosts.
- **Investigating before revoking.** NIST SP 800-57 Pt.1 Rev.5 §5.5: on compromise, all use of the key to apply cryptographic protection *shall* cease and the key *shall* be revoked; §8.3.5 revokes "as soon as feasible" on reason to believe disclosure occurred. Forensics runs alongside containment, not before it.
- **Revoking only the leaked key.** §5.5.2 requires the re-keying to be monitored so that *all affected keys* are covered — derived keys sharing a seed or HSM module are in the exposure radius.
- **Putting key material into the incident record.** A revocation notice identifies the key "excluding the key itself" (§8.3.5). Reports get pasted into tickets, insurance packs and log aggregators.
- **Treating an exchange blacklist request as a control.** Freezing is discretionary and usually too late.

## Verification

- Construct `AccessControlPolicy(authorized_networks=["192.168.1.0/24", "10.0.0.5"], authorized_destinations=[<cold wallet>])` and `KeyForensicsAnalyzer(policy)`. Analyse an incident with one successful `EXPORT_KEY` from `198.51.100.44` and one 50 ETH outflow after the leak time ⟹ `KEY_COMPROMISE_CONFIRMED`, `unauthorized_successful_access_count == 1`, `exfiltrated_by_asset == {"ETH": "50"}`, a 64-hex `evidence_sha256`, and `containment.revoke_key_ids` covering the key and its derived keys.
- Classification checks: a 403 from a non-allowlisted IP ⟹ `COMPROMISE_SUSPECTED` with `unauthorized_attempt_count == 1` and `unauthorized_successful_access_count == 0`; a successful `EXPORT_KEY` from an allowlisted IP ⟹ `COMPROMISE_SUSPECTED`; routine `SIGN_TRANSACTION` from an allowlisted IP ⟹ no finding.
- Evidence-gap checks: an empty log set, and a log window that does not span `suspected_leak_time`, must each yield `INSUFFICIENT_EVIDENCE` with `containment_required=True` — never `NO_EVIDENCE_OF_COMPROMISE`.
- Attribution checks: a pre-leak-time outflow and an outflow to an authorized destination must not enter `exfiltrated_by_asset`; a transfer exactly at the leak time must; a 90 s `clock_offset_seconds` must pull a transfer 30 s before the leak time back into the unauthorized set.
- Digest checks: mutating any log IP, any timestamp, any transfer amount, the record order, or `analysis_time` must change `evidence_sha256`; `compute_evidence_digest(build_evidence_manifest(...))` must reproduce it exactly.
- Quantitative checks: `0.1 + 0.2 + 1e-18` ETH must report as `"0.300000000000000001"`, and ETH and USDC must never be summed together.
- Fail-closed checks: float amounts, `NaN`/`Infinity`/negative amounts, naive timestamps, invalid IPs, out-of-range status codes, an empty `authorized_networks`, a non-SHA-256 artifact digest, and a key-material-shaped `key_id` must each raise `KeyForensicsError`.
- Run `python -m unittest discover -s skills/post-incident-forensics-for-suspected-key-compromise/scripts` and confirm a 100% pass rate.

## Related Skills

- `recovery-plan-for-lost-or-compromised-keys`
- `on-chain-transaction-monitoring-for-anomalies`
- `structured-logging-for-post-incident-forensics`
- `post-breach-root-cause-analysis-template`
- `key-rotation-schedule-for-hot-wallet-keys`
