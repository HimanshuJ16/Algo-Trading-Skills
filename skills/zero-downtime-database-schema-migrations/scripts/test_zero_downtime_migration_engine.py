import unittest

from zero_downtime_migration_engine import (
    BackfillDirective,
    BackfillIncompleteError,
    DatabaseEngine,
    GeneratedDDL,
    InvalidIdentifierError,
    InvalidPhaseTransitionError,
    MAX_IDENTIFIER_BYTES,
    MigrationEngineError,
    MigrationPhase,
    MigrationPlan,
    MigrationStatus,
    UnsupportedDatabaseEngineError,
    ZeroDowntimeMigrationEngine,
)


def make_plan(**overrides):
    kwargs = dict(
        plan_id="MIG-001",
        table_name="orders",
        old_column="client_order_id",
        new_column="cl_ord_id",
        column_type="VARCHAR(64)",
        db_engine=DatabaseEngine.POSTGRESQL,
        total_records=5000,
    )
    kwargs.update(overrides)
    return MigrationPlan(**kwargs)


class TestDDLGeneration(unittest.TestCase):
    def setUp(self):
        self.engine = ZeroDowntimeMigrationEngine()
        self.pg_plan = make_plan()

    def test_generate_postgresql_ddl(self):
        ddl = self.engine.generate_expand_contract_ddl(self.pg_plan)
        self.assertIn("ADD COLUMN cl_ord_id VARCHAR(64) DEFAULT NULL", ddl.expand_sql)
        self.assertIn("CREATE INDEX CONCURRENTLY", ddl.concurrent_index_sql)
        self.assertIn("idx_orders_cl_ord_id", ddl.concurrent_index_sql)
        self.assertIn("DROP COLUMN client_order_id", ddl.contract_sql)

    def test_postgresql_ddl_carries_lock_timeout(self):
        """ADD COLUMN takes ACCESS EXCLUSIVE and waits indefinitely by default."""
        ddl = self.engine.generate_expand_contract_ddl(self.pg_plan)
        self.assertIn("lock_timeout", ddl.lock_timeout_sql)

    def test_postgresql_ddl_carries_invalid_index_cleanup(self):
        """A failed CONCURRENTLY build leaves an INVALID index needing a drop."""
        ddl = self.engine.generate_expand_contract_ddl(self.pg_plan)
        self.assertIn("DROP INDEX CONCURRENTLY", ddl.index_cleanup_sql)
        self.assertIn("idx_orders_cl_ord_id", ddl.index_cleanup_sql)

    def test_postgresql_notes_flag_transaction_block_restriction(self):
        ddl = self.engine.generate_expand_contract_ddl(self.pg_plan)
        joined = " ".join(ddl.notes)
        self.assertIn("transaction block", joined)
        self.assertIn("ACCESS EXCLUSIVE", joined)

    def test_verification_sql_counts_residual_nulls(self):
        ddl = self.engine.generate_expand_contract_ddl(self.pg_plan)
        self.assertIn("FROM orders WHERE cl_ord_id IS NULL", ddl.verification_sql)

    # -- MySQL ---------------------------------------------------------
    def test_mysql_ddl_prefers_instant_algorithm(self):
        """INSTANT is metadata-only; INPLACE DROP COLUMN rebuilds the table."""
        ddl = self.engine.generate_expand_contract_ddl(
            make_plan(db_engine=DatabaseEngine.MYSQL_INNODB)
        )
        self.assertIn("ALGORITHM=INSTANT", ddl.expand_sql)
        self.assertIn("ALGORITHM=INSTANT", ddl.contract_sql)
        self.assertIn("ALGORITHM=INPLACE, LOCK=NONE", ddl.expand_fallback_sql)
        self.assertIn("ALGORITHM=INPLACE, LOCK=NONE", ddl.contract_fallback_sql)

    def test_mysql_instant_statements_carry_no_lock_clause(self):
        """Regression: only LOCK=DEFAULT is permitted with ALGORITHM=INSTANT."""
        ddl = self.engine.generate_expand_contract_ddl(
            make_plan(db_engine=DatabaseEngine.MYSQL_INNODB)
        )
        for stmt in (ddl.expand_sql, ddl.contract_sql):
            self.assertIn("ALGORITHM=INSTANT", stmt)
            self.assertNotIn("LOCK=", stmt)

    def test_mysql_create_index_uses_no_comma_before_options(self):
        """Regression: `CREATE INDEX ... (col), ALGORITHM=...` is a syntax error.

        The comma-separated option list is an ALTER TABLE construct. CREATE
        INDEX's grammar is `ON tbl (key_part,...) [index_option]
        [algorithm_option | lock_option]` with no separator, so the generated
        statement must not place a comma after the key-part list.
        """
        ddl = self.engine.generate_expand_contract_ddl(
            make_plan(db_engine=DatabaseEngine.MYSQL_INNODB)
        )
        self.assertIn("(cl_ord_id) ALGORITHM=INPLACE LOCK=NONE", ddl.concurrent_index_sql)
        self.assertNotIn("), ALGORITHM", ddl.concurrent_index_sql)
        self.assertNotIn("INPLACE, LOCK", ddl.concurrent_index_sql)
        # A secondary index has no INSTANT form.
        self.assertNotIn("INSTANT", ddl.concurrent_index_sql)

    def test_mysql_ddl_bounds_metadata_lock_wait(self):
        """lock_wait_timeout defaults to one year, i.e. unbounded in practice."""
        ddl = self.engine.generate_expand_contract_ddl(
            make_plan(db_engine=DatabaseEngine.MYSQL_INNODB)
        )
        self.assertIn("lock_wait_timeout", ddl.lock_timeout_sql)

    # -- CockroachDB ---------------------------------------------------
    def test_cockroachdb_index_omits_concurrently_keyword(self):
        """CREATE INDEX is already an online schema change on CockroachDB."""
        ddl = self.engine.generate_expand_contract_ddl(
            make_plan(db_engine=DatabaseEngine.COCKROACHDB)
        )
        self.assertNotIn("CONCURRENTLY", ddl.concurrent_index_sql)
        self.assertIn("CREATE INDEX idx_orders_cl_ord_id ON orders (cl_ord_id)", ddl.concurrent_index_sql)

    def test_cockroachdb_notes_flag_unrecoverable_column_drop(self):
        ddl = self.engine.generate_expand_contract_ddl(
            make_plan(db_engine=DatabaseEngine.COCKROACHDB)
        )
        joined = " ".join(ddl.notes)
        self.assertIn("rolled back", joined)
        self.assertIn("background job", joined)

    # -- Engine dispatch -----------------------------------------------
    def test_unknown_engine_raises_instead_of_unbound_local(self):
        """Regression: the dispatch chain used to fall through with no else,
        raising UnboundLocalError instead of a diagnosable error."""
        plan = make_plan()
        object.__setattr__(plan, "db_engine", "SQLITE")  # bypass __post_init__
        with self.assertRaises(UnsupportedDatabaseEngineError):
            self.engine.generate_expand_contract_ddl(plan)

    def test_non_plan_argument_rejected(self):
        with self.assertRaises(MigrationEngineError):
            self.engine.generate_expand_contract_ddl({"table_name": "orders"})


