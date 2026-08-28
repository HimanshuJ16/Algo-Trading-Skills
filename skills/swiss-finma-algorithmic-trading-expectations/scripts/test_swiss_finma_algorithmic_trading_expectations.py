import logging
import unittest

from swiss_finma_algorithmic_trading_expectations import (
    Config, Engine,
    ComplianceChecker, SwissFINMAComplianceEngine,
    AlgoTradingSystemAuditSpec, ComplianceRecord,
    SwissAlgoControl, CONTROL_CITATIONS, SUPPORTED_VENUES,
)

logging.getLogger("swiss_finma_algorithmic_trading_expectations").addHandler(
    logging.NullHandler())
logging.getLogger("swiss_finma_algorithmic_trading_expectations").propagate = False

C = SwissAlgoControl

#: Controls that apply to every audited system. The DEA control is conditional, so it
#: is deliberately excluded. Derived from the provisions, not from the engine: FMIO
#: Art. 31(2) lit. a-e (six controls, counting lit. e's three sub-precautions and the
#: chapeau separately), the SIX cl. 11.1.4 identification/notification/record set
#: (five), and FINMA Circ. 13/8 mn 63 documentation (one).
BASE_CONTROLS = frozenset({
    C.ORDER_FLAGGING, C.ALGORITHM_IDENTIFICATION, C.INITIATING_TRADER,
    C.EXCHANGE_NOTIFICATION, C.ORDER_RECORD_KEEPING, C.PEAK_CAPACITY,
    C.TRADING_THRESHOLDS, C.MARKET_ABUSE_PREVENTION, C.ALGORITHM_TESTING,
    C.ORDER_TO_TRADE_RATIO, C.ORDER_FLOW_THROTTLING, C.MINIMUM_TICK_SIZE,
    C.STRATEGY_DOCUMENTATION,
})


def make_spec(**overrides):
    """A fully compliant SIX participant. Numeric values are the firm's own settings,
    not regulatory thresholds -- no Swiss provision states either figure."""
    base = dict(
        algo_id="CH_MM_EQ_01",
        strategy_version="2.1.0",
        governance_owner="Head of Trading Technology",
        venue="SIX_SWISS_EXCHANGE",
        flags_algo_generated_orders=True,
        algorithm_identifier="ALGO-CH-0042",
        initiating_trader_id="TRD-117",
        reported_algo_trading_to_exchange=True,
        records_orders_including_cancellations=True,
        capacity_tested_for_peak_volume=True,
        has_pre_trade_thresholds=True,
        threshold_calibration_reference="RISK-CAL-2026-03 s4.2",
        prevents_market_abuse_art_142_143=True,
        algorithms_and_controls_tested=True,
        limits_order_to_trade_ratio=True,
        max_order_to_trade_ratio=25.0,
        can_throttle_order_flow=True,
        max_message_rate_per_sec=400.0,
        enforces_minimum_tick_size=True,
        strategy_documentation_reference="ALGO-DOC-0042 rev C",
        provides_direct_electronic_access=False,
        can_delete_client_orders_on_demand=False,
    )
    base.update(overrides)
    return AlgoTradingSystemAuditSpec(**base)


def failed_codes(record):
    """The control identifiers in a record's findings, without the prose."""
    return {entry.split(":", 1)[0] for entry in record.failed_controls}


class TestEngineLegacy(unittest.TestCase):
    """The inert structural shims. Retained for backward compatibility."""

    def test_init(self):
        self.assertEqual(Engine(Config(name="test")).config.name, "test")

    def test_run(self):
        self.assertTrue(Engine(Config(name="test")).run())

    def test_default_config_name_matches_the_skill(self):
        self.assertEqual(
            Config().name, "swiss-finma-algorithmic-trading-expectations")


