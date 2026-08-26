import unittest

from japan_fsa_high_speed_trading_registration import (
    DEFAULT_DESIGNATED_VENUES,
    HST_STRATEGY_TYPES,
    JapanFsaHstComplianceEngine,
    JapanFsaHstTraderSpec,
)


def compliant_spec(**overrides) -> JapanFsaHstTraderSpec:
    """A fully compliant co-located HST order; override one field per test."""
    base = dict(
        trader_id="HFT_ALPHA_TOKYO_01",
        fsa_hst_reg_id="関東財務局長（高速）第48号",
        is_registered_with_fsa=True,
        is_algo_automated=True,
        is_colocated=True,
        latency_ms=2.0,
        order_value_jpy=50_000_000.0,
        has_kill_switch_enabled=True,
        has_resident_compliance_manager=True,
        venue="TSE",
        has_contention_free_transmission=True,
        is_hst_order_flagged=True,
        trading_strategy_type="MARKET_MAKING",
        is_foreign_entity=True,
    )
    base.update(overrides)
    return JapanFsaHstTraderSpec(**base)


class TestHighSpeedTradingClassification(unittest.TestCase):
    """FIEA art. 2(41) is conjunctive and structural -- never a latency number."""

    def setUp(self):
        self.engine = JapanFsaHstComplianceEngine()

    def test_classification_is_independent_of_latency(self):
        # Regression: an earlier revision classified an order as high-speed
        # trading only when latency_ms <= 20, a threshold that appears nowhere
        # in FIEA art. 2(41) or the Cabinet Office Order on Definitions. A slow
        # but co-located automated order is still high-speed trading, and an
        # unregistered one must still be rejected.
        for latency in (0.5, 20.0, 20.1, 250.0, 5_000.0):
            report = self.engine.audit_japan_fsa_hst_trader(
                compliant_spec(latency_ms=latency)
            )
            self.assertTrue(report.is_hst_classified, msg=f"latency={latency}")
            self.assertEqual(report.status, "FSA_HST_APPROVED", msg=f"latency={latency}")
            self.assertEqual(report.latency_ms, latency)

    def test_slow_unregistered_colocated_order_is_still_rejected(self):
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(latency_ms=900.0, is_registered_with_fsa=False, fsa_hst_reg_id="")
        )
        self.assertTrue(report.is_hst_classified)
        self.assertEqual(report.status, "REJECTED_UNREGISTERED_HST")

    def test_manual_trading_is_not_high_speed_trading(self):
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(is_algo_automated=False, is_registered_with_fsa=False,
                           fsa_hst_reg_id="")
        )
        self.assertFalse(report.is_hst_classified)
        self.assertEqual(report.status, "NOT_HIGH_SPEED_TRADING")

    def test_non_colocated_order_is_not_high_speed_trading(self):
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(is_colocated=False, is_registered_with_fsa=False,
                           fsa_hst_reg_id="")
        )
        self.assertFalse(report.is_hst_classified)
        self.assertEqual(report.status, "NOT_HIGH_SPEED_TRADING")

    def test_contended_transmission_is_not_high_speed_trading(self):
        # Cabinet Office Order on Definitions art. 26(2) requires BOTH the
        # location leg and the contention-avoidance leg.
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(has_contention_free_transmission=False,
                           is_registered_with_fsa=False, fsa_hst_reg_id="")
        )
        self.assertFalse(report.is_hst_classified)

    def test_undesignated_venue_is_outside_the_definition(self):
        # Transmission must be to a venue designated under Cabinet Office Order
        # on Definitions art. 26(1). Nothing else can be high-speed trading.
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(venue="LSE", is_registered_with_fsa=False, fsa_hst_reg_id="")
        )
        self.assertFalse(report.is_hst_classified)
        self.assertEqual(report.status, "NOT_HIGH_SPEED_TRADING")

    def test_every_default_designated_venue_classifies(self):
        for venue in DEFAULT_DESIGNATED_VENUES:
            report = self.engine.audit_japan_fsa_hst_trader(compliant_spec(venue=venue))
            self.assertTrue(report.is_hst_classified, msg=venue)

    def test_designated_venue_list_is_overridable(self):
        engine = JapanFsaHstComplianceEngine(designated_venues=("TSE",))
        self.assertFalse(
            engine.audit_japan_fsa_hst_trader(compliant_spec(venue="OSE")).is_hst_classified
        )
        self.assertTrue(
            engine.audit_japan_fsa_hst_trader(compliant_spec(venue="TSE")).is_hst_classified
        )

    def test_venue_code_normalisation(self):
        for venue in ("tse", " TSE ", "sbi-japannext", "SBI japannext"):
            report = self.engine.audit_japan_fsa_hst_trader(compliant_spec(venue=venue))
            self.assertTrue(report.is_hst_classified, msg=venue)

    def test_missing_inputs_resolve_conservatively_with_a_warning(self):
        # An absent venue or contention flag must never make an order look out
        # of scope; it is assumed in scope and a warning is recorded.
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(venue="", has_contention_free_transmission=None)
        )
        self.assertTrue(report.is_hst_classified)
        self.assertEqual(len(report.warnings), 2)
        self.assertTrue(any("Venue not supplied" in w for w in report.warnings))


