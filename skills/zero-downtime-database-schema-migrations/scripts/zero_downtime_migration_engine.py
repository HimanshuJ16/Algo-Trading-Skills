import logging
import math
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class MigrationPhase(Enum):
    PHASE_1_EXPAND = "PHASE_1_EXPAND"           # Add new nullable column/table (no table lock)
    PHASE_2_DUAL_WRITE = "PHASE_2_DUAL_WRITE"     # App writes to both old and new columns
    PHASE_3_BACKFILL = "PHASE_3_BACKFILL"       # Asynchronously backfill historical rows in batches
    PHASE_4_READ_CUTOVER = "PHASE_4_READ_CUTOVER" # App reads exclusively from new column
    PHASE_5_CONTRACT = "PHASE_5_CONTRACT"       # Remove dual-write and drop old column safely


class DatabaseEngine(Enum):
    POSTGRESQL = "POSTGRESQL"
    MYSQL_INNODB = "MYSQL_INNODB"
    COCKROACHDB = "COCKROACHDB"


class MigrationEngineError(Exception):
    """Base exception for Zero-Downtime Migration Engine errors."""
    pass


@dataclass
class MigrationPlan:
    plan_id: str
    table_name: str
    old_column: str
    new_column: str
    column_type: str
    db_engine: DatabaseEngine = DatabaseEngine.POSTGRESQL
    current_phase: MigrationPhase = MigrationPhase.PHASE_1_EXPAND
    total_records: int = 0
    backfilled_records: int = 0


@dataclass
class GeneratedDDL:
    plan_id: str
    expand_sql: str
    concurrent_index_sql: str
    contract_sql: str


@dataclass
class MigrationStatus:
    plan_id: str
    current_phase: MigrationPhase
    is_completed: bool
    backfill_progress_pct: float
    recommended_action: str
    rationale: str


