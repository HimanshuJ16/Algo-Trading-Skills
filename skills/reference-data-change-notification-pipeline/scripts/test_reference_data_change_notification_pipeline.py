import logging
import unittest
from dataclasses import FrozenInstanceError

from reference_data_change_notification_pipeline import (
    CHANGE_ADDED,
    CHANGE_MODIFIED,
    CHANGE_REMOVED,
    ChangeDetectionConfigError,
    ChangeNotification,
    DEFAULT_CRITICAL_FIELDS,
    DEFAULT_WARNING_FIELDS,
    NotificationConsumer,
    ReferenceDataChangeNotificationPipelineConfig,
    ReferenceDataChangeNotificationPipelineEngine,
    SEVERITY_CRITICAL,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    STATUS_CHANGES_DETECTED,
    STATUS_ENGINE_DISABLED,
    STATUS_NO_CHANGES,
    SnapshotError,
)

# Keep expected-vs-actual failures readable; the engine logs a WARNING per critical change.
logging.getLogger("reference_data_change_notification_pipeline").setLevel(logging.CRITICAL)


class _Recorder:
    """Callback that records what it was handed, optionally failing on one field."""

    def __init__(self, fail_on_field=None):
        self.received = []
        self.fail_on_field = fail_on_field

    def __call__(self, notification):
        if notification.field_name == self.fail_on_field:
            raise RuntimeError("sink unavailable")
        self.received.append(notification)


class TestDetectionBasics(unittest.TestCase):
    def setUp(self):
        self.engine = ReferenceDataChangeNotificationPipelineEngine()

    def test_ticker_rename_is_critical(self):
        """FB -> META (effective 2022-06-09) must surface as a CRITICAL identity change."""
        before = {"symbol": "FB", "exchange": "XNAS", "lot_size": 100, "cusip": "30303M102"}
        after = {"symbol": "META", "exchange": "XNAS", "lot_size": 100, "cusip": "30303M102"}

        report = self.engine.detect_changes("INST_META", before, after, as_of="2022-06-09")

        self.assertEqual(report.status, STATUS_CHANGES_DETECTED)
        self.assertEqual(report.total_changes, 1)
        self.assertEqual(report.critical_changes, 1)
        self.assertEqual(report.max_severity, SEVERITY_CRITICAL)
        self.assertEqual(report.as_of, "2022-06-09")

        notification = report.notifications[0]
        self.assertEqual(notification.field_name, "symbol")
        self.assertEqual(notification.old_value, "FB")
        self.assertEqual(notification.new_value, "META")
        self.assertEqual(notification.change_type, CHANGE_MODIFIED)
        self.assertEqual(notification.severity, SEVERITY_CRITICAL)
        self.assertEqual(notification.as_of, "2022-06-09")

    def test_cusip_unchanged_across_ticker_rename_raises_nothing(self):
        """The CUSIP is not a function of the ticker, so it must not be reported."""
        before = {"symbol": "FB", "cusip": "30303M102"}
        after = {"symbol": "META", "cusip": "30303M102"}

        fields = {n.field_name for n in self.engine.detect_changes("I", before, after).notifications}
        self.assertEqual(fields, {"symbol"})

    def test_lot_size_change_is_warning_not_info(self):
        """Regression: lot_size used to classify as INFO despite being an order-construction field."""
        before = {"symbol": "AAPL", "lot_size": 100}
        after = {"symbol": "AAPL", "lot_size": 200}

        report = self.engine.detect_changes("INST_002", before, after)
        self.assertEqual(report.critical_changes, 0)
        self.assertEqual(report.warning_changes, 1)
        self.assertEqual(report.info_changes, 0)
        self.assertEqual(report.max_severity, SEVERITY_WARNING)
        self.assertEqual(report.notifications[0].severity, SEVERITY_WARNING)

    def test_unrecognized_field_is_info(self):
        report = self.engine.detect_changes(
            "INST_003", {"long_name": "Apple Inc"}, {"long_name": "Apple Inc."}
        )
        self.assertEqual(report.info_changes, 1)
        self.assertEqual(report.max_severity, SEVERITY_INFO)

    def test_no_changes(self):
        snapshot = {"symbol": "TSLA", "exchange": "XNAS", "currency": "USD"}
        report = self.engine.detect_changes("INST_004", snapshot, dict(snapshot))
        self.assertEqual(report.status, STATUS_NO_CHANGES)
        self.assertEqual(report.total_changes, 0)
        self.assertIsNone(report.max_severity)
        self.assertEqual(report.notifications, [])

    def test_notifications_are_ordered_by_field_name(self):
        before = {"zeta": 1, "alpha": 1, "middle": 1}
        after = {"zeta": 2, "alpha": 2, "middle": 2}
        report = self.engine.detect_changes("INST_005", before, after)
        self.assertEqual([n.field_name for n in report.notifications], ["alpha", "middle", "zeta"])

    def test_counts_sum_to_total(self):
        before = {"symbol": "A", "lot_size": 1, "note": "x"}
        after = {"symbol": "B", "lot_size": 2, "note": "y"}
        report = self.engine.detect_changes("INST_006", before, after)
        self.assertEqual(
            report.critical_changes + report.warning_changes + report.info_changes,
            report.total_changes,
        )
        self.assertEqual(report.total_changes, 3)


