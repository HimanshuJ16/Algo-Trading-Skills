import unittest

from config_drift_detector import (
    DEFAULT_PROTECTED_KEYS,
    ConfigAuditReport,
    ConfigurationDriftDetector,
)


class TestConfigurationDriftDetector(unittest.TestCase):

    def setUp(self):
        self.detector = ConfigurationDriftDetector(allowed_overrides={"api_url", "log_level", "port"})

        self.golden_baseline = {
            "strategy": {
                "max_order_usd": 100000,
                "risk_stop_loss_pct": 0.02
            },
            "system": {
                "api_url": "https://prod.api.exchange.com",
                "log_level": "INFO",
                "threads": 8
            }
        }

    def test_identical_configs_pass(self):
        report = self.detector.audit(self.golden_baseline, self.golden_baseline)
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.critical_drift_count, 0)

    def test_allowed_override_passes(self):
        target_config = {
            "strategy": {
                "max_order_usd": 100000,
                "risk_stop_loss_pct": 0.02
            },
            "system": {
                "api_url": "https://staging.api.exchange.com",  # Allowed override
                "log_level": "DEBUG",                           # Allowed override
                "threads": 8
            }
        }
        report = self.detector.audit(self.golden_baseline, target_config)
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.critical_drift_count, 0)
        self.assertEqual(report.allowed_override_count, 2)

    def test_critical_drift_fails(self):
        # max_order_usd changed from 100,000 to 1,000,000 (Critical risk parameter mismatch)
        target_config = {
            "strategy": {
                "max_order_usd": 1000000,
                "risk_stop_loss_pct": 0.02
            },
            "system": {
                "api_url": "https://prod.api.exchange.com",
                "log_level": "INFO",
                "threads": 8
            }
        }
        report = self.detector.audit(self.golden_baseline, target_config)
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.critical_drift_count, 1)

    def test_missing_key_fails(self):
        # risk_stop_loss_pct missing in target config
        target_config = {
            "strategy": {
                "max_order_usd": 100000
            },
            "system": {
                "api_url": "https://prod.api.exchange.com",
                "log_level": "INFO",
                "threads": 8
            }
        }
        report = self.detector.audit(self.golden_baseline, target_config)
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.critical_drift_count, 1)

    def test_extra_key_is_warning_and_does_not_block(self):
        target_config = {
            "strategy": {
                "max_order_usd": 100000,
                "risk_stop_loss_pct": 0.02
            },
            "system": {
                "api_url": "https://prod.api.exchange.com",
                "log_level": "INFO",
                "threads": 8,
                "experimental_flag": True
            }
        }
        report = self.detector.audit(self.golden_baseline, target_config)
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.warning_drift_count, 1)
        self.assertEqual(report.drift_items[0].key_path, "system.experimental_flag")


class TestZeroToleranceWhitelist(unittest.TestCase):
    """Regression: an explicitly empty allowed_overrides must mean zero tolerance.

    The previous implementation used `allowed_overrides or {...}`, so an empty
    set was falsy and was silently replaced by the permissive built-in default,
    turning the strictest possible request into the loosest available policy.
    """

    def test_empty_set_does_not_fall_back_to_default_whitelist(self):
        detector = ConfigurationDriftDetector(allowed_overrides=set())
        self.assertEqual(detector.allowed_overrides, set())

        # broker_endpoint is in the built-in default whitelist; under zero
        # tolerance a prod -> sandbox endpoint swap must block deployment.
        report = detector.audit(
            {"broker_endpoint": "https://prod.broker.example"},
            {"broker_endpoint": "https://sandbox.broker.example"},
        )
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.critical_drift_count, 1)
        self.assertEqual(report.allowed_override_count, 0)

    def test_none_uses_default_whitelist(self):
        detector = ConfigurationDriftDetector()
        self.assertEqual(
            detector.allowed_overrides,
            set(ConfigurationDriftDetector.DEFAULT_ALLOWED_OVERRIDES),
        )
        report = detector.audit({"log_level": "INFO"}, {"log_level": "DEBUG"})
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.allowed_override_count, 1)


