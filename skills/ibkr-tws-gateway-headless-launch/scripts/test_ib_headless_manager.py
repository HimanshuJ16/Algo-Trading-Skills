"""
Unit tests for ibkr-tws-gateway-headless-launch skill.

Covers:
1. Paper/live port guard in both directions, plus the unclassifiable-custom-port path.
2. Config input validation (port range, client_id, timeout, auto_restart_time format).
3. TCP socket readiness probe, including unresolvable-host escalation.
4. wait_for_gateway_ready success, retry-budget exhaustion, and no-trailing-sleep.
5. monitor_gateway_health disconnect/reconnect detection, flap suppression, and
   callback-fault isolation.
6. Docker Compose spec security invariants (loopback bind, socat relay port, read-only
   default, healthcheck binary availability, secret-based password).
"""
import socket
import unittest
from unittest import mock

from ib_headless_manager import (
    DEFAULT_IB_GATEWAY_IMAGE,
    IB_LIVE_GATEWAY_PORT,
    IB_LIVE_TWS_PORT,
    IB_PAPER_GATEWAY_PORT,
    IB_PAPER_TWS_PORT,
    SOCAT_RELAY_PORTS,
    GatewayHealthReport,
    IBGatewayConfig,
    IBGatewayError,
    IBGatewayHeadlessManager,
)


