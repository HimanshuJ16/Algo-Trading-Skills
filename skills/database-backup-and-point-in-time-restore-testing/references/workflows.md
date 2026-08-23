# Workflows for Database Backup and Point-In-Time Restore Testing

## A. Continuous archiving hygiene (ongoing)

1. Stream WAL to durable off-site storage and take periodic base backups.
2. Alert on **archive staleness**, not just on archive errors: the dangerous
   state is an `archive_command` that has quietly stopped succeeding while
   backups appear to be "running". The age of the newest archived WAL segment is
   the signal that matters.
3. Record each base backup's *completion* time. PITR replays only WAL after it,
   and PostgreSQL requires the stop point to be after the backup's end.

## B. Metadata drill (this module, weekly)

1. Export snapshot and WAL metadata from the archive catalogue.
2. Pick a target timestamp inside the window the archive is supposed to cover.
3. Run `perform_pitr_restore` with an independently derived `ExpectedDatabaseState`.
4. Fail the drill on any of: `recovery_target_reached = False`, `wal_gap_detected`,
   RPO or RTO breach, `integrity_verified = False`. Treat
   `integrity_verified = None` as *unverified*, not as a pass.
5. Archive the report. Comparing drills over time is what reveals an archive
   degrading slowly rather than failing loudly.

## C. Full restore drill (quarterly, or after any archive/topology change)

1. Restore the base backup into an **isolated** instance — never one that any
   live trading process can reach.
2. Replay to the target with `recovery_target_time` and
   `recovery_target_action = pause`, so the state can be inspected before promotion.
3. Measure wall-clock duration end to end; that number, not the metadata
   arithmetic, is the RTO input.
4. Reconcile at the application level: order and fill ledgers against broker
   records, positions against the custodian. A database that starts is not a
   database that reconciles.
5. Tear the instance down and rotate any credentials it used.

## D. Interpreting a failed drill

| Symptom | Likely cause | First action |
|---|---|---|
| `recovery_target_reached = False`, large RPO | Archiving stopped; WAL never reached durable storage | Check `archive_command` exit status and destination credentials/quota |
| `wal_gap_detected` with `first_missing_lsn` | Segment lost, overwritten, or expired by a retention policy | Compare retention window against the drill target; check for archive overwrite |
| RPO breach but target reached | Archive cadence too slow for the objective | Shorten `archive_timeout` / move to streaming, or renegotiate the objective |
| RTO breach | Restore throughput, WAL volume to replay, or provisioning latency | Take base backups more frequently to shorten replay |
| `integrity_verified = False` | Replay diverged, or the expectation is stale | Reconcile against broker records before trusting either side |
| `PitrRestoreError` on ingest | Duplicate LSN, non-monotonic archive, unknown operation | Fix the export from the archive catalogue; do not suppress |
