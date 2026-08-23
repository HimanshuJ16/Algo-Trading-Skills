"""
database-backup-and-point-in-time-restore-testing: PITR drill simulator for
trading databases (PostgreSQL / TimescaleDB continuous WAL archiving).

What this module is and is not
------------------------------
It is a **drill harness over backup metadata**: given the base snapshots and the
WAL sequence an archive actually contains, it answers "if we had to recover to
T_target right now, would it work, how much trade data would we lose, and would
it land inside our RPO/RTO targets?" It replays an *abstract* WAL model (one
record = one row-level change), not real WAL pages.

It is **not** a PostgreSQL recovery implementation and it cannot prove the
on-disk backup is restorable. Only an actual restore into an isolated instance
can do that. Use this to catch the failure real drills catch too late -- an
archive that silently stopped advancing -- and to keep the RPO/RTO arithmetic
honest and auditable between drills.

Recovery semantics modelled
---------------------------
The model follows PostgreSQL's documented archive-recovery behaviour:

* **Inclusive target.** ``recovery_target_inclusive`` defaults to ``on``, which
  stops "just after the specified recovery target" and *includes* transactions
  whose commit time is exactly the target. A record at exactly
  ``target_timestamp_ms`` is therefore replayed.
* **A target that cannot be reached is a failure, not a partial success.**
  "If a recovery target is configured but the archive recovery ends before the
  target is reached, the server will shut down with a fatal error." An archive
  that stops short of T_target is a failed recovery *and* a data-loss event --
  never a clean restore.
* **Recovery needs an unbroken WAL chain.** "To recover successfully using
  continuous archiving ... you need a continuous sequence of archived WAL
  files"; corrupt or missing WAL halts replay at that point. A gap in the
  sequence therefore truncates the replay here rather than being skipped over.
* **The snapshot must precede the target.** "The stop point must be after the
  ending time of the base backup." ``timestamp_ms`` on a snapshot is its
  *completion* time, and only WAL strictly after it is replayed.

See ``references/standards.md`` for sources.

RPO convention
--------------
NIST SP 800-34 Rev. 1 defines the recovery point objective as "the point in time
to which data must be recovered after an outage". The quantity a drill measures
is therefore the *shortfall* against that point:

    rpo_seconds = max(0, (T_target - T_recovery_horizon) / 1000)

where ``T_recovery_horizon`` is the furthest point the usable archive proves it
reaches. It is the width of the data-loss window, so it is zero only when the
archive genuinely covers the target.
"""
from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

OP_INSERT = "INSERT"
OP_UPDATE = "UPDATE"
OP_DELETE = "DELETE"
#: Row-level operations the replay model understands. Anything else is rejected
#: rather than ignored: a mistyped operation would otherwise silently produce a
#: wrong row count that then "passes" the parity check.
VALID_OPERATIONS = frozenset({OP_INSERT, OP_UPDATE, OP_DELETE})


class PitrRestoreError(ValueError):
    """Raised when backup metadata or engine configuration is unusable.

    A drill must fail loudly. Metadata that cannot describe a coherent recovery
    (a non-finite timestamp, duplicate LSNs, an unknown operation) would
    otherwise yield an authoritative-looking report asserting an RPO that was
    never actually measured.
    """


@dataclass
class BaseSnapshot:
    """A completed base backup.

    ``timestamp_ms`` is the backup's *completion* time (``pg_backup_stop``), not
    its start: WAL strictly after this point is what recovery replays.

    ``last_lsn_included`` closes a blind spot in gap detection. Contiguity is
    only checkable *between* the records that survive the post-snapshot filter,
    so a hole sitting immediately after the snapshot -- the archive jumping
    straight from the backup to LSN 40 because 31-39 were lost -- looks like a
    perfectly contiguous run. Recording the last LSN the base backup covers lets
    the engine check that first step too. Leave it ``None`` if unknown, and
    accept that the blind spot remains.
    """

    snapshot_id: str
    timestamp_ms: float
    table_rows: Dict[str, int]          # e.g. {'trade_orders': 1000, 'positions': 50}
    state_checksum: str
    last_lsn_included: Optional[int] = None


