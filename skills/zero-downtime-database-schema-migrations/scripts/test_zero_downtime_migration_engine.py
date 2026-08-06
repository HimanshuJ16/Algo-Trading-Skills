import unittest
from zero_downtime_migration_engine import (
    MigrationPhase,
    DatabaseEngine,
    MigrationPlan,
    GeneratedDDL,
    MigrationStatus,
    ZeroDowntimeMigrationEngine,
    MigrationEngineError,
)


class TestZeroDowntimeMigrationEngine(unittest.TestCase):
    def setUp(self):
        self.engine = ZeroDowntimeMigrationEngine()
        self.pg_plan = MigrationPlan(
            plan_id="MIG-001",
            table_name="orders",
            old_column="client_order_id",
            new_column="cl_ord_id",
            column_type="VARCHAR(64)",
            db_engine=DatabaseEngine.POSTGRESQL,
            total_records=5000,
        )

    def test_generate_postgresql_ddl(self):
        ddl = self.engine.generate_expand_contract_ddl(self.pg_plan)
        self.assertIn("ADD COLUMN cl_ord_id VARCHAR(64) DEFAULT NULL", ddl.expand_sql)
        self.assertIn("CREATE INDEX CONCURRENTLY", ddl.concurrent_index_sql)
        self.assertIn("DROP COLUMN client_order_id", ddl.contract_sql)

    def test_generate_mysql_ddl(self):
        mysql_plan = MigrationPlan(
            plan_id="MIG-002",
            table_name="fills",
            old_column="trade_id",
            new_column="fill_id",
            column_type="BIGINT",
            db_engine=DatabaseEngine.MYSQL_INNODB,
        )
        ddl = self.engine.generate_expand_contract_ddl(mysql_plan)
        self.assertIn("ALGORITHM=INPLACE, LOCK=NONE", ddl.expand_sql)
        self.assertIn("ALGORITHM=INPLACE, LOCK=NONE", ddl.contract_sql)

    def test_advance_migration_phases(self):
        # Phase 1 -> Phase 2
        st2 = self.engine.advance_migration_phase(self.pg_plan, MigrationPhase.PHASE_2_DUAL_WRITE)
        self.assertEqual(st2.current_phase, MigrationPhase.PHASE_2_DUAL_WRITE)

        # Phase 2 -> Phase 3
        st3 = self.engine.advance_migration_phase(self.pg_plan, MigrationPhase.PHASE_3_BACKFILL)
        self.assertEqual(st3.current_phase, MigrationPhase.PHASE_3_BACKFILL)

    def test_execute_batched_backfill_progress(self):
        self.pg_plan.current_phase = MigrationPhase.PHASE_3_BACKFILL
        st = self.engine.execute_batched_backfill_step(self.pg_plan, batch_size=2500)

        self.assertEqual(self.pg_plan.backfilled_records, 2500)
        self.assertEqual(st.backfill_progress_pct, 50.0)

        st2 = self.engine.execute_batched_backfill_step(self.pg_plan, batch_size=3000)
        self.assertEqual(self.pg_plan.backfilled_records, 5000)
        self.assertEqual(st2.backfill_progress_pct, 100.0)

    def test_prevent_cutover_when_backfill_incomplete(self):
        self.pg_plan.current_phase = MigrationPhase.PHASE_3_BACKFILL
        self.pg_plan.backfilled_records = 2000  # Only 2000 of 5000 backfilled

        with self.assertRaises(MigrationEngineError):
            self.engine.advance_migration_phase(self.pg_plan, MigrationPhase.PHASE_4_READ_CUTOVER)

    def test_invalid_phase_jump_raises_error(self):
        # Attempting to jump directly from Phase 1 to Phase 4
        with self.assertRaises(MigrationEngineError):
            self.engine.advance_migration_phase(self.pg_plan, MigrationPhase.PHASE_4_READ_CUTOVER)


if __name__ == "__main__":
    unittest.main()