class TestPresenceSemantics(unittest.TestCase):
    """Absent must never be conflated with a field explicitly published as None."""

    def setUp(self):
        self.engine = ReferenceDataChangeNotificationPipelineEngine()

    def test_field_removal_is_detected_when_prior_value_was_none(self):
        """Regression: dict.get() made {'isin': None} -> {} compare equal, hiding the removal."""
        report = self.engine.detect_changes("INST_007", {"isin": None}, {})
        self.assertEqual(report.total_changes, 1)
        notification = report.notifications[0]
        self.assertEqual(notification.change_type, CHANGE_REMOVED)
        self.assertTrue(notification.old_present)
        self.assertFalse(notification.new_present)

    def test_field_addition_with_none_value_is_detected(self):
        report = self.engine.detect_changes("INST_008", {}, {"isin": None})
        self.assertEqual(report.total_changes, 1)
        self.assertEqual(report.notifications[0].change_type, CHANGE_ADDED)
        self.assertTrue(report.notifications[0].new_present)

    def test_none_on_both_sides_is_not_a_change(self):
        report = self.engine.detect_changes("INST_009", {"isin": None}, {"isin": None})
        self.assertEqual(report.status, STATUS_NO_CHANGES)

    def test_vendor_stops_publishing_a_value_is_a_modification_not_a_removal(self):
        report = self.engine.detect_changes("INST_010", {"isin": "US0378331005"}, {"isin": None})
        notification = report.notifications[0]
        self.assertEqual(notification.change_type, CHANGE_MODIFIED)
        self.assertTrue(notification.new_present)
        self.assertIsNone(notification.new_value)


class TestSeverityClassification(unittest.TestCase):
    def setUp(self):
        self.engine = ReferenceDataChangeNotificationPipelineEngine()

    def test_critical_field_matching_is_case_insensitive(self):
        """A vendor publishing 'Symbol' must not silently downgrade to INFO."""
        report = self.engine.detect_changes("INST_011", {"Symbol": "FB"}, {"Symbol": "META"})
        self.assertEqual(report.notifications[0].severity, SEVERITY_CRITICAL)

    def test_removal_of_an_info_field_is_floored_at_warning(self):
        report = self.engine.detect_changes("INST_012", {"long_name": "Apple Inc"}, {})
        self.assertEqual(report.notifications[0].severity, SEVERITY_WARNING)

    def test_removal_of_a_critical_field_stays_critical(self):
        report = self.engine.detect_changes("INST_013", {"symbol": "AAPL"}, {})
        self.assertEqual(report.notifications[0].severity, SEVERITY_CRITICAL)

    def test_addition_is_not_escalated(self):
        report = self.engine.detect_changes("INST_014", {}, {"long_name": "Apple Inc"})
        self.assertEqual(report.notifications[0].severity, SEVERITY_INFO)

    def test_removal_floor_is_configurable(self):
        engine = ReferenceDataChangeNotificationPipelineEngine(
            ReferenceDataChangeNotificationPipelineConfig(removal_min_severity=SEVERITY_INFO)
        )
        report = engine.detect_changes("INST_015", {"long_name": "x"}, {})
        self.assertEqual(report.notifications[0].severity, SEVERITY_INFO)

    def test_custom_field_sets_override_defaults(self):
        engine = ReferenceDataChangeNotificationPipelineEngine(
            ReferenceDataChangeNotificationPipelineConfig(
                critical_fields={"lot_size"}, warning_fields={"symbol"}
            )
        )
        report = engine.detect_changes(
            "INST_016", {"symbol": "A", "lot_size": 1}, {"symbol": "B", "lot_size": 2}
        )
        by_field = {n.field_name: n.severity for n in report.notifications}
        self.assertEqual(by_field["lot_size"], SEVERITY_CRITICAL)
        self.assertEqual(by_field["symbol"], SEVERITY_WARNING)

    def test_default_field_sets_are_disjoint(self):
        self.assertEqual(DEFAULT_CRITICAL_FIELDS & DEFAULT_WARNING_FIELDS, frozenset())


