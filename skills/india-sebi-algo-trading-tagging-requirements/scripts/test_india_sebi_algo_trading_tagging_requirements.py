"""Behavioural tests for the SEBI algo tagging / OTR compliance gate.

Expected OTR values are derived by hand from the message and trade counts, not
by re-running the implementation's own expression.
"""

import unittest

from india_sebi_algo_trading_tagging_requirements import (
    CATEGORY_CLI,
    CATEGORY_PRO,
    DEFAULT_THRESHOLD_OPS,
    EXCHANGE_BSE,
    EXCHANGE_NSE,
    ORDER_TYPE_IOC,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_MARKET,
    OTR_COOLING_OFF_LEVEL_REACHED,
    OTR_COOLING_OFF_TRIGGERED,
    OTR_NORMAL,
    OTR_PENALTY_SLAB,
    OTR_UNDEFINED_NO_TRADES,
    SEGMENT_CM,
    SEGMENT_COM,
    SEGMENT_FO,
    SOURCE_BROKER_ALGO,
    SOURCE_CLIENT_API,
    SOURCE_DMA,
    SOURCE_IBT_STWT,
    SOURCE_VENDOR_API,
    STATUS_APPROVED,
    STATUS_INVALID_CATEGORY,
    STATUS_MARKET_ORDER,
    STATUS_NNF_NOT_ALGO,
    STATUS_OPS_BREACH,
    STATUS_OUT_OF_SCOPE_DMA,
    STATUS_RESTRICTED_ORDER_TYPE,
    STATUS_STATIC_IP,
    STATUS_UNREGISTERED,
    STATUS_UNTAGGED,
    TAG_GENERIC,
    TAG_REGISTERED,
    V_INVALID_CATEGORY,
    V_MARKET_ORDER,
    V_NNF_MALFORMED,
    V_NNF_NOT_ALGO,
    V_OPS_AT_BOUNDARY,
    V_OPS_BREACH,
    V_OTR_COOLING_OFF_LEVEL,
    V_OTR_COOLING_OFF_TRIGGERED,
    V_OTR_SLAB,
    V_OTR_UNDEFINED,
    V_STATIC_IP,
    V_UNREGISTERED,
    V_UNTAGGED,
    SebiAlgoOrderPayload,
    SebiAlgoTaggingEngine,
    SebiOtrMetrics,
)


def make_payload(**overrides):
    """A compliant registered-algo order; override one fact at a time."""
    defaults = dict(
        algo_id="NSE_ALGO_99812",
        client_category=CATEGORY_PRO,
        symbol="RELIANCE",
        exchange=EXCHANGE_NSE,
        side="BUY",
        price=2500.0,
        quantity=100,
        algo_tag_kind=TAG_REGISTERED,
        segment=SEGMENT_CM,
        order_source=SOURCE_CLIENT_API,
        order_type=ORDER_TYPE_LIMIT,
        is_registered_with_exchange=True,
        orders_per_second_ops=1.0,
        static_ip_whitelisted=True,
    )
    defaults.update(overrides)
    return SebiAlgoOrderPayload(**defaults)


NORMAL_OTR = SebiOtrMetrics(total_order_messages=200, total_executed_trades=5)
"""200 / 5 = 40.0 -- comfortably below the 500 slab floor."""


class BaseEngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = SebiAlgoTaggingEngine()


