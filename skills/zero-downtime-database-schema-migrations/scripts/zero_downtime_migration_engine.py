"""
zero-downtime-database-schema-migrations:
Plans and gates the 5-phase expand-contract schema migration -- generating the
non-blocking DDL for PostgreSQL, MySQL/InnoDB and CockroachDB, pacing the
batched backfill against replica lag, and refusing the reader cutover until the
backfill is proven complete *against the database*, not against a counter.

This module generates and gates. It never opens a connection and never executes
SQL: the statements it emits are reviewed by a human and applied by the
migration tool of record (Alembic, Flyway, Liquibase, gh-ost, pt-osc).

Design rule: "zero downtime" is a property of the lock, not of the statement
------------------------------------------------------------------------------
The dangerous idea this module exists to correct is that expand-contract DDL is
lock-free. It is not.

  * PostgreSQL ``ALTER TABLE ... ADD COLUMN`` and ``DROP COLUMN`` both take an
    **ACCESS EXCLUSIVE** lock ("An ACCESS EXCLUSIVE lock is acquired unless
    explicitly noted" -- ALTER TABLE, Description), and ACCESS EXCLUSIVE
    "conflicts with locks of all modes", *including* the ACCESS SHARE that every
    plain ``SELECT`` takes. The statement itself is fast -- since PostgreSQL 11 a
    non-volatile ``DEFAULT`` is stored in the catalogue and "in neither case is a
    rewrite of the table required" -- but a transaction seeking a conflicting
    lock "will wait indefinitely for conflicting locks to be released". A
    millisecond of DDL parked behind one long-running analytics query is how a
    "zero downtime" migration stops an order book. Hence ``lock_timeout_sql``:
    every emitted PostgreSQL plan is prefixed with a bounded ``lock_timeout`` so
    the DDL fails fast and is retried, rather than waiting.
  * MySQL is the same shape with different spelling. DDL needs an exclusive
    metadata lock and the server "must not permit one session to perform a DDL
    statement on a table that is used in an uncompleted ... transaction in
    another session", so the ALTER blocks behind open transactions. The bound is
    ``lock_wait_timeout``, whose default is **31536000 seconds (one year)** --
    i.e. unbounded in practice unless you set it.

Design rule: prefer the algorithm that only touches metadata
------------------------------------------------------------------------------
For MySQL 8.0 InnoDB, ``ALGORITHM=INSTANT`` is the default for adding a column
since 8.0.12 and for dropping one since 8.0.29, and only modifies metadata.
``ALGORITHM=INPLACE`` is *not* the safe conservative choice it looks like:
per the online-DDL operations table, dropping a column in place **rebuilds the
table**, which on a large fills table is hours of I/O instead of milliseconds.
So the emitted statement is INSTANT, and ``expand_fallback_sql`` /
``contract_fallback_sql`` carry the INPLACE form for the documented cases where
INSTANT is refused (``ROW_FORMAT=COMPRESSED``, tables with FULLTEXT indexes,
columns with functional indexes, or a table that has exhausted its 64 row
versions -- ``ERROR 4092``). Note that only ``LOCK=DEFAULT`` is permitted with
``ALGORITHM=INSTANT``, which is why the INSTANT statements carry no LOCK clause
while the INPLACE fallbacks carry ``LOCK=NONE``.

Adding a secondary index is the exception: it has no INSTANT form, so the index
statement is always ``ALGORITHM=INPLACE LOCK=NONE`` -- written with **no comma**
before the options, because the ``CREATE INDEX`` grammar is
``ON tbl_name (key_part,...) [index_option] [algorithm_option | lock_option]``
with no separator. ``CREATE INDEX ... (col), ALGORITHM=INPLACE`` is a syntax
error; the comma form is only valid in ``ALTER TABLE``, whose options genuinely
are a comma-separated list.

Design rule: a backfill counter is not evidence
------------------------------------------------------------------------------
``backfilled_records / total_records`` reaching 100% does **not** mean no NULLs
remain, and treating it as if it did is the precise failure this skill exists to
prevent. ``total_records`` is a snapshot taken when the plan was written; the
table is live. Any row written after dual-write is fully rolled out populates
``new_column`` itself and never needed backfilling, but any row written in the
window between the Phase 1 expand and the *last* instance picking up dual-write
code has a NULL ``new_column`` and is not counted by a plan-time total. The
counter can read 100.0% with those rows still NULL, and cutting reads over then
returns NULL for real historical orders.

So ``advance_migration_phase`` will not enter Phase 4 on the counter alone. It
requires ``residual_null_rows``, the result of ``GeneratedDDL.verification_sql``
run against the primary at cutover time, and requires it to be exactly zero.
``MigrationPlan.require_residual_null_check`` can disable this, and doing so is
documented as unsafe.

Design rule: validate identifiers, do not quote them
------------------------------------------------------------------------------
Table and column names are interpolated into DDL text, so they are validated
against a strict identifier pattern and anything else is rejected. Quoting was
rejected as the mitigation because in PostgreSQL "quoting an identifier also
makes it case-sensitive, whereas unquoted names are always folded to lower
case": emitting ``"Orders"`` for a plan that says ``Orders`` would silently
address a *different* table than the application's unquoted ``Orders``.
Identifiers are also length-checked at 63 bytes, PostgreSQL's NAMEDATALEN-1,
because longer names "will be truncated" silently -- two generated index names
differing only past byte 63 would collide.

Scope
-----
DDL generation and phase gating for a single old-column -> new-column migration.
This module does not connect to a database, does not execute or transactionally
manage SQL, and does not take the backup that Phase 5 rollback depends on
(``database-backup-and-point-in-time-restore-testing``). Sequencing the
application deploys that each phase implies is
``blue-green-deployment-for-live-strategy-updates``; choosing the window is
``deployment-freeze-windows-around-market-events``.
"""
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "MigrationEngineError",
    "InvalidIdentifierError",
    "InvalidPhaseTransitionError",
    "BackfillIncompleteError",
    "UnsupportedDatabaseEngineError",
    "MigrationPhase",
    "DatabaseEngine",
    "BackfillDirective",
    "MigrationPlan",
    "GeneratedDDL",
    "MigrationStatus",
    "ZeroDowntimeMigrationEngine",
    "MAX_IDENTIFIER_BYTES",
]