@dataclass
class WalRecord:
    """One row-level change in the archived WAL sequence.

    ``lsn_id`` must be a monotonically increasing **archive sequence number**
    (1, 2, 3, ...), not a raw PostgreSQL ``pg_lsn`` byte offset. Contiguity of
    this sequence is what lets the engine detect a hole in the archive; feed
    byte offsets and every step looks like a gap (see
    ``require_contiguous_lsn``).
    """

    lsn_id: int
    timestamp_ms: float
    table_name: str
    operation: str                      # 'INSERT', 'UPDATE', 'DELETE'
    row_id: str
    payload: str


@dataclass
class ExpectedDatabaseState:
    """Independently derived expectation of the state at ``T_target``.

    Supply this from the application side -- reconciled order/trade ledgers,
    broker statements -- not from the same WAL the engine just replayed, or the
    check is circular and verifies nothing.
    """

    table_rows: Dict[str, int]
    state_checksum: Optional[str] = None


@dataclass
class PitrBackupAuditReport:
    database_name: str
    target_recovery_timestamp_ms: float
    snapshot_used_id: str
    wal_records_replayed_count: int
    restored_table_rows: Dict[str, int]
    restored_checksum: str
    rpo_seconds: float
    rto_minutes: float
    is_rpo_compliant: bool
    is_rto_compliant: bool
    is_restoration_successful: bool
    #: Timestamp of the last WAL record recovery actually applied.
    last_recoverable_timestamp_ms: float = 0.0
    #: False when the usable archive ends before ``T_target`` -- in PostgreSQL
    #: this is a fatal recovery failure, not a partial restore.
    recovery_target_reached: bool = False
    #: True when the LSN sequence has a hole; replay is truncated there.
    wal_gap_detected: bool = False
    first_missing_lsn: Optional[int] = None
    #: WAL records in the archive after the snapshot (replayed or not).
    wal_records_available_count: int = 0
    #: True/False against a supplied ``ExpectedDatabaseState``; ``None`` when no
    #: expectation was supplied, i.e. integrity was *not* verified.
    integrity_verified: Optional[bool] = None
    findings: List[str] = field(default_factory=list)


