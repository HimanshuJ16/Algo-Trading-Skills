"""
Unit tests for broker-status-page-monitoring-integration.

The payload fixtures mirror the shape of a real Atlassian Statuspage v2
``summary.json`` as observed on 2026-08-20 against
``https://status.alpaca.markets/api/v2/summary.json``: ``page``/``components``/
``incidents``/``scheduled_maintenances``/``status`` at the top level, and component
objects carrying ``name``, ``status``, ``group``, ``group_id`` and
``only_show_if_degraded``.

Expected verdicts are derived from the decision table in
``diagnose_execution_failure``'s docstring, not from re-running the implementation's
own branching. The clock is frozen and injected so freshness assertions are exact.
"""
import logging
import threading
import unittest

from status_monitor import (
    DEFAULT_MAX_STATUS_AGE_S,
    BrokerPlatformState,
    BrokerStatusPageMonitor,
    IncidentDiagnosis,
)

FROZEN_NOW = 1_000_000.0


def summary_payload(indicator, components, description="", updated_at=None):
    """Builds a Statuspage-shaped summary.json body."""
    return {
        "page": {
            "id": "bf49n9xp9x2w",
            "name": "Alpaca",
            "url": "https://status.alpaca.markets",
            "time_zone": "America/New_York",
            "updated_at": updated_at or "2026-08-19T20:54:15.984-04:00",
        },
        "components": components,
        "incidents": [],
        "scheduled_maintenances": [],
        "status": {"indicator": indicator, "description": description},
    }


def component(name, status, group=False, group_id=None):
    return {
        "id": name.lower().replace(" ", ""),
        "name": name,
        "status": status,
        "group": group,
        "group_id": group_id,
        "only_show_if_degraded": False,
        "showcase": False,
        "description": None,
    }


def responder(payload, status_code=200):
    """A transport that always returns the same payload, counting its calls."""

    def http_fn(url):
        http_fn.calls += 1
        http_fn.urls.append(url)
        return status_code, payload

    http_fn.calls = 0
    http_fn.urls = []
    return http_fn


ALL_OPERATIONAL = [
    component("Live Trading API", "operational", group=True),
    component("Orders", "operational", group_id="live"),
    component("Market Data Streaming API", "operational", group=True),
]

TRADING_API_DOWN = [
    component("Live Trading API", "major_outage", group=True),
    component("Orders", "major_outage", group_id="live"),
    component("Market Data Streaming API", "operational", group=True),
]


class MonitorTestBase(unittest.TestCase):
    def setUp(self):
        self.now = FROZEN_NOW
        # Silence the module's expected warnings; individual tests re-enable via
        # assertLogs where the log line is the behaviour under test.
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    def clock(self):
        return self.now

    def build(self, http_fn, **kwargs):
        kwargs.setdefault("now_fn", self.clock)
        return BrokerStatusPageMonitor(http_fn=http_fn, **kwargs)