#: PostgreSQL NAMEDATALEN-1. Longer identifiers are silently truncated by
#: PostgreSQL and rejected outright by MySQL, so both are refused here.
MAX_IDENTIFIER_BYTES = 63

# Unquoted SQL identifier. Deliberately narrow: this text is interpolated into
# DDL, and anything outside this pattern is refused rather than escaped.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

# A column type: a word (or words, e.g. DOUBLE PRECISION, TIMESTAMP WITH TIME
# ZONE), an optional precision/scale, an optional array suffix. No commas
# outside the precision, no parenthesis nesting, no statement terminators.
_COLUMN_TYPE_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9 ]*(\(\s*\d+(\s*,\s*\d+)?\s*\))?(\s*\[\s*\])?$"
)
_MAX_COLUMN_TYPE_LEN = 64

# Constraint keywords that match the type pattern as bare words but do not
# belong in a type. NOT NULL is the one that matters: adding a NOT NULL column
# is the exact blocking DDL the expand phase exists to avoid, and it would ride
# in as "VARCHAR 64 NOT NULL" without this check.
_FORBIDDEN_TYPE_KEYWORDS = (
    "NOT NULL", "DEFAULT", "GENERATED", "PRIMARY", "UNIQUE", "REFERENCES",
    "CHECK", "CONSTRAINT", "AUTO INCREMENT", "IDENTITY",
)


class MigrationEngineError(Exception):
    """Base exception for Zero-Downtime Migration Engine errors."""


class InvalidIdentifierError(MigrationEngineError):
    """A table, column or type name is not safe to interpolate into DDL."""


class InvalidPhaseTransitionError(MigrationEngineError):
    """A phase change was requested that the expand-contract order forbids."""


class BackfillIncompleteError(MigrationEngineError):
    """Reader cutover was requested before the backfill was proven complete."""


class UnsupportedDatabaseEngineError(MigrationEngineError):
    """No non-blocking DDL dialect is defined for the requested engine."""


class MigrationPhase(Enum):
    PHASE_1_EXPAND = "PHASE_1_EXPAND"             # Add new nullable column (brief ACCESS EXCLUSIVE / metadata lock)
    PHASE_2_DUAL_WRITE = "PHASE_2_DUAL_WRITE"     # App writes to both old and new columns
    PHASE_3_BACKFILL = "PHASE_3_BACKFILL"         # Asynchronously backfill historical rows in batches
    PHASE_4_READ_CUTOVER = "PHASE_4_READ_CUTOVER" # App reads exclusively from new column
    PHASE_5_CONTRACT = "PHASE_5_CONTRACT"         # Remove dual-write and drop old column


class DatabaseEngine(Enum):
    POSTGRESQL = "POSTGRESQL"
    MYSQL_INNODB = "MYSQL_INNODB"
    COCKROACHDB = "COCKROACHDB"


class BackfillDirective(Enum):
    """What the caller's backfill loop should do next."""

    CONTINUE = "CONTINUE"   # Apply the next batch immediately
    THROTTLE = "THROTTLE"   # Replica lag over budget; pause, do not apply a batch
    COMPLETE = "COMPLETE"   # Counter exhausted; verify residual NULLs, then cut over