class TestComplianceCheckerFailsClosed(unittest.TestCase):
    """Regression: the previous ComplianceChecker attested Swiss compliance for any
    string, having assessed nothing. These tests fail against that implementation."""

    def setUp(self):
        self.checker = ComplianceChecker()

    def test_bare_identifier_is_not_a_compliance_finding(self):
        res = self.checker.check_compliance("T1")
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.finma_score_pct, 0.0)
        self.assertIn("NOT ASSESSED", res.notes)

    def test_note_does_not_claim_finfrag_compliance(self):
        note = self.checker.check_compliance("T1").notes.lower()
        self.assertNotIn("compliant with", note)

    def test_batch_check_is_fail_closed_for_every_identifier(self):
        res = self.checker.batch_check(["T1", "T2"])
        self.assertEqual(len(res), 2)
        self.assertTrue(all(not r.is_compliant for r in res))

    def test_empty_and_non_string_identifiers_do_not_raise_and_stay_closed(self):
        for identifier in ("", "   ", None, 17):
            with self.subTest(identifier=identifier):
                self.assertFalse(self.checker.check_compliance(identifier).is_compliant)

    def test_checker_delegates_to_the_engine_when_a_spec_is_supplied(self):
        res = self.checker.check_compliance("ignored", spec=make_spec())
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.trade_id, "CH_MM_EQ_01")


class TestEngineConstruction(unittest.TestCase):

    def test_default_venue_is_six(self):
        self.assertEqual(SwissFINMAComplianceEngine().venue, "SIX_SWISS_EXCHANGE")

    def test_venue_is_case_and_whitespace_insensitive(self):
        self.assertEqual(
            SwissFINMAComplianceEngine("  six_swiss_exchange ").venue,
            "SIX_SWISS_EXCHANGE")

    def test_other_swiss_venues_are_refused_rather_than_assumed_identical(self):
        # BX Swiss publishes its own Participant Rules with no algorithmic-trading
        # clause; auditing it against the SIX set would invent obligations.
        for venue in ("BX_SWISS", "SDX", "", None, 3):
            with self.subTest(venue=venue):
                with self.assertRaises(ValueError):
                    SwissFINMAComplianceEngine(venue)

    def test_supported_venues_is_six_only(self):
        self.assertEqual(SUPPORTED_VENUES, ("SIX_SWISS_EXCHANGE",))