class TestIndicatorParsing(MonitorTestBase):
    def test_none_indicator_is_operational(self):
        monitor = self.build(responder(summary_payload("none", ALL_OPERATIONAL)))
        summary = monitor.fetch_status("alpaca")

        self.assertEqual(summary.page_state, BrokerPlatformState.OPERATIONAL)
        self.assertEqual(summary.state, BrokerPlatformState.OPERATIONAL)
        self.assertEqual(summary.indicator, "none")
        self.assertEqual(summary.affected_components, [])
        self.assertTrue(summary.fetch_ok)

    def test_minor_indicator_is_degraded_not_outage(self):
        monitor = self.build(responder(summary_payload("minor", ALL_OPERATIONAL)))
        self.assertEqual(
            monitor.fetch_status("alpaca").page_state, BrokerPlatformState.DEGRADED
        )

    def test_major_and_critical_indicators_are_major_outage(self):
        for indicator in ("major", "critical"):
            with self.subTest(indicator=indicator):
                monitor = self.build(
                    responder(summary_payload(indicator, TRADING_API_DOWN))
                )
                self.assertEqual(
                    monitor.fetch_status("alpaca").page_state,
                    BrokerPlatformState.MAJOR_OUTAGE,
                )

    def test_maintenance_indicator_is_distinguished_from_outage(self):
        monitor = self.build(responder(summary_payload("maintenance", ALL_OPERATIONAL)))
        self.assertEqual(
            monitor.fetch_status("alpaca").page_state, BrokerPlatformState.MAINTENANCE
        )

    def test_unrecognised_indicator_is_unknown_not_major_outage(self):
        # Regression: an unrecognised indicator used to fall through to MAJOR_OUTAGE,
        # which both halts trading and suppresses bug tickets off a typo.
        monitor = self.build(responder(summary_payload("nonesuch", ALL_OPERATIONAL)))
        summary = monitor.fetch_status("alpaca")

        self.assertEqual(summary.page_state, BrokerPlatformState.UNKNOWN)
        self.assertEqual(summary.state, BrokerPlatformState.UNKNOWN)

    def test_null_indicator_is_unknown_not_operational(self):
        # str(None) is "None", which lowercases to "none" — the documented operational
        # value. A null or missing indicator must not read as "All Systems Operational".
        for payload in (
            summary_payload(None, ALL_OPERATIONAL),
            {"status": {"description": "?"}, "components": ALL_OPERATIONAL},
        ):
            with self.subTest(payload=payload):
                monitor = self.build(responder(payload))
                summary = monitor.fetch_status("alpaca")
                self.assertEqual(summary.page_state, BrokerPlatformState.UNKNOWN)
                self.assertEqual(summary.indicator, "unknown")

    def test_null_description_does_not_leak_the_string_none(self):
        payload = summary_payload("none", ALL_OPERATIONAL)
        payload["status"]["description"] = None
        monitor = self.build(responder(payload))
        self.assertEqual(monitor.fetch_status("alpaca").description, "")

    def test_indicator_is_case_and_whitespace_normalised(self):
        monitor = self.build(responder(summary_payload("  CRITICAL ", TRADING_API_DOWN)))
        summary = monitor.fetch_status("alpaca")

        self.assertEqual(summary.indicator, "critical")
        self.assertEqual(summary.page_state, BrokerPlatformState.MAJOR_OUTAGE)

    def test_page_updated_at_is_captured_but_not_used_for_freshness(self):
        monitor = self.build(
            responder(
                summary_payload(
                    "none", ALL_OPERATIONAL, updated_at="2019-01-01T00:00:00.000-05:00"
                )
            )
        )
        summary = monitor.fetch_status("alpaca")

        self.assertEqual(summary.page_updated_at, "2019-01-01T00:00:00.000-05:00")
        # Freshness comes from the local fetch clock, so a years-old page timestamp on
        # an otherwise healthy page must not make the reading stale.
        self.assertEqual(summary.last_updated, FROZEN_NOW)
        diag = monitor.diagnose_execution_failure("alpaca", "boom")
        self.assertTrue(diag.evidence_is_fresh)


