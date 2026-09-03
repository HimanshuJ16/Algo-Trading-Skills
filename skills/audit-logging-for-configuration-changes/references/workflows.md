# Workflows for Configuration Audit Logging

## Recording a change

1. **Intercept.** Any UI dashboard, CLI tool, or API endpoint that mutates an
   audited configuration parameter routes the request through
   `ConfigurationAuditLogger.process_change_request`. No backdoor paths — direct
   DB writes, flat-file edits — are permitted for audited parameters. A backdoor
   is not a documentation problem; it is the failure this skill exists to prevent.

2. **Capture the principal and the reason.** The caller supplies a `user_id` taken
   from a verified session (SSO/JWT claims), never from a request body a client
   can set, and prompts the human for a free-text `justification`. Requests with a
   blank `parameter_name`, a blank `user_id`, or a justification shorter than
   `min_justification_chars` after stripping are rejected — and still recorded.

   The length floor is an engineering default with no regulatory basis. Set it from
   firm policy:

   ```python
   audit = ConfigurationAuditLogger(environment="production", min_justification_chars=20)
   ```

3. **Record and chain.** Under a single lock, `process_change_request` assigns a
   monotonic `sequence_number`, a high-precision UTC `timestamp_utc`, the
   originating `environment`, and a SHA-256 `record_hash` chained to the previous
   record's hash via `prev_hash`. Both approved and rejected attempts are recorded.

   One instance owns one chain. Instantiate the logger once per audited
   configuration domain and share it; a per-request instance produces a chain of
   length one, which proves nothing about ordering or completeness. Multiple
   processes produce multiple independent chains — verify each on its own, or
   funnel changes through a single writer.

4. **Emit.** Approved records are emitted at INFO as `AUDIT_LOG_ENTRY`; rejected
   attempts at WARNING as `AUDIT_LOG_REJECTED`. Both use canonical (sorted-key)
   JSON so a SIEM ingests a stable shape. Emission happens inside the lock, so the
   order of lines in the log matches the order of the chain — an out-of-order log
   would look like tampering to an examiner.

5. **Commit only on approval.** If `is_approved` is `True`, the calling system
   applies the change. If `False`, the change must **not** be applied. The record
   is emitted either way.

6. **Forward.** A daemon (Fluentd, Filebeat) ships the emitted lines to append-only
   storage: a WORM sink such as S3 Object Lock, or an electronic recordkeeping
   system relying on the 17a-4(f) audit-trail alternative. Either satisfies the
   preservation requirement; the choice belongs to the firm's compliance function,
   not to this engine.

7. **Publish the chain head.** After each batch, write `audit.chain_head_hash` to a
   store the trading host cannot rewrite — a separate account's object-lock bucket,
   a compliance mailbox, a signed daily attestation. Without an external head,
   truncation of the newest records is invisible.

   ```python
   publish_to_compliance_store(
       date=today, chain_head=audit.chain_head_hash, count=len(audit.records)
   )
   ```

## Verifying at examination

Work from the archive, not from a live process — the whole point is to check records
the emitting system may since have altered.

```python
records = [ConfigChangeRecord.from_json(line) for line in archived_lines]
is_intact, reason = verify_chain(records)
if not is_intact:
    raise AuditIntegrityError(reason)          # names the first failing record

head = records[-1].record_hash if records else GENESIS_PREV_HASH
if head != externally_published_head:
    raise AuditIntegrityError("records were truncated after the head was published")
```

- `verify_chain` walks the records in order, checks that sequence numbers run
  1, 2, 3, … without gaps, that each `prev_hash` equals the previous
  `record_hash`, and that each `record_hash` recomputes from the record's own
  content. It returns `(True, None)` or `(False, reason)` naming the first
  record that fails and how.
- `from_json` rejects a line with a missing field, and also one with an *extra*
  field — an archive carrying data the hash does not cover is not verifiable, and
  silently ignoring the extra key would hide that.
- To check a slice of a longer chain, pass `expect_genesis=False`. That anchors on
  the slice's own first record, so it proves the slice is internally consistent and
  says nothing about the records before it. Use it for spot checks, not for a
  completeness assertion.
- The comparison against the externally published head is a separate step and is
  the only one that detects truncation. `verify_chain` cannot do it, because a
  truncated chain is a valid chain.

## Investigating a verification failure

| `reason` contains | What happened | First thing to check |
|---|---|---|
| `hash does not match its content` | A field of that record was edited in place | Whether the archive is append-only in fact, not just in policy |
| `does not link to its predecessor` | A record was edited *and* its own hash recomputed, or a record was replaced | The record before the named one — that is the edited one |
| `expected N` / `out of order` | A record was deleted from the middle, duplicated, or the lines were re-sorted | Whether the shipping pipeline re-orders lines (a timestamp sort will) |
| Chain verifies but head differs | The newest records were truncated | The published head's timestamp versus the archive's last record |
