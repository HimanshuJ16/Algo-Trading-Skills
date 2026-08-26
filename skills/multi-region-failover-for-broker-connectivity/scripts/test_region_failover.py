"""
Unit tests for multi-region-failover-for-broker-connectivity.

The tests are grouped by the property they defend:

  * probing        - state transitions, and that a raising probe is a failure
  * target trust   - a never-probed or stale endpoint is not a failover target
  * fencing        - flow does not move until the outgoing path is fenced
  * failback       - cooldown, stability and flap suppression, on a monotonic clock
  * configuration  - misconfiguration fails loudly at startup, not silently at runtime
"""
import logging
import threading
import time
import unittest
from unittest import mock

from region_failover import (
    BrokerEndpoint,
    EndpointState,
    FailoverOutcome,
    RegionFailoverManager,
    SwitchKind,
)

# Keep expected warning/error logging out of the test output.
logging.getLogger("region_failover").setLevel(logging.CRITICAL)

PRIMARY_URL = "https://api-east.broker.example"
BACKUP_URL = "https://api-west.broker.example"


class _ScriptedProbe:
    """Health probe driven by a per-endpoint flag, recording call counts."""

    def __init__(self, healthy_by_name=None, raises_for=()):
        self.healthy = dict(healthy_by_name or {})
        self.raises_for = set(raises_for)
        self.calls = []

    def __call__(self, endpoint: BrokerEndpoint) -> bool:
        self.calls.append(endpoint.name)
        if endpoint.name in self.raises_for:
            raise ConnectionResetError(f"connection reset by peer: {endpoint.name}")
        return self.healthy.get(endpoint.name, True)


def _build(probe, **kwargs):
    """Two-endpoint manager with fencing off unless a test asks for it."""
    kwargs.setdefault("require_fence", False)
    mgr = RegionFailoverManager(health_check_fn=probe, **kwargs)
    mgr.register_endpoint("primary", "us-east-1", PRIMARY_URL, is_primary=True)
    mgr.register_endpoint("backup", "us-west-2", BACKUP_URL)
    mgr.validate_configuration()
    return mgr


def _drive_down(mgr, name):
    """Probe an endpoint until it reaches DOWN."""
    for _ in range(mgr.failure_threshold):
        mgr.probe_health(name)


class TestProbing(unittest.TestCase):

    def test_fresh_endpoint_is_unknown_not_healthy(self):
        """A registered endpoint has not been shown to work."""
        mgr = _build(_ScriptedProbe())
        self.assertEqual(mgr.endpoints["backup"].state, EndpointState.UNKNOWN)
        self.assertIsNone(mgr.health_age_seconds("backup"))
        self.assertFalse(mgr.is_health_fresh("backup"))

    def test_degraded_then_down_at_threshold(self):
        """DOWN is reached exactly at failure_threshold, not before."""
        mgr = _build(_ScriptedProbe({"primary": False}), failure_threshold=3)
        self.assertEqual(mgr.probe_health("primary"), EndpointState.DEGRADED)
        self.assertEqual(mgr.probe_health("primary"), EndpointState.DEGRADED)
        self.assertEqual(mgr.probe_health("primary"), EndpointState.DOWN)
        self.assertEqual(mgr.endpoints["primary"].consecutive_failures, 3)

    def test_success_resets_failure_counter(self):
        probe = _ScriptedProbe({"primary": False})
        mgr = _build(probe, failure_threshold=3)
        mgr.probe_health("primary")
        mgr.probe_health("primary")
        probe.healthy["primary"] = True
        self.assertEqual(mgr.probe_health("primary"), EndpointState.HEALTHY)
        self.assertEqual(mgr.endpoints["primary"].consecutive_failures, 0)
        self.assertEqual(mgr.endpoints["primary"].consecutive_successes, 1)

    def test_raising_probe_counts_as_failure(self):
        """Regression: connection errors arrive as exceptions, not False.

        An exception escaping probe_health leaves consecutive_failures untouched,
        so the endpoint dies without ever being marked DOWN and failover never
        fires. This is the common real-world failure, not the rare one.
        """
        mgr = _build(_ScriptedProbe(raises_for=["primary"]), failure_threshold=2)
        self.assertEqual(mgr.probe_health("primary"), EndpointState.DEGRADED)
        self.assertEqual(mgr.probe_health("primary"), EndpointState.DOWN)
        self.assertIn("ConnectionResetError", mgr.endpoints["primary"].last_probe_error)

    def test_probe_error_cleared_on_recovery(self):
        probe = _ScriptedProbe(raises_for=["primary"])
        mgr = _build(probe, failure_threshold=2)
        mgr.probe_health("primary")
        probe.raises_for.clear()
        mgr.probe_health("primary")
        self.assertIsNone(mgr.endpoints["primary"].last_probe_error)

    def test_keyboard_interrupt_still_propagates(self):
        """Only Exception is absorbed; operator interrupts must not be eaten."""
        def probe(_endpoint):
            raise KeyboardInterrupt()

        mgr = _build(probe)
        with self.assertRaises(KeyboardInterrupt):
            mgr.probe_health("primary")

    def test_truthy_non_bool_probe_result_is_accepted(self):
        mgr = _build(lambda ep: 1)
        self.assertEqual(mgr.probe_health("primary"), EndpointState.HEALTHY)

    def test_probe_unknown_endpoint_raises(self):
        mgr = _build(_ScriptedProbe())
        with self.assertRaises(ValueError):
            mgr.probe_health("does-not-exist")
        with self.assertRaises(ValueError):
            mgr.health_age_seconds("does-not-exist")