# ---------------------------------------------------------------------------
# Approval path
# ---------------------------------------------------------------------------
class TestApproval(BaseEngineTest):
    def test_compliant_registered_order_is_approved(self):
        report = self.engine.audit_sebi_algo_order(make_payload(), NORMAL_OTR)

        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertFalse(report.blocks_order)
        self.assertEqual(report.violations, ())
        self.assertEqual(report.algo_id, "NSE_ALGO_99812")
        self.assertEqual(report.client_category, CATEGORY_PRO)
        self.assertEqual(report.calculated_otr_ratio, 40.0)
        self.assertEqual(report.otr_status, OTR_NORMAL)
        self.assertTrue(report.is_algo_id_valid)
        self.assertTrue(report.is_category_valid)

    def test_compliant_generic_tagged_order_below_threshold_is_approved(self):
        payload = make_payload(
            algo_id="NSE_GENERIC_ALGO",
            algo_tag_kind=TAG_GENERIC,
            is_registered_with_exchange=False,
            orders_per_second_ops=9.0,
            client_category=CATEGORY_CLI,
        )
        report = self.engine.audit_sebi_algo_order(payload, NORMAL_OTR)

        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(report.is_ops_within_threshold)
        self.assertEqual(report.violations, ())

    def test_fields_are_normalised_to_upper_case(self):
        payload = make_payload(
            exchange="bse", segment="fo", client_category="cli", order_type="limit",
            algo_tag_kind="registered", order_source="client_api", side="buy",
            algo_id="  NSE_ALGO_1  ",
        )
        report = self.engine.audit_sebi_algo_order(payload, NORMAL_OTR)

        self.assertEqual(report.exchange, EXCHANGE_BSE)
        self.assertEqual(report.segment, SEGMENT_FO)
        self.assertEqual(report.client_category, CATEGORY_CLI)
        self.assertEqual(report.algo_id, "NSE_ALGO_1")
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_rejected_order_still_reports_the_real_otr(self):
        """A blocked order whose report zeroes the OTR no longer says what was
        stopped. 12,000 / 4 = 3,000."""
        metrics = SebiOtrMetrics(
            total_order_messages=12_000,
            total_executed_trades=4,
            prior_cooling_off_instances_30d=0,
        )
        report = self.engine.audit_sebi_algo_order(make_payload(algo_id=""), metrics)

        self.assertEqual(report.status, STATUS_UNTAGGED)
        self.assertEqual(report.calculated_otr_ratio, 3000.0)
        self.assertEqual(report.otr_status, OTR_COOLING_OFF_LEVEL_REACHED)
        self.assertIn(V_UNTAGGED, report.violations)
        self.assertIn(V_OTR_COOLING_OFF_LEVEL, report.violations)


# ---------------------------------------------------------------------------
# Tagging
# ---------------------------------------------------------------------------
class TestTagging(BaseEngineTest):
    def test_untagged_order_is_rejected(self):
        report = self.engine.audit_sebi_algo_order(make_payload(algo_id=""), NORMAL_OTR)

        self.assertEqual(report.status, STATUS_UNTAGGED)
        self.assertTrue(report.blocks_order)
        self.assertFalse(report.is_algo_id_valid)
        self.assertIn(V_UNTAGGED, report.violations)

    def test_whitespace_only_algo_id_is_untagged(self):
        report = self.engine.audit_sebi_algo_order(
            make_payload(algo_id="   "), NORMAL_OTR
        )
        self.assertEqual(report.status, STATUS_UNTAGGED)

    def test_untagged_order_with_valid_category_still_records_category_as_valid(self):
        """Regression: the reject path used to hard-code is_category_valid=False,
        filing a false statement about a category that was in fact correct."""
        report = self.engine.audit_sebi_algo_order(
            make_payload(algo_id="", client_category=CATEGORY_PRO), NORMAL_OTR
        )
        self.assertEqual(report.status, STATUS_UNTAGGED)
        self.assertTrue(report.is_category_valid)
        self.assertNotIn(V_INVALID_CATEGORY, report.violations)

    def test_registered_tag_without_exchange_registration_is_rejected(self):
        report = self.engine.audit_sebi_algo_order(
            make_payload(is_registered_with_exchange=False), NORMAL_OTR
        )
        self.assertEqual(report.status, STATUS_UNREGISTERED)
        self.assertIn(V_UNREGISTERED, report.violations)

    def test_unregistered_reject_path_evaluates_category_rather_than_assuming_it(self):
        """Regression: the reject path used to hard-code is_category_valid=True
        without ever looking at the category."""
        report = self.engine.audit_sebi_algo_order(
            make_payload(is_registered_with_exchange=False, client_category="RETAIL"),
            NORMAL_OTR,
        )
        self.assertEqual(report.status, STATUS_UNREGISTERED)
        self.assertFalse(report.is_category_valid)
        self.assertIn(V_INVALID_CATEGORY, report.violations)

    def test_generic_tag_does_not_require_exchange_registration(self):
        payload = make_payload(
            algo_tag_kind=TAG_GENERIC, is_registered_with_exchange=False
        )
        report = self.engine.audit_sebi_algo_order(payload, NORMAL_OTR)
        self.assertEqual(report.status, STATUS_APPROVED)