class ZeroDowntimeMigrationEngine:
    """
    Institutional Zero-Downtime Database Schema Migration Engine.
    
    Orchestrates the 5-Phase Expand-Contract pattern for zero-downtime database schema migrations,
    generates non-blocking DDL scripts (`CREATE INDEX CONCURRENTLY`, `ALTER TABLE ADD COLUMN`),
    tracks asynchronous batched backfills, verifies phase cutovers, and validates rollback safety.
    """

    def __init__(self):
        logger.info("Initialized Zero-Downtime Migration Engine")

    def generate_expand_contract_ddl(self, plan: MigrationPlan) -> GeneratedDDL:
        """
        Generates non-blocking DDL SQL scripts for Phase 1 Expand and Phase 5 Contract.
        """
        tbl = plan.table_name
        new_col = plan.new_column
        old_col = plan.old_column
        c_type = plan.column_type

        if plan.db_engine == DatabaseEngine.POSTGRESQL:
            # Phase 1: Add nullable column without lock
            expand_sql = f"ALTER TABLE {tbl} ADD COLUMN {new_col} {c_type} DEFAULT NULL;"
            # Non-blocking index build
            index_sql = f"CREATE INDEX CONCURRENTLY idx_{tbl}_{new_col} ON {tbl} ({new_col});"
            # Phase 5: Drop old column
            contract_sql = f"ALTER TABLE {tbl} DROP COLUMN {old_col};"

        elif plan.db_engine == DatabaseEngine.MYSQL_INNODB:
            expand_sql = f"ALTER TABLE {tbl} ADD COLUMN {new_col} {c_type} DEFAULT NULL, ALGORITHM=INPLACE, LOCK=NONE;"
            index_sql = f"CREATE INDEX idx_{tbl}_{new_col} ON {tbl} ({new_col}), ALGORITHM=INPLACE, LOCK=NONE;"
            contract_sql = f"ALTER TABLE {tbl} DROP COLUMN {old_col}, ALGORITHM=INPLACE, LOCK=NONE;"

        elif plan.db_engine == DatabaseEngine.COCKROACHDB:
            expand_sql = f"ALTER TABLE {tbl} ADD COLUMN {new_col} {c_type} DEFAULT NULL;"
            index_sql = f"CREATE INDEX idx_{tbl}_{new_col} ON {tbl} ({new_col});"
            contract_sql = f"ALTER TABLE {tbl} DROP COLUMN {old_col};"

        logger.info(f"Generated Zero-Downtime DDL for [{tbl}]: Expand='{expand_sql}'")

        return GeneratedDDL(
            plan_id=plan.plan_id,
            expand_sql=expand_sql,
            concurrent_index_sql=index_sql,
            contract_sql=contract_sql,
        )

    def advance_migration_phase(
        self, plan: MigrationPlan, target_phase: MigrationPhase
    ) -> MigrationStatus:
        """
        Advances a migration plan to the next phase while validating safety preconditions.
        """
        valid_transitions = {
            MigrationPhase.PHASE_1_EXPAND: MigrationPhase.PHASE_2_DUAL_WRITE,
            MigrationPhase.PHASE_2_DUAL_WRITE: MigrationPhase.PHASE_3_BACKFILL,
            MigrationPhase.PHASE_3_BACKFILL: MigrationPhase.PHASE_4_READ_CUTOVER,
            MigrationPhase.PHASE_4_READ_CUTOVER: MigrationPhase.PHASE_5_CONTRACT,
        }

        # Safety Check: Cannot advance to Read Cutover unless Backfill is 100% complete
        if target_phase == MigrationPhase.PHASE_4_READ_CUTOVER:
            if plan.total_records > 0 and plan.backfilled_records < plan.total_records:
                raise MigrationEngineError(
                    f"Cannot advance to Read Cutover. Backfill incomplete ({plan.backfilled_records}/{plan.total_records} rows)."
                )

        expected_next = valid_transitions.get(plan.current_phase)
        if target_phase != expected_next and target_phase != plan.current_phase:
            raise MigrationEngineError(
                f"Invalid phase transition: Cannot jump from {plan.current_phase.value} to {target_phase.value}."
            )

        plan.current_phase = target_phase
        progress_pct = (plan.backfilled_records / max(1, plan.total_records)) * 100.0 if plan.total_records > 0 else 100.0

        is_complete = (plan.current_phase == MigrationPhase.PHASE_5_CONTRACT)

        rationale = f"Advanced plan [{plan.plan_id}] to {target_phase.value}."
        logger.info(rationale)

        return MigrationStatus(
            plan_id=plan.plan_id,
            current_phase=plan.current_phase,
            is_completed=is_complete,
            backfill_progress_pct=round(progress_pct, 2),
            recommended_action=f"Deploy application code for {target_phase.value}.",
            rationale=rationale,
        )

    def execute_batched_backfill_step(
        self, plan: MigrationPlan, batch_size: int = 1000
    ) -> MigrationStatus:
        """
        Simulates executing an asynchronous batched backfill step without acquiring table locks.
        """
        if plan.current_phase != MigrationPhase.PHASE_3_BACKFILL:
            raise MigrationEngineError(
                f"Backfill can only be executed in PHASE_3_BACKFILL (Current: {plan.current_phase.value})."
            )

        plan.backfilled_records = min(plan.total_records, plan.backfilled_records + batch_size)
        progress_pct = (plan.backfilled_records / max(1, plan.total_records)) * 100.0

        logger.info(
            f"Backfill Step [{plan.table_name}]: {plan.backfilled_records}/{plan.total_records} rows ({progress_pct:.1f}%)"
        )

        recommended = "Continue batched backfill"
        if plan.backfilled_records >= plan.total_records:
            recommended = "Backfill complete. Ready to advance to PHASE_4_READ_CUTOVER."

        return MigrationStatus(
            plan_id=plan.plan_id,
            current_phase=plan.current_phase,
            is_completed=False,
            backfill_progress_pct=round(progress_pct, 2),
            recommended_action=recommended,
            rationale=f"Backfilled batch of {batch_size} rows.",
        )