class TestIdentifierValidation(unittest.TestCase):
    def setUp(self):
        self.engine = ZeroDowntimeMigrationEngine()

    def test_sql_injection_in_column_name_rejected(self):
        """Regression: identifiers are interpolated into DDL, so a hostile
        column name previously produced a working DROP TABLE."""
        with self.assertRaises(InvalidIdentifierError):
            make_plan(old_column="client_order_id; DROP TABLE orders; --")

    def test_sql_injection_in_table_name_rejected(self):
        with self.assertRaises(InvalidIdentifierError):
            make_plan(table_name="orders; DROP TABLE fills; --")

    def test_injection_in_column_type_rejected(self):
        with self.assertRaises(InvalidIdentifierError):
            make_plan(column_type="VARCHAR(64); DROP TABLE orders; --")

    def test_legitimate_column_types_accepted(self):
        for c_type in ("BIGINT", "VARCHAR(64)", "NUMERIC(18,8)", "TIMESTAMPTZ",
                       "DOUBLE PRECISION", "TIMESTAMP WITH TIME ZONE", "TEXT[]"):
            plan = make_plan(column_type=c_type)
            self.assertTrue(self.engine.generate_expand_contract_ddl(plan).expand_sql)

    def test_over_length_identifier_rejected(self):
        with self.assertRaises(InvalidIdentifierError):
            make_plan(new_column="c" * (MAX_IDENTIFIER_BYTES + 1))

    def test_over_length_generated_index_name_rejected(self):
        """Each identifier fits, but `idx_<table>_<column>` does not.

        PostgreSQL truncates silently at 63 bytes, so two such names could
        collide on one table.
        """
        plan = make_plan(table_name="t" * 40, new_column="c" * 30)
        with self.assertRaises(InvalidIdentifierError):
            self.engine.generate_expand_contract_ddl(plan)

    def test_post_construction_mutation_is_revalidated(self):
        """MigrationPlan is a mutable dataclass, so __post_init__ is not a
        boundary. A field reassigned after construction must still be checked
        before it reaches the DDL text."""
        plan = make_plan()
        plan.old_column = "client_order_id; DROP TABLE orders; --"
        with self.assertRaises(InvalidIdentifierError):
            self.engine.generate_expand_contract_ddl(plan)

    def test_not_null_smuggled_through_column_type_rejected(self):
        """`VARCHAR 64 NOT NULL` matches the bare-word type pattern, and would
        emit the blocking NOT NULL DDL the expand phase exists to avoid."""
        with self.assertRaises(InvalidIdentifierError) as ctx:
            make_plan(column_type="VARCHAR 64 NOT NULL")
        self.assertIn("NOT NULL", str(ctx.exception))

    def test_other_constraint_keywords_rejected_in_column_type(self):
        for c_type in ("BIGINT PRIMARY KEY", "INT UNIQUE", "BIGINT GENERATED ALWAYS",
                       "INT DEFAULT 0", "INT AUTO INCREMENT"):
            with self.assertRaises(InvalidIdentifierError):
                make_plan(column_type=c_type)

    def test_old_and_new_column_must_differ_case_insensitively(self):
        """PostgreSQL folds unquoted identifiers to lower case."""
        with self.assertRaises(InvalidIdentifierError):
            make_plan(old_column="cl_ord_id", new_column="CL_ORD_ID")

    def test_empty_plan_id_rejected(self):
        with self.assertRaises(InvalidIdentifierError):
            make_plan(plan_id="   ")

    def test_negative_record_counts_rejected(self):
        with self.assertRaises(MigrationEngineError):
            make_plan(total_records=-1)
        with self.assertRaises(MigrationEngineError):
            make_plan(backfilled_records=-1)

    def test_backfilled_cannot_exceed_total(self):
        with self.assertRaises(MigrationEngineError):
            make_plan(total_records=100, backfilled_records=101)

    def test_non_positive_lag_budget_rejected(self):
        with self.assertRaises(MigrationEngineError):
            make_plan(replica_lag_budget_seconds=0.0)
        with self.assertRaises(MigrationEngineError):
            make_plan(replica_lag_budget_seconds=float("nan"))


