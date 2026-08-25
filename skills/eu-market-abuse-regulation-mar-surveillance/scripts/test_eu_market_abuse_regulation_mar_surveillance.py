"""Unit tests for eu-market-abuse-regulation-mar-surveillance."""
import unittest

from eu_market_abuse_regulation_mar_surveillance import (
    EuMarSurveillanceAuditReport,
    EuMarSurveillanceEngine,
    MarSurveillanceAlert,
    OrderExecutionEvent,
    STOR_STATUS_DRAFT,
)

MS = 1_000_000        # nanoseconds in a millisecond
SEC = 1_000_000_000   # nanoseconds in a second

ISIN = "DE0007100000"
SYMBOL = "MBG"


def make_event(
    event_id: str,
    event_type: str,
    timestamp_ns: int,
    cl_ord_id: str = "ORD_0",
    side: str = "BUY",
    buyer: str = "ACC_PROP_100",
    seller: str = "ACC_MM_200",
    account_id: str = "",
    order_qty: int = 100,
    price: float = 62.00,
    isin: str = ISIN,
    symbol: str = SYMBOL,
) -> OrderExecutionEvent:
    return OrderExecutionEvent(
        event_id=event_id,
        cl_ord_id=cl_ord_id,
        isin=isin,
        symbol=symbol,
        side=side,
        order_qty=order_qty,
        price=price,
        event_type=event_type,
        timestamp_ns=timestamp_ns,
        buyer_account_id=buyer,
        seller_account_id=seller,
        account_id=account_id,
    )


def layering_batch(
    n_orders: int,
    n_cancels: int,
    lifespan_ns: int,
    account_id: str = "ACC_SPOOF",
    side: str = "BUY",
    start_ns: int = 10 * SEC,
) -> list:
    """
    ``n_orders`` NEWs spaced 1ms apart, of which ``n_cancels`` are cancelled after
    ``lifespan_ns``. Cancel ratio and lifespan are therefore known exactly by
    construction rather than read back off the implementation.
    """
    events = []
    for i in range(n_orders):
        events.append(
            make_event(
                f"EV_NEW_{account_id}_{i}", "NEW", start_ns + i * MS,
                cl_ord_id=f"ORD_{account_id}_{i}", side=side, account_id=account_id,
            )
        )
    for i in range(n_cancels):
        events.append(
            make_event(
                f"EV_CAN_{account_id}_{i}", "CANCEL", start_ns + i * MS + lifespan_ns,
                cl_ord_id=f"ORD_{account_id}_{i}", side=side, account_id=account_id,
            )
        )
    return events


class TestWashTradeDetection(unittest.TestCase):

    def setUp(self):
        self.engine = EuMarSurveillanceEngine()

    def test_wash_trade_detection_and_stor_draft(self):
        event = make_event(
            "EV_FILL_01", "FILL", 1_000_000, cl_ord_id="ORD_100",
            buyer="ACC_PROP_100", seller="ACC_PROP_100", order_qty=1000, price=62.50,
        )
        report = self.engine.audit_events_for_mar_patterns([event])

        self.assertEqual(report.wash_trade_alerts_count, 1)
        self.assertIsNotNone(report.stor_filing_payload)
        self.assertEqual(report.alerts[0].alert_type, "WASH_TRADE_ALERT")
        self.assertEqual(report.alerts[0].severity, "CRITICAL")
        self.assertEqual(report.alerts[0].account_id, "ACC_PROP_100")
        self.assertEqual(report.alerts[0].event_ids, ("EV_FILL_01",))
        self.assertTrue(report.alerts[0].human_review_required)

    def test_distinct_counterparties_are_not_flagged(self):
        event = make_event("EV_FILL_02", "FILL", 1_000_000, buyer="ACC_A", seller="ACC_B")
        report = self.engine.audit_events_for_mar_patterns([event])

        self.assertEqual(report.wash_trade_alerts_count, 0)
        self.assertEqual(report.alerts, [])
        self.assertIsNone(report.stor_filing_payload)

    def test_common_beneficial_owner_across_sub_accounts_is_flagged(self):
        # Regression: string equality alone misses a self-cross booked across two
        # sub-accounts of one owner, which MAR Annex I Section A still treats as
        # "no change in beneficial ownership".
        event = make_event("EV_FILL_03", "FILL", 1_000_000, buyer="ACC_SUB_1", seller="ACC_SUB_2")

        naive = EuMarSurveillanceEngine()
        self.assertEqual(naive.audit_events_for_mar_patterns([event]).wash_trade_alerts_count, 0)

        mapped = EuMarSurveillanceEngine(
            beneficial_owner_map={"ACC_SUB_1": "ENTITY_X", "ACC_SUB_2": "ENTITY_X"}
        )
        report = mapped.audit_events_for_mar_patterns([event])
        self.assertEqual(report.wash_trade_alerts_count, 1)
        self.assertEqual(report.alerts[0].account_id, "ENTITY_X")
        self.assertIn("common owner", report.alerts[0].details)

    def test_missing_counterparty_id_is_not_treated_as_a_self_cross(self):
        event = make_event("EV_FILL_04", "FILL", 1_000_000, buyer="", seller="")
        self.assertEqual(
            self.engine.audit_events_for_mar_patterns([event]).wash_trade_alerts_count, 0
        )