class TestComponentParsing(MonitorTestBase):
    def test_group_components_are_reported_separately_from_leaves(self):
        monitor = self.build(responder(summary_payload("critical", TRADING_API_DOWN)))
        summary = monitor.fetch_status("alpaca")

        # "Live Trading API" is the group; "Orders" is its failing child. Counting both
        # in affected_components would double-report one impairment.
        self.assertEqual(summary.affected_components, ["Orders"])
        self.assertEqual(summary.affected_component_groups, ["Live Trading API"])

    def test_partial_outage_component_counts_as_impaired(self):
        comps = [component("Orders", "partial_outage")]
        monitor = self.build(
            responder(summary_payload("minor", comps)),
            dependency_components={"alpaca": ["Orders"]},
        )
        self.assertEqual(
            monitor.fetch_status("alpaca").dependency_state,
            BrokerPlatformState.MAJOR_OUTAGE,
        )

    def test_degraded_performance_component_maps_to_degraded(self):
        comps = [component("Orders", "degraded_performance")]
        monitor = self.build(
            responder(summary_payload("minor", comps)),
            dependency_components={"alpaca": ["Orders"]},
        )
        self.assertEqual(
            monitor.fetch_status("alpaca").dependency_state, BrokerPlatformState.DEGRADED
        )

    def test_under_maintenance_component_maps_to_maintenance(self):
        comps = [component("Orders", "under_maintenance")]
        monitor = self.build(
            responder(summary_payload("none", comps)),
            dependency_components={"alpaca": ["Orders"]},
        )
        self.assertEqual(
            monitor.fetch_status("alpaca").dependency_state,
            BrokerPlatformState.MAINTENANCE,
        )

    def test_component_with_missing_status_is_not_treated_as_operational(self):
        # Regression: `status not in ("operational", None)` read a missing status as
        # healthy, so an unreadable component silently contributed to an all-clear.
        comps = [{"name": "Orders", "group": False}]
        monitor = self.build(
            responder(summary_payload("none", comps)),
            dependency_components={"alpaca": ["Orders"]},
        )
        summary = monitor.fetch_status("alpaca")

        self.assertEqual(summary.dependency_state, BrokerPlatformState.UNKNOWN)
        self.assertIn("Orders", summary.affected_components)

    def test_unnamed_component_does_not_crash_parsing(self):
        comps = [{"status": "major_outage", "group": False}]
        monitor = self.build(responder(summary_payload("critical", comps)))
        self.assertEqual(
            monitor.fetch_status("alpaca").affected_components, ["Unnamed Component"]
        )

    def test_non_dict_component_entries_are_skipped(self):
        comps = ["garbage", None, component("Orders", "major_outage")]
        monitor = self.build(responder(summary_payload("critical", comps)))
        self.assertEqual(monitor.fetch_status("alpaca").affected_components, ["Orders"])

    def test_components_absent_entirely(self):
        payload = summary_payload("none", ALL_OPERATIONAL)
        del payload["components"]
        monitor = self.build(responder(payload))
        summary = monitor.fetch_status("alpaca")

        self.assertEqual(summary.affected_components, [])
        self.assertEqual(summary.page_state, BrokerPlatformState.OPERATIONAL)