class TestSpecValidation(unittest.TestCase):

    def setUp(self):
        self.engine = SwissFINMAComplianceEngine()

    def test_non_spec_argument_raises(self):
        for bad in ({}, None, "CH_MM_EQ_01", 42):
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    self.engine.audit_algo_system(bad)

    def test_blank_algo_id_raises_rather_than_producing_an_unattributable_record(self):
        for algo_id in ("", "   "):
            with self.subTest(algo_id=algo_id):
                with self.assertRaises(ValueError):
                    self.engine.audit_algo_system(make_spec(algo_id=algo_id))

    def test_non_string_algo_id_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_algo_system(make_spec(algo_id=None))

    def test_truthy_non_bool_attestations_raise(self):
        # "yes", 1 and [] are all truthy or falsy by accident; none is an assessment.
        for value in ("yes", 1, 0, [], None):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    self.engine.audit_algo_system(
                        make_spec(capacity_tested_for_peak_volume=value))

    def test_every_boolean_attestation_is_type_checked(self):
        bool_fields = [
            "flags_algo_generated_orders", "reported_algo_trading_to_exchange",
            "records_orders_including_cancellations", "capacity_tested_for_peak_volume",
            "has_pre_trade_thresholds", "prevents_market_abuse_art_142_143",
            "algorithms_and_controls_tested", "limits_order_to_trade_ratio",
            "can_throttle_order_flow", "enforces_minimum_tick_size",
            "provides_direct_electronic_access", "can_delete_client_orders_on_demand",
        ]
        for name in bool_fields:
            with self.subTest(field=name):
                with self.assertRaises(TypeError):
                    self.engine.audit_algo_system(make_spec(**{name: "true"}))

    def test_non_string_evidence_fields_raise(self):
        for name in ("algorithm_identifier", "initiating_trader_id",
                     "threshold_calibration_reference",
                     "strategy_documentation_reference",
                     "strategy_version", "governance_owner"):
            with self.subTest(field=name):
                with self.assertRaises(TypeError):
                    self.engine.audit_algo_system(make_spec(**{name: 7}))

    def test_nan_infinite_negative_zero_and_bool_rates_raise(self):
        for value in (float("nan"), float("inf"), -1.0, 0.0, True, "400"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.engine.audit_algo_system(
                        make_spec(max_message_rate_per_sec=value))
                with self.assertRaises(ValueError):
                    self.engine.audit_algo_system(
                        make_spec(max_order_to_trade_ratio=value))

    def test_spec_venue_must_be_supported(self):
        with self.assertRaises(ValueError):
            self.engine.audit_algo_system(make_spec(venue="BX_SWISS"))


class TestFullyCompliantAudit(unittest.TestCase):

    def setUp(self):
        self.engine = SwissFINMAComplianceEngine()

    def test_compliant_system_passes_with_no_findings(self):
        record = self.engine.audit_algo_system(make_spec())
        self.assertIsInstance(record, ComplianceRecord)
        self.assertTrue(record.is_compliant)
        self.assertEqual(record.failed_controls, [])
        self.assertEqual(record.finma_score_pct, 100.0)

    def test_the_thirteen_base_controls_are_the_applicable_set(self):
        record = self.engine.audit_algo_system(make_spec())
        self.assertEqual(
            set(record.applicable_controls), {c.value for c in BASE_CONTROLS})
        self.assertEqual(len(record.applicable_controls), 13)

    def test_record_carries_a_citation_for_every_applicable_control(self):
        record = self.engine.audit_algo_system(make_spec())
        self.assertEqual(set(record.citations), set(record.applicable_controls))
        for code, citation in record.citations.items():
            with self.subTest(code=code):
                self.assertTrue(citation.strip())

    def test_notes_do_not_assert_an_invented_numeric_threshold(self):
        notes = self.engine.audit_algo_system(make_spec()).notes
        for invented in ("100 msg", "CHF 500", "5%", "microsecond"):
            with self.subTest(invented=invented):
                self.assertNotIn(invented, notes)

    def test_notes_disclaim_the_percentage_as_a_regulatory_metric(self):
        self.assertIn(
            "not a regulatory metric",
            self.engine.audit_algo_system(make_spec()).notes)


class TestIndividualControls(unittest.TestCase):
    """Each control must fire on its own provision and on nothing else."""

    def setUp(self):
        self.engine = SwissFINMAComplianceEngine()

    def assert_only(self, control, **overrides):
        record = self.engine.audit_algo_system(make_spec(**overrides))
        self.assertFalse(record.is_compliant)
        self.assertEqual(failed_codes(record), {control.value})
        return record

    def test_unflagged_orders_breach_cl_11_1_4(self):
        self.assert_only(C.ORDER_FLAGGING, flags_algo_generated_orders=False)

    def test_missing_algorithm_identifier(self):
        self.assert_only(C.ALGORITHM_IDENTIFICATION, algorithm_identifier="")

    def test_whitespace_is_not_an_algorithm_identifier(self):
        # "   " is truthy in Python; a naive check would pass it.
        self.assert_only(C.ALGORITHM_IDENTIFICATION, algorithm_identifier="   ")

    def test_missing_initiating_trader(self):
        self.assert_only(C.INITIATING_TRADER, initiating_trader_id=" ")

    def test_exchange_not_notified(self):
        self.assert_only(
            C.EXCHANGE_NOTIFICATION, reported_algo_trading_to_exchange=False)

    def test_orders_and_cancellations_not_retained(self):
        self.assert_only(
            C.ORDER_RECORD_KEEPING, records_orders_including_cancellations=False)

    def test_peak_capacity_untested(self):
        self.assert_only(C.PEAK_CAPACITY, capacity_tested_for_peak_volume=False)

    def test_market_abuse_controls_absent(self):
        self.assert_only(
            C.MARKET_ABUSE_PREVENTION, prevents_market_abuse_art_142_143=False)

    def test_algorithms_untested(self):
        self.assert_only(C.ALGORITHM_TESTING, algorithms_and_controls_tested=False)

    def test_minimum_tick_size_unenforced(self):
        self.assert_only(C.MINIMUM_TICK_SIZE, enforces_minimum_tick_size=False)

    def test_thresholds_without_a_calibration_record_fail(self):
        # FMIO Art. 31(2)(b) requires thresholds to be "appropriate"; a threshold with
        # no recorded basis cannot be shown to be.
        self.assert_only(
            C.TRADING_THRESHOLDS,
            has_pre_trade_thresholds=True, threshold_calibration_reference="")

    def test_calibration_record_without_thresholds_also_fails(self):
        self.assert_only(
            C.TRADING_THRESHOLDS,
            has_pre_trade_thresholds=False,
            threshold_calibration_reference="RISK-CAL-2026-03 s4.2")

    def test_otr_attestation_without_a_configured_ratio_fails(self):
        self.assert_only(
            C.ORDER_TO_TRADE_RATIO,
            limits_order_to_trade_ratio=True, max_order_to_trade_ratio=None)

    def test_throttling_attestation_without_a_configured_rate_fails(self):
        self.assert_only(
            C.ORDER_FLOW_THROTTLING,
            can_throttle_order_flow=True, max_message_rate_per_sec=None)

    def test_documentation_requires_both_a_reference_and_an_owner(self):
        self.assert_only(
            C.STRATEGY_DOCUMENTATION, strategy_documentation_reference="")
        self.assert_only(C.STRATEGY_DOCUMENTATION, governance_owner="   ")


class TestNoInventedNumericCeiling(unittest.TestCase):
    """Swiss law states no message-rate cap and no order-to-trade ratio. A firm's own
    setting, whatever its magnitude, must not be scored against a fabricated ceiling."""

    def setUp(self):
        self.engine = SwissFINMAComplianceEngine()

    def test_a_rate_far_above_the_old_hard_coded_100_is_compliant(self):
        # Regression: the previous engine failed any rate > 100/s, citing FinfraG.
        for rate in (101.0, 150.0, 5000.0, 250000.0):
            with self.subTest(rate=rate):
                record = self.engine.audit_algo_system(
                    make_spec(max_message_rate_per_sec=rate))
                self.assertTrue(record.is_compliant)

    def test_a_high_order_to_trade_ratio_is_recorded_not_penalised(self):
        record = self.engine.audit_algo_system(make_spec(max_order_to_trade_ratio=900.0))
        self.assertTrue(record.is_compliant)


class TestDeaControlIsConditional(unittest.TestCase):
    """SIX Trading Rules cl. 4.3.4 and Directive 7 bind a participant that provides
    direct electronic or sponsored access. They do not reach a proprietary-only firm."""

    def setUp(self):
        self.engine = SwissFINMAComplianceEngine()

    def test_dea_control_is_not_applicable_without_dea(self):
        record = self.engine.audit_algo_system(
            make_spec(provides_direct_electronic_access=False,
                      can_delete_client_orders_on_demand=False))
        self.assertNotIn(C.DEA_ORDER_DELETION.value, record.applicable_controls)
        self.assertTrue(record.is_compliant)

    def test_dea_provider_without_deletion_capability_fails(self):
        record = self.engine.audit_algo_system(
            make_spec(provides_direct_electronic_access=True,
                      can_delete_client_orders_on_demand=False))
        self.assertFalse(record.is_compliant)
        self.assertEqual(failed_codes(record), {C.DEA_ORDER_DELETION.value})
        self.assertEqual(len(record.applicable_controls), 14)

    def test_dea_provider_with_deletion_capability_passes(self):
        record = self.engine.audit_algo_system(
            make_spec(provides_direct_electronic_access=True,
                      can_delete_client_orders_on_demand=True))
        self.assertTrue(record.is_compliant)
        self.assertIn(C.DEA_ORDER_DELETION.value, record.applicable_controls)


class TestScoring(unittest.TestCase):

    def setUp(self):
        self.engine = SwissFINMAComplianceEngine()

    def test_score_is_over_applicable_controls_only(self):
        # One failure out of 13 applicable -> 12/13. Computed from the provision count,
        # not from the engine's own expression.
        record = self.engine.audit_algo_system(make_spec(enforces_minimum_tick_size=False))
        self.assertAlmostEqual(record.finma_score_pct, 1200.0 / 13.0, places=9)

    def test_dea_changes_the_denominator_not_just_the_numerator(self):
        # Same single failure, but 14 controls apply once DEA is provided.
        record = self.engine.audit_algo_system(
            make_spec(enforces_minimum_tick_size=False,
                      provides_direct_electronic_access=True,
                      can_delete_client_orders_on_demand=True))
        self.assertAlmostEqual(record.finma_score_pct, 1300.0 / 14.0, places=9)

    def test_a_system_with_nothing_in_place_scores_zero_and_lists_every_control(self):
        empty = AlgoTradingSystemAuditSpec(
            algo_id="CH_BAD_BOT", strategy_version="0.9", governance_owner="")
        record = self.engine.audit_algo_system(empty)
        self.assertFalse(record.is_compliant)
        self.assertEqual(record.finma_score_pct, 0.0)
        self.assertEqual(failed_codes(record), {c.value for c in BASE_CONTROLS})

    def test_partial_score_is_never_reported_as_compliant(self):
        record = self.engine.audit_algo_system(make_spec(algorithms_and_controls_tested=False))
        self.assertGreater(record.finma_score_pct, 90.0)
        self.assertFalse(record.is_compliant)


class TestFindingsAreTraceable(unittest.TestCase):

    def setUp(self):
        self.engine = SwissFINMAComplianceEngine()

    def test_every_finding_carries_its_citation(self):
        empty = AlgoTradingSystemAuditSpec(
            algo_id="CH_BAD_BOT", strategy_version="0.9", governance_owner="")
        record = self.engine.audit_algo_system(empty)
        for entry in record.failed_controls:
            with self.subTest(entry=entry[:40]):
                self.assertTrue(entry.rstrip().endswith("]"))
                self.assertIn("[", entry)

    def test_citations_exist_for_every_declared_control(self):
        for control in SwissAlgoControl:
            with self.subTest(control=control):
                self.assertIn(control, CONTROL_CITATIONS)
                self.assertTrue(CONTROL_CITATIONS[control].strip())

    def test_no_citation_attributes_a_control_to_the_act_rather_than_the_ordinance(self):
        # FMIA/FinfraG itself never mentions algorithmic trading; the provision is
        # FMIO Art. 31. A citation reading "FMIA Art. 31" would be wrong.
        for control, citation in CONTROL_CITATIONS.items():
            with self.subTest(control=control):
                self.assertNotIn("FMIA Art. 31", citation)

    def test_multiple_gaps_are_all_reported(self):
        record = self.engine.audit_algo_system(
            make_spec(flags_algo_generated_orders=False,
                      enforces_minimum_tick_size=False,
                      algorithm_identifier=""))
        self.assertEqual(
            failed_codes(record),
            {C.ORDER_FLAGGING.value, C.MINIMUM_TICK_SIZE.value,
             C.ALGORITHM_IDENTIFICATION.value})

    def test_failure_is_logged_at_warning_and_success_at_info(self):
        logger_name = "swiss_finma_algorithmic_trading_expectations"
        with self.assertLogs(logger_name, level="WARNING") as captured:
            self.engine.audit_algo_system(make_spec(enforces_minimum_tick_size=False))
        self.assertIn(C.MINIMUM_TICK_SIZE.value, "".join(captured.output))
        with self.assertLogs(logger_name, level="INFO") as captured:
            self.engine.audit_algo_system(make_spec())
        self.assertNotIn("WARNING", "".join(captured.output))

    def test_audits_are_deterministic(self):
        first = self.engine.audit_algo_system(make_spec())
        second = self.engine.audit_algo_system(make_spec())
        self.assertEqual(first, second)


if __name__ == '__main__':
    unittest.main()