class TestSpoofingDetection(unittest.TestCase):

    def setUp(self):
        self.engine = EuMarSurveillanceEngine(spoof_cancel_ratio_threshold=0.90)

    def test_spoofing_layering_detection(self):
        # 10 NEW, 9 CANCEL at 5ms lifespan -> exactly 0.90 cancel ratio, at threshold.
        report = self.engine.audit_events_for_mar_patterns(layering_batch(10, 9, 5 * MS))

        self.assertEqual(report.spoofing_alerts_count, 1)
        self.assertIsNotNone(report.stor_filing_payload)
        self.assertEqual(report.alerts[0].alert_type, "SPOOFING_ALERT")
        self.assertAlmostEqual(report.alerts[0].metric_value, 0.90)
        self.assertEqual(report.alerts[0].account_id, "ACC_SPOOF")

    def test_slow_cancels_do_not_trigger_the_alert(self):
        # Regression: the cancel ratio alone is 0.90, but every order rested for 500ms,
        # far above the 100ms fast-cancel window. Ratio-only logic flags this; correct
        # lifespan handling does not.
        report = self.engine.audit_events_for_mar_patterns(layering_batch(10, 9, 500 * MS))

        self.assertEqual(report.spoofing_alerts_count, 0)
        self.assertIsNone(report.stor_filing_payload)

    def test_cancel_ratio_just_below_threshold_is_not_flagged(self):
        # 8 of 10 cancelled = 0.80 < 0.90.
        report = self.engine.audit_events_for_mar_patterns(layering_batch(10, 8, 5 * MS))
        self.assertEqual(report.spoofing_alerts_count, 0)

    def test_ratio_is_computed_per_owner_not_across_the_whole_batch(self):
        # Regression: a global ratio dilutes one spoofer inside honest flow. Here the
        # spoofer cancels 9 of 10 fast; a market maker posts 40 orders and cancels none.
        # Batch-wide ratio = 9/50 = 0.18 (no alert); per-owner ratio = 0.90 (alert).
        events = layering_batch(10, 9, 5 * MS, account_id="ACC_SPOOF")
        events += layering_batch(40, 0, 0, account_id="ACC_HONEST_MM", start_ns=20 * SEC)

        report = self.engine.audit_events_for_mar_patterns(events)

        self.assertEqual(report.spoofing_alerts_count, 1)
        self.assertEqual(report.alerts[0].account_id, "ACC_SPOOF")
        self.assertEqual(report.groups_examined, 2)

    def test_alert_is_attributed_to_the_instrument_actually_traded(self):
        # Regression: attributing the alert to the first NEW in the batch put another
        # issuer's ISIN on a regulatory record.
        events = layering_batch(6, 0, 0, account_id="ACC_OTHER", start_ns=1 * SEC)
        events = [
            make_event(
                e.event_id, e.event_type, e.timestamp_ns, cl_ord_id=e.cl_ord_id,
                account_id=e.account_id, isin="FR0000120271", symbol="TTE",
            )
            for e in events
        ]
        events += layering_batch(10, 9, 5 * MS, account_id="ACC_SPOOF", start_ns=20 * SEC)

        report = self.engine.audit_events_for_mar_patterns(events)

        self.assertEqual(report.spoofing_alerts_count, 1)
        self.assertEqual(report.alerts[0].isin, ISIN)
        self.assertEqual(report.alerts[0].symbol, SYMBOL)

    def test_opposite_side_fill_raises_severity_and_is_recorded(self):
        events = layering_batch(10, 9, 5 * MS, account_id="ACC_SPOOF", side="BUY")
        events.append(
            make_event(
                "EV_FILL_OPP", "FILL", 10 * SEC + 3 * MS, cl_ord_id="ORD_REAL", side="SELL",
                buyer="ACC_COUNTERPARTY", seller="ACC_SPOOF", account_id="ACC_SPOOF",
            )
        )
        report = self.engine.audit_events_for_mar_patterns(events)

        alert = next(a for a in report.alerts if a.alert_type == "SPOOFING_ALERT")
        self.assertEqual(alert.severity, "HIGH")
        self.assertTrue(alert.opposite_side_fill_observed)
        self.assertIn("EV_FILL_OPP", alert.event_ids)

    def test_layering_without_opposite_fill_is_medium_and_suppressible(self):
        events = layering_batch(10, 9, 5 * MS)

        lenient = EuMarSurveillanceEngine()
        alert = lenient.audit_events_for_mar_patterns(events).alerts[0]
        self.assertEqual(alert.severity, "MEDIUM")
        self.assertFalse(alert.opposite_side_fill_observed)

        strict = EuMarSurveillanceEngine(require_opposite_side_fill=True)
        self.assertEqual(strict.audit_events_for_mar_patterns(events).spoofing_alerts_count, 0)

    def test_fast_cancels_on_both_sides_have_no_opposite_side(self):
        # Cancelling hard on both sides is not the one-sided Annex II shape: there is no
        # "other side" left for a fill to land on, so it stays MEDIUM and strict mode
        # drops it entirely.
        events = layering_batch(6, 6, 2 * MS, account_id="ACC_TWOSIDED", side="BUY")
        events += [
            make_event(
                f"EV_NEW_S_{i}", "NEW", 30 * SEC + i * MS, cl_ord_id=f"ORD_S_{i}",
                side="SELL", account_id="ACC_TWOSIDED",
            )
            for i in range(6)
        ]
        events += [
            make_event(
                f"EV_CAN_S_{i}", "CANCEL", 30 * SEC + i * MS + 2 * MS, cl_ord_id=f"ORD_S_{i}",
                side="SELL", account_id="ACC_TWOSIDED",
            )
            for i in range(6)
        ]

        alert = self.engine.audit_events_for_mar_patterns(events).alerts[0]
        self.assertEqual(alert.severity, "MEDIUM")
        self.assertFalse(alert.opposite_side_fill_observed)

        strict = EuMarSurveillanceEngine(require_opposite_side_fill=True)
        self.assertEqual(strict.audit_events_for_mar_patterns(events).spoofing_alerts_count, 0)

    def test_cancels_without_a_matching_new_are_excluded_and_counted(self):
        # A truncated batch must under-report, never fabricate: 10 NEWs, plus 9 CANCELs
        # for order ids the batch never saw.
        events = layering_batch(10, 0, 0, account_id="ACC_SPOOF")
        events += [
            make_event(
                f"EV_ORPHAN_CAN_{i}", "CANCEL", 11 * SEC + i * MS,
                cl_ord_id=f"ORD_ELSEWHERE_{i}", account_id="ACC_SPOOF",
            )
            for i in range(9)
        ]
        report = self.engine.audit_events_for_mar_patterns(events)

        self.assertEqual(report.spoofing_alerts_count, 0)
        self.assertEqual(report.unmatched_cancels, 9)

    def test_too_few_orders_for_a_meaningful_ratio(self):
        # 4 NEW / 4 CANCEL is a 100% ratio on a sample too small to be evidence.
        report = self.engine.audit_events_for_mar_patterns(layering_batch(4, 4, 1 * MS))
        self.assertEqual(report.spoofing_alerts_count, 0)

    def test_result_is_independent_of_input_ordering(self):
        events = layering_batch(10, 9, 5 * MS)
        forward = self.engine.audit_events_for_mar_patterns(events)
        reversed_report = self.engine.audit_events_for_mar_patterns(list(reversed(events)))

        self.assertEqual(forward.spoofing_alerts_count, reversed_report.spoofing_alerts_count)
        self.assertEqual(forward.alerts[0].alert_id, reversed_report.alerts[0].alert_id)
        self.assertEqual(forward.alerts[0].details, reversed_report.alerts[0].details)