class TestValueComparison(unittest.TestCase):
    def setUp(self):
        self.engine = ReferenceDataChangeNotificationPipelineEngine()

    def test_type_change_is_reported_as_a_change(self):
        """'100' -> 100 is a schema change and must not be silently coerced away."""
        report = self.engine.detect_changes("INST_017", {"lot_size": "100"}, {"lot_size": 100})
        self.assertEqual(report.total_changes, 1)

    def test_numerically_equal_values_of_different_numeric_types_are_not_a_change(self):
        report = self.engine.detect_changes("INST_018", {"lot_size": 100}, {"lot_size": 100.0})
        self.assertEqual(report.status, STATUS_NO_CHANGES)

    def test_uncomparable_value_is_treated_as_changed(self):
        class Hostile:
            def __eq__(self, other):
                raise TypeError("uncomparable")

            __hash__ = None

        report = self.engine.detect_changes("INST_019", {"blob": Hostile()}, {"blob": Hostile()})
        self.assertEqual(report.total_changes, 1)

    def test_nan_is_reported_as_a_change_every_cycle(self):
        """Documented consequence of ``==``: NaN never equals itself. Canonicalize upstream."""
        report = self.engine.detect_changes(
            "INST_019b", {"strike": float("nan")}, {"strike": float("nan")}
        )
        self.assertEqual(report.total_changes, 1)

    def test_change_key_survives_a_value_whose_repr_raises(self):
        """The key is read inside the delivery-failure handler; it must never raise."""

        class Unrenderable:
            def __repr__(self):
                raise RuntimeError("no repr for you")

            def __eq__(self, other):
                return False

            __hash__ = None

        report = self.engine.detect_changes(
            "INST_019c", {"blob": Unrenderable()}, {"blob": Unrenderable()}
        )
        key = report.notifications[0].change_key
        self.assertIn("unrepresentable Unrenderable", key)

        failing = _Recorder(fail_on_field="blob")
        result = self.engine.route_notifications(
            report, [NotificationConsumer("sink", failing)]
        )
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.failures[0].change_key, key)


class TestSnapshotModes(unittest.TestCase):
    def test_delta_mode_ignores_fields_absent_from_after(self):
        engine = ReferenceDataChangeNotificationPipelineEngine(
            ReferenceDataChangeNotificationPipelineConfig(treat_missing_as_removal=False)
        )
        before = {"symbol": "AAPL", "exchange": "XNAS", "lot_size": 100}
        after = {"lot_size": 200}

        report = engine.detect_changes("INST_020", before, after)
        self.assertEqual(report.total_changes, 1)
        self.assertEqual(report.notifications[0].field_name, "lot_size")

    def test_full_snapshot_mode_reports_a_delta_payload_as_mass_removal(self):
        """The default mode is only correct for full snapshots; this is the trap it sets."""
        engine = ReferenceDataChangeNotificationPipelineEngine()
        before = {"symbol": "AAPL", "exchange": "XNAS", "lot_size": 100}
        after = {"lot_size": 200}

        report = engine.detect_changes("INST_021", before, after)
        self.assertEqual(report.total_changes, 3)
        self.assertEqual(
            sum(1 for n in report.notifications if n.change_type == CHANGE_REMOVED), 2
        )