class TestDependencyScoping(MonitorTestBase):
    def test_dependency_outage_detected_while_page_indicator_reads_none(self):
        # The skill's headline pitfall: the blended page indicator says "All Systems
        # Operational" while the component actually carrying order flow is down.
        comps = [
            component("Orders", "major_outage"),
            component("Website", "operational"),
        ]
        monitor = self.build(
            responder(summary_payload("none", comps)),
            dependency_components={"alpaca": ["Orders"]},
        )
        summary = monitor.fetch_status("alpaca")

        self.assertEqual(summary.page_state, BrokerPlatformState.OPERATIONAL)
        self.assertEqual(summary.dependency_state, BrokerPlatformState.MAJOR_OUTAGE)
        self.assertEqual(summary.state, BrokerPlatformState.MAJOR_OUTAGE)

        diag = monitor.diagnose_execution_failure("alpaca", "order rejected")
        self.assertEqual(diag.diagnosis, IncidentDiagnosis.EXTERNAL_BROKER_OUTAGE)

    def test_unrelated_component_outage_does_not_suppress_bug_ticket(self):
        # A major outage confined to a component this system does not use must not
        # silence a genuine internal bug.
        comps = [
            component("Orders", "operational"),
            component("Broker API (Sandbox)", "major_outage"),
        ]
        monitor = self.build(
            responder(summary_payload("critical", comps)),
            dependency_components={"alpaca": ["Orders"]},
        )
        monitor.fetch_status("alpaca")

        diag = monitor.diagnose_execution_failure("alpaca", "ValueError: bad price")
        # Page-wide critical plus healthy dependencies is genuinely ambiguous: neither
        # suppress nor file automatically.
        self.assertEqual(diag.diagnosis, IncidentDiagnosis.UNKNOWN_FAILURE)

    def test_dependency_matching_is_case_insensitive(self):
        comps = [component("Live Trading API", "major_outage")]
        monitor = self.build(
            responder(summary_payload("none", comps)),
            dependency_components={"ALPACA": ["  live trading api  "]},
        )
        summary = monitor.fetch_status("Alpaca")

        self.assertEqual(summary.dependency_state, BrokerPlatformState.MAJOR_OUTAGE)
        self.assertEqual(summary.unmatched_dependencies, [])

    def test_unmatched_dependency_is_surfaced_and_logged(self):
        monitor = self.build(
            responder(summary_payload("none", ALL_OPERATIONAL)),
            dependency_components={"alpaca": ["Renamed Component"]},
        )
        logging.disable(logging.NOTSET)
        with self.assertLogs("status_monitor", level="WARNING") as captured:
            summary = monitor.fetch_status("alpaca")

        self.assertEqual(summary.unmatched_dependencies, ["Renamed Component"])
        self.assertIn("Renamed Component", "".join(captured.output))
        # No dependency evidence at all, so the page state is used instead.
        self.assertEqual(summary.dependency_state, BrokerPlatformState.UNKNOWN)
        self.assertEqual(summary.state, BrokerPlatformState.OPERATIONAL)

    def test_worst_of_several_dependencies_wins(self):
        comps = [
            component("Orders", "operational"),
            component("Market Data", "major_outage"),
        ]
        monitor = self.build(
            responder(summary_payload("minor", comps)),
            dependency_components={"alpaca": ["Orders", "Market Data"]},
        )
        self.assertEqual(
            monitor.fetch_status("alpaca").dependency_state,
            BrokerPlatformState.MAJOR_OUTAGE,
        )

    def test_one_unreadable_dependency_blocks_an_all_clear(self):
        comps = [
            component("Orders", "operational"),
            component("Market Data", "who_knows"),
        ]
        monitor = self.build(
            responder(summary_payload("none", comps)),
            dependency_components={"alpaca": ["Orders", "Market Data"]},
        )
        self.assertEqual(
            monitor.fetch_status("alpaca").dependency_state, BrokerPlatformState.UNKNOWN
        )

    def test_unreadable_dependency_does_not_mask_a_worse_sibling(self):
        comps = [
            component("Orders", "major_outage"),
            component("Market Data", "who_knows"),
        ]
        monitor = self.build(
            responder(summary_payload("none", comps)),
            dependency_components={"alpaca": ["Orders", "Market Data"]},
        )
        self.assertEqual(
            monitor.fetch_status("alpaca").dependency_state,
            BrokerPlatformState.MAJOR_OUTAGE,
        )