class TestQuoteStuffingDetection(unittest.TestCase):

    def burst(self, count: int, spacing_ns: int, account_id: str = "ACC_HFT") -> list:
        return [
            make_event(
                f"EV_MSG_{account_id}_{i}", "NEW", 5 * SEC + i * spacing_ns,
                cl_ord_id=f"ORD_{account_id}_{i}", account_id=account_id,
            )
            for i in range(count)
        ]

    def test_burst_above_threshold_is_flagged(self):
        engine = EuMarSurveillanceEngine(quote_rate_threshold_per_sec=100)
        # 150 messages spaced 1ms apart span 149ms -> all inside one second.
        report = engine.audit_events_for_mar_patterns(self.burst(150, 1 * MS))

        self.assertEqual(report.quote_stuffing_alerts_count, 1)
        alert = report.alerts[0]
        self.assertEqual(alert.alert_type, "QUOTE_STUFFING_ALERT")
        self.assertEqual(alert.metric_value, 150.0)
        self.assertEqual(alert.account_id, "ACC_HFT")

    def test_same_message_count_spread_over_ten_seconds_is_not_flagged(self):
        engine = EuMarSurveillanceEngine(quote_rate_threshold_per_sec=100)
        # 150 messages at 100ms spacing = 15 msgs/s peak.
        report = engine.audit_events_for_mar_patterns(self.burst(150, 100 * MS))
        self.assertEqual(report.quote_stuffing_alerts_count, 0)

    def test_threshold_is_strict_greater_than(self):
        engine = EuMarSurveillanceEngine(quote_rate_threshold_per_sec=100)
        self.assertEqual(
            engine.audit_events_for_mar_patterns(self.burst(100, 1 * MS)).quote_stuffing_alerts_count, 0
        )
        self.assertEqual(
            engine.audit_events_for_mar_patterns(self.burst(101, 1 * MS)).quote_stuffing_alerts_count, 1
        )

    def test_burst_straddling_a_calendar_second_boundary_is_still_caught(self):
        # 120 messages at 1ms spacing starting 60ms before the second boundary: fixed
        # one-second buckets would split them 60/60 and see no breach.
        engine = EuMarSurveillanceEngine(quote_rate_threshold_per_sec=100)
        events = [
            make_event(
                f"EV_STRADDLE_{i}", "NEW", 5 * SEC - 60 * MS + i * MS,
                cl_ord_id=f"ORD_S_{i}", account_id="ACC_HFT",
            )
            for i in range(120)
        ]
        self.assertEqual(
            engine.audit_events_for_mar_patterns(events).quote_stuffing_alerts_count, 1
        )

    def test_rate_is_per_owner_and_instrument(self):
        engine = EuMarSurveillanceEngine(quote_rate_threshold_per_sec=100)
        events = self.burst(80, 1 * MS, account_id="ACC_A") + self.burst(80, 1 * MS, account_id="ACC_B")
        self.assertEqual(
            engine.audit_events_for_mar_patterns(events).quote_stuffing_alerts_count, 0
        )

    def test_fills_are_not_counted_as_quotes(self):
        engine = EuMarSurveillanceEngine(quote_rate_threshold_per_sec=100)
        events = [
            make_event(
                f"EV_FILL_{i}", "FILL", 5 * SEC + i * MS, cl_ord_id=f"ORD_F_{i}",
                buyer="ACC_HFT", seller="ACC_CP", account_id="ACC_HFT",
            )
            for i in range(150)
        ]
        self.assertEqual(
            engine.audit_events_for_mar_patterns(events).quote_stuffing_alerts_count, 0
        )


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = EuMarSurveillanceEngine()

    def test_unknown_event_type_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_events_for_mar_patterns([make_event("EV_1", "PARTIAL_FILL", 1)])

    def test_unknown_side_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_events_for_mar_patterns([make_event("EV_1", "NEW", 1, side="LONG")])

    def test_non_positive_quantity_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_events_for_mar_patterns([make_event("EV_1", "NEW", 1, order_qty=0)])

    def test_non_finite_price_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_events_for_mar_patterns([make_event("EV_1", "NEW", 1, price=float("nan"))])

    def test_negative_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_events_for_mar_patterns([make_event("EV_1", "NEW", -1)])

    def test_unidentifiable_instrument_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_events_for_mar_patterns([make_event("EV_1", "NEW", 1, isin="", symbol="")])

    def test_duplicate_event_id_is_rejected(self):
        events = [make_event("EV_DUP", "NEW", 1), make_event("EV_DUP", "NEW", 2)]
        with self.assertRaises(ValueError):
            self.engine.audit_events_for_mar_patterns(events)

    def test_non_event_object_is_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.audit_events_for_mar_patterns([{"event_type": "NEW"}])

    def test_invalid_engine_parameters_are_rejected(self):
        with self.assertRaises(ValueError):
            EuMarSurveillanceEngine(spoof_cancel_ratio_threshold=1.5)
        with self.assertRaises(ValueError):
            EuMarSurveillanceEngine(spoof_cancel_ratio_threshold=0.0)
        with self.assertRaises(ValueError):
            EuMarSurveillanceEngine(spoof_max_lifespan_ms=0.0)
        with self.assertRaises(ValueError):
            EuMarSurveillanceEngine(quote_rate_threshold_per_sec=0)
        with self.assertRaises(ValueError):
            EuMarSurveillanceEngine(min_orders_for_cancel_ratio=1)