class TestPhaseTransitions(unittest.TestCase):
    def setUp(self):
        self.engine = ZeroDowntimeMigrationEngine()
        self.plan = make_plan()

    def test_advance_through_early_phases(self):
        st2 = self.engine.advance_migration_phase(self.plan, MigrationPhase.PHASE_2_DUAL_WRITE)
        self.assertEqual(st2.current_phase, MigrationPhase.PHASE_2_DUAL_WRITE)
        st3 = self.engine.advance_migration_phase(self.plan, MigrationPhase.PHASE_3_BACKFILL)
        self.assertEqual(st3.current_phase, MigrationPhase.PHASE_3_BACKFILL)

    def test_invalid_phase_jump_reports_the_jump(self):
        """Regression: the backfill gate used to run before the transition
        check, so a Phase 1 -> Phase 4 jump raised 'backfill incomplete' and
        the transition rule itself was never exercised."""
        with self.assertRaises(InvalidPhaseTransitionError) as ctx:
            self.engine.advance_migration_phase(self.plan, MigrationPhase.PHASE_4_READ_CUTOVER)
        self.assertIn("cannot go from PHASE_1_EXPAND", str(ctx.exception))

    def test_backwards_transition_rejected(self):
        self.plan.current_phase = MigrationPhase.PHASE_3_BACKFILL
        with self.assertRaises(InvalidPhaseTransitionError):
            self.engine.advance_migration_phase(self.plan, MigrationPhase.PHASE_2_DUAL_WRITE)

    def test_self_transition_is_idempotent(self):
        st = self.engine.advance_migration_phase(self.plan, MigrationPhase.PHASE_1_EXPAND)
        self.assertEqual(st.current_phase, MigrationPhase.PHASE_1_EXPAND)

    def test_non_phase_target_rejected(self):
        with self.assertRaises(InvalidPhaseTransitionError):
            self.engine.advance_migration_phase(self.plan, "PHASE_4_READ_CUTOVER")

    def test_phase_5_marks_completion(self):
        self.plan.current_phase = MigrationPhase.PHASE_4_READ_CUTOVER
        st = self.engine.advance_migration_phase(self.plan, MigrationPhase.PHASE_5_CONTRACT)
        self.assertTrue(st.is_completed)