class TestFetchFailureHandling(MonitorTestBase):
    def test_http_error_yields_unknown_not_a_health_claim(self):
        monitor = self.build(responder({}, status_code=503))
        summary = monitor.fetch_status("alpaca")

        self.assertFalse(summary.fetch_ok)
        self.assertEqual(summary.state, BrokerPlatformState.UNKNOWN)
        self.assertIn("503", monitor.last_fetch_error["alpaca"])

    def test_transport_exception_is_contained_and_recorded(self):
        def exploding(url):
            raise ConnectionError("dns failure")

        monitor = self.build(exploding)
        summary = monitor.fetch_status("alpaca")

        self.assertFalse(summary.fetch_ok)
        self.assertEqual(summary.state, BrokerPlatformState.UNKNOWN)
        self.assertIn("dns failure", monitor.last_fetch_error["alpaca"])

    def test_payload_without_status_object_is_unusable(self):
        for payload in ({"components": []}, {"status": "operational"}, [], None):
            with self.subTest(payload=payload):
                monitor = self.build(responder(payload))
                self.assertFalse(monitor.fetch_status("alpaca").fetch_ok)

    def test_failed_fetch_does_not_destroy_the_last_good_reading(self):
        # During an incident the status page is itself under load. One 503 must not
        # discard a reading taken seconds earlier.
        state = {"fail": False}

        def flaky(url):
            if state["fail"]:
                return 503, {}
            return 200, summary_payload("critical", TRADING_API_DOWN)

        monitor = self.build(flaky)
        monitor.fetch_status("alpaca")
        state["fail"] = True
        self.now += 1.0
        monitor.fetch_status("alpaca")

        cached, age = monitor.get_cached_status("alpaca")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.page_state, BrokerPlatformState.MAJOR_OUTAGE)
        self.assertEqual(age, 1.0)

    def test_unconfigured_broker_raises(self):
        monitor = self.build(responder(summary_payload("none", ALL_OPERATIONAL)))
        with self.assertRaises(ValueError):
            monitor.fetch_status("not-a-broker")

    def test_missing_transport_raises(self):
        monitor = BrokerStatusPageMonitor(now_fn=self.clock)
        with self.assertRaises(RuntimeError):
            monitor.fetch_status("alpaca")

    def test_invalid_constructor_bounds_rejected(self):
        with self.assertRaises(ValueError):
            BrokerStatusPageMonitor(max_status_age_s=0)
        with self.assertRaises(ValueError):
            BrokerStatusPageMonitor(min_refetch_interval_s=-1)

    def test_empty_url_map_resolves_no_brokers(self):
        monitor = BrokerStatusPageMonitor(
            broker_status_urls={}, http_fn=responder({}), now_fn=self.clock
        )
        self.assertEqual(monitor.broker_status_urls, {})
        with self.assertRaises(ValueError):
            monitor.fetch_status("alpaca")

    def test_default_urls_are_the_documented_statuspage_endpoints(self):
        monitor = BrokerStatusPageMonitor()
        self.assertEqual(
            monitor.broker_status_urls["alpaca"],
            "https://status.alpaca.markets/api/v2/summary.json",
        )
        self.assertEqual(
            monitor.broker_status_urls["coinbase"],
            "https://status.coinbase.com/api/v2/summary.json",
        )