class TestNnfTagging(BaseEngineTest):
    def test_nnf_id_with_algo_digit_is_accepted(self):
        # 15 digits; 13th digit (index 12) is "4" -> an algo order.
        report = self.engine.audit_sebi_algo_order(
            make_payload(nnf_id="444444444444" + "4" + "12"), NORMAL_OTR
        )
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_nnf_id_with_non_algo_digit_is_rejected(self):
        report = self.engine.audit_sebi_algo_order(
            make_payload(nnf_id="444444444444" + "1" + "12"), NORMAL_OTR
        )
        self.assertEqual(report.status, STATUS_NNF_NOT_ALGO)
        self.assertIn(V_NNF_NOT_ALGO, report.violations)

    def test_malformed_nnf_id_is_rejected(self):
        for bad in ("12345", "44444444444441", "44444444444440X", "4444444444444XY"):
            with self.subTest(nnf_id=bad):
                report = self.engine.audit_sebi_algo_order(
                    make_payload(nnf_id=bad), NORMAL_OTR
                )
                self.assertEqual(report.status, STATUS_NNF_NOT_ALGO)
                self.assertIn(V_NNF_MALFORMED, report.violations)

    def test_non_string_nnf_id_is_malformed_not_an_attribute_error(self):
        report = self.engine.audit_sebi_algo_order(
            make_payload(nnf_id=444444444444412), NORMAL_OTR
        )
        self.assertEqual(report.status, STATUS_NNF_NOT_ALGO)
        self.assertIn(V_NNF_MALFORMED, report.violations)

    def test_absent_nnf_id_makes_no_claim(self):
        report = self.engine.audit_sebi_algo_order(
            make_payload(nnf_id=None), NORMAL_OTR
        )
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertNotIn(V_NNF_NOT_ALGO, report.violations)
        self.assertNotIn(V_NNF_MALFORMED, report.violations)

    def test_custom_algo_digit_set_is_honoured(self):
        engine = SebiAlgoTaggingEngine(algo_nnf_digits=frozenset({"7"}))
        approved = engine.audit_sebi_algo_order(
            make_payload(nnf_id="444444444444" + "7" + "12"), NORMAL_OTR
        )
        rejected = engine.audit_sebi_algo_order(
            make_payload(nnf_id="444444444444" + "4" + "12"), NORMAL_OTR
        )
        self.assertEqual(approved.status, STATUS_APPROVED)
        self.assertEqual(rejected.status, STATUS_NNF_NOT_ALGO)


# ---------------------------------------------------------------------------
# Threshold Order Per Second
# ---------------------------------------------------------------------------
class TestOpsThreshold(BaseEngineTest):
    def test_generic_tag_above_threshold_is_rejected(self):
        payload = make_payload(algo_tag_kind=TAG_GENERIC, orders_per_second_ops=10.5)
        report = self.engine.audit_sebi_algo_order(payload, NORMAL_OTR)

        self.assertEqual(report.status, STATUS_OPS_BREACH)
        self.assertFalse(report.is_ops_within_threshold)
        self.assertIn(V_OPS_BREACH, report.violations)
        self.assertEqual(report.threshold_ops, DEFAULT_THRESHOLD_OPS)

    def test_generic_tag_just_below_threshold_is_approved(self):
        payload = make_payload(algo_tag_kind=TAG_GENERIC, orders_per_second_ops=9.99)
        report = self.engine.audit_sebi_algo_order(payload, NORMAL_OTR)
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertNotIn(V_OPS_AT_BOUNDARY, report.violations)

    def test_generic_tag_exactly_at_threshold_is_flagged_but_not_blocked(self):
        """The sources differ at exactly 10 OPS; the ambiguity is surfaced in the
        audit record rather than silently resolved."""
        payload = make_payload(algo_tag_kind=TAG_GENERIC, orders_per_second_ops=10.0)
        report = self.engine.audit_sebi_algo_order(payload, NORMAL_OTR)

        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertFalse(report.blocks_order)
        self.assertIn(V_OPS_AT_BOUNDARY, report.violations)
        self.assertTrue(report.is_ops_within_threshold)

    def test_registered_algo_is_not_gated_by_the_registration_threshold(self):
        """The threshold is what forces registration; a registered algo has
        already cleared it (NSE/INVG/67858 Annexure B heading and C.1)."""
        payload = make_payload(algo_tag_kind=TAG_REGISTERED, orders_per_second_ops=250.0)
        report = self.engine.audit_sebi_algo_order(payload, NORMAL_OTR)

        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(report.is_ops_within_threshold)

    def test_broker_may_configure_a_lower_threshold(self):
        engine = SebiAlgoTaggingEngine(threshold_ops=5.0)
        payload = make_payload(algo_tag_kind=TAG_GENERIC, orders_per_second_ops=7.0)
        report = engine.audit_sebi_algo_order(payload, NORMAL_OTR)

        self.assertEqual(report.status, STATUS_OPS_BREACH)
        self.assertEqual(report.threshold_ops, 5.0)