class TestReadCutoverGate(unittest.TestCase):
    def setUp(self):
        self.engine = ZeroDowntimeMigrationEngine()
        self.plan = make_plan()
        self.plan.current_phase = MigrationPhase.PHASE_3_BACKFILL

    def test_prevent_cutover_when_counter_incomplete(self):
        self.plan.backfilled_records = 2000  # of 5000
        with self.assertRaises(BackfillIncompleteError):
            self.engine.advance_migration_phase(
                self.plan, MigrationPhase.PHASE_4_READ_CUTOVER, residual_null_rows=0
            )
        self.assertEqual(self.plan.current_phase, MigrationPhase.PHASE_3_BACKFILL)

    def test_counter_at_100pct_is_not_sufficient_evidence(self):
        """Regression: total_records is a plan-time snapshot. Rows written
        before dual-write finished rolling out are NULL and uncounted, so a
        100% counter must not by itself unlock the cutover."""
        self.plan.backfilled_records = self.plan.total_records
        with self.assertRaises(BackfillIncompleteError) as ctx:
            self.engine.advance_migration_phase(self.plan, MigrationPhase.PHASE_4_READ_CUTOVER)
        self.assertIn("IS NULL", str(ctx.exception))
        self.assertEqual(self.plan.current_phase, MigrationPhase.PHASE_3_BACKFILL)

    def test_residual_nulls_block_cutover_despite_full_counter(self):
        self.plan.backfilled_records = self.plan.total_records
        with self.assertRaises(BackfillIncompleteError) as ctx:
            self.engine.advance_migration_phase(
                self.plan, MigrationPhase.PHASE_4_READ_CUTOVER, residual_null_rows=17
            )
        self.assertIn("17 row(s)", str(ctx.exception))
        self.assertEqual(self.plan.current_phase, MigrationPhase.PHASE_3_BACKFILL)

    def test_zero_residual_nulls_permits_cutover(self):
        self.plan.backfilled_records = self.plan.total_records
        st = self.engine.advance_migration_phase(
            self.plan, MigrationPhase.PHASE_4_READ_CUTOVER, residual_null_rows=0
        )
        self.assertEqual(st.current_phase, MigrationPhase.PHASE_4_READ_CUTOVER)
        self.assertTrue(st.residual_nulls_verified)

    def test_opt_out_records_that_cutover_was_unverified(self):
        plan = make_plan(require_residual_null_check=False)
        plan.current_phase = MigrationPhase.PHASE_3_BACKFILL
        plan.backfilled_records = plan.total_records
        st = self.engine.advance_migration_phase(plan, MigrationPhase.PHASE_4_READ_CUTOVER)
        self.assertEqual(st.current_phase, MigrationPhase.PHASE_4_READ_CUTOVER)
        self.assertFalse(st.residual_nulls_verified)

    def test_negative_residual_count_rejected(self):
        self.plan.backfilled_records = self.plan.total_records
        with self.assertRaises(MigrationEngineError):
            self.engine.advance_migration_phase(
                self.plan, MigrationPhase.PHASE_4_READ_CUTOVER, residual_null_rows=-1
            )

    def test_boolean_residual_count_rejected(self):
        """`residual_null_rows=True` must not be read as the integer 1."""
        self.plan.backfilled_records = self.plan.total_records
        with self.assertRaises(MigrationEngineError):
            self.engine.advance_migration_phase(
                self.plan, MigrationPhase.PHASE_4_READ_CUTOVER, residual_null_rows=True
            )

    def test_empty_table_still_requires_verification(self):
        """total_records defaults to 0, which makes the counter trivially
        complete; the authoritative check is what actually gates the cutover."""
        plan = make_plan(total_records=0)
        plan.current_phase = MigrationPhase.PHASE_3_BACKFILL
        with self.assertRaises(BackfillIncompleteError):
            self.engine.advance_migration_phase(plan, MigrationPhase.PHASE_4_READ_CUTOVER)