class TestStorDraftAndReport(unittest.TestCase):

    def setUp(self):
        self.engine = EuMarSurveillanceEngine()

    def test_empty_batch_produces_a_clean_report(self):
        report = self.engine.audit_events_for_mar_patterns([])

        self.assertIsInstance(report, EuMarSurveillanceAuditReport)
        self.assertEqual(report.total_events_audited, 0)
        self.assertEqual(report.alerts, [])
        self.assertIsNone(report.stor_filing_payload)
        self.assertEqual(report.groups_examined, 0)

    def test_draft_never_claims_to_be_submitted(self):
        event = make_event("EV_FILL_01", "FILL", 1_000, buyer="ACC_X", seller="ACC_X")
        payload = self.engine.audit_events_for_mar_patterns([event]).stor_filing_payload

        self.assertEqual(payload["status"], STOR_STATUS_DRAFT)
        self.assertIn("DRAFT", payload["report_type"])
        self.assertIn("2016/957", payload["template_source"])
        self.assertIn("596/2014", payload["legal_basis"])
        self.assertIn("five years", payload["record_retention"])
        self.assertIn("human analysis", payload["human_analysis_required"])
        self.assertNotIn("READY_FOR_SUBMISSION", "".join(payload.values()))

    def test_draft_carries_the_parameters_that_produced_the_alerts(self):
        engine = EuMarSurveillanceEngine(spoof_cancel_ratio_threshold=0.75, quote_rate_threshold_per_sec=250)
        report = engine.audit_events_for_mar_patterns(layering_batch(10, 9, 5 * MS))

        self.assertEqual(report.detection_parameters["spoof_cancel_ratio_threshold"], "0.7500")
        self.assertEqual(report.detection_parameters["quote_rate_threshold_per_sec"], "250")
        self.assertIn("spoof_cancel_ratio_threshold=0.7500", report.stor_filing_payload["detection_parameters"])

    def test_alert_ids_are_deterministic_across_alert_mixes(self):
        spoof_only = self.engine.audit_events_for_mar_patterns(layering_batch(10, 9, 5 * MS))
        with_wash = self.engine.audit_events_for_mar_patterns(
            [make_event("EV_FILL_W", "FILL", 1_000, buyer="ACC_X", seller="ACC_X")]
            + layering_batch(10, 9, 5 * MS)
        )

        spoof_id = next(a.alert_id for a in spoof_only.alerts if a.alert_type == "SPOOFING_ALERT")
        mixed_id = next(a.alert_id for a in with_wash.alerts if a.alert_type == "SPOOFING_ALERT")
        self.assertEqual(spoof_id, mixed_id)

    def test_all_alert_types_can_be_raised_in_one_batch(self):
        engine = EuMarSurveillanceEngine(quote_rate_threshold_per_sec=20)
        events = layering_batch(30, 29, 1 * MS, account_id="ACC_SPOOF")
        events.append(
            make_event("EV_FILL_W", "FILL", 9 * SEC, buyer="ACC_X", seller="ACC_X")
        )
        report = engine.audit_events_for_mar_patterns(events)

        self.assertEqual(report.wash_trade_alerts_count, 1)
        self.assertEqual(report.spoofing_alerts_count, 1)
        self.assertEqual(report.quote_stuffing_alerts_count, 1)
        self.assertEqual(len(report.alerts), 3)
        self.assertTrue(all(isinstance(a, MarSurveillanceAlert) for a in report.alerts))
        self.assertTrue(all(a.human_review_required for a in report.alerts))
        self.assertTrue(all(a.indicator_reference for a in report.alerts))


if __name__ == "__main__":
    unittest.main()