class TestChangeKey(unittest.TestCase):
    def setUp(self):
        self.engine = ReferenceDataChangeNotificationPipelineEngine()

    def test_change_key_is_stable_across_reruns(self):
        before = {"symbol": "FB"}
        after = {"symbol": "META"}
        first = self.engine.detect_changes("I", before, after).notifications[0]
        second = self.engine.detect_changes("I", dict(before), dict(after)).notifications[0]
        self.assertEqual(first.change_key, second.change_key)

    def test_change_key_ignores_observation_time(self):
        a = self.engine.detect_changes("I", {"symbol": "FB"}, {"symbol": "META"}, as_of="t1")
        b = self.engine.detect_changes("I", {"symbol": "FB"}, {"symbol": "META"}, as_of="t2")
        self.assertEqual(a.notifications[0].change_key, b.notifications[0].change_key)

    def test_change_key_distinguishes_absent_from_none(self):
        removal = self.engine.detect_changes("I", {"isin": "X"}, {}).notifications[0]
        nulled = self.engine.detect_changes("I", {"isin": "X"}, {"isin": None}).notifications[0]
        self.assertNotEqual(removal.change_key, nulled.change_key)

    def test_change_key_distinguishes_instruments(self):
        a = self.engine.detect_changes("I1", {"symbol": "FB"}, {"symbol": "META"}).notifications[0]
        b = self.engine.detect_changes("I2", {"symbol": "FB"}, {"symbol": "META"}).notifications[0]
        self.assertNotEqual(a.change_key, b.change_key)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = ReferenceDataChangeNotificationPipelineEngine()

    def test_blank_instrument_id_raises(self):
        for bad in ("", "   ", None, 7):
            with self.subTest(instrument_id=bad):
                with self.assertRaises(SnapshotError):
                    self.engine.detect_changes(bad, {}, {})

    def test_non_mapping_snapshot_raises(self):
        with self.assertRaises(SnapshotError):
            self.engine.detect_changes("I", [("symbol", "AAPL")], {})
        with self.assertRaises(SnapshotError):
            self.engine.detect_changes("I", {}, None)

    def test_non_string_field_name_raises(self):
        with self.assertRaises(SnapshotError):
            self.engine.detect_changes("I", {1: "x"}, {})

    def test_validation_runs_even_when_engine_disabled(self):
        engine = ReferenceDataChangeNotificationPipelineEngine(
            ReferenceDataChangeNotificationPipelineConfig(enabled=False)
        )
        with self.assertRaises(SnapshotError):
            engine.detect_changes("", {}, {})

    def test_overlapping_field_sets_rejected(self):
        with self.assertRaises(ChangeDetectionConfigError):
            ReferenceDataChangeNotificationPipelineConfig(
                critical_fields={"symbol"}, warning_fields={"Symbol"}
            )

    def test_invalid_removal_severity_rejected(self):
        with self.assertRaises(ChangeDetectionConfigError):
            ReferenceDataChangeNotificationPipelineConfig(removal_min_severity="URGENT")

    def test_string_field_set_rejected(self):
        """A bare string is an iterable of characters and would silently mis-configure the set."""
        with self.assertRaises(ChangeDetectionConfigError):
            ReferenceDataChangeNotificationPipelineConfig(critical_fields="symbol")

    def test_non_string_field_name_in_config_rejected(self):
        with self.assertRaises(ChangeDetectionConfigError):
            ReferenceDataChangeNotificationPipelineConfig(critical_fields={7})

    def test_empty_field_name_in_config_rejected(self):
        with self.assertRaises(ChangeDetectionConfigError):
            ReferenceDataChangeNotificationPipelineConfig(critical_fields={"  "})


class TestDisabledEngine(unittest.TestCase):
    def test_disabled_engine_reports_status_rather_than_no_changes(self):
        engine = ReferenceDataChangeNotificationPipelineEngine(
            ReferenceDataChangeNotificationPipelineConfig(enabled=False)
        )
        report = engine.detect_changes("INST_022", {"symbol": "FB"}, {"symbol": "META"})
        self.assertEqual(report.status, STATUS_ENGINE_DISABLED)
        self.assertNotEqual(report.status, STATUS_NO_CHANGES)
        self.assertEqual(report.total_changes, 0)
        self.assertIn("NOT compared", report.audit_notes)