class TestBatchedBackfill(unittest.TestCase):
    def setUp(self):
        self.engine = ZeroDowntimeMigrationEngine()
        self.plan = make_plan()
        self.plan.current_phase = MigrationPhase.PHASE_3_BACKFILL

    def test_backfill_progress_accumulates_and_caps(self):
        st = self.engine.execute_batched_backfill_step(self.plan, batch_size=2500)
        self.assertEqual(self.plan.backfilled_records, 2500)
        self.assertEqual(st.backfill_progress_pct, 50.0)
        self.assertIs(st.directive, BackfillDirective.CONTINUE)

        st2 = self.engine.execute_batched_backfill_step(self.plan, batch_size=3000)
        self.assertEqual(self.plan.backfilled_records, 5000)
        self.assertEqual(st2.backfill_progress_pct, 100.0)
        self.assertIs(st2.directive, BackfillDirective.COMPLETE)

    def test_backfill_outside_phase_3_rejected(self):
        self.plan.current_phase = MigrationPhase.PHASE_2_DUAL_WRITE
        with self.assertRaises(InvalidPhaseTransitionError):
            self.engine.execute_batched_backfill_step(self.plan)

    def test_zero_batch_size_rejected(self):
        """A zero batch would spin the caller's loop forever."""
        with self.assertRaises(MigrationEngineError):
            self.engine.execute_batched_backfill_step(self.plan, batch_size=0)

    def test_negative_batch_size_rejected(self):
        """Regression: a negative batch walked backfilled_records backwards,
        silently un-doing progress that the cutover gate reads."""
        self.plan.backfilled_records = 3000
        with self.assertRaises(MigrationEngineError):
            self.engine.execute_batched_backfill_step(self.plan, batch_size=-1000)
        self.assertEqual(self.plan.backfilled_records, 3000)

    def test_oversized_batch_warns_but_proceeds(self):
        with self.assertLogs("zero_downtime_migration_engine", level="WARNING") as logs:
            self.engine.execute_batched_backfill_step(self.plan, batch_size=50_000)
        self.assertTrue(any("max_batch_size" in line for line in logs.output))
        self.assertEqual(self.plan.backfilled_records, 5000)

    def test_replica_lag_over_budget_withholds_the_batch(self):
        st = self.engine.execute_batched_backfill_step(
            self.plan, batch_size=1000, replica_lag_seconds=2.5
        )
        self.assertIs(st.directive, BackfillDirective.THROTTLE)
        self.assertEqual(self.plan.backfilled_records, 0)

    def test_replica_lag_within_budget_applies_the_batch(self):
        st = self.engine.execute_batched_backfill_step(
            self.plan, batch_size=1000, replica_lag_seconds=0.4
        )
        self.assertIs(st.directive, BackfillDirective.CONTINUE)
        self.assertEqual(self.plan.backfilled_records, 1000)

    def test_lag_exactly_at_budget_is_not_throttled(self):
        """The documented control is lag < 1.0s budget; the boundary applies."""
        st = self.engine.execute_batched_backfill_step(
            self.plan, batch_size=1000, replica_lag_seconds=1.0
        )
        self.assertIs(st.directive, BackfillDirective.CONTINUE)
        self.assertEqual(self.plan.backfilled_records, 1000)

    def test_nan_replica_lag_rejected(self):
        """A missing lag reading must not be silently treated as zero lag."""
        with self.assertRaises(MigrationEngineError):
            self.engine.execute_batched_backfill_step(
                self.plan, batch_size=1000, replica_lag_seconds=float("nan")
            )
        self.assertEqual(self.plan.backfilled_records, 0)

    def test_negative_replica_lag_rejected(self):
        with self.assertRaises(MigrationEngineError):
            self.engine.execute_batched_backfill_step(
                self.plan, batch_size=1000, replica_lag_seconds=-0.5
            )


