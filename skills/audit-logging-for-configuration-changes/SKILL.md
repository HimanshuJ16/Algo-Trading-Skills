---
name: audit-logging-for-configuration-changes
description: >-
  Use when a change to a trading parameter or risk-control setting must leave evidence
  of who changed what, from which value to which, and why. A hash-chained record, not an
  authorisation gate; manual overrides belong to risk-control-bypass-audit-logging.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: deployment-ops
  tags: compliance, audit-logging, change-management, tamper-evident, finra-3110, risk-controls
  brokers_frameworks: generic
  version: "1.3.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when changes to live trading algorithms, risk-control parameters, or
system configuration need to leave an evidence trail rather than a shrug. The engine
intercepts a change request, checks that it names a parameter, an authenticated
principal and a reason, and emits a canonical JSON record chained by SHA-256 to the
record before it.

It is worth being precise about *whose* obligation this serves, because "the
regulators require an audit log" is the kind of claim that is usually stated too
broadly:

- **FINRA member broker-dealers** are subject to Rule 3110 (Supervision): written
  supervisory procedures under 3110(b)(1), a record of supervisory designations kept
  at least three years under 3110(b)(6)(B), and internal inspection reports kept at
  least three years under 3110(c)(2).
- **FINRA Regulatory Notice 15-09** (March 2015) is *guidance*, not a rule. It offers
  "suggested effective practices" that "complement, rather than supplant, obligations
  firms have under existing or future rules", among them "a development and change
  management process that tracks the development of new trading code or material
  changes to existing code", including "a review of test results and a set of approval
  protocols". It does not require that the *reason* for a change be documented.
- **US broker-dealers with market access** are subject to SEA Rule 15c3-5, and this is
  the actual authority behind the `justification` field. SEC Division of Trading and
  Markets FAQ No. 18 says that where a threshold is raised in accordance with
  supervisory procedures, "the reasons for any such modification should be
  appropriately documented and retained as part of the broker-dealer's books and
  records."
- **SEC Rule 17a-4** governs preservation of the resulting records. Since the 2022
  amendments (Release 34-96034; effective 3 January 2023, compliance date 3 May 2023
  for broker-dealers) a system may satisfy 17a-4(f) by **either** WORM **or** an
  audit-trail arrangement that permits recreation of an original record if it is
  modified or deleted, capturing the date and time of each creation, modification or
  deletion and the identity of the person responsible. WORM is no longer the only
  permitted route.
- **SEC Regulation SCI** applies only to "SCI entities" — SROs, certain ATSs, plan
  processors and certain exempt clearing agencies. The March 2023 proposal to extend it
  to certain large broker-dealers and SBSDRs (88 FR 23146) was **formally withdrawn**
  on 12 June 2025. Do not describe SCI-style controls as SCI compliance if the firm is
  outside the perimeter.

Everyone else — a proprietary trader, a fund's internal system, an individual running
an algorithm — has no configuration-audit rule pointed at them and should use this as
operational hygiene.

## When NOT to Use

- **As an authorisation gate.** This engine records that a change was requested; it
  does not decide who may make it. Maker-checker approval and RBAC belong upstream —
  see `risk-control-configuration-change-approval-workflow`.
- **As the system of record.** The chain lives in process memory and is tamper-*evident*,
  not immutable. Persist the emitted lines and publish the chain head to storage the
  trading host cannot rewrite.
- For routine application logs that are not configuration changes — use
  `structured-logging-for-post-incident-forensics`.
- For order/fill audit trails — use `data-lineage-tracking-for-audit-and-debugging` or
  venue CAT reporting tooling.
- For manual overrides of a risk control that is actively rejecting orders — that is an
  emergency bypass with its own evidence requirements, see
  `risk-control-bypass-audit-logging`.

## Prerequisites

- Python 3.9+ (standard library only).
- A single `ConfigurationAuditLogger` instance per audited configuration domain. The
  sequence number and chain are per-instance; two instances produce two independent
  chains that cannot be interleaved into one.
- An authenticated caller context so `user_id` is a verified principal. Never accept
  `user_id` from an unverified request body — a forged principal is worse than a
  missing one, because it looks like evidence.
- Append-only storage for the emitted lines (S3 Object Lock, Splunk with data-integrity
  controls, or a 17a-4(f)-compliant recordkeeping system), plus somewhere to publish the
  chain head that the trading host cannot rewrite.
- The firm's own justification policy. `MIN_JUSTIFICATION_LENGTH` defaults to 5 and has
  **no regulatory basis**; override it via `min_justification_chars`.

## Workflow

1. **Intercept the update.** Every UI, CLI, or API path that mutates an audited
   parameter routes through `ConfigurationAuditLogger.process_change_request`. Direct
   database writes and flat-file edits are backdoors that leave the firm blind at
   examination; close them rather than documenting them.