class TestFailoverTargetTrust(unittest.TestCase):

    def test_never_probed_backup_is_not_a_failover_target(self):
        """Regression: the dangerous default.

        With endpoints defaulting to HEALTHY and eligibility meaning 'state is
        HEALTHY', a backup nobody ever probed is a valid target and the system
        fails over blind onto an endpoint it has never reached.
        """
        mgr = _build(_ScriptedProbe({"primary": False}), failure_threshold=2)
        _drive_down(mgr, "primary")

        self.assertEqual(mgr.eligible_targets(), [])
        decision = mgr.evaluate_failover()
        self.assertEqual(decision.outcome, FailoverOutcome.NO_TARGET_AVAILABLE)
        self.assertTrue(decision.requires_trading_halt)
        self.assertFalse(decision.switched)
        self.assertEqual(mgr.active_endpoint, "primary")

    def test_stale_backup_health_is_not_a_failover_target(self):
        """A probe result old enough to be untrustworthy does not qualify."""
        mgr = _build(
            _ScriptedProbe({"primary": False}),
            failure_threshold=2,
            max_health_age_seconds=30.0,
        )
        mgr.probe_health("backup")
        _drive_down(mgr, "primary")
        # Age the backup's probe past the freshness limit.
        mgr.endpoints["backup"].last_check_monotonic = time.monotonic() - 31.0

        self.assertFalse(mgr.is_health_fresh("backup"))
        self.assertEqual(
            mgr.evaluate_failover().outcome, FailoverOutcome.NO_TARGET_AVAILABLE
        )

    def test_failover_to_freshly_probed_backup(self):
        mgr = _build(_ScriptedProbe({"primary": False}), failure_threshold=3)
        _drive_down(mgr, "primary")
        mgr.probe_health("backup")

        decision = mgr.evaluate_failover()
        self.assertEqual(decision.outcome, FailoverOutcome.SWITCHED)
        self.assertTrue(decision.switched)
        self.assertFalse(decision.requires_trading_halt)
        self.assertEqual(mgr.active_endpoint, "backup")
        self.assertEqual(mgr.get_active_endpoint().region, "us-west-2")

        event = decision.event
        self.assertEqual(event.from_endpoint, "primary")
        self.assertEqual(event.to_endpoint, "backup")
        self.assertEqual(event.kind, SwitchKind.FAILOVER)
        self.assertEqual(event.from_state, EndpointState.DOWN)
        self.assertEqual(len(mgr.failover_history), 1)

    def test_no_failover_while_active_is_healthy_or_degraded(self):
        probe = _ScriptedProbe()
        mgr = _build(probe, failure_threshold=3)
        mgr.probe_health("primary")
        mgr.probe_health("backup")
        decision = mgr.evaluate_failover()
        self.assertEqual(decision.outcome, FailoverOutcome.NO_ACTION)
        self.assertFalse(decision.requires_trading_halt)

        probe.healthy["primary"] = False
        mgr.probe_health("primary")  # DEGRADED, one failure short of DOWN
        self.assertEqual(mgr.endpoints["primary"].state, EndpointState.DEGRADED)
        self.assertEqual(mgr.evaluate_failover().outcome, FailoverOutcome.NO_ACTION)
        self.assertEqual(mgr.active_endpoint, "primary")

    def test_targets_ordered_by_priority_then_name(self):
        """Failover order is explicit, not dictionary insertion order."""
        probe = _ScriptedProbe({"primary": False})
        mgr = RegionFailoverManager(
            health_check_fn=probe, failure_threshold=2, require_fence=False,
        )
        mgr.register_endpoint("primary", "us-east-1", PRIMARY_URL, is_primary=True)
        mgr.register_endpoint("far", "ap-south-1", "https://ap.example", priority=50)
        mgr.register_endpoint("near", "us-west-2", BACKUP_URL, priority=10)
        mgr.validate_configuration()

        _drive_down(mgr, "primary")
        mgr.probe_health("far")
        mgr.probe_health("near")

        self.assertEqual([ep.name for ep in mgr.eligible_targets()], ["near", "far"])
        self.assertEqual(mgr.evaluate_failover().active_endpoint, "near")

    def test_failover_back_to_primary_when_backup_dies(self):
        """A dead backup with a healthy primary is a failover, not a failback."""
        probe = _ScriptedProbe({"primary": False})
        mgr = _build(probe, failure_threshold=2, cooldown_seconds=3600.0)
        _drive_down(mgr, "primary")
        mgr.probe_health("backup")
        mgr.evaluate_failover()
        self.assertEqual(mgr.active_endpoint, "backup")

        probe.healthy["primary"] = True
        probe.healthy["backup"] = False
        mgr.probe_health("primary")
        _drive_down(mgr, "backup")

        # Cooldown is 1 hour, but failover is involuntary and must not be gated.
        decision = mgr.evaluate_failover()
        self.assertEqual(decision.outcome, FailoverOutcome.SWITCHED)
        self.assertEqual(mgr.active_endpoint, "primary")
        self.assertEqual(decision.event.kind, SwitchKind.FAILOVER)