class TestRouting(unittest.TestCase):
    def setUp(self):
        self.engine = ReferenceDataChangeNotificationPipelineEngine()
        self.report = self.engine.detect_changes(
            "INST_023",
            {"symbol": "FB", "lot_size": 100, "long_name": "Facebook"},
            {"symbol": "META", "lot_size": 200, "long_name": "Meta"},
        )
        self.assertEqual(self.report.total_changes, 3)

    def test_min_severity_filters_deliveries(self):
        risk = _Recorder()
        dashboard = _Recorder()
        result = self.engine.route_notifications(
            self.report,
            [
                NotificationConsumer("risk", risk, min_severity=SEVERITY_CRITICAL),
                NotificationConsumer("dashboard", dashboard, min_severity=SEVERITY_INFO),
            ],
        )
        self.assertEqual([n.field_name for n in risk.received], ["symbol"])
        self.assertEqual(len(dashboard.received), 3)
        self.assertEqual(result.delivered, 4)
        self.assertEqual(result.skipped_below_threshold, 2)
        self.assertTrue(result.all_delivered)

    def test_warning_threshold_includes_critical(self):
        oms = _Recorder()
        self.engine.route_notifications(
            self.report, [NotificationConsumer("oms", oms, min_severity=SEVERITY_WARNING)]
        )
        self.assertEqual(sorted(n.field_name for n in oms.received), ["lot_size", "symbol"])

    def test_a_failing_consumer_does_not_block_the_others(self):
        broken = _Recorder(fail_on_field="symbol")
        healthy = _Recorder()
        result = self.engine.route_notifications(
            self.report,
            [
                NotificationConsumer("broken", broken, min_severity=SEVERITY_CRITICAL),
                NotificationConsumer("healthy", healthy, min_severity=SEVERITY_CRITICAL),
            ],
        )
        self.assertEqual([n.field_name for n in healthy.received], ["symbol"])
        self.assertFalse(result.all_delivered)
        self.assertEqual(result.failed_consumers, ["broken"])
        self.assertEqual(result.delivered, 1)
        self.assertEqual(len(result.failures), 1)
        self.assertIn("RuntimeError", result.failures[0].error)
        self.assertEqual(
            result.failures[0].change_key, self.report.notifications[-1].change_key
        )

    def test_failure_in_one_notification_does_not_stop_the_same_consumer(self):
        flaky = _Recorder(fail_on_field="symbol")
        result = self.engine.route_notifications(
            self.report, [NotificationConsumer("flaky", flaky, min_severity=SEVERITY_INFO)]
        )
        self.assertEqual(sorted(n.field_name for n in flaky.received), ["long_name", "lot_size"])
        self.assertEqual(result.delivered, 2)
        self.assertEqual(len(result.failures), 1)

    def test_routing_a_clean_report_delivers_nothing(self):
        clean = self.engine.detect_changes("I", {"symbol": "AAPL"}, {"symbol": "AAPL"})
        sink = _Recorder()
        result = self.engine.route_notifications(clean, [NotificationConsumer("sink", sink)])
        self.assertEqual(result.delivered, 0)
        self.assertTrue(result.all_delivered)
        self.assertEqual(sink.received, [])

    def test_duplicate_consumer_names_rejected(self):
        with self.assertRaises(ChangeDetectionConfigError):
            self.engine.route_notifications(
                self.report,
                [NotificationConsumer("dup", _Recorder()), NotificationConsumer("dup", _Recorder())],
            )

    def test_invalid_consumer_severity_rejected(self):
        with self.assertRaises(ChangeDetectionConfigError):
            self.engine.route_notifications(
                self.report, [NotificationConsumer("x", _Recorder(), min_severity="URGENT")]
            )

    def test_non_callable_callback_rejected(self):
        with self.assertRaises(ChangeDetectionConfigError):
            self.engine.route_notifications(
                self.report, [NotificationConsumer("x", "not-callable")]
            )

    def test_consumers_are_validated_before_any_delivery(self):
        good = _Recorder()
        with self.assertRaises(ChangeDetectionConfigError):
            self.engine.route_notifications(
                self.report,
                [
                    NotificationConsumer("good", good),
                    NotificationConsumer("bad", _Recorder(), min_severity="URGENT"),
                ],
            )
        self.assertEqual(good.received, [])

    def test_notification_is_immutable_for_consumers(self):
        notification = self.report.notifications[0]
        with self.assertRaises(FrozenInstanceError):
            notification.severity = SEVERITY_INFO
        self.assertIsInstance(notification, ChangeNotification)


if __name__ == "__main__":
    unittest.main()