# ---------------------------------------------------------------------------
# Order type restrictions
# ---------------------------------------------------------------------------
class TestOrderTypeRestrictions(BaseEngineTest):
    def test_algo_market_order_is_rejected(self):
        report = self.engine.audit_sebi_algo_order(
            make_payload(order_type=ORDER_TYPE_MARKET), NORMAL_OTR
        )
        self.assertEqual(report.status, STATUS_MARKET_ORDER)
        self.assertFalse(report.is_order_type_permitted)
        self.assertIn(V_MARKET_ORDER, report.violations)

    def test_ioc_is_rejected_in_the_commodity_segment_only(self):
        commodity = self.engine.audit_sebi_algo_order(
            make_payload(segment=SEGMENT_COM, order_type=ORDER_TYPE_IOC), NORMAL_OTR
        )
        equity = self.engine.audit_sebi_algo_order(
            make_payload(segment=SEGMENT_FO, order_type=ORDER_TYPE_IOC), NORMAL_OTR
        )

        self.assertEqual(commodity.status, STATUS_RESTRICTED_ORDER_TYPE)
        self.assertEqual(equity.status, STATUS_APPROVED)


# ---------------------------------------------------------------------------
# Static IP
# ---------------------------------------------------------------------------
class TestStaticIp(BaseEngineTest):
    def test_api_sources_require_a_whitelisted_static_ip(self):
        for source in (SOURCE_CLIENT_API, SOURCE_VENDOR_API, SOURCE_BROKER_ALGO):
            with self.subTest(source=source):
                report = self.engine.audit_sebi_algo_order(
                    make_payload(order_source=source, static_ip_whitelisted=False),
                    NORMAL_OTR,
                )
                self.assertEqual(report.status, STATUS_STATIC_IP)
                self.assertFalse(report.is_static_ip_compliant)
                self.assertIn(V_STATIC_IP, report.violations)

    def test_member_frontend_does_not_carry_the_client_static_ip_gate(self):
        """NSE retail-algo FAQ (3 Nov 2025) Q3/Q6: a client static IP is required
        only for a tech-savvy investor using an API."""
        report = self.engine.audit_sebi_algo_order(
            make_payload(order_source=SOURCE_IBT_STWT, static_ip_whitelisted=False),
            NORMAL_OTR,
        )
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(report.is_static_ip_compliant)