class TestFencing(unittest.TestCase):

    def test_failover_blocked_until_fence_confirmed(self):
        mgr = RegionFailoverManager(
            health_check_fn=_ScriptedProbe({"primary": False}),
            failure_threshold=2,
            require_fence=True,
        )
        mgr.register_endpoint("primary", "us-east-1", PRIMARY_URL, is_primary=True)
        mgr.register_endpoint("backup", "us-west-2", BACKUP_URL)
        _drive_down(mgr, "primary")
        mgr.probe_health("backup")

        blocked = mgr.evaluate_failover()
        self.assertEqual(blocked.outcome, FailoverOutcome.FENCE_REQUIRED)
        self.assertFalse(blocked.switched)
        self.assertFalse(blocked.requires_trading_halt)
        self.assertEqual(mgr.active_endpoint, "primary")
        self.assertEqual(mgr.failover_history, [])

        allowed = mgr.evaluate_failover(fence_confirmed=True)
        self.assertEqual(allowed.outcome, FailoverOutcome.SWITCHED)
        self.assertEqual(mgr.active_endpoint, "backup")

    def test_no_target_outranks_fence_requirement(self):
        """Do not ask an operator to fence when there is nowhere to go."""
        mgr = RegionFailoverManager(
            health_check_fn=_ScriptedProbe({"primary": False}),
            failure_threshold=2,
            require_fence=True,
        )
        mgr.register_endpoint("primary", "us-east-1", PRIMARY_URL, is_primary=True)
        mgr.register_endpoint("backup", "us-west-2", BACKUP_URL)
        _drive_down(mgr, "primary")  # backup never probed

        decision = mgr.evaluate_failover()
        self.assertEqual(decision.outcome, FailoverOutcome.NO_TARGET_AVAILABLE)
        self.assertTrue(decision.requires_trading_halt)