# Phase ordering used for both forward transitions and rollback.
_PHASE_ORDER: Tuple[MigrationPhase, ...] = (
    MigrationPhase.PHASE_1_EXPAND,
    MigrationPhase.PHASE_2_DUAL_WRITE,
    MigrationPhase.PHASE_3_BACKFILL,
    MigrationPhase.PHASE_4_READ_CUTOVER,
    MigrationPhase.PHASE_5_CONTRACT,
)


def _validate_identifier(value: str, role: str) -> str:
    """Return ``value`` if it is a safe unquoted SQL identifier, else raise."""
    if not isinstance(value, str):
        raise InvalidIdentifierError(f"{role} must be a string, got {type(value).__name__}.")
    candidate = value.strip()
    if not _IDENTIFIER_RE.match(candidate):
        raise InvalidIdentifierError(
            f"{role} {value!r} is not a valid unquoted SQL identifier "
            f"(expected /^[A-Za-z_][A-Za-z0-9_$]*$/). Identifiers are interpolated "
            f"into DDL and are refused rather than escaped."
        )
    if len(candidate.encode("utf-8")) > MAX_IDENTIFIER_BYTES:
        raise InvalidIdentifierError(
            f"{role} {value!r} is {len(candidate.encode('utf-8'))} bytes; the limit is "
            f"{MAX_IDENTIFIER_BYTES} (PostgreSQL truncates silently, MySQL errors)."
        )
    return candidate


def _validate_column_type(value: str) -> str:
    """Return ``value`` if it is a safe column type expression, else raise."""
    if not isinstance(value, str):
        raise InvalidIdentifierError(f"column_type must be a string, got {type(value).__name__}.")
    candidate = " ".join(value.split())
    if not candidate or len(candidate) > _MAX_COLUMN_TYPE_LEN:
        raise InvalidIdentifierError(
            f"column_type {value!r} must be 1..{_MAX_COLUMN_TYPE_LEN} characters."
        )
    if not _COLUMN_TYPE_RE.match(candidate):
        raise InvalidIdentifierError(
            f"column_type {value!r} is not a recognised type expression "
            f"(e.g. 'BIGINT', 'VARCHAR(64)', 'NUMERIC(18,8)', 'TIMESTAMPTZ')."
        )
    upper = candidate.upper()
    for keyword in _FORBIDDEN_TYPE_KEYWORDS:
        if keyword in upper:
            raise InvalidIdentifierError(
                f"column_type {value!r} contains the constraint keyword {keyword!r}. "
                f"column_type is the type alone; the expand phase adds the column as "
                f"NULLable on purpose, and a NOT NULL or defaulted column is the blocking "
                f"DDL this skill exists to avoid."
            )
    return candidate


@dataclass
class MigrationPlan:
    """One old-column -> new-column expand-contract migration.

    ``total_records`` is a plan-time snapshot of the rows needing backfill. It
    paces the batch loop; it is deliberately **not** sufficient evidence for
    cutover -- see ``require_residual_null_check``.
    """

    plan_id: str
    table_name: str
    old_column: str
    new_column: str
    column_type: str
    db_engine: DatabaseEngine = DatabaseEngine.POSTGRESQL
    current_phase: MigrationPhase = MigrationPhase.PHASE_1_EXPAND
    total_records: int = 0
    backfilled_records: int = 0
    #: Replica lag ceiling, in seconds, above which the backfill throttles.
    replica_lag_budget_seconds: float = 1.0
    #: Batch sizes above this are honoured but warned about; large batches hold
    #: row locks and generate replication volume for longer.
    max_batch_size: int = 5000
    #: When True (default), Phase 4 requires an authoritative count of rows
    #: still NULL in ``new_column``. Setting it False cuts reads over on the
    #: backfill counter alone, which can read 100% while rows written during
    #: the dual-write rollout are still NULL. Unsafe; documented as such.
    require_residual_null_check: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.plan_id, str) or not self.plan_id.strip():
            raise InvalidIdentifierError("plan_id must be a non-empty string.")
        self.plan_id = self.plan_id.strip()
        self.table_name = _validate_identifier(self.table_name, "table_name")
        self.old_column = _validate_identifier(self.old_column, "old_column")
        self.new_column = _validate_identifier(self.new_column, "new_column")
        self.column_type = _validate_column_type(self.column_type)

        if self.old_column.lower() == self.new_column.lower():
            raise InvalidIdentifierError(
                f"old_column and new_column are both {self.old_column!r}; PostgreSQL folds "
                f"unquoted identifiers to lower case, so these name the same column."
            )
        if not isinstance(self.db_engine, DatabaseEngine):
            raise UnsupportedDatabaseEngineError(
                f"db_engine must be a DatabaseEngine, got {self.db_engine!r}."
            )
        if not isinstance(self.current_phase, MigrationPhase):
            raise InvalidPhaseTransitionError(
                f"current_phase must be a MigrationPhase, got {self.current_phase!r}."
            )
        # bool is a subclass of int; reject it explicitly.
        for name in ("total_records", "backfilled_records", "max_batch_size"):
            val = getattr(self, name)
            if isinstance(val, bool) or not isinstance(val, int):
                raise MigrationEngineError(f"{name} must be an int, got {val!r}.")
        if self.total_records < 0:
            raise MigrationEngineError(f"total_records must be >= 0, got {self.total_records}.")
        if self.backfilled_records < 0:
            raise MigrationEngineError(
                f"backfilled_records must be >= 0, got {self.backfilled_records}."
            )
        if self.backfilled_records > self.total_records:
            raise MigrationEngineError(
                f"backfilled_records ({self.backfilled_records}) exceeds total_records "
                f"({self.total_records})."
            )
        if self.max_batch_size < 1:
            raise MigrationEngineError(f"max_batch_size must be >= 1, got {self.max_batch_size}.")
        lag_budget = self.replica_lag_budget_seconds
        if isinstance(lag_budget, bool) or not isinstance(lag_budget, (int, float)):
            raise MigrationEngineError(
                f"replica_lag_budget_seconds must be a number, got {lag_budget!r}."
            )
        # NaN fails both comparisons below, which is the intent: an unusable
        # budget must not silently disable throttling.
        if not lag_budget > 0:
            raise MigrationEngineError(
                f"replica_lag_budget_seconds must be a positive number, got {lag_budget}."
            )