class TestDiagnosis(MonitorTestBase):
    def test_page_outage_suppresses_bug_ticket(self):
        monitor = self.build(responder(summary_payload("critical", TRADING_API_DOWN)))
        monitor.fetch_status("alpaca")

        diag = monitor.diagnose_execution_failure("alpaca", "Connection refused")
        self.assertEqual(diag.diagnosis, IncidentDiagnosis.EXTERNAL_BROKER_OUTAGE)
        self.assertEqual(diag.platform_state, BrokerPlatformState.MAJOR_OUTAGE)
        self.assertTrue(diag.evidence_is_fresh)
        self.assertIn("Orders", diag.affected_components)

    def test_page_maintenance_is_external_not_internal(self):
        monitor = self.build(responder(summary_payload("maintenance", ALL_OPERATIONAL)))
        monitor.fetch_status("alpaca")

        diag = monitor.diagnose_execution_failure("alpaca", "503 from broker")
        self.assertEqual(diag.diagnosis, IncidentDiagnosis.EXTERNAL_BROKER_OUTAGE)
        self.assertEqual(diag.platform_state, BrokerPlatformState.MAINTENANCE)

    def test_operational_page_escalates_internal_bug(self):
        monitor = self.build(responder(summary_payload("none", ALL_OPERATIONAL)))
        monitor.fetch_status("alpaca")

        diag = monitor.diagnose_execution_failure("alpaca", "ValueError: bad price")
        self.assertEqual(diag.diagnosis, IncidentDiagnosis.INTERNAL_APPLICATION_BUG)
        self.assertEqual(diag.platform_state, BrokerPlatformState.OPERATIONAL)

    def test_minor_incident_does_not_suppress_a_bug_ticket(self):
        # Regression: DEGRADED used to return EXTERNAL_BROKER_OUTAGE, so any unrelated
        # minor incident anywhere on the page silenced a real internal bug. A minor
        # indicator is ambiguous evidence, not exculpatory evidence.
        monitor = self.build(responder(summary_payload("minor", ALL_OPERATIONAL)))
        monitor.fetch_status("alpaca")

        diag = monitor.diagnose_execution_failure("alpaca", "ValueError: bad price")
        self.assertEqual(diag.diagnosis, IncidentDiagnosis.UNKNOWN_FAILURE)
        self.assertEqual(diag.platform_state, BrokerPlatformState.DEGRADED)

    def test_unreachable_status_feed_is_unknown_not_operational(self):
        # Regression: an unreachable feed used to diagnose INTERNAL_APPLICATION_BUG,
        # filing a code-bug ticket off no evidence at all.
        monitor = self.build(responder({}, status_code=500))

        diag = monitor.diagnose_execution_failure("alpaca", "Connection refused")
        self.assertEqual(diag.diagnosis, IncidentDiagnosis.UNKNOWN_FAILURE)
        self.assertEqual(diag.platform_state, BrokerPlatformState.UNKNOWN)
        self.assertFalse(diag.evidence_is_fresh)

    def test_unrecognised_indicator_diagnoses_unknown(self):
        monitor = self.build(responder(summary_payload("nonesuch", ALL_OPERATIONAL)))
        monitor.fetch_status("alpaca")

        diag = monitor.diagnose_execution_failure("alpaca", "boom")
        self.assertEqual(diag.diagnosis, IncidentDiagnosis.UNKNOWN_FAILURE)

    def test_misconfigured_monitor_never_raises_into_the_caller(self):
        # Regression: both of these used to return INTERNAL_APPLICATION_BUG with
        # platform_state OPERATIONAL — a configuration error was reported as positive
        # evidence that the broker was healthy.
        monitor = BrokerStatusPageMonitor(now_fn=self.clock)  # no transport
        diag = monitor.diagnose_execution_failure("alpaca", "boom")
        self.assertEqual(diag.diagnosis, IncidentDiagnosis.UNKNOWN_FAILURE)
        self.assertEqual(diag.platform_state, BrokerPlatformState.UNKNOWN)

        monitor = self.build(responder(summary_payload("none", ALL_OPERATIONAL)))
        diag = monitor.diagnose_execution_failure("not-a-broker", "boom")
        self.assertEqual(diag.diagnosis, IncidentDiagnosis.UNKNOWN_FAILURE)
        self.assertEqual(diag.platform_state, BrokerPlatformState.UNKNOWN)

    def test_diagnosis_fetches_when_no_reading_is_cached(self):
        http_fn = responder(summary_payload("critical", TRADING_API_DOWN))
        monitor = self.build(http_fn)

        diag = monitor.diagnose_execution_failure("alpaca", "Connection refused")
        self.assertEqual(http_fn.calls, 1)
        self.assertEqual(diag.diagnosis, IncidentDiagnosis.EXTERNAL_BROKER_OUTAGE)

    def test_broker_name_is_normalised(self):
        monitor = self.build(responder(summary_payload("none", ALL_OPERATIONAL)))
        monitor.fetch_status("  ALPACA  ")
        self.assertIn("alpaca", monitor.last_status)

        diag = monitor.diagnose_execution_failure(" Alpaca ", "boom")
        self.assertEqual(diag.diagnosis, IncidentDiagnosis.INTERNAL_APPLICATION_BUG)
        self.assertEqual(diag.broker_name, "alpaca")

    def test_original_error_is_preserved_in_every_explanation(self):
        for payload, code in (
            (summary_payload("none", ALL_OPERATIONAL), 200),
            (summary_payload("critical", TRADING_API_DOWN), 200),
            (summary_payload("minor", ALL_OPERATIONAL), 200),
            ({}, 500),
        ):
            with self.subTest(code=code):
                monitor = self.build(responder(payload, status_code=code))
                diag = monitor.diagnose_execution_failure("alpaca", "SENTINEL-ERR-42")
                self.assertIn("SENTINEL-ERR-42", diag.explanation)