class TestRegistrationRoute(unittest.TestCase):
    def setUp(self):
        self.engine = JapanFsaHstComplianceEngine()

    def test_registered_hst_is_approved(self):
        report = self.engine.audit_japan_fsa_hst_trader(compliant_spec())
        self.assertEqual(report.status, "FSA_HST_APPROVED")
        self.assertTrue(report.is_hst_classified)
        self.assertTrue(report.is_fsa_registered)
        self.assertTrue(report.is_kill_switch_active)
        self.assertTrue(report.is_pre_trade_limit_valid)
        self.assertEqual(report.breaches, ())
        self.assertEqual(report.warnings, ())
        self.assertEqual(report.registration_route, "HST_REGISTRATION")

    def test_unregistered_hst_is_rejected(self):
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(trader_id="ROGUE_HFT", is_registered_with_fsa=False,
                           fsa_hst_reg_id="")
        )
        self.assertEqual(report.status, "REJECTED_UNREGISTERED_HST")
        self.assertTrue(report.is_hst_classified)
        self.assertFalse(report.is_fsa_registered)

    def test_registration_claimed_without_an_id_is_rejected(self):
        report = self.engine.audit_japan_fsa_hst_trader(compliant_spec(fsa_hst_reg_id="   "))
        self.assertEqual(report.status, "REJECTED_MISSING_REGISTRATION_ID")

    def test_fibo_needs_no_hst_registration_only_a_29_2_notification(self):
        # A registered financial instruments business operator does not appear
        # on the high-speed trader register; it notifies under FIEA art.
        # 29-2(1)(vii). Demanding an HST number of it is a false rejection.
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(
                trader_id="JP_SECURITIES_CO",
                is_registered_with_fsa=False,
                fsa_hst_reg_id="",
                is_financial_instruments_business_operator=True,
                has_filed_fiea_29_2_notification=True,
                is_foreign_entity=False,
            )
        )
        self.assertEqual(report.status, "FSA_HST_APPROVED")
        self.assertEqual(report.registration_route, "FIEA_29_2_NOTIFICATION")
        self.assertTrue(report.is_fsa_registered)

    def test_fibo_without_the_29_2_notification_is_rejected(self):
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(
                is_registered_with_fsa=False,
                fsa_hst_reg_id="",
                is_financial_instruments_business_operator=True,
                has_filed_fiea_29_2_notification=False,
                is_foreign_entity=False,
            )
        )
        self.assertEqual(report.status, "REJECTED_UNNOTIFIED_FIBO_HST")

    def test_unrecognised_registration_id_warns_but_does_not_reject(self):
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(fsa_hst_reg_id="KLFB_No_3120")
        )
        self.assertEqual(report.status, "FSA_HST_APPROVED")
        self.assertTrue(any("does not match" in w for w in report.warnings))

    def test_parse_registration_number_accepts_issued_and_ascii_forms(self):
        parse = JapanFsaHstComplianceEngine.parse_hst_registration_number
        self.assertEqual(parse("関東財務局長（高速）第48号"), 48)
        self.assertEqual(parse("関東財務局長(高速)第1号"), 1)
        self.assertEqual(parse("Kanto LFB (HST) No. 90"), 90)
        self.assertEqual(parse("kanto high-speed no.7"), 7)
        self.assertIsNone(parse("KLFB_No_3120"))
        self.assertIsNone(parse(""))


