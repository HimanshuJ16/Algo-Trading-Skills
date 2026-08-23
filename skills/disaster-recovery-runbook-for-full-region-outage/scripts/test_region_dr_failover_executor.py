import logging
import unittest

from region_dr_failover_executor import (
    DEFAULT_STEP_LATENCIES,
    OUTCOME_ABORTED,
    OUTCOME_DEGRADED_TRADING_HALTED,
    OUTCOME_FAILED,
    OUTCOME_SUCCESSFUL,
    OUTCOME_SUCCESSFUL_WITH_DATA_LOSS,
    STEP_CANCEL_OPEN_ORDERS,
    STEP_COMPUTE_BOOTSTRAP_RECONCILE,
    STEP_DNS_SWITCHOVER,
    STEP_PROMOTE_SECONDARY_DB,
    STEP_RESUME_TRADING,
    DrFailoverError,
    RegionDrFailoverExecutorEngine,
)

# The engine logs at CRITICAL by design; keep drill runs from polluting output.
logging.getLogger("region_dr_failover_executor").disabled = True


class TestRegionDrFailoverExecutorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = RegionDrFailoverExecutorEngine(
            primary_region="us-east-1",
            secondary_region="us-west-2",
            rto_sla_sec=300.0,
            max_rpo_sec=15.0,
        )
        self.latencies = {
            "OUTAGE_VERIFICATION": 5.0,
            "CANCEL_OPEN_ORDERS": 10.0,
            "PROMOTE_SECONDARY_DB": 45.0,
            "DNS_SWITCHOVER": 15.0,
            "COMPUTE_BOOTSTRAP_RECONCILE": 30.0,
            "RESUME_TRADING": 5.0,
        }

    def _failover(self, **overrides):
        kwargs = dict(
            replication_lag_sec=1.5,
            simulated_step_latencies=self.latencies,
            primary_write_fenced=True,
            cancel_all_confirmed=True,
            dns_ttl_sec=60.0,
        )
        kwargs.update(overrides)
        return self.engine.execute_region_failover(**kwargs)

    def _step(self, report, name):
        return next(s for s in report.executed_steps if s.step_name == name)

    # --- the clean path ------------------------------------------------------

    def test_full_region_dr_failover_execution_success(self):
        # 110s of steps + 60s DNS TTL = 170s, inside the 300s objective.
        report = self._failover()

        self.assertEqual(report.outcome, OUTCOME_SUCCESSFUL)
        self.assertTrue(report.is_failover_successful)
        self.assertTrue(report.is_rto_compliant)
        self.assertTrue(report.is_rpo_compliant)
        self.assertTrue(report.is_trading_resumed)
        self.assertEqual(report.total_elapsed_seconds, 170.0)
        self.assertEqual(len(report.executed_steps), 6)
        self.assertTrue(all(s.is_success for s in report.executed_steps))

    def test_mandated_step_order_is_cancel_then_promote_then_dns(self):
        names = [s.step_name for s in self._failover().executed_steps]
        self.assertLess(names.index(STEP_CANCEL_OPEN_ORDERS), names.index(STEP_PROMOTE_SECONDARY_DB))
        self.assertLess(names.index(STEP_PROMOTE_SECONDARY_DB), names.index(STEP_DNS_SWITCHOVER))
        self.assertLess(names.index(STEP_DNS_SWITCHOVER), names.index(STEP_RESUME_TRADING))
        self.assertEqual([s.step_number for s in self._failover().executed_steps], [1, 2, 3, 4, 5, 6])

    # --- split-brain interlock ----------------------------------------------

    def test_promotion_blocked_when_primary_writes_not_fenced(self):
        # Regression: the previous engine promoted unconditionally and returned
        # success. Write fencing is best-effort, so promotion needs evidence.
        report = self._failover(primary_write_fenced=False)

        promote = self._step(report, STEP_PROMOTE_SECONDARY_DB)
        self.assertTrue(promote.is_blocked)
        self.assertFalse(promote.is_success)
        self.assertEqual(promote.elapsed_seconds, 0.0)
        self.assertEqual(report.outcome, OUTCOME_ABORTED)
        self.assertFalse(report.is_failover_successful)
        self.assertTrue(any("split-brain" in f for f in report.findings))

    def test_steps_after_a_blocked_interlock_do_not_run(self):
        report = self._failover(primary_write_fenced=False)

        for name in (STEP_DNS_SWITCHOVER, STEP_COMPUTE_BOOTSTRAP_RECONCILE, STEP_RESUME_TRADING):
            self.assertTrue(self._step(report, name).is_blocked)
        # Only the two steps before the interlock contributed to elapsed time.
        self.assertEqual(report.total_elapsed_seconds, 15.0)
        self.assertEqual(report.dns_ttl_seconds, 0.0)

    # --- open-order interlock ------------------------------------------------

    def test_trading_not_resumed_when_cancellation_unconfirmed(self):
        # Secondary comes up and reconciles, but the book is not confirmed flat.
        report = self._failover(cancel_all_confirmed=False)

        self.assertTrue(self._step(report, STEP_COMPUTE_BOOTSTRAP_RECONCILE).is_success)
        self.assertTrue(self._step(report, STEP_RESUME_TRADING).is_blocked)
        self.assertEqual(report.outcome, OUTCOME_DEGRADED_TRADING_HALTED)
        self.assertFalse(report.is_trading_resumed)
        self.assertFalse(report.is_failover_successful)
        self.assertTrue(any("GTC/GTD" in f for f in report.findings))

    def test_dispatching_cancel_all_is_not_the_same_as_confirming_it(self):
        # The CANCEL_OPEN_ORDERS step succeeding means "dispatched", which alone
        # must not unlock trading.
        report = self._failover(cancel_all_confirmed=False)
        self.assertTrue(self._step(report, STEP_CANCEL_OPEN_ORDERS).is_success)
        self.assertFalse(report.is_trading_resumed)

    # --- RPO -----------------------------------------------------------------

    def test_over_rpo_promotion_blocked_without_explicit_data_loss_acceptance(self):
        # Regression: 900s of replication lag previously reported SUCCESS.
        report = self._failover(replication_lag_sec=900.0)

        self.assertFalse(report.is_rpo_compliant)
        self.assertTrue(self._step(report, STEP_PROMOTE_SECONDARY_DB).is_blocked)
        self.assertEqual(report.outcome, OUTCOME_ABORTED)
        self.assertFalse(report.is_failover_successful)

    def test_over_rpo_promotion_allowed_when_data_loss_explicitly_accepted(self):
        report = self._failover(replication_lag_sec=900.0, accept_data_loss=True)

        self.assertEqual(report.outcome, OUTCOME_SUCCESSFUL_WITH_DATA_LOSS)
        self.assertTrue(report.is_failover_successful)   # a deliberate decision
        self.assertFalse(report.is_rpo_compliant)        # but never silently compliant
        self.assertTrue(any("RPO BREACH" in f for f in report.findings))

    def test_rpo_boundary_exactly_at_objective_is_compliant(self):
        report = self._failover(replication_lag_sec=15.0)
        self.assertTrue(report.is_rpo_compliant)
        self.assertEqual(report.outcome, OUTCOME_SUCCESSFUL)

    def test_rpo_just_over_objective_is_not_compliant(self):
        report = self._failover(replication_lag_sec=15.01, accept_data_loss=True)
        self.assertFalse(report.is_rpo_compliant)

    # --- RTO accounting ------------------------------------------------------

    def test_dns_ttl_counts_toward_rto(self):
        short = self._failover(dns_ttl_sec=60.0)
        long = self._failover(dns_ttl_sec=120.0)

        self.assertEqual(long.total_elapsed_seconds - short.total_elapsed_seconds, 60.0)
        self.assertEqual(long.dns_ttl_seconds, 120.0)

    def test_ttl_alone_can_breach_the_rto_objective(self):
        # 110s of steps is well inside 300s; a 300s TTL is not.
        report = self._failover(dns_ttl_sec=300.0)

        self.assertEqual(report.total_elapsed_seconds, 410.0)
        self.assertFalse(report.is_rto_compliant)
        self.assertEqual(report.outcome, OUTCOME_FAILED)
        # A missed objective is not a halted desk: every step ran and trading
        # did resume. Readers must use is_trading_resumed for that question
        # rather than inferring "halted" from the FAILED outcome.
        self.assertTrue(report.is_trading_resumed)
        self.assertTrue(all(s.is_success for s in report.executed_steps))
        self.assertTrue(any("RTO BREACH" in f for f in report.findings))
        self.assertTrue(any("unreachable regardless of step speed" in f for f in report.findings))

    def test_rto_boundary_exactly_at_objective_is_compliant(self):
        # 110s of steps + 190s TTL = exactly 300s.
        report = self._failover(dns_ttl_sec=190.0)
        self.assertEqual(report.total_elapsed_seconds, 300.0)
        self.assertTrue(report.is_rto_compliant)

    def test_pre_existing_connection_warning_always_accompanies_dns_switchover(self):
        self.assertTrue(any("pre-existing" in f for f in self._failover().findings))

    # --- reported step failures ---------------------------------------------

    def test_failed_step_marks_failure_and_blocks_downstream(self):
        report = self._failover(failed_steps=[STEP_DNS_SWITCHOVER])

        dns = self._step(report, STEP_DNS_SWITCHOVER)
        self.assertFalse(dns.is_success)
        self.assertFalse(dns.is_blocked)          # attempted and failed, not skipped
        self.assertEqual(dns.elapsed_seconds, 15.0)
        self.assertTrue(self._step(report, STEP_RESUME_TRADING).is_blocked)
        self.assertEqual(report.outcome, OUTCOME_FAILED)
        self.assertEqual(report.dns_ttl_seconds, 0.0)  # no TTL for a switchover that failed

    def test_failed_cancel_dispatch_blocks_promotion(self):
        report = self._failover(failed_steps=[STEP_CANCEL_OPEN_ORDERS])

        self.assertTrue(self._step(report, STEP_PROMOTE_SECONDARY_DB).is_blocked)
        self.assertEqual(report.outcome, OUTCOME_FAILED)

    # --- configuration and input validation ---------------------------------

    def test_failing_over_into_the_same_region_raises(self):
        with self.assertRaises(DrFailoverError):
            RegionDrFailoverExecutorEngine(primary_region="us-east-1", secondary_region="us-east-1")

    def test_empty_region_raises(self):
        with self.assertRaises(DrFailoverError):
            RegionDrFailoverExecutorEngine(primary_region="", secondary_region="us-west-2")

    def test_non_positive_rto_sla_raises(self):
        with self.assertRaises(DrFailoverError):
            RegionDrFailoverExecutorEngine(rto_sla_sec=0.0)

    def test_unknown_step_name_in_latencies_raises(self):
        with self.assertRaises(DrFailoverError):
            self._failover(simulated_step_latencies={"DNS": 15.0})

    def test_unknown_step_name_in_failed_steps_raises(self):
        with self.assertRaises(DrFailoverError):
            self._failover(failed_steps=["PROMOTE_DB"])

    def test_negative_latency_raises(self):
        with self.assertRaises(DrFailoverError):
            self._failover(simulated_step_latencies={STEP_DNS_SWITCHOVER: -1.0})

    def test_negative_or_nan_replication_lag_raises(self):
        with self.assertRaises(DrFailoverError):
            self._failover(replication_lag_sec=-1.0)
        with self.assertRaises(DrFailoverError):
            self._failover(replication_lag_sec=float("nan"))

    def test_negative_dns_ttl_raises(self):
        with self.assertRaises(DrFailoverError):
            self._failover(dns_ttl_sec=-1.0)

    def test_defaults_are_fail_closed(self):
        # No evidence supplied: the run must stop at the promotion interlock
        # rather than assume a clean failover.
        report = self.engine.execute_region_failover()

        self.assertEqual(report.outcome, OUTCOME_ABORTED)
        self.assertFalse(report.is_failover_successful)
        self.assertFalse(report.is_trading_resumed)

    def test_default_latency_table_covers_every_step(self):
        report = self._failover(simulated_step_latencies=None)
        self.assertEqual(len(DEFAULT_STEP_LATENCIES), 6)
        self.assertEqual(report.total_elapsed_seconds, sum(DEFAULT_STEP_LATENCIES.values()) + 60.0)


if __name__ == '__main__':
    unittest.main()