# ---------------------------------------------------------------------------
# DMA carve-out
# ---------------------------------------------------------------------------
class TestDmaCarveOut(BaseEngineTest):
    def test_dma_orders_are_reported_out_of_scope_not_approved(self):
        report = self.engine.audit_sebi_algo_order(
            make_payload(order_source=SOURCE_DMA), NORMAL_OTR
        )
        self.assertEqual(report.status, STATUS_OUT_OF_SCOPE_DMA)
        self.assertNotEqual(report.status, STATUS_APPROVED)
        self.assertFalse(report.blocks_order)
        self.assertIn("Annexure J.1", report.audit_notes)

    def test_dma_carve_out_does_not_launder_an_untagged_order_into_an_approval(self):
        report = self.engine.audit_sebi_algo_order(
            make_payload(order_source=SOURCE_DMA, algo_id=""), NORMAL_OTR
        )
        self.assertEqual(report.status, STATUS_OUT_OF_SCOPE_DMA)
        self.assertFalse(report.is_algo_id_valid)

    def test_dma_report_states_the_real_otr_not_a_false_no_trades(self):
        """The OTR framework has no DMA carve-out, so reporting the DMA path as
        OTR_UNDEFINED_NO_TRADES when trades occurred would be a false record."""
        report = self.engine.audit_sebi_algo_order(
            make_payload(order_source=SOURCE_DMA), NORMAL_OTR
        )
        self.assertEqual(report.calculated_otr_ratio, 40.0)
        self.assertEqual(report.otr_status, OTR_NORMAL)

    def test_dma_report_still_counts_a_cooling_off_instance(self):
        metrics = SebiOtrMetrics(
            total_order_messages=25_000,
            total_executed_trades=10,
            prior_cooling_off_instances_30d=2,
        )
        report = self.engine.audit_sebi_algo_order(
            make_payload(order_source=SOURCE_DMA), metrics
        )
        self.assertEqual(report.otr_status, OTR_COOLING_OFF_TRIGGERED)
        self.assertEqual(report.otr_cooling_off_instances_30d, 3)


# ---------------------------------------------------------------------------
# Client category
# ---------------------------------------------------------------------------
class TestClientCategory(BaseEngineTest):
    def test_invalid_category_is_rejected(self):
        for bad in ("RETAIL", "", "PROP", "CLIENT"):
            with self.subTest(category=bad):
                report = self.engine.audit_sebi_algo_order(
                    make_payload(client_category=bad), NORMAL_OTR
                )
                self.assertEqual(report.status, STATUS_INVALID_CATEGORY)
                self.assertFalse(report.is_category_valid)

    def test_both_valid_categories_are_accepted(self):
        for category in (CATEGORY_PRO, CATEGORY_CLI):
            with self.subTest(category=category):
                report = self.engine.audit_sebi_algo_order(
                    make_payload(client_category=category), NORMAL_OTR
                )
                self.assertEqual(report.status, STATUS_APPROVED)


# ---------------------------------------------------------------------------
# Order-to-Trade Ratio
# ---------------------------------------------------------------------------
class TestOtr(BaseEngineTest):
    def test_otr_is_messages_over_trades(self):
        # 900 / 3 = 300.0
        otr = self.engine.calculate_otr(
            SebiOtrMetrics(total_order_messages=900, total_executed_trades=3)
        )
        self.assertEqual(otr, 300.0)

    def test_exempt_messages_are_excluded_from_the_ratio(self):
        """+/-0.75%-of-LTP orders, DMM market-making orders and exchange rejects
        do not count (SEBI 2018 para 14; SEBI 4 Feb 2026). (1000 - 400) / 4 = 150."""
        otr = self.engine.calculate_otr(
            SebiOtrMetrics(
                total_order_messages=1000,
                total_executed_trades=4,
                exempt_order_messages=400,
            )
        )
        self.assertEqual(otr, 150.0)

    def test_zero_trades_yields_none_not_the_message_count(self):
        """Regression: returning the message count as the ratio placed the worst
        possible day (400 messages, zero fills) below the 500 slab floor."""
        metrics = SebiOtrMetrics(total_order_messages=400, total_executed_trades=0)

        self.assertIsNone(self.engine.calculate_otr(metrics))

        report = self.engine.audit_sebi_algo_order(make_payload(), metrics)
        self.assertIsNone(report.calculated_otr_ratio)
        self.assertEqual(report.otr_status, OTR_UNDEFINED_NO_TRADES)
        self.assertIn(V_OTR_UNDEFINED, report.violations)

    def test_no_messages_and_no_trades_is_not_flagged(self):
        metrics = SebiOtrMetrics(total_order_messages=0, total_executed_trades=0)
        report = self.engine.audit_sebi_algo_order(make_payload(), metrics)

        self.assertEqual(report.otr_status, OTR_UNDEFINED_NO_TRADES)
        self.assertNotIn(V_OTR_UNDEFINED, report.violations)
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_penalty_slab_floor_is_inclusive(self):
        # 2500 / 5 = 500.0 exactly.
        metrics = SebiOtrMetrics(total_order_messages=2500, total_executed_trades=5)
        report = self.engine.audit_sebi_algo_order(make_payload(), metrics)

        self.assertEqual(report.calculated_otr_ratio, 500.0)
        self.assertEqual(report.otr_status, OTR_PENALTY_SLAB)
        self.assertIn(V_OTR_SLAB, report.violations)

    def test_just_below_the_slab_floor_is_normal(self):
        # 2495 / 5 = 499.0
        metrics = SebiOtrMetrics(total_order_messages=2495, total_executed_trades=5)
        report = self.engine.audit_sebi_algo_order(make_payload(), metrics)

        self.assertEqual(report.calculated_otr_ratio, 499.0)
        self.assertEqual(report.otr_status, OTR_NORMAL)

    def test_a_penalty_slab_breach_does_not_block_the_order(self):
        """The OTR framework is an economic disincentive levied on the member,
        not a per-order pre-trade reject."""
        metrics = SebiOtrMetrics(total_order_messages=2500, total_executed_trades=5)
        report = self.engine.audit_sebi_algo_order(make_payload(), metrics)

        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertFalse(report.blocks_order)