2. **Validate.** In order, the logger requires a non-blank `parameter_name` (a record
   that cannot name what changed proves nothing), a `justification` of at least
   `min_justification_chars` after stripping, a non-blank `user_id`, and `old_value !=
   new_value`. The first failure becomes the `rejection_reason`; the constants
   `REASON_PARAMETER_NAME`, `REASON_JUSTIFICATION`, `REASON_USER_ID` and `REASON_NO_OP`
   let callers branch without matching prose.
3. **Record and chain.** Under one lock, the engine assigns a monotonic
   `sequence_number`, a high-precision UTC `timestamp_utc`, the originating
   `environment`, and a SHA-256 `record_hash` over the canonical JSON of every other
   field including `prev_hash`. Order the records by `sequence_number`, never by
   `timestamp_utc` — the wall clock can step backwards under an NTP correction.
4. **Emit.** Approved records go out at INFO as `AUDIT_LOG_ENTRY`, rejected attempts at
   WARNING as `AUDIT_LOG_REJECTED`, both as sorted-key JSON for deterministic SIEM
   ingestion. Emission happens inside the lock so log order matches chain order.
5. **Commit only on approval.** Apply the underlying change when `is_approved` is
   `True`. When it is `False`, the change must not be applied — but the record is still
   emitted and retained, because a rejected attempt is itself a supervisory event.
6. **Publish the chain head.** Write `chain_head_hash` to storage the trading process
   cannot alter. This is the only thing that reveals truncation of the newest records,
   and it is what NIST SP 800-92 §3.1 means by protecting the digest.
7. **Verify at examination.** Rebuild records from the archived lines with
   `ConfigChangeRecord.from_json`, then call `verify_chain(records)`. It returns
   `(True, None)` or `(False, reason)` naming the first record that fails and why —
   content edited, link broken, or sequence out of order. Compare the last
   `record_hash` against the externally published head. Use
   `verify_chain(window, expect_genesis=False)` to check a slice of a longer chain;
   that proves internal consistency only and says nothing about earlier records.

## Common Pitfalls

- **Treating the length floor as a rule.** `MIN_JUSTIFICATION_LENGTH = 5` is an
  engineering default with no regulatory basis. A character count cannot judge whether
  a justification is adequate; it only catches `"ok"`. Adequacy is a human review
  question, and citing FINRA for the number is a claim the Notice does not support.
- **Claiming Reg SCI compliance outside the SCI perimeter.** Reg SCI binds SCI entities.
  The 2023 proposal to extend it to large broker-dealers was withdrawn in June 2025;
  a system description that still says "SCI-mandated" is now wrong.
- **Assuming WORM is the only permitted sink.** Since the 2022 amendments, 17a-4(f)
  accepts an audit-trail arrangement as an alternative to WORM. Designing as though
  WORM were mandatory can force an unnecessary second recordkeeping system.
- **Assuming JSON or a hash means immutable.** The hash chain provides *detection*.
  Anything that can rewrite the whole log can recompute the whole chain; prevention is
  the storage layer's job.
- **Believing the chain detects truncation.** Deleting the newest N records leaves a
  chain that verifies perfectly. Only an externally held chain head exposes it.
- **Sharing one logger across processes, or building one per request.** The sequence and
  chain are per-instance and in-memory. Two instances make two chains; a per-request
  instance makes a chain of length one and proves nothing about ordering.
- **Ordering the audit trail by timestamp.** Use `sequence_number`. NTP corrections and
  DST-naive local times both reorder a timestamp-sorted trail.
- **Dropping rejected attempts.** Logging only successful changes hides exactly the
  events — a missing justification, an unauthenticated principal — that a supervisor
  needs to see.
- **Letting an exotic config value drop the record.** Values that are not JSON-native
  are coerced to their string form, and a comparison that raises is treated as a real
  change rather than propagating. Prefer JSON-native values anyway: the string form of
  an unordered container is not stable between processes.

## Verification

Run `python -m unittest discover -s skills/audit-logging-for-configuration-changes/scripts`
to confirm the engine: rejects requests missing a parameter name, justification or
principal, and no-op changes; honours a configured justification floor; emits monotonic
sequence numbers and chains `prev_hash` to the prior `record_hash`; detects an in-place
edit, a middle deletion, reordering, and an edit whose own hash was recomputed;
verifies a chain rebuilt from emitted JSON and rejects an archive line with an extra or
missing field; keeps `chain_head_hash` in step with the last record; assigns
gap-free sequence numbers under eight concurrent writer threads; and records rather
than drops config values that cannot be rendered or compared. Then work through
`assets/checklist.md`.

## Related Skills

- `risk-control-configuration-change-approval-workflow`
- `risk-control-bypass-audit-logging`
- `configuration-drift-detection-across-environments`
- `structured-logging-for-post-incident-forensics`
- `record-retention-periods-by-jurisdiction`