class TestProtectedRiskParameters(unittest.TestCase):
    """Risk-control parameters must never be downgraded to an ALLOWED override.

    `references/standards.md` states this as a zero-tolerance requirement; the
    previous implementation documented it but did not enforce it, so a single
    line in a whitelist could disable the gate on the parameter it exists to
    protect.
    """

    def test_whitelisting_a_protected_key_is_rejected_at_construction(self):
        with self.assertRaises(ValueError) as ctx:
            ConfigurationDriftDetector(allowed_overrides={"max_position_size"})
        self.assertIn("max_position_size", str(ctx.exception))

    def test_whitelisting_a_protected_dotted_path_is_rejected(self):
        with self.assertRaises(ValueError):
            ConfigurationDriftDetector(allowed_overrides={"risk.kill_switch_enabled"})

    def test_protected_key_drift_is_critical_even_if_whitelist_is_mutated(self):
        # Guards against post-construction mutation of the whitelist.
        detector = ConfigurationDriftDetector(allowed_overrides={"api_url"})
        detector.allowed_overrides.add("max_order_usd")

        report = detector.audit(
            {"risk": {"max_order_usd": 100_000}},
            {"risk": {"max_order_usd": 1_000_000_000}},
        )
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.drift_items[0].severity, "CRITICAL")
        self.assertIn("Protected risk-control parameter", report.drift_items[0].description)

    def test_protected_keys_can_be_narrowed_explicitly(self):
        detector = ConfigurationDriftDetector(
            allowed_overrides={"max_order_qty"}, protected_keys=set()
        )
        report = detector.audit({"max_order_qty": 10}, {"max_order_qty": 20})
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.allowed_override_count, 1)

    def test_documented_risk_parameters_are_protected_by_default(self):
        for key in ("kill_switch_enabled", "max_position_size", "stop_loss_pct"):
            self.assertIn(key, DEFAULT_PROTECTED_KEYS)


class TestFlatteningEdgeCases(unittest.TestCase):

    def test_empty_nested_dict_is_still_audited(self):
        """Regression: an empty nested dict used to vanish during flattening,
        so a subtree present in the baseline and absent from the target was
        reported as zero keys audited and a clean PASS."""
        report = ConfigurationDriftDetector().audit({"risk": {}}, {})
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.critical_drift_count, 1)
        self.assertEqual(report.drift_items[0].key_path, "risk")

    def test_ambiguous_key_path_is_rejected(self):
        """Regression: a literal 'a.b' key colliding with nested {'a': {'b': ...}}
        used to silently overwrite one branch, hiding real drift behind a PASS."""
        detector = ConfigurationDriftDetector()
        with self.assertRaises(ValueError) as ctx:
            detector.audit(
                {"a": {"b": 1}, "a.b": 1},
                {"a": {"b": 999}, "a.b": 1},
            )
        self.assertIn("a.b", str(ctx.exception))

    def test_dotted_key_without_collision_is_allowed(self):
        detector = ConfigurationDriftDetector(allowed_overrides=set())
        report = detector.audit(
            {"logging": {"handlers.console": "INFO"}},
            {"logging": {"handlers.console": "INFO"}},
        )
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.total_keys_audited, 1)

    def test_deeply_nested_drift_is_detected(self):
        detector = ConfigurationDriftDetector(allowed_overrides=set())
        report = detector.audit(
            {"a": {"b": {"c": {"d": 1}}}},
            {"a": {"b": {"c": {"d": 2}}}},
        )
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.drift_items[0].key_path, "a.b.c.d")