class TestConductObligations(unittest.TestCase):
    def setUp(self):
        self.engine = JapanFsaHstComplianceEngine()

    def test_missing_kill_switch_is_rejected(self):
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(has_kill_switch_enabled=False)
        )
        self.assertEqual(report.status, "REJECTED_MISSING_KILL_SWITCH")
        self.assertFalse(report.is_kill_switch_active)

    def test_unflagged_hst_order_is_rejected(self):
        # TSE Business Regulations art. 14(1)(7): the order must carry the
        # high-speed trading indicator.
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(is_hst_order_flagged=False)
        )
        self.assertEqual(report.status, "REJECTED_MISSING_HST_ORDER_FLAG")
        self.assertFalse(report.is_order_flagged_as_hst)

    def test_missing_or_unknown_strategy_type_is_rejected(self):
        for strategy in ("", "SCALPING", "latency arbitrage"):
            report = self.engine.audit_japan_fsa_hst_trader(
                compliant_spec(trading_strategy_type=strategy)
            )
            self.assertEqual(
                report.status, "REJECTED_INVALID_TRADING_STRATEGY", msg=repr(strategy)
            )

    def test_all_four_strategy_types_are_accepted(self):
        for strategy in HST_STRATEGY_TYPES:
            report = self.engine.audit_japan_fsa_hst_trader(
                compliant_spec(trading_strategy_type=strategy)
            )
            self.assertEqual(report.status, "FSA_HST_APPROVED", msg=strategy)

    def test_strategy_type_is_normalised(self):
        for strategy in ("market_making", "Market Making", "market-making"):
            report = self.engine.audit_japan_fsa_hst_trader(
                compliant_spec(trading_strategy_type=strategy)
            )
            self.assertEqual(report.status, "FSA_HST_APPROVED", msg=strategy)

    def test_strategy_absent_from_the_business_method_statement_is_rejected(self):
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(
                trading_strategy_type="DIRECTIONAL",
                notified_strategy_types=("MARKET_MAKING", "ARBITRAGE"),
            )
        )
        self.assertEqual(report.status, "REJECTED_INVALID_TRADING_STRATEGY")
        self.assertFalse(report.is_strategy_type_valid)

    def test_strategy_present_in_the_business_method_statement_is_accepted(self):
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(
                trading_strategy_type="ARBITRAGE",
                notified_strategy_types=("MARKET_MAKING", "ARBITRAGE"),
            )
        )
        self.assertEqual(report.status, "FSA_HST_APPROVED")

    def test_foreign_entity_without_a_japan_representative_is_rejected(self):
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(is_foreign_entity=True, has_resident_compliance_manager=False)
        )
        self.assertEqual(report.status, "REJECTED_NO_JAPAN_REPRESENTATIVE")
        self.assertFalse(report.is_japan_representative_valid)

    def test_unknown_domicile_is_treated_as_foreign(self):
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(is_foreign_entity=None, has_resident_compliance_manager=False)
        )
        self.assertEqual(report.status, "REJECTED_NO_JAPAN_REPRESENTATIVE")

    def test_domestic_entity_needs_no_japan_representative(self):
        # FIEA art. 66-53(5)(c)/(6)(b) is a refusal ground for foreign
        # applicants only.
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(is_foreign_entity=False, has_resident_compliance_manager=False)
        )
        self.assertEqual(report.status, "FSA_HST_APPROVED")
        self.assertTrue(report.is_japan_representative_valid)

    def test_conduct_checks_do_not_apply_to_non_hst_orders(self):
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(
                is_colocated=False,
                is_registered_with_fsa=False,
                fsa_hst_reg_id="",
                has_kill_switch_enabled=False,
                is_hst_order_flagged=False,
                trading_strategy_type="",
                has_resident_compliance_manager=False,
            )
        )
        self.assertEqual(report.status, "NOT_HIGH_SPEED_TRADING")
        self.assertEqual(report.breaches, ())