class TestFreshness(MonitorTestBase):
    def test_stale_reading_is_not_used_as_evidence(self):
        state = {"fail": False}

        def flaky(url):
            if state["fail"]:
                raise ConnectionError("status page unreachable")
            return 200, summary_payload("none", ALL_OPERATIONAL)

        monitor = self.build(flaky)
        monitor.fetch_status("alpaca")

        state["fail"] = True
        self.now += DEFAULT_MAX_STATUS_AGE_S + 1.0
        diag = monitor.diagnose_execution_failure("alpaca", "order timed out")

        self.assertEqual(diag.diagnosis, IncidentDiagnosis.UNKNOWN_FAILURE)
        self.assertFalse(diag.evidence_is_fresh)
        self.assertIn("freshness bound", diag.explanation)

    def test_reading_exactly_at_the_bound_is_still_fresh(self):
        monitor = self.build(
            responder(summary_payload("none", ALL_OPERATIONAL)), max_status_age_s=100.0
        )
        monitor.fetch_status("alpaca")
        self.now += 100.0

        diag = monitor.diagnose_execution_failure("alpaca", "boom")
        self.assertTrue(diag.evidence_is_fresh)
        self.assertEqual(diag.diagnosis, IncidentDiagnosis.INTERNAL_APPLICATION_BUG)
        self.assertEqual(diag.status_age_s, 100.0)

    def test_stale_reading_is_refreshed_when_the_feed_recovers(self):
        monitor = self.build(responder(summary_payload("critical", TRADING_API_DOWN)))
        monitor.fetch_status("alpaca")
        self.now += DEFAULT_MAX_STATUS_AGE_S + 1.0

        diag = monitor.diagnose_execution_failure("alpaca", "boom")
        self.assertTrue(diag.evidence_is_fresh)
        self.assertEqual(diag.status_age_s, 0.0)
        self.assertEqual(diag.diagnosis, IncidentDiagnosis.EXTERNAL_BROKER_OUTAGE)

    def test_fresh_cached_reading_is_not_refetched(self):
        http_fn = responder(summary_payload("none", ALL_OPERATIONAL))
        monitor = self.build(http_fn)
        monitor.fetch_status("alpaca")
        self.now += 1.0

        monitor.diagnose_execution_failure("alpaca", "boom")
        self.assertEqual(http_fn.calls, 1)


class TestRefetchThrottle(MonitorTestBase):
    def test_failure_burst_does_not_stampede_the_status_endpoint(self):
        http_fn = responder({}, status_code=503)
        monitor = self.build(http_fn, min_refetch_interval_s=10.0)

        for _ in range(50):
            diag = monitor.diagnose_execution_failure("alpaca", "order rejected")
            self.assertEqual(diag.diagnosis, IncidentDiagnosis.UNKNOWN_FAILURE)

        self.assertEqual(http_fn.calls, 1)

    def test_refetch_resumes_after_the_interval_elapses(self):
        http_fn = responder({}, status_code=503)
        monitor = self.build(http_fn, min_refetch_interval_s=10.0)

        monitor.diagnose_execution_failure("alpaca", "boom")
        self.now += 10.0
        monitor.diagnose_execution_failure("alpaca", "boom")

        self.assertEqual(http_fn.calls, 2)

    def test_explicit_fetch_status_is_never_throttled(self):
        # A poller's own loop owns its cadence; the throttle exists only to bound the
        # implicit refresh on the order-failure path.
        http_fn = responder(summary_payload("none", ALL_OPERATIONAL))
        monitor = self.build(http_fn, min_refetch_interval_s=3600.0)

        for _ in range(3):
            monitor.fetch_status("alpaca")

        self.assertEqual(http_fn.calls, 3)


class TestConcurrency(MonitorTestBase):
    def test_concurrent_poll_and_diagnose_stay_consistent(self):
        monitor = self.build(responder(summary_payload("critical", TRADING_API_DOWN)))
        monitor.fetch_status("alpaca")
        verdicts = []
        errors = []

        def poll():
            try:
                for _ in range(200):
                    monitor.fetch_status("alpaca")
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        def diagnose():
            try:
                for _ in range(200):
                    verdicts.append(
                        monitor.diagnose_execution_failure("alpaca", "boom").diagnosis
                    )
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=poll), threading.Thread(target=diagnose)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(verdicts), 200)
        self.assertEqual(set(verdicts), {IncidentDiagnosis.EXTERNAL_BROKER_OUTAGE})


if __name__ == "__main__":
    unittest.main()