class TestOtrCoolingOff(BaseEngineTest):
    #  25,000 / 10 = 2,500 -- above the 2,000 cooling-off level.
    BREACH = dict(total_order_messages=25_000, total_executed_trades=10)

    def test_first_instance_at_the_level_does_not_trigger_the_suspension(self):
        """Regression: a single day at 2,000 used to be reported as a cooling-off
        penalty. SEBI/HO/MRD1/DSAP/CIR/P/2020/107 requires the *third* instance
        in the rolling 30-day window."""
        metrics = SebiOtrMetrics(**self.BREACH, prior_cooling_off_instances_30d=0)
        report = self.engine.audit_sebi_algo_order(make_payload(), metrics)

        self.assertEqual(report.calculated_otr_ratio, 2500.0)
        self.assertEqual(report.otr_status, OTR_COOLING_OFF_LEVEL_REACHED)
        self.assertEqual(report.otr_cooling_off_instances_30d, 1)
        self.assertNotIn(V_OTR_COOLING_OFF_TRIGGERED, report.violations)
        self.assertIn(V_OTR_COOLING_OFF_LEVEL, report.violations)

    def test_second_instance_still_does_not_trigger(self):
        metrics = SebiOtrMetrics(**self.BREACH, prior_cooling_off_instances_30d=1)
        report = self.engine.audit_sebi_algo_order(make_payload(), metrics)

        self.assertEqual(report.otr_status, OTR_COOLING_OFF_LEVEL_REACHED)
        self.assertEqual(report.otr_cooling_off_instances_30d, 2)

    def test_third_instance_triggers_the_cooling_off_suspension(self):
        metrics = SebiOtrMetrics(**self.BREACH, prior_cooling_off_instances_30d=2)
        report = self.engine.audit_sebi_algo_order(make_payload(), metrics)

        self.assertEqual(report.otr_status, OTR_COOLING_OFF_TRIGGERED)
        self.assertEqual(report.otr_cooling_off_instances_30d, 3)
        self.assertIn(V_OTR_COOLING_OFF_TRIGGERED, report.violations)
        self.assertIn("first 15 minutes", report.audit_notes)
        self.assertEqual(report.otr_cooling_off_lookback_days, 30)

    def test_prior_instances_do_not_escalate_a_compliant_day(self):
        """Two prior breaches plus a clean day is still a clean day."""
        metrics = SebiOtrMetrics(
            total_order_messages=200,
            total_executed_trades=5,
            prior_cooling_off_instances_30d=2,
        )
        report = self.engine.audit_sebi_algo_order(make_payload(), metrics)

        self.assertEqual(report.otr_status, OTR_NORMAL)
        self.assertEqual(report.otr_cooling_off_instances_30d, 2)

    def test_cooling_off_level_boundary_is_inclusive(self):
        # 20,000 / 10 = 2,000.0 exactly.
        metrics = SebiOtrMetrics(total_order_messages=20_000, total_executed_trades=10)
        report = self.engine.audit_sebi_algo_order(make_payload(), metrics)
        self.assertEqual(report.otr_status, OTR_COOLING_OFF_LEVEL_REACHED)

    def test_just_below_the_cooling_off_level_is_only_a_penalty_slab(self):
        # 19,990 / 10 = 1,999.0
        metrics = SebiOtrMetrics(total_order_messages=19_990, total_executed_trades=10)
        report = self.engine.audit_sebi_algo_order(make_payload(), metrics)
        self.assertEqual(report.otr_status, OTR_PENALTY_SLAB)