class TestPreTradeValueLimits(unittest.TestCase):
    def test_hard_limit_breach_is_rejected(self):
        engine = JapanFsaHstComplianceEngine(max_order_value_limit_jpy=100_000_000.0)
        report = engine.audit_japan_fsa_hst_trader(
            compliant_spec(order_value_jpy=100_000_001.0)
        )
        self.assertEqual(report.status, "REJECTED_PRE_TRADE_LIMIT_EXCEEDED")
        self.assertFalse(report.is_pre_trade_limit_valid)

    def test_order_exactly_at_the_hard_limit_is_accepted(self):
        engine = JapanFsaHstComplianceEngine(max_order_value_limit_jpy=100_000_000.0)
        report = engine.audit_japan_fsa_hst_trader(
            compliant_spec(order_value_jpy=100_000_000.0)
        )
        self.assertEqual(report.status, "FSA_HST_APPROVED")
        self.assertTrue(report.is_pre_trade_limit_valid)

    def test_soft_limit_breach_warns_and_lets_the_order_through(self):
        engine = JapanFsaHstComplianceEngine(
            max_order_value_limit_jpy=100_000_000.0,
            soft_order_value_limit_jpy=30_000_000.0,
        )
        report = engine.audit_japan_fsa_hst_trader(compliant_spec(order_value_jpy=50_000_000.0))
        self.assertEqual(report.status, "FSA_HST_APPROVED")
        self.assertTrue(report.is_pre_trade_limit_valid)
        self.assertTrue(any("soft pre-trade limit" in w for w in report.warnings))

    def test_hard_limit_applies_to_non_hst_orders_too(self):
        # The value limit is a house control, not an FSA rule, so it does not
        # switch off when the order falls outside FIEA art. 2(41).
        engine = JapanFsaHstComplianceEngine(max_order_value_limit_jpy=10_000_000.0)
        report = engine.audit_japan_fsa_hst_trader(
            compliant_spec(is_colocated=False, order_value_jpy=20_000_000.0)
        )
        self.assertFalse(report.is_hst_classified)
        self.assertEqual(report.status, "REJECTED_PRE_TRADE_LIMIT_EXCEEDED")

    def test_soft_limit_above_hard_limit_is_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            JapanFsaHstComplianceEngine(
                max_order_value_limit_jpy=10_000_000.0,
                soft_order_value_limit_jpy=20_000_000.0,
            )

    def test_non_positive_limits_are_rejected_at_construction(self):
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            with self.assertRaises(ValueError, msg=repr(bad)):
                JapanFsaHstComplianceEngine(max_order_value_limit_jpy=bad)

    def test_empty_designated_venue_list_is_rejected(self):
        with self.assertRaises(ValueError):
            JapanFsaHstComplianceEngine(designated_venues=())