class TestFailback(unittest.TestCase):

    def _failed_over(self, probe, **kwargs):
        kwargs.setdefault("failure_threshold", 2)
        mgr = _build(probe, **kwargs)
        _drive_down(mgr, "primary")
        mgr.probe_health("backup")
        mgr.evaluate_failover()
        assert mgr.active_endpoint == "backup"
        return mgr

    def test_no_failback_while_primary_unrecovered(self):
        probe = _ScriptedProbe({"primary": False})
        mgr = self._failed_over(probe, cooldown_seconds=0.0)
        mgr.probe_health("primary")
        decision = mgr.evaluate_failback()
        self.assertEqual(decision.outcome, FailoverOutcome.NO_ACTION)
        self.assertEqual(mgr.active_endpoint, "backup")

    def test_cooldown_blocks_failback(self):
        probe = _ScriptedProbe({"primary": False})
        mgr = self._failed_over(
            probe, cooldown_seconds=600.0, failback_success_threshold=1,
        )
        probe.healthy["primary"] = True
        mgr.probe_health("primary")

        decision = mgr.evaluate_failback()
        self.assertEqual(decision.outcome, FailoverOutcome.COOLDOWN_ACTIVE)
        self.assertEqual(mgr.active_endpoint, "backup")

    def test_cooldown_survives_a_wall_clock_step(self):
        """Regression: cooldown must be measured on the monotonic clock.

        An NTP correction that steps time.time() forward past the cooldown would
        release the failback gate immediately if the interval were measured on
        the wall clock. time.monotonic() is unaffected by such a step.
        """
        probe = _ScriptedProbe({"primary": False})
        mgr = self._failed_over(
            probe, cooldown_seconds=600.0, failback_success_threshold=1,
        )
        probe.healthy["primary"] = True
        mgr.probe_health("primary")

        stepped = time.time() + 86_400.0
        with mock.patch("region_failover.time.time", return_value=stepped):
            decision = mgr.evaluate_failback()
        self.assertEqual(decision.outcome, FailoverOutcome.COOLDOWN_ACTIVE)
        self.assertEqual(mgr.active_endpoint, "backup")

    def test_stability_threshold_blocks_failback(self):
        """One successful probe after cooldown is not evidence of recovery."""
        probe = _ScriptedProbe({"primary": False})
        mgr = self._failed_over(
            probe, cooldown_seconds=0.0, failback_success_threshold=3,
        )
        probe.healthy["primary"] = True

        mgr.probe_health("primary")
        self.assertEqual(mgr.evaluate_failback().outcome, FailoverOutcome.NOT_STABLE_YET)
        mgr.probe_health("primary")
        self.assertEqual(mgr.evaluate_failback().outcome, FailoverOutcome.NOT_STABLE_YET)
        mgr.probe_health("primary")

        decision = mgr.evaluate_failback()
        self.assertEqual(decision.outcome, FailoverOutcome.SWITCHED)
        self.assertEqual(mgr.active_endpoint, "primary")
        self.assertEqual(decision.event.kind, SwitchKind.FAILBACK)

    def test_a_flapping_primary_never_accumulates_stability(self):
        """Success, failure, success is not three consecutive successes."""
        probe = _ScriptedProbe({"primary": False})
        mgr = self._failed_over(
            probe, cooldown_seconds=0.0, failback_success_threshold=3,
        )
        for _ in range(5):
            probe.healthy["primary"] = True
            mgr.probe_health("primary")
            mgr.probe_health("primary")
            probe.healthy["primary"] = False
            mgr.probe_health("primary")
            self.assertNotEqual(
                mgr.evaluate_failback().outcome, FailoverOutcome.SWITCHED
            )
        self.assertEqual(mgr.active_endpoint, "backup")

    def test_failback_rate_limit_suppresses_a_flapping_primary(self):
        probe = _ScriptedProbe({"primary": False})
        mgr = _build(
            probe,
            failure_threshold=2,
            cooldown_seconds=0.0,
            failback_success_threshold=1,
            max_failbacks_per_window=2,
            failback_window_seconds=3600.0,
        )

        def cycle():
            probe.healthy["primary"] = False
            _drive_down(mgr, "primary")
            mgr.probe_health("backup")
            mgr.evaluate_failover()
            probe.healthy["primary"] = True
            mgr.probe_health("primary")
            return mgr.evaluate_failback()

        self.assertEqual(cycle().outcome, FailoverOutcome.SWITCHED)
        self.assertEqual(cycle().outcome, FailoverOutcome.SWITCHED)

        suppressed = cycle()
        self.assertEqual(suppressed.outcome, FailoverOutcome.FLAP_SUPPRESSED)
        self.assertEqual(mgr.active_endpoint, "backup")
        self.assertFalse(suppressed.requires_trading_halt)

    def test_failback_requires_fence_confirmation(self):
        probe = _ScriptedProbe({"primary": False})
        mgr = RegionFailoverManager(
            health_check_fn=probe,
            failure_threshold=2,
            cooldown_seconds=0.0,
            failback_success_threshold=1,
            require_fence=True,
        )
        mgr.register_endpoint("primary", "us-east-1", PRIMARY_URL, is_primary=True)
        mgr.register_endpoint("backup", "us-west-2", BACKUP_URL)
        _drive_down(mgr, "primary")
        mgr.probe_health("backup")
        mgr.evaluate_failover(fence_confirmed=True)

        probe.healthy["primary"] = True
        mgr.probe_health("primary")
        self.assertEqual(
            mgr.evaluate_failback().outcome, FailoverOutcome.FENCE_REQUIRED
        )
        self.assertEqual(mgr.active_endpoint, "backup")
        self.assertEqual(
            mgr.evaluate_failback(fence_confirmed=True).outcome,
            FailoverOutcome.SWITCHED,
        )

    def test_failback_is_a_no_op_on_the_primary(self):
        mgr = _build(_ScriptedProbe())
        mgr.probe_health("primary")
        decision = mgr.evaluate_failback()
        self.assertEqual(decision.outcome, FailoverOutcome.NO_ACTION)
        self.assertEqual(mgr.failover_history, [])

    def test_stale_primary_health_blocks_failback(self):
        probe = _ScriptedProbe({"primary": False})
        mgr = self._failed_over(
            probe,
            cooldown_seconds=0.0,
            failback_success_threshold=1,
            max_health_age_seconds=30.0,
        )
        probe.healthy["primary"] = True
        mgr.probe_health("primary")
        mgr.endpoints["primary"].last_check_monotonic = time.monotonic() - 31.0

        self.assertEqual(mgr.evaluate_failback().outcome, FailoverOutcome.NO_ACTION)
        self.assertEqual(mgr.active_endpoint, "backup")