@dataclass
class GeneratedDDL:
    """The statements for one migration, in the order they are applied.

    ``expand_fallback_sql`` and ``contract_fallback_sql`` are empty strings for
    engines that have no second algorithm to fall back to.
    """

    plan_id: str
    expand_sql: str
    concurrent_index_sql: str
    contract_sql: str
    expand_fallback_sql: str = ""
    contract_fallback_sql: str = ""
    #: Run before the DDL in the same session: bounds how long the statement
    #: will wait for its lock instead of waiting indefinitely.
    lock_timeout_sql: str = ""
    #: Removes a failed CONCURRENTLY build's INVALID index, which the planner
    #: ignores but writes still maintain.
    index_cleanup_sql: str = ""
    #: The authoritative cutover gate: must return 0 before Phase 4.
    verification_sql: str = ""
    #: Engine-specific caveats a reviewer must read before applying.
    notes: Tuple[str, ...] = field(default_factory=tuple)


@dataclass
class MigrationStatus:
    plan_id: str
    current_phase: MigrationPhase
    #: True once the plan has *entered* PHASE_5_CONTRACT. The DROP COLUMN
    #: itself is the caller's step; this does not assert it has been applied.
    is_completed: bool
    backfill_progress_pct: float
    recommended_action: str
    rationale: str
    #: Set by ``execute_batched_backfill_step``; None for phase transitions.
    directive: Optional[BackfillDirective] = None
    #: True when Phase 4 was gated on an authoritative residual-NULL count,
    #: False when it was gated on the backfill counter alone, None otherwise.
    residual_nulls_verified: Optional[bool] = None