class TestMultipleBreachesAndReportIntegrity(unittest.TestCase):
    def setUp(self):
        self.engine = JapanFsaHstComplianceEngine()

    def test_all_breaches_are_reported_not_just_the_first(self):
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(
                is_registered_with_fsa=False,
                fsa_hst_reg_id="",
                has_kill_switch_enabled=False,
                is_hst_order_flagged=False,
                has_resident_compliance_manager=False,
                order_value_jpy=500_000_000.0,
            )
        )
        self.assertEqual(report.status, "REJECTED_UNREGISTERED_HST")
        self.assertEqual(
            set(report.breaches),
            {
                "REJECTED_UNREGISTERED_HST",
                "REJECTED_NO_JAPAN_REPRESENTATIVE",
                "REJECTED_MISSING_KILL_SWITCH",
                "REJECTED_MISSING_HST_ORDER_FLAG",
                "REJECTED_PRE_TRADE_LIMIT_EXCEEDED",
            },
        )

    def test_limit_flag_is_evaluated_on_every_rejection_path(self):
        # Regression: an earlier revision hard-coded is_pre_trade_limit_valid to
        # True on the unregistered and kill-switch paths, so the audit trail
        # asserted a check that had never run.
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(
                is_registered_with_fsa=False,
                fsa_hst_reg_id="",
                order_value_jpy=900_000_000.0,
            )
        )
        self.assertEqual(report.status, "REJECTED_UNREGISTERED_HST")
        self.assertFalse(report.is_pre_trade_limit_valid)

    def test_audit_notes_name_the_status_and_every_breach(self):
        report = self.engine.audit_japan_fsa_hst_trader(
            compliant_spec(has_kill_switch_enabled=False, is_hst_order_flagged=False)
        )
        self.assertIn("REJECTED_MISSING_KILL_SWITCH", report.audit_notes)
        self.assertIn("REJECTED_MISSING_HST_ORDER_FLAG", report.audit_notes)
        self.assertIn("HFT_ALPHA_TOKYO_01", report.audit_notes)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = JapanFsaHstComplianceEngine()

    def test_non_finite_or_non_positive_order_value_is_rejected(self):
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.engine.audit_japan_fsa_hst_trader(compliant_spec(order_value_jpy=bad))

    def test_non_finite_or_negative_latency_is_rejected(self):
        for bad in (-1.0, float("nan"), float("inf")):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.engine.audit_japan_fsa_hst_trader(compliant_spec(latency_ms=bad))

    def test_blank_trader_id_is_rejected(self):
        for bad in ("", "   "):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.engine.audit_japan_fsa_hst_trader(compliant_spec(trader_id=bad))

    def test_non_string_fields_are_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.audit_japan_fsa_hst_trader(compliant_spec(fsa_hst_reg_id=48))
        with self.assertRaises(TypeError):
            self.engine.audit_japan_fsa_hst_trader(compliant_spec(venue=None))
        with self.assertRaises(TypeError):
            self.engine.audit_japan_fsa_hst_trader(compliant_spec(trading_strategy_type=None))

    def test_boolean_order_value_is_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.audit_japan_fsa_hst_trader(compliant_spec(order_value_jpy=True))

    def test_bare_string_notified_strategy_types_is_rejected(self):
        # 'MARKET_MAKING' would otherwise iterate character by character and
        # silently fail every membership test.
        with self.assertRaises(TypeError):
            self.engine.audit_japan_fsa_hst_trader(
                compliant_spec(notified_strategy_types="MARKET_MAKING")
            )

    def test_wrong_spec_type_is_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.audit_japan_fsa_hst_trader({"trader_id": "X"})

    def test_classify_guards_its_own_inputs(self):
        # classify_high_speed_trading is public and may be called without a full
        # audit, so it must raise TypeError rather than leak an AttributeError.
        with self.assertRaises(TypeError):
            self.engine.classify_high_speed_trading(compliant_spec(venue=None))
        with self.assertRaises(TypeError):
            self.engine.classify_high_speed_trading({"venue": "TSE"})


if __name__ == "__main__":
    unittest.main()