class TestStalenessReporting(unittest.TestCase):

    def test_stalled_probe_loop_is_reported_not_acted_on(self):
        """A frozen HEALTHY state must be surfaced, but is not proof of an outage."""
        mgr = _build(_ScriptedProbe(), max_health_age_seconds=30.0)
        mgr.probe_health("primary")
        mgr.endpoints["primary"].last_check_monotonic = time.monotonic() - 120.0

        decision = mgr.evaluate_failover()
        self.assertEqual(decision.outcome, FailoverOutcome.NO_ACTION)
        self.assertEqual(len(decision.notes), 1)
        self.assertIn("probe loop may have stalled", decision.notes[0])

    def test_never_probed_active_endpoint_is_reported(self):
        mgr = _build(_ScriptedProbe())
        decision = mgr.evaluate_failover()
        self.assertEqual(decision.outcome, FailoverOutcome.NO_ACTION)
        self.assertIn("never been probed", decision.notes[0])


class TestConfiguration(unittest.TestCase):

    def test_health_check_fn_is_required(self):
        with self.assertRaises(TypeError):
            RegionFailoverManager()  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            RegionFailoverManager(health_check_fn=None)  # type: ignore[arg-type]

    def test_constructor_parameter_validation(self):
        probe = _ScriptedProbe()
        for kwargs in (
            {"failure_threshold": 0},
            {"failure_threshold": -1},
            {"cooldown_seconds": -1.0},
            {"max_health_age_seconds": 0.0},
            {"failback_success_threshold": 0},
            {"max_failbacks_per_window": 0},
            {"failback_window_seconds": 0.0},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    RegionFailoverManager(health_check_fn=probe, **kwargs)

    def test_registration_rejects_blank_fields(self):
        mgr = RegionFailoverManager(health_check_fn=_ScriptedProbe())
        for args in (
            ("", "us-east-1", PRIMARY_URL),
            ("primary", "  ", PRIMARY_URL),
            ("primary", "us-east-1", ""),
        ):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    mgr.register_endpoint(*args)

    def test_duplicate_registration_rejected(self):
        """Re-registering would silently discard live health state."""
        mgr = RegionFailoverManager(health_check_fn=_ScriptedProbe())
        mgr.register_endpoint("primary", "us-east-1", PRIMARY_URL, is_primary=True)
        with self.assertRaises(ValueError):
            mgr.register_endpoint("primary", "us-east-2", BACKUP_URL)

    def test_second_primary_rejected(self):
        mgr = RegionFailoverManager(health_check_fn=_ScriptedProbe())
        mgr.register_endpoint("primary", "us-east-1", PRIMARY_URL, is_primary=True)
        with self.assertRaises(ValueError):
            mgr.register_endpoint("other", "us-west-2", BACKUP_URL, is_primary=True)

    def test_default_priority_prefers_the_primary(self):
        mgr = _build(_ScriptedProbe())
        self.assertEqual(mgr.endpoints["primary"].priority, 0)
        self.assertEqual(mgr.endpoints["backup"].priority, 100)

    def test_validate_configuration_requires_a_primary(self):
        mgr = RegionFailoverManager(health_check_fn=_ScriptedProbe())
        mgr.register_endpoint("backup-a", "us-west-2", BACKUP_URL)
        mgr.register_endpoint("backup-b", "eu-west-1", "https://eu.example")
        with self.assertRaises(ValueError):
            mgr.validate_configuration()

    def test_validate_configuration_requires_a_second_endpoint(self):
        mgr = RegionFailoverManager(health_check_fn=_ScriptedProbe())
        mgr.register_endpoint("primary", "us-east-1", PRIMARY_URL, is_primary=True)
        with self.assertRaises(ValueError):
            mgr.validate_configuration()

    def test_evaluations_raise_without_an_active_endpoint(self):
        """A backup-only configuration must not evaluate to a silent no-op."""
        mgr = RegionFailoverManager(health_check_fn=_ScriptedProbe())
        mgr.register_endpoint("backup", "us-west-2", BACKUP_URL)
        self.assertIsNone(mgr.get_active_endpoint())
        with self.assertRaises(RuntimeError):
            mgr.evaluate_failover()
        with self.assertRaises(RuntimeError):
            mgr.evaluate_failback()


class TestConcurrency(unittest.TestCase):

    def test_concurrent_probes_and_reads_keep_state_consistent(self):
        """The probe loop and the order path touch this object from two threads."""
        probe = _ScriptedProbe({"primary": False})
        mgr = _build(probe, failure_threshold=200)
        errors = []

        def prober():
            try:
                for _ in range(200):
                    mgr.probe_health("primary")
            except Exception as exc:  # surfaced below, not swallowed
                errors.append(exc)

        def reader():
            try:
                for _ in range(200):
                    mgr.get_active_endpoint()
                    mgr.eligible_targets()
                    mgr.evaluate_failover()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=prober), threading.Thread(target=reader)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(mgr.endpoints["primary"].consecutive_failures, 200)
        self.assertEqual(mgr.active_endpoint, "primary")


if __name__ == "__main__":
    unittest.main()