class TestOtrRoundingBoundaries(BaseEngineTest):
    """Thresholds must be tested against the unrounded ratio.

    Rounding first turns a true 1999.999 into "2000.00" and manufactures an
    instance towards a cooling-off suspension that never occurred.
    """

    def test_a_ratio_that_rounds_up_to_the_cooling_off_level_does_not_reach_it(self):
        # 3,999,998 / 2000 = 1999.999 exactly -> rounds to 2000.0 for display.
        metrics = SebiOtrMetrics(
            total_order_messages=3_999_998, total_executed_trades=2_000
        )
        report = self.engine.audit_sebi_algo_order(make_payload(), metrics)

        self.assertEqual(report.calculated_otr_ratio, 2000.0)
        self.assertEqual(report.otr_status, OTR_PENALTY_SLAB)
        self.assertEqual(report.otr_cooling_off_instances_30d, 0)
        self.assertNotIn(V_OTR_COOLING_OFF_LEVEL, report.violations)

    def test_a_ratio_that_rounds_up_to_the_slab_floor_does_not_reach_it(self):
        # 999,998 / 2000 = 499.999 -> rounds to 500.0 for display.
        metrics = SebiOtrMetrics(
            total_order_messages=999_998, total_executed_trades=2_000
        )
        report = self.engine.audit_sebi_algo_order(make_payload(), metrics)

        self.assertEqual(report.calculated_otr_ratio, 500.0)
        self.assertEqual(report.otr_status, OTR_NORMAL)
        self.assertNotIn(V_OTR_SLAB, report.violations)

    def test_a_ratio_that_rounds_down_to_a_threshold_still_breaches_it(self):
        # 4,000,002 / 2000 = 2000.001 -> rounds to 2000.0 but is above the level.
        metrics = SebiOtrMetrics(
            total_order_messages=4_000_002, total_executed_trades=2_000
        )
        report = self.engine.audit_sebi_algo_order(make_payload(), metrics)

        self.assertEqual(report.calculated_otr_ratio, 2000.0)
        self.assertEqual(report.otr_status, OTR_COOLING_OFF_LEVEL_REACHED)


# ---------------------------------------------------------------------------
# Multiple violations and precedence
# ---------------------------------------------------------------------------
class TestViolationAggregation(BaseEngineTest):
    def test_every_breach_is_recorded_not_only_the_headline(self):
        payload = make_payload(
            algo_id="",
            client_category="RETAIL",
            order_type=ORDER_TYPE_MARKET,
            static_ip_whitelisted=False,
        )
        metrics = SebiOtrMetrics(
            total_order_messages=25_000,
            total_executed_trades=10,
            prior_cooling_off_instances_30d=2,
        )
        report = self.engine.audit_sebi_algo_order(payload, metrics)

        self.assertEqual(report.status, STATUS_UNTAGGED)
        for expected in (
            V_UNTAGGED,
            V_INVALID_CATEGORY,
            V_MARKET_ORDER,
            V_STATIC_IP,
            V_OTR_COOLING_OFF_TRIGGERED,
        ):
            self.assertIn(expected, report.violations)

    def test_untagged_outranks_a_market_order(self):
        report = self.engine.audit_sebi_algo_order(
            make_payload(algo_id="", order_type=ORDER_TYPE_MARKET), NORMAL_OTR
        )
        self.assertEqual(report.status, STATUS_UNTAGGED)

    def test_ops_breach_outranks_an_invalid_category(self):
        payload = make_payload(
            algo_tag_kind=TAG_GENERIC,
            orders_per_second_ops=50.0,
            client_category="RETAIL",
        )
        report = self.engine.audit_sebi_algo_order(payload, NORMAL_OTR)
        self.assertEqual(report.status, STATUS_OPS_BREACH)
        self.assertIn(V_INVALID_CATEGORY, report.violations)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------