class PitrBackupTesterEngine:
    """Audits WAL continuous archiving by simulating a PITR drill.

    The engine is deterministic and side-effect free: identical metadata in,
    identical report out, so drill results are comparable week over week.
    """

    def __init__(
        self,
        database_name: str = "TradingDB",
        max_rpo_sec: float = 60.0,
        max_rto_min: float = 15.0,
    ) -> None:
        if not _is_finite(max_rpo_sec) or max_rpo_sec < 0:
            raise PitrRestoreError(f"max_rpo_sec must be a non-negative number, got {max_rpo_sec!r}")
        if not _is_finite(max_rto_min) or max_rto_min < 0:
            raise PitrRestoreError(f"max_rto_min must be a non-negative number, got {max_rto_min!r}")
        self.database_name = database_name
        self.max_rpo_sec = max_rpo_sec
        self.max_rto_min = max_rto_min

    def perform_pitr_restore(
        self,
        base_snapshots: Sequence[BaseSnapshot],
        wal_logs: Sequence[WalRecord],
        target_timestamp_ms: float,
        simulated_restore_time_sec: float = 120.0,
        expected_state: Optional[ExpectedDatabaseState] = None,
        require_contiguous_lsn: bool = True,
    ) -> PitrBackupAuditReport:
        """Simulate recovery to ``target_timestamp_ms`` and audit the outcome.

        Selects the latest base snapshot completing at or before the target,
        replays the archived WAL after it up to and including the target,
        measures the data-loss window and restore duration against the SLAs, and
        -- when ``expected_state`` is supplied -- checks the restored row counts
        and checksum against it.

        Args:
            base_snapshots: Candidate base backups. Must be non-empty.
            wal_logs: Archived WAL records. Input order is irrelevant; they are
                sorted by ``lsn_id`` before replay.
            target_timestamp_ms: Recovery target, epoch milliseconds. Inclusive,
                matching ``recovery_target_inclusive = on``.
            simulated_restore_time_sec: Measured (or budgeted) wall-clock
                duration of the restore, used for the RTO audit.
            expected_state: Independently derived expectation of the state at
                the target. Omitting it leaves ``integrity_verified`` ``None``
                -- the restore is then unverified, and the report says so.
            require_contiguous_lsn: Treat a hole in the ``lsn_id`` sequence as a
                missing WAL segment. Disable only when ``lsn_id`` is not a dense
                sequence.

        Returns:
            The populated :class:`PitrBackupAuditReport`.

        Raises:
            PitrRestoreError: If the metadata is unusable -- no eligible
                snapshot, non-finite or negative values, duplicate LSNs, LSN
                order contradicting commit-time order, or an unknown operation.
        """
        if not _is_finite(target_timestamp_ms):
            raise PitrRestoreError(
                f"target_timestamp_ms must be a finite number, got {target_timestamp_ms!r}"
            )
        if not _is_finite(simulated_restore_time_sec) or simulated_restore_time_sec < 0:
            raise PitrRestoreError(
                "simulated_restore_time_sec must be a non-negative number, "
                f"got {simulated_restore_time_sec!r}"
            )
        if not base_snapshots:
            raise PitrRestoreError("No base snapshots supplied; PITR requires at least one base backup")

        base_snap = self._select_base_snapshot(base_snapshots, target_timestamp_ms)
        findings: List[str] = []

        candidates = self._ordered_replay_candidates(wal_logs, base_snap)
        usable, gap_lsn = self._truncate_at_gap(candidates, require_contiguous_lsn, base_snap)
        if gap_lsn is not None:
            findings.append(
                f"WAL GAP: archive sequence breaks before LSN {gap_lsn}; replay truncated there. "
                "Recovery halts at a missing or corrupt WAL segment -- it does not skip past it."
            )

        replayed = [w for w in usable if w.timestamp_ms <= target_timestamp_ms]
        restored_rows, restored_checksum, replay_findings = self._replay(base_snap, replayed)
        findings.extend(replay_findings)

        # The horizon is the furthest point the *usable* archive proves it
        # reaches: records past the target are not applied, but they do evidence
        # that the archive spans the target.
        horizon_ms = max((w.timestamp_ms for w in usable), default=base_snap.timestamp_ms)
        last_recoverable_ms = max((w.timestamp_ms for w in replayed), default=base_snap.timestamp_ms)
        target_reached = horizon_ms >= target_timestamp_ms

        rpo_sec = round(max(0.0, (target_timestamp_ms - horizon_ms) / 1000.0), 3)
        rto_min = round(simulated_restore_time_sec / 60.0, 3)
        is_rpo_ok = rpo_sec <= self.max_rpo_sec
        is_rto_ok = rto_min <= self.max_rto_min

        if not target_reached:
            findings.append(
                f"RECOVERY TARGET UNREACHABLE: usable archive ends at {horizon_ms}ms, "
                f"{rpo_sec}s short of target {target_timestamp_ms}ms. PostgreSQL shuts down with a "
                "fatal error when archive recovery ends before the configured target."
            )
        if not is_rpo_ok:
            findings.append(
                f"RPO BREACH: {rpo_sec}s of data loss exceeds the {self.max_rpo_sec}s objective."
            )
        if not is_rto_ok:
            findings.append(
                f"RTO BREACH: {rto_min}m restore exceeds the {self.max_rto_min}m objective."
            )

        integrity_verified, integrity_findings = self._verify_integrity(
            restored_rows, restored_checksum, expected_state
        )
        findings.extend(integrity_findings)

        is_success = (
            target_reached
            and gap_lsn is None
            and is_rpo_ok
            and is_rto_ok
            and integrity_verified is not False
        )

        logger.info(
            "PITR drill [%s]: target=%sms snapshot=%s replayed=%d/%d rpo=%ss(max %ss) "
            "rto=%sm(max %sm) target_reached=%s gap=%s integrity=%s success=%s",
            self.database_name, target_timestamp_ms, base_snap.snapshot_id,
            len(replayed), len(candidates), rpo_sec, self.max_rpo_sec, rto_min,
            self.max_rto_min, target_reached, gap_lsn, integrity_verified, is_success,
        )

        return PitrBackupAuditReport(
            database_name=self.database_name,
            target_recovery_timestamp_ms=target_timestamp_ms,
            snapshot_used_id=base_snap.snapshot_id,
            wal_records_replayed_count=len(replayed),
            restored_table_rows=restored_rows,
            restored_checksum=restored_checksum,
            rpo_seconds=rpo_sec,
            rto_minutes=rto_min,
            is_rpo_compliant=is_rpo_ok,
            is_rto_compliant=is_rto_ok,
            is_restoration_successful=is_success,
            last_recoverable_timestamp_ms=last_recoverable_ms,
            recovery_target_reached=target_reached,
            wal_gap_detected=gap_lsn is not None,
            first_missing_lsn=gap_lsn,
            wal_records_available_count=len(candidates),
            integrity_verified=integrity_verified,
            findings=findings,
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _select_base_snapshot(
        base_snapshots: Sequence[BaseSnapshot], target_timestamp_ms: float
    ) -> BaseSnapshot:
        for snap in base_snapshots:
            if not _is_finite(snap.timestamp_ms):
                raise PitrRestoreError(
                    f"Snapshot {snap.snapshot_id!r} has a non-finite timestamp_ms ({snap.timestamp_ms!r})"
                )
            for table, rows in snap.table_rows.items():
                if rows < 0:
                    raise PitrRestoreError(
                        f"Snapshot {snap.snapshot_id!r} has a negative row count for {table!r} ({rows})"
                    )
        eligible = [s for s in base_snapshots if s.timestamp_ms <= target_timestamp_ms]
        if not eligible:
            raise PitrRestoreError(
                f"No base snapshot completed at or before target timestamp {target_timestamp_ms}ms; "
                "recovery cannot start after its own target"
            )
        return max(eligible, key=lambda s: s.timestamp_ms)

    @staticmethod
    def _ordered_replay_candidates(
        wal_logs: Sequence[WalRecord], base_snap: BaseSnapshot
    ) -> List[WalRecord]:
        """Validate the archive and return post-snapshot records in LSN order."""
        seen_lsns: Dict[int, WalRecord] = {}
        for w in wal_logs:
            if not _is_finite(w.timestamp_ms):
                raise PitrRestoreError(
                    f"WAL record {w.lsn_id} has a non-finite timestamp_ms ({w.timestamp_ms!r})"
                )
            if w.operation not in VALID_OPERATIONS:
                raise PitrRestoreError(
                    f"WAL record {w.lsn_id} has unknown operation {w.operation!r}; "
                    f"expected one of {sorted(VALID_OPERATIONS)}"
                )
            if w.lsn_id in seen_lsns:
                raise PitrRestoreError(
                    f"Duplicate LSN {w.lsn_id} in archive; the replay order is ambiguous"
                )
            seen_lsns[w.lsn_id] = w

        ordered = sorted(wal_logs, key=lambda w: w.lsn_id)
        for prev, nxt in zip(ordered, ordered[1:]):
            if nxt.timestamp_ms < prev.timestamp_ms:
                raise PitrRestoreError(
                    f"WAL record {nxt.lsn_id} (t={nxt.timestamp_ms}ms) precedes LSN {prev.lsn_id} "
                    f"(t={prev.timestamp_ms}ms); LSN order must not contradict commit-time order"
                )
        # Strictly after the snapshot's completion time: the base backup already
        # contains everything at or before it.
        return [w for w in ordered if w.timestamp_ms > base_snap.timestamp_ms]

    @staticmethod
    def _truncate_at_gap(
        candidates: List[WalRecord], require_contiguous_lsn: bool, base_snap: BaseSnapshot
    ) -> Tuple[List[WalRecord], Optional[int]]:
        """Cut the replay at the first hole in the LSN sequence."""
        if not require_contiguous_lsn or not candidates:
            return list(candidates), None
        # A hole between the base backup and the first replayable record is
        # invisible from the candidate list alone; catch it when the snapshot
        # records how far it reaches.
        if base_snap.last_lsn_included is not None:
            if candidates[0].lsn_id != base_snap.last_lsn_included + 1:
                return [], base_snap.last_lsn_included + 1
        for index, (prev, nxt) in enumerate(zip(candidates, candidates[1:]), start=1):
            if nxt.lsn_id != prev.lsn_id + 1:
                return candidates[:index], prev.lsn_id + 1
        return list(candidates), None

    @staticmethod
    def _replay(
        base_snap: BaseSnapshot, replayed: Sequence[WalRecord]
    ) -> Tuple[Dict[str, int], str, List[str]]:
        """Apply records to the snapshot's row counts and digest the sequence.

        The digest covers the base checksum plus every applied record including
        its payload, so it detects a changed, reordered, or altered replay
        sequence. It is a *replay-determinism digest*, not a PostgreSQL data
        checksum: it says the same WAL was applied, not that the pages are
        intact.
        """
        restored_rows = dict(base_snap.table_rows)
        digest = hashlib.sha256(base_snap.state_checksum.encode("utf-8"))
        findings: List[str] = []

        for w in replayed:
            rows = restored_rows.setdefault(w.table_name, 0)
            if w.operation == OP_INSERT:
                restored_rows[w.table_name] = rows + 1
            elif w.operation == OP_DELETE:
                if rows == 0:
                    findings.append(
                        f"REPLAY ANOMALY: LSN {w.lsn_id} deletes {w.row_id!r} from empty table "
                        f"{w.table_name!r}; snapshot row counts and the WAL stream disagree."
                    )
                else:
                    restored_rows[w.table_name] = rows - 1
            # OP_UPDATE leaves the row count unchanged but still advances the digest.
            digest.update(
                f"{w.lsn_id}|{w.table_name}|{w.operation}|{w.row_id}|{w.payload}".encode("utf-8")
            )

        return restored_rows, digest.hexdigest(), findings

    @staticmethod
    def _verify_integrity(
        restored_rows: Dict[str, int],
        restored_checksum: str,
        expected_state: Optional[ExpectedDatabaseState],
    ) -> Tuple[Optional[bool], List[str]]:
        if expected_state is not None and not expected_state.table_rows and expected_state.state_checksum is None:
            raise PitrRestoreError(
                "ExpectedDatabaseState asserts nothing (no table_rows, no state_checksum); "
                "it would report integrity as verified without checking anything"
            )
        if expected_state is None:
            return None, [
                "INTEGRITY NOT VERIFIED: no expected state supplied. A database that starts is not "
                "a database that reconciles -- compare restored ledgers against broker records."
            ]

        findings: List[str] = []
        for table, expected_rows in expected_state.table_rows.items():
            actual = restored_rows.get(table)
            if actual is None:
                findings.append(
                    f"ROW PARITY: table {table!r} missing from restored state (expected {expected_rows})."
                )
            elif actual != expected_rows:
                findings.append(
                    f"ROW PARITY: table {table!r} restored {actual} rows, expected {expected_rows} "
                    f"(delta {actual - expected_rows:+d})."
                )
        if expected_state.state_checksum is not None and expected_state.state_checksum != restored_checksum:
            findings.append(
                f"CHECKSUM MISMATCH: restored {restored_checksum[:16]}..., "
                f"expected {expected_state.state_checksum[:16]}...; the replayed WAL sequence differs."
            )
        return not findings, findings


def _is_finite(value: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