class ZeroDowntimeMigrationEngine:
    """Generates non-blocking expand-contract DDL and gates the phase changes.

    Stateless: every method takes the ``MigrationPlan`` it acts on. Phase
    transitions mutate that plan in place and are not synchronized, so a plan
    should be driven by one migration runner at a time.
    """

    def __init__(self) -> None:
        logger.info("Initialized Zero-Downtime Migration Engine")

    # ------------------------------------------------------------------
    # DDL generation
    # ------------------------------------------------------------------
    def _index_name(self, plan: MigrationPlan) -> str:
        name = f"idx_{plan.table_name}_{plan.new_column}"
        if len(name.encode("utf-8")) > MAX_IDENTIFIER_BYTES:
            raise InvalidIdentifierError(
                f"Generated index name {name!r} is {len(name.encode('utf-8'))} bytes, over the "
                f"{MAX_IDENTIFIER_BYTES}-byte limit. PostgreSQL would truncate it silently "
                f"(risking a collision with another generated name) and MySQL would reject it. "
                f"Shorten table_name/new_column or name the index explicitly."
            )
        return name

    def generate_expand_contract_ddl(self, plan: MigrationPlan) -> GeneratedDDL:
        """Generate the non-blocking DDL for Phase 1 expand and Phase 5 contract.

        The statements are emitted for review and execution by the migration
        tool of record; nothing here connects to a database.
        """
        if not isinstance(plan, MigrationPlan):
            raise MigrationEngineError(f"plan must be a MigrationPlan, got {type(plan).__name__}.")

        # MigrationPlan is a mutable dataclass, so __post_init__ is not a
        # boundary: a field reassigned after construction has never been
        # checked. Everything interpolated below is re-validated here.
        tbl = _validate_identifier(plan.table_name, "table_name")
        new_col = _validate_identifier(plan.new_column, "new_column")
        old_col = _validate_identifier(plan.old_column, "old_column")
        c_type = _validate_column_type(plan.column_type)
        idx = self._index_name(plan)

        expand_fallback = ""
        contract_fallback = ""

        if plan.db_engine == DatabaseEngine.POSTGRESQL:
            # ADD COLUMN takes ACCESS EXCLUSIVE, which conflicts with the ACCESS
            # SHARE every SELECT holds. It is fast (a non-volatile DEFAULT is
            # catalogue-only since PG 11) but it must not wait unbounded for the
            # lock, hence lock_timeout.
            lock_timeout_sql = "SET lock_timeout = '3s';"
            expand_sql = f"ALTER TABLE {tbl} ADD COLUMN {new_col} {c_type} DEFAULT NULL;"
            # CONCURRENTLY cannot run inside a transaction block.
            index_sql = f"CREATE INDEX CONCURRENTLY {idx} ON {tbl} ({new_col});"
            index_cleanup_sql = f"DROP INDEX CONCURRENTLY IF EXISTS {idx};"
            contract_sql = f"ALTER TABLE {tbl} DROP COLUMN {old_col};"
            notes = (
                "ADD COLUMN and DROP COLUMN both take ACCESS EXCLUSIVE, which conflicts with "
                "every lock mode including the ACCESS SHARE taken by SELECT. Run them behind "
                "lock_timeout and retry on timeout; never let them queue behind a long query.",
                "Add the column with DEFAULT NULL or a non-volatile default only. A volatile "
                "default (random(), gen_random_uuid(), clock_timestamp()) rewrites the entire "
                "table and its indexes under that ACCESS EXCLUSIVE lock.",
                "CREATE INDEX CONCURRENTLY cannot run inside a transaction block -- Alembic and "
                "Flyway wrap migrations in one by default, so this statement must be applied "
                "outside transactional migration control.",
                "A failed CONCURRENTLY build leaves an INVALID index that the planner ignores "
                "but writes still maintain. Check pg_index.indisvalid, then apply "
                "index_cleanup_sql and rebuild.",
                "DROP COLUMN does not reclaim space; the column is made invisible and the space "
                "returns only as rows are rewritten.",
            )

        elif plan.db_engine == DatabaseEngine.MYSQL_INNODB:
            # Default lock_wait_timeout is 31536000s (one year): unbounded in
            # practice. DDL needs an exclusive metadata lock and blocks behind
            # any open transaction touching the table.
            lock_timeout_sql = "SET SESSION lock_wait_timeout = 3;"
            # INSTANT is the default for ADD COLUMN since 8.0.12 and is
            # metadata-only. Only LOCK=DEFAULT is permitted with INSTANT, so no
            # LOCK clause is emitted here.
            expand_sql = (
                f"ALTER TABLE {tbl} ADD COLUMN {new_col} {c_type} DEFAULT NULL, "
                f"ALGORITHM=INSTANT;"
            )
            expand_fallback = (
                f"ALTER TABLE {tbl} ADD COLUMN {new_col} {c_type} DEFAULT NULL, "
                f"ALGORITHM=INPLACE, LOCK=NONE;"
            )
            # A secondary index has no INSTANT form. CREATE INDEX takes its
            # options with NO comma separator -- the comma form is an ALTER
            # TABLE construct and is a syntax error here.
            index_sql = f"CREATE INDEX {idx} ON {tbl} ({new_col}) ALGORITHM=INPLACE LOCK=NONE;"
            index_cleanup_sql = f"DROP INDEX {idx} ON {tbl} ALGORITHM=INPLACE LOCK=NONE;"
            # INSTANT is the default for DROP COLUMN since 8.0.29. Dropping a
            # column INPLACE rebuilds the whole table.
            contract_sql = f"ALTER TABLE {tbl} DROP COLUMN {old_col}, ALGORITHM=INSTANT;"
            contract_fallback = (
                f"ALTER TABLE {tbl} DROP COLUMN {old_col}, ALGORITHM=INPLACE, LOCK=NONE;"
            )
            notes = (
                "ALGORITHM=INSTANT requires MySQL 8.0.12+ for ADD COLUMN and 8.0.29+ for DROP "
                "COLUMN. Use the fallback statements on older servers.",
                "INSTANT is refused on ROW_FORMAT=COMPRESSED tables, tables with FULLTEXT "
                "indexes, columns with functional indexes, and after 64 row versions "
                "(ERROR 4092). Check INNODB_TABLES.TOTAL_ROW_VERSIONS before relying on it.",
                "The INPLACE fallback for DROP COLUMN rebuilds the entire table. It permits "
                "concurrent DML, but budget the I/O and the replication volume.",
                "Only LOCK=DEFAULT is permitted with ALGORITHM=INSTANT, which is why the INSTANT "
                "statements carry no LOCK clause.",
                "lock_wait_timeout defaults to 31536000 seconds (one year). Set it per session "
                "or the DDL waits effectively forever for its metadata lock.",
            )

        elif plan.db_engine == DatabaseEngine.COCKROACHDB:
            # Schema changes are asynchronous background jobs and do not hold
            # locks on table data, so there is no lock timeout to set.
            lock_timeout_sql = ""
            expand_sql = f"ALTER TABLE {tbl} ADD COLUMN {new_col} {c_type} DEFAULT NULL;"
            # No CONCURRENTLY keyword: CREATE INDEX is already an online schema
            # change here.
            index_sql = f"CREATE INDEX {idx} ON {tbl} ({new_col});"
            index_cleanup_sql = f"DROP INDEX {tbl}@{idx};"
            contract_sql = f"ALTER TABLE {tbl} DROP COLUMN {old_col};"
            notes = (
                "Schema changes run as asynchronous background jobs and return before they are "
                "applied. Poll the returned job ID to completion; do not treat statement "
                "success as schema success.",
                "Do not put these statements in a multi-statement explicit transaction: schema "
                "change DDL can fail while other statements succeed, leaving the transaction "
                "partially committed and partially aborted.",
                "Phase 5 is the risk point on this engine: some column drops cannot be rolled "
                "back properly, and the rollback may succeed with the column data partially or "
                "totally missing. Take a verified backup before the contract step.",
            )

        else:
            raise UnsupportedDatabaseEngineError(
                f"No non-blocking DDL dialect defined for {plan.db_engine!r}. "
                f"Supported: {', '.join(e.value for e in DatabaseEngine)}."
            )

        verification_sql = (
            f"SELECT count(*) AS residual_null_rows FROM {tbl} WHERE {new_col} IS NULL;"
        )

        logger.info(
            "Generated zero-downtime DDL for %s.%s -> %s on %s",
            tbl, old_col, new_col, plan.db_engine.value,
        )

        return GeneratedDDL(
            plan_id=plan.plan_id,
            expand_sql=expand_sql,
            concurrent_index_sql=index_sql,
            contract_sql=contract_sql,
            expand_fallback_sql=expand_fallback,
            contract_fallback_sql=contract_fallback,
            lock_timeout_sql=lock_timeout_sql,
            index_cleanup_sql=index_cleanup_sql,
            verification_sql=verification_sql,
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Phase transitions
    # ------------------------------------------------------------------
    def _progress_pct(self, plan: MigrationPlan) -> float:
        if plan.total_records <= 0:
            return 100.0
        return (plan.backfilled_records / plan.total_records) * 100.0

    def advance_migration_phase(
        self,
        plan: MigrationPlan,
        target_phase: MigrationPhase,
        residual_null_rows: Optional[int] = None,
    ) -> MigrationStatus:
        """Advance one phase, validating the preconditions for that phase.

        ``residual_null_rows`` is the result of ``GeneratedDDL.verification_sql``
        run against the primary. It is required to enter Phase 4 unless
        ``plan.require_residual_null_check`` is False, and it must be exactly 0:
        the backfill counter is a plan-time snapshot and can read 100% while
        rows written during the dual-write rollout are still NULL.
        """
        if not isinstance(plan, MigrationPlan):
            raise MigrationEngineError(f"plan must be a MigrationPlan, got {type(plan).__name__}.")
        if not isinstance(target_phase, MigrationPhase):
            raise InvalidPhaseTransitionError(
                f"target_phase must be a MigrationPhase, got {target_phase!r}."
            )

        # Transition legality is checked first, so an illegal jump reports the
        # jump rather than whichever precondition happens to fail alongside it.
        if target_phase != plan.current_phase:
            current_idx = _PHASE_ORDER.index(plan.current_phase)
            target_idx = _PHASE_ORDER.index(target_phase)
            if target_idx != current_idx + 1:
                raise InvalidPhaseTransitionError(
                    f"Invalid phase transition: cannot go from {plan.current_phase.value} to "
                    f"{target_phase.value}. Expand-contract phases advance one step at a time; "
                    f"use rollback_migration_phase() to step back."
                )

        verified: Optional[bool] = None
        if target_phase == MigrationPhase.PHASE_4_READ_CUTOVER:
            verified = self._gate_read_cutover(plan, residual_null_rows)

        plan.current_phase = target_phase
        is_complete = plan.current_phase == MigrationPhase.PHASE_5_CONTRACT
        rationale = f"Advanced plan [{plan.plan_id}] to {target_phase.value}."
        logger.info(rationale)

        return MigrationStatus(
            plan_id=plan.plan_id,
            current_phase=plan.current_phase,
            is_completed=is_complete,
            backfill_progress_pct=round(self._progress_pct(plan), 2),
            recommended_action=f"Deploy application code for {target_phase.value}.",
            rationale=rationale,
            residual_nulls_verified=verified,
        )

    def _gate_read_cutover(
        self, plan: MigrationPlan, residual_null_rows: Optional[int]
    ) -> bool:
        """Return True if cutover was gated on an authoritative count."""
        if plan.backfilled_records < plan.total_records:
            raise BackfillIncompleteError(
                f"Cannot advance to Read Cutover. Backfill incomplete "
                f"({plan.backfilled_records}/{plan.total_records} rows)."
            )

        verification_sql = (
            f"SELECT count(*) FROM {plan.table_name} WHERE {plan.new_column} IS NULL;"
        )
        if residual_null_rows is None:
            if plan.require_residual_null_check:
                raise BackfillIncompleteError(
                    f"Cannot advance to Read Cutover without an authoritative residual-NULL "
                    f"count. The backfill counter reads "
                    f"{round(self._progress_pct(plan), 2)}%, but total_records is a plan-time "
                    f"snapshot and does not include rows written before dual-write finished "
                    f"rolling out. Run `{verification_sql}` on the primary and pass the result "
                    f"as residual_null_rows."
                )
            logger.warning(
                "Plan [%s] is cutting reads over on the backfill counter alone "
                "(require_residual_null_check=False). Rows written during the dual-write "
                "rollout may still be NULL in %s.",
                plan.plan_id, plan.new_column,
            )
            return False

        if isinstance(residual_null_rows, bool) or not isinstance(residual_null_rows, int):
            raise MigrationEngineError(
                f"residual_null_rows must be an int, got {residual_null_rows!r}."
            )
        if residual_null_rows < 0:
            raise MigrationEngineError(
                f"residual_null_rows must be >= 0, got {residual_null_rows}."
            )
        if residual_null_rows > 0:
            raise BackfillIncompleteError(
                f"Cannot advance to Read Cutover. {residual_null_rows} row(s) in "
                f"{plan.table_name} still have {plan.new_column} IS NULL despite the backfill "
                f"counter reading {round(self._progress_pct(plan), 2)}%. Reading from "
                f"{plan.new_column} now returns NULL for those rows. Confirm dual-write is live "
                f"on every instance, then backfill the remainder."
            )
        return True

    def rollback_migration_phase(
        self, plan: MigrationPlan, reason: str = "unspecified"
    ) -> MigrationStatus:
        """Step the plan back one phase after a failed deploy.

        Phases 2-4 are reversible by redeploying the previous application code
        (the new column keeps receiving writes; nothing is destroyed). Phase 5
        is not: the old column's data is gone once dropped, so this raises and
        directs the caller to the restore path instead.

        ``backfilled_records`` is deliberately not reset -- backfilled rows stay
        correct across a code rollback, and zeroing the counter would force a
        redundant re-scan of the table.
        """
        if not isinstance(plan, MigrationPlan):
            raise MigrationEngineError(f"plan must be a MigrationPlan, got {type(plan).__name__}.")

        if plan.current_phase == MigrationPhase.PHASE_5_CONTRACT:
            raise InvalidPhaseTransitionError(
                f"Cannot roll back PHASE_5_CONTRACT for plan [{plan.plan_id}]: the old column "
                f"has been dropped and its data is not recoverable from the live table. "
                f"Restore {plan.table_name}.{plan.old_column} from backup "
                f"(database-backup-and-point-in-time-restore-testing)."
            )
        if plan.current_phase == MigrationPhase.PHASE_1_EXPAND:
            raise InvalidPhaseTransitionError(
                f"Plan [{plan.plan_id}] is already at PHASE_1_EXPAND. To abandon the migration, "
                f"drop {plan.new_column} -- no application code depends on it yet."
            )

        previous = _PHASE_ORDER[_PHASE_ORDER.index(plan.current_phase) - 1]
        rolled_from = plan.current_phase
        plan.current_phase = previous
        rationale = (
            f"Rolled plan [{plan.plan_id}] back from {rolled_from.value} to {previous.value}: "
            f"{reason}."
        )
        logger.warning(rationale)

        return MigrationStatus(
            plan_id=plan.plan_id,
            current_phase=plan.current_phase,
            is_completed=False,
            backfill_progress_pct=round(self._progress_pct(plan), 2),
            recommended_action=f"Redeploy the application code for {previous.value}.",
            rationale=rationale,
        )

    # ------------------------------------------------------------------
    # Backfill
    # ------------------------------------------------------------------
    def execute_batched_backfill_step(
        self,
        plan: MigrationPlan,
        batch_size: int = 1000,
        replica_lag_seconds: Optional[float] = None,
    ) -> MigrationStatus:
        """Account for one backfill batch and say what the loop should do next.

        This updates the plan's progress counters; the caller executes the
        ``UPDATE ... WHERE new_column IS NULL LIMIT n`` itself.

        Pass ``replica_lag_seconds`` from replica monitoring. When it exceeds
        ``plan.replica_lag_budget_seconds`` the returned directive is THROTTLE
        and **no batch is counted** -- the caller must pause rather than apply
        the batch, because a backfill that outruns replication starves every
        read replica the trading system reads from.
        """
        if not isinstance(plan, MigrationPlan):
            raise MigrationEngineError(f"plan must be a MigrationPlan, got {type(plan).__name__}.")
        if plan.current_phase != MigrationPhase.PHASE_3_BACKFILL:
            raise InvalidPhaseTransitionError(
                f"Backfill can only be executed in PHASE_3_BACKFILL (current: "
                f"{plan.current_phase.value})."
            )
        if isinstance(batch_size, bool) or not isinstance(batch_size, int):
            raise MigrationEngineError(f"batch_size must be an int, got {batch_size!r}.")
        if batch_size < 1:
            # A zero batch would spin the caller's loop forever; a negative one
            # would walk backfilled_records backwards and silently un-do
            # verified progress ahead of the cutover gate.
            raise MigrationEngineError(f"batch_size must be >= 1, got {batch_size}.")
        if batch_size > plan.max_batch_size:
            logger.warning(
                "Backfill batch_size %d exceeds max_batch_size %d for plan [%s]; large batches "
                "hold row locks and generate replication volume for longer.",
                batch_size, plan.max_batch_size, plan.plan_id,
            )

        if replica_lag_seconds is not None:
            if isinstance(replica_lag_seconds, bool) or not isinstance(
                replica_lag_seconds, (int, float)
            ):
                raise MigrationEngineError(
                    f"replica_lag_seconds must be a number, got {replica_lag_seconds!r}."
                )
            if replica_lag_seconds != replica_lag_seconds:  # NaN
                raise MigrationEngineError(
                    "replica_lag_seconds is NaN; a missing lag reading must not be treated as "
                    "zero lag. Pass None only when lag is genuinely not monitored."
                )
            if replica_lag_seconds < 0:
                raise MigrationEngineError(
                    f"replica_lag_seconds must be >= 0, got {replica_lag_seconds}."
                )
            if replica_lag_seconds > plan.replica_lag_budget_seconds:
                pct = self._progress_pct(plan)
                logger.warning(
                    "Throttling backfill for plan [%s]: replica lag %.3fs exceeds budget %.3fs. "
                    "No batch applied.",
                    plan.plan_id, replica_lag_seconds, plan.replica_lag_budget_seconds,
                )
                return MigrationStatus(
                    plan_id=plan.plan_id,
                    current_phase=plan.current_phase,
                    is_completed=False,
                    backfill_progress_pct=round(pct, 2),
                    recommended_action=(
                        f"Pause backfill: replica lag {replica_lag_seconds:.3f}s exceeds budget "
                        f"{plan.replica_lag_budget_seconds:.3f}s."
                    ),
                    rationale="Batch withheld to let read replicas catch up.",
                    directive=BackfillDirective.THROTTLE,
                )

        plan.backfilled_records = min(plan.total_records, plan.backfilled_records + batch_size)
        progress_pct = self._progress_pct(plan)

        logger.info(
            "Backfill step [%s]: %d/%d rows (%.1f%%)",
            plan.table_name, plan.backfilled_records, plan.total_records, progress_pct,
        )

        if plan.backfilled_records >= plan.total_records:
            directive = BackfillDirective.COMPLETE
            recommended = (
                "Backfill counter exhausted. Run the residual-NULL verification query and pass "
                "the result to advance_migration_phase() before cutting reads over."
            )
        else:
            directive = BackfillDirective.CONTINUE
            recommended = "Continue batched backfill"

        return MigrationStatus(
            plan_id=plan.plan_id,
            current_phase=plan.current_phase,
            is_completed=False,
            backfill_progress_pct=round(progress_pct, 2),
            recommended_action=recommended,
            rationale=f"Backfilled batch of {batch_size} rows.",
            directive=directive,
        )