class FakeClock:
    """Deterministic monotonic clock advanced explicitly by the injected sleep."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _paper_manager(**overrides) -> IBGatewayHeadlessManager:
    kwargs = {"is_paper": True, "port": IB_PAPER_GATEWAY_PORT}
    kwargs.update(overrides)
    return IBGatewayHeadlessManager(IBGatewayConfig(**kwargs))


class TestPortModeGuard(unittest.TestCase):

    def test_valid_paper_config(self):
        mgr = _paper_manager()
        self.assertEqual(mgr.config.port, IB_PAPER_GATEWAY_PORT)

    def test_paper_mode_with_live_gateway_port_rejected(self):
        with self.assertRaises(IBGatewayError):
            IBGatewayHeadlessManager(IBGatewayConfig(is_paper=True, port=IB_LIVE_GATEWAY_PORT))

    def test_paper_mode_with_live_tws_port_rejected(self):
        with self.assertRaises(IBGatewayError):
            IBGatewayHeadlessManager(IBGatewayConfig(is_paper=True, port=IB_LIVE_TWS_PORT))

    def test_live_mode_with_paper_port_rejected(self):
        """Regression: the live->paper direction must be guarded, not just paper->live."""
        for paper_port in (IB_PAPER_GATEWAY_PORT, IB_PAPER_TWS_PORT):
            with self.subTest(port=paper_port):
                with self.assertRaises(IBGatewayError):
                    IBGatewayHeadlessManager(IBGatewayConfig(is_paper=False, port=paper_port))

    def test_live_mode_with_live_port_accepted(self):
        mgr = IBGatewayHeadlessManager(IBGatewayConfig(is_paper=False, port=IB_LIVE_GATEWAY_PORT))
        self.assertFalse(mgr.config.is_paper)

    def test_custom_port_allowed_but_warns(self):
        """A non-default port cannot be classified, so it is allowed with a warning."""
        with self.assertLogs("ib_headless_manager", level="WARNING") as captured:
            mgr = IBGatewayHeadlessManager(IBGatewayConfig(is_paper=True, port=4444))
        self.assertEqual(mgr.config.port, 4444)
        self.assertTrue(any("not an IBKR default port" in line for line in captured.output))


class TestConfigValidation(unittest.TestCase):

    def test_port_out_of_range_rejected(self):
        for bad_port in (0, -1, 65536):
            with self.subTest(port=bad_port):
                with self.assertRaises(IBGatewayError):
                    IBGatewayConfig(port=bad_port)

    def test_non_integer_port_rejected(self):
        with self.assertRaises(IBGatewayError):
            IBGatewayConfig(port="4002")

    def test_blank_host_rejected(self):
        with self.assertRaises(IBGatewayError):
            IBGatewayConfig(host="   ")

    def test_negative_client_id_rejected(self):
        with self.assertRaises(IBGatewayError):
            IBGatewayConfig(client_id=-1)

    def test_zero_client_id_allowed(self):
        self.assertEqual(IBGatewayConfig(client_id=0).client_id, 0)

    def test_non_positive_timeout_rejected(self):
        for bad in (0, -0.5):
            with self.subTest(timeout=bad):
                with self.assertRaises(IBGatewayError):
                    IBGatewayConfig(timeout_seconds=bad)

    def test_auto_restart_time_format_enforced(self):
        for bad in ("23:45", "11:45PM", "13:45 PM", "11:60 PM", ""):
            with self.subTest(value=bad):
                with self.assertRaises(IBGatewayError):
                    IBGatewayConfig(auto_restart_time=bad)
        self.assertEqual(IBGatewayConfig(auto_restart_time="11:45 PM").auto_restart_time, "11:45 PM")

    def test_read_only_api_defaults_to_protective_setting(self):
        """IBKR ships Read-Only API enabled; this config must not silently invert that."""
        self.assertTrue(IBGatewayConfig().read_only_api)

    def test_bind_address_defaults_to_loopback(self):
        self.assertEqual(IBGatewayConfig().bind_address, "127.0.0.1")


class TestSocketProbe(unittest.TestCase):

    def test_probe_closed_port_returns_false(self):
        self.assertFalse(IBGatewayHeadlessManager.probe_gateway_port("127.0.0.1", 59999, timeout=0.1))

    def test_probe_open_port_returns_true(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        _, port = server.getsockname()
        server.listen(1)
        try:
            self.assertTrue(IBGatewayHeadlessManager.probe_gateway_port("127.0.0.1", port, timeout=0.5))
        finally:
            server.close()

    def test_unresolvable_host_raises_instead_of_returning_false(self):
        """A DNS fault is permanent; folding it into 'not up yet' burns the whole budget."""
        with mock.patch("ib_headless_manager.socket.create_connection", side_effect=socket.gaierror(-2, "no name")):
            with self.assertRaises(IBGatewayError):
                IBGatewayHeadlessManager.probe_gateway_port("gateway.invalid", 4002, timeout=0.1)


class TestWaitForGatewayReady(unittest.TestCase):

    def test_returns_true_on_first_success(self):
        mgr = _paper_manager()
        with mock.patch.object(IBGatewayHeadlessManager, "probe_gateway_port", return_value=True):
            with mock.patch("ib_headless_manager.time.sleep") as slept:
                self.assertTrue(mgr.wait_for_gateway_ready(max_retries=5, retry_interval=1.0))
        slept.assert_not_called()

    def test_succeeds_after_transient_failures(self):
        mgr = _paper_manager()
        with mock.patch.object(IBGatewayHeadlessManager, "probe_gateway_port", side_effect=[False, False, True]):
            with mock.patch("ib_headless_manager.time.sleep") as slept:
                self.assertTrue(mgr.wait_for_gateway_ready(max_retries=5, retry_interval=1.0))
        self.assertEqual(slept.call_count, 2)

    def test_raises_after_budget_exhausted(self):
        mgr = _paper_manager()
        with mock.patch.object(IBGatewayHeadlessManager, "probe_gateway_port", return_value=False):
            with mock.patch("ib_headless_manager.time.sleep"):
                with self.assertRaises(IBGatewayError):
                    mgr.wait_for_gateway_ready(max_retries=3, retry_interval=0.01)

    def test_no_sleep_after_final_failed_attempt(self):
        """Regression: the old loop slept once more after the last probe before raising."""
        mgr = _paper_manager()
        with mock.patch.object(IBGatewayHeadlessManager, "probe_gateway_port", return_value=False):
            with mock.patch("ib_headless_manager.time.sleep") as slept:
                with self.assertRaises(IBGatewayError):
                    mgr.wait_for_gateway_ready(max_retries=3, retry_interval=1.0)
        self.assertEqual(slept.call_count, 2)

    def test_invalid_retry_arguments_rejected(self):
        mgr = _paper_manager()
        with self.assertRaises(IBGatewayError):
            mgr.wait_for_gateway_ready(max_retries=0)
        with self.assertRaises(IBGatewayError):
            mgr.wait_for_gateway_ready(retry_interval=-1.0)


class TestMonitorGatewayHealth(unittest.TestCase):

    def _run(self, probe_results, **kwargs):
        mgr = _paper_manager()
        clock = FakeClock()
        events = {"down": [], "up": []}
        with mock.patch.object(IBGatewayHeadlessManager, "probe_gateway_port", side_effect=probe_results):
            report = mgr.monitor_gateway_health(
                poll_interval=kwargs.pop("poll_interval", 10.0),
                max_polls=len(probe_results),
                on_disconnect=events["down"].append,
                on_reconnect=events["up"].append,
                sleep_fn=clock.sleep,
                clock_fn=clock,
                **kwargs,
            )
        return report, events

    def test_all_healthy_reports_no_events(self):
        report, events = self._run([True, True, True])
        self.assertIsInstance(report, GatewayHealthReport)
        self.assertEqual(report.polls, 3)
        self.assertEqual(report.successful_probes, 3)
        self.assertEqual(report.disconnect_events, 0)
        self.assertTrue(report.healthy_at_exit)
        self.assertEqual(events["down"], [])

    def test_restart_cycle_detected_with_measured_downtime(self):
        # 10s poll interval: down declared on poll 3, restored on poll 5 -> 20s downtime.
        report, events = self._run([True, False, False, False, True], unhealthy_threshold=2)
        self.assertEqual(report.disconnect_events, 1)
        self.assertEqual(report.reconnect_events, 1)
        self.assertEqual(events["down"], [2])
        self.assertAlmostEqual(report.total_downtime_seconds, 20.0)
        self.assertAlmostEqual(events["up"][0], 20.0)
        self.assertTrue(report.healthy_at_exit)

    def test_single_dropped_probe_does_not_declare_disconnect(self):
        """Flap suppression: one failed probe under threshold=2 must not fire on_disconnect."""
        report, events = self._run([True, False, True, True], unhealthy_threshold=2)
        self.assertEqual(report.disconnect_events, 0)
        self.assertEqual(report.failed_probes, 1)
        self.assertEqual(events["down"], [])

    def test_threshold_one_declares_immediately(self):
        report, events = self._run([True, False, True], unhealthy_threshold=1)
        self.assertEqual(report.disconnect_events, 1)
        self.assertEqual(events["down"], [1])

    def test_still_down_at_exit_accrues_downtime_and_flags_unhealthy(self):
        report, _ = self._run([False, False, False], unhealthy_threshold=1)
        self.assertFalse(report.healthy_at_exit)
        self.assertEqual(report.disconnect_events, 1)
        self.assertGreater(report.total_downtime_seconds, 0.0)

    def test_faulty_callback_does_not_kill_monitor(self):
        mgr = _paper_manager()
        clock = FakeClock()

        def explode(_value):
            raise ValueError("hook is broken")

        with mock.patch.object(IBGatewayHeadlessManager, "probe_gateway_port",
                               side_effect=[True, False, True]):
            report = mgr.monitor_gateway_health(
                poll_interval=1.0,
                unhealthy_threshold=1,
                max_polls=3,
                on_disconnect=explode,
                on_reconnect=explode,
                sleep_fn=clock.sleep,
                clock_fn=clock,
            )
        self.assertEqual(report.polls, 3)
        self.assertEqual(report.disconnect_events, 1)
        self.assertEqual(report.reconnect_events, 1)

    def test_invalid_monitor_arguments_rejected(self):
        mgr = _paper_manager()
        for overrides in ({"poll_interval": -1.0}, {"unhealthy_threshold": 0}, {"max_polls": 0}):
            with self.subTest(**overrides):
                kwargs = {"max_polls": 1}
                kwargs.update(overrides)
                with self.assertRaises(IBGatewayError):
                    mgr.monitor_gateway_health(**kwargs)


class TestDockerSpecGeneration(unittest.TestCase):

    def test_paper_spec_shape(self):
        spec = _paper_manager().generate_docker_spec()
        service = spec["services"]["ib-gateway"]
        self.assertEqual(service["environment"]["TRADING_MODE"], "paper")
        self.assertEqual(service["image"], DEFAULT_IB_GATEWAY_IMAGE)

    def test_obsolete_compose_version_key_absent(self):
        """The Compose Specification marks top-level `version` obsolete."""
        self.assertNotIn("version", _paper_manager().generate_docker_spec())

    def test_image_reference_is_the_real_published_image(self):
        """Regression: the previous spec named a nonexistent 'gnzrb' org."""
        self.assertTrue(DEFAULT_IB_GATEWAY_IMAGE.startswith("ghcr.io/gnzsnz/ib-gateway"))
        self.assertNotIn("gnzrb", _paper_manager().generate_docker_spec()["services"]["ib-gateway"]["image"])

    def test_port_publishes_on_loopback_and_maps_to_socat_relay(self):
        """Regression: '4002:4002' both bound 0.0.0.0 and targeted a container-local port."""
        service = _paper_manager().generate_docker_spec()["services"]["ib-gateway"]
        self.assertEqual(
            service["ports"],
            [f"127.0.0.1:{IB_PAPER_GATEWAY_PORT}:{SOCAT_RELAY_PORTS[IB_PAPER_GATEWAY_PORT]}"],
        )

    def test_non_loopback_bind_warns(self):
        mgr = _paper_manager(bind_address="0.0.0.0")
        with self.assertLogs("ib_headless_manager", level="WARNING") as captured:
            spec = mgr.generate_docker_spec()
        self.assertTrue(any("exposes unauthenticated order" in line for line in captured.output))
        self.assertTrue(spec["services"]["ib-gateway"]["ports"][0].startswith("0.0.0.0:"))

    def test_read_only_api_default_is_yes(self):
        env = _paper_manager().generate_docker_spec()["services"]["ib-gateway"]["environment"]
        self.assertEqual(env["READ_ONLY_API"], "yes")

    def test_disabling_read_only_on_live_warns(self):
        mgr = IBGatewayHeadlessManager(
            IBGatewayConfig(is_paper=False, port=IB_LIVE_GATEWAY_PORT, read_only_api=False)
        )
        with self.assertLogs("ib_headless_manager", level="WARNING") as captured:
            env = mgr.generate_docker_spec()["services"]["ib-gateway"]["environment"]
        self.assertEqual(env["READ_ONLY_API"], "no")
        self.assertEqual(env["TRADING_MODE"], "live")
        self.assertTrue(any("READ_ONLY_API is disabled" in line for line in captured.output))

    def test_healthcheck_does_not_depend_on_netcat(self):
        """The image ships socat and bash but not netcat; `nc -z` could never pass."""
        test_cmd = _paper_manager().generate_docker_spec()["services"]["ib-gateway"]["healthcheck"]["test"]
        self.assertEqual(test_cmd[0], "CMD-SHELL")
        self.assertNotIn("nc ", test_cmd[1])
        self.assertIn(str(SOCAT_RELAY_PORTS[IB_PAPER_GATEWAY_PORT]), test_cmd[1])

    def test_password_delivered_as_secret_by_default(self):
        spec = _paper_manager().generate_docker_spec()
        service = spec["services"]["ib-gateway"]
        self.assertEqual(service["environment"]["TWS_PASSWORD_FILE"], "/run/secrets/ibkr_password")
        self.assertNotIn("TWS_PASSWORD", service["environment"])
        self.assertIn("ibkr_password", spec["secrets"])
        self.assertEqual(service["secrets"], ["ibkr_password"])

    def test_env_password_path_when_opted_out(self):
        spec = _paper_manager(use_password_file=False).generate_docker_spec()
        service = spec["services"]["ib-gateway"]
        self.assertEqual(service["environment"]["TWS_PASSWORD"], "${IBKR_PASSWORD}")
        self.assertNotIn("secrets", spec)

    def test_restart_policy_is_unless_stopped(self):
        """`always` would resurrect a container an operator deliberately stopped."""
        self.assertEqual(
            _paper_manager().generate_docker_spec()["services"]["ib-gateway"]["restart"],
            "unless-stopped",
        )

    def test_auto_restart_time_emitted_only_when_configured(self):
        env = _paper_manager().generate_docker_spec()["services"]["ib-gateway"]["environment"]
        self.assertNotIn("AUTO_RESTART_TIME", env)

        env = _paper_manager(auto_restart_time="11:45 PM", time_zone="America/New_York") \
            .generate_docker_spec()["services"]["ib-gateway"]["environment"]
        self.assertEqual(env["AUTO_RESTART_TIME"], "11:45 PM")
        self.assertEqual(env["TIME_ZONE"], "America/New_York")

    def test_missing_auto_restart_time_warns(self):
        with self.assertLogs("ib_headless_manager", level="WARNING") as captured:
            _paper_manager().generate_docker_spec()
        self.assertTrue(any("auto_restart_time is not set" in line for line in captured.output))

    def test_non_gateway_port_rejected(self):
        mgr = IBGatewayHeadlessManager(IBGatewayConfig(is_paper=True, port=IB_PAPER_TWS_PORT))
        with self.assertRaises(IBGatewayError):
            mgr.generate_docker_spec()


if __name__ == "__main__":
    unittest.main()