class TestPayloadValidation(BaseEngineTest):
    def assert_raises(self, **overrides):
        with self.assertRaises(ValueError):
            self.engine.audit_sebi_algo_order(make_payload(**overrides), NORMAL_OTR)

    def test_unknown_enumerated_fields_raise(self):
        self.assert_raises(exchange="LSE")
        self.assert_raises(segment="XX")
        self.assert_raises(order_source="TELEPATHY")
        self.assert_raises(order_type="ICEBERG")
        self.assert_raises(algo_tag_kind="MAYBE")
        self.assert_raises(side="HOLD")

    def test_non_positive_quantity_raises(self):
        self.assert_raises(quantity=0)
        self.assert_raises(quantity=-100)

    def test_non_finite_or_negative_price_raises(self):
        self.assert_raises(price=float("nan"))
        self.assert_raises(price=float("inf"))
        self.assert_raises(price=-1.0)

    def test_blank_symbol_raises(self):
        self.assert_raises(symbol="")
        self.assert_raises(symbol="   ")

    def test_negative_or_non_finite_ops_raises(self):
        self.assert_raises(orders_per_second_ops=-1.0)
        self.assert_raises(orders_per_second_ops=float("nan"))

    def test_zero_price_is_allowed(self):
        """A zero limit price is a caller decision the exchange polices, not a
        malformed payload."""
        report = self.engine.audit_sebi_algo_order(make_payload(price=0.0), NORMAL_OTR)
        self.assertEqual(report.status, STATUS_APPROVED)


class TestMetricsValidation(unittest.TestCase):
    def test_negative_counters_raise(self):
        with self.assertRaises(ValueError):
            SebiOtrMetrics(total_order_messages=-1, total_executed_trades=1)
        with self.assertRaises(ValueError):
            SebiOtrMetrics(total_order_messages=1, total_executed_trades=-1)
        with self.assertRaises(ValueError):
            SebiOtrMetrics(
                total_order_messages=1, total_executed_trades=1,
                exempt_order_messages=-1,
            )
        with self.assertRaises(ValueError):
            SebiOtrMetrics(
                total_order_messages=1, total_executed_trades=1,
                prior_cooling_off_instances_30d=-1,
            )

    def test_exempt_messages_cannot_exceed_the_total(self):
        with self.assertRaises(ValueError):
            SebiOtrMetrics(
                total_order_messages=100,
                total_executed_trades=1,
                exempt_order_messages=101,
            )

    def test_chargeable_messages_net_off_the_exemptions(self):
        metrics = SebiOtrMetrics(
            total_order_messages=1000,
            total_executed_trades=10,
            exempt_order_messages=250,
        )
        self.assertEqual(metrics.chargeable_order_messages, 750)


class TestEngineConfigValidation(unittest.TestCase):
    def test_invalid_thresholds_raise(self):
        with self.assertRaises(ValueError):
            SebiAlgoTaggingEngine(threshold_ops=0)
        with self.assertRaises(ValueError):
            SebiAlgoTaggingEngine(threshold_ops=float("inf"))
        with self.assertRaises(ValueError):
            SebiAlgoTaggingEngine(otr_penalty_slab_floor=-1)
        with self.assertRaises(ValueError):
            SebiAlgoTaggingEngine(cooling_off_instance_count=0)
        with self.assertRaises(ValueError):
            SebiAlgoTaggingEngine(cooling_off_lookback_days=0)
        with self.assertRaises(ValueError):
            SebiAlgoTaggingEngine(algo_nnf_digits=frozenset())

    def test_cooling_off_level_below_the_slab_floor_raises(self):
        with self.assertRaises(ValueError):
            SebiAlgoTaggingEngine(
                otr_penalty_slab_floor=2000.0, otr_cooling_off_level=500.0
            )

    def test_defaults_match_the_published_thresholds(self):
        engine = SebiAlgoTaggingEngine()
        self.assertEqual(engine.threshold_ops, 10.0)
        self.assertEqual(engine.otr_penalty_slab_floor, 500.0)
        self.assertEqual(engine.otr_cooling_off_level, 2000.0)
        self.assertEqual(engine.cooling_off_instance_count, 3)
        self.assertEqual(engine.cooling_off_lookback_days, 30)


if __name__ == "__main__":
    unittest.main()