class TestRollback(unittest.TestCase):
    def setUp(self):
        self.engine = ZeroDowntimeMigrationEngine()
        self.plan = make_plan()

    def test_rollback_steps_back_one_phase(self):
        self.plan.current_phase = MigrationPhase.PHASE_4_READ_CUTOVER
        self.plan.backfilled_records = 5000
        st = self.engine.rollback_migration_phase(self.plan, reason="cutover latency regression")
        self.assertEqual(st.current_phase, MigrationPhase.PHASE_3_BACKFILL)
        self.assertIn("cutover latency regression", st.rationale)

    def test_rollback_preserves_backfill_progress(self):
        self.plan.current_phase = MigrationPhase.PHASE_4_READ_CUTOVER
        self.plan.backfilled_records = 5000
        self.engine.rollback_migration_phase(self.plan)
        self.assertEqual(self.plan.backfilled_records, 5000)

    def test_phase_5_rollback_refused_and_points_at_restore(self):
        """The old column's data is gone once dropped."""
        self.plan.current_phase = MigrationPhase.PHASE_5_CONTRACT
        with self.assertRaises(InvalidPhaseTransitionError) as ctx:
            self.engine.rollback_migration_phase(self.plan)
        self.assertIn("restore", str(ctx.exception).lower())

    def test_rollback_at_phase_1_refused(self):
        with self.assertRaises(InvalidPhaseTransitionError):
            self.engine.rollback_migration_phase(self.plan)


class TestEndToEndMigration(unittest.TestCase):
    """Smoke test: drive one plan through all five phases."""

    def test_full_expand_contract_sequence(self):
        engine = ZeroDowntimeMigrationEngine()
        plan = make_plan(total_records=4000)
        ddl = engine.generate_expand_contract_ddl(plan)
        self.assertIsInstance(ddl, GeneratedDDL)

        engine.advance_migration_phase(plan, MigrationPhase.PHASE_2_DUAL_WRITE)
        engine.advance_migration_phase(plan, MigrationPhase.PHASE_3_BACKFILL)

        guard = 0
        while plan.backfilled_records < plan.total_records:
            guard += 1
            self.assertLess(guard, 100, "backfill loop failed to terminate")
            status = engine.execute_batched_backfill_step(
                plan, batch_size=1000, replica_lag_seconds=0.2
            )
            self.assertIsInstance(status, MigrationStatus)
        self.assertIs(status.directive, BackfillDirective.COMPLETE)

        cutover = engine.advance_migration_phase(
            plan, MigrationPhase.PHASE_4_READ_CUTOVER, residual_null_rows=0
        )
        self.assertTrue(cutover.residual_nulls_verified)

        final = engine.advance_migration_phase(plan, MigrationPhase.PHASE_5_CONTRACT)
        self.assertTrue(final.is_completed)
        self.assertEqual(final.backfill_progress_pct, 100.0)


if __name__ == "__main__":
    unittest.main()
