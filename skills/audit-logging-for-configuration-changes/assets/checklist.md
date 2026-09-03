# Checklist for Configuration Audit Logging

## Scope and applicability
- [ ] Identify which obligation the trail serves — FINRA Rule 3110 supervision,
      SEA Rule 15c3-5 FAQ No. 18 documented reasons, 17a-4 preservation, or none of
      them (operational hygiene) — and record that determination.
- [ ] Confirm no document or dashboard describes the firm as SCI-mandated unless it is
      an SCI entity. The 2023 proposal to extend Reg SCI to large broker-dealers was
      withdrawn on 12 June 2025.
- [ ] Confirm the justification length floor was set from firm policy, not accepted as
      a regulatory number — `MIN_JUSTIFICATION_LENGTH = 5` has no regulatory basis.

## Capture
- [ ] Confirm every mutation path (UI, CLI, API, migration script, admin console)
      routes through `process_change_request`, and that direct DB and flat-file edits
      to audited parameters are closed off, not merely discouraged.
- [ ] Confirm `user_id` comes from a verified session, never from a client-supplied
      request body.
- [ ] Confirm changes are blocked when `parameter_name` is empty or whitespace-only.
- [ ] Confirm changes are blocked when `justification` is empty, whitespace-only, or
      shorter than the configured floor.
- [ ] Confirm changes are blocked when `user_id` is empty or whitespace-only.
- [ ] Confirm no-op changes (identical `old_value`/`new_value`) are recorded but not
      approved.
- [ ] Confirm rejected attempts are emitted at WARNING and retained, not just approved
      changes.
- [ ] Confirm the caller applies the change only when `is_approved` is `True`.
- [ ] Confirm the originating `environment` is captured on every record.
- [ ] Confirm high-precision UTC timestamps, and that nothing downstream orders the
      trail by `timestamp_utc` instead of `sequence_number`.

## Chain integrity
- [ ] Confirm one logger instance owns the chain for each audited configuration
      domain — not one per request, and not several writing to one archive.
- [ ] Confirm each record carries a monotonic `sequence_number` and a SHA-256
      `record_hash` chained to the prior record's hash via `prev_hash`.
- [ ] Confirm `verify_chain` returns `(True, None)` for the current archive.
- [ ] Confirm `verify_chain` was exercised against a deliberately edited record and
      named the right one.
- [ ] Confirm records rebuilt from archived JSON via `from_json` verify identically to
      the live ones.
- [ ] Confirm `chain_head_hash` is published to storage the trading host cannot
      rewrite, on a defined cadence — without it, truncation of the newest records is
      undetectable.
- [ ] Confirm the published head is actually compared against the archive during
      review, not merely stored.

## Persistence
- [ ] Confirm the sink satisfies the firm's elected 17a-4(f) route — WORM format or an
      audit-trail arrangement — and that the election is documented. JSON format alone
      is not immutability, and the hash chain is not a substitute for either route.
- [ ] Confirm the retention period matches the applicable jurisdiction — see
      `record-retention-periods-by-jurisdiction`.
- [ ] Confirm log shipping preserves line order and does not re-sort by timestamp.

## Tests
- [ ] Run `python -m unittest discover -s skills/audit-logging-for-configuration-changes/scripts`.

## Sign-off
- Deployment / SecOps Engineer: ___________________________
- Compliance reviewer (applicability determination): ___________________________
- Date: ___________________________