class TestTypeAndValueSemantics(unittest.TestCase):

    def test_string_vs_int_type_coercion_is_drift(self):
        detector = ConfigurationDriftDetector(allowed_overrides=set())
        report = detector.audit({"threads": 8}, {"threads": "8"})
        self.assertFalse(report.is_compliant)
        self.assertIn("str", report.drift_items[0].description)

    def test_bool_vs_int_is_drift(self):
        """True == 1 in Python; only the type check catches this, and it matters
        because a kill switch read as an int may not behave as a flag."""
        detector = ConfigurationDriftDetector()
        report = detector.audit({"kill_switch_enabled": True}, {"kill_switch_enabled": 1})
        self.assertFalse(report.is_compliant)

    def test_int_vs_float_is_drift(self):
        detector = ConfigurationDriftDetector(allowed_overrides=set())
        report = detector.audit({"threads": 8}, {"threads": 8.0})
        self.assertFalse(report.is_compliant)

    def test_list_comparison_is_order_sensitive(self):
        detector = ConfigurationDriftDetector(allowed_overrides=set())
        report = detector.audit({"venues": ["A", "B"]}, {"venues": ["B", "A"]})
        self.assertFalse(report.is_compliant)

    def test_none_value_matching_is_not_confused_with_missing_key(self):
        detector = ConfigurationDriftDetector(allowed_overrides=set())
        report = detector.audit({"tag": None}, {"tag": None})
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.total_keys_audited, 1)


class TestWhitelistMatching(unittest.TestCase):

    def test_exact_dotted_path_whitelist_is_scoped(self):
        detector = ConfigurationDriftDetector(allowed_overrides={"system.api_url"})
        report = detector.audit(
            {"system": {"api_url": "a"}, "feed": {"api_url": "a"}},
            {"system": {"api_url": "b"}, "feed": {"api_url": "b"}},
        )
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.allowed_override_count, 1)
        self.assertEqual(report.critical_drift_count, 1)

    def test_bare_leaf_name_whitelist_matches_anywhere_in_tree(self):
        """Documented sharp edge: a bare leaf name is tree-wide, not scoped."""
        detector = ConfigurationDriftDetector(allowed_overrides={"api_url"})
        report = detector.audit(
            {"system": {"api_url": "a"}, "feed": {"api_url": "a"}},
            {"system": {"api_url": "b"}, "feed": {"api_url": "b"}},
        )
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.allowed_override_count, 2)

    def test_missing_whitelisted_key_is_still_critical(self):
        """An override may change a value, not delete a setting the engine reads."""
        detector = ConfigurationDriftDetector(allowed_overrides={"log_level"})
        report = detector.audit({"log_level": "INFO"}, {})
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.critical_drift_count, 1)


class TestInputValidation(unittest.TestCase):

    def test_non_dict_baseline_raises_type_error(self):
        with self.assertRaises(TypeError):
            ConfigurationDriftDetector().audit(None, {})

    def test_non_dict_target_raises_type_error(self):
        with self.assertRaises(TypeError):
            ConfigurationDriftDetector().audit({}, ["not", "a", "dict"])

    def test_empty_configs_are_compliant(self):
        report = ConfigurationDriftDetector().audit({}, {})
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.total_keys_audited, 0)


class TestReportShape(unittest.TestCase):

    def test_counts_are_consistent_with_drift_items(self):
        detector = ConfigurationDriftDetector(allowed_overrides={"log_level"})
        report = detector.audit(
            {"log_level": "INFO", "threads": 8, "gone": 1},
            {"log_level": "DEBUG", "threads": 9, "extra": 2},
        )
        self.assertIsInstance(report, ConfigAuditReport)
        severities = [i.severity for i in report.drift_items]
        self.assertEqual(severities.count("CRITICAL"), report.critical_drift_count)
        self.assertEqual(severities.count("WARNING"), report.warning_drift_count)
        self.assertEqual(severities.count("ALLOWED"), report.allowed_override_count)
        self.assertEqual(report.total_keys_audited, 4)
        self.assertFalse(report.is_compliant)


if __name__ == '__main__':
    unittest.main()
