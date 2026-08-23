"""
Unit tests for dubai-financial-market-dfm-api.

Tick and price-band expectations are derived from the published DFM reference data
(tick structure effective 06 April 2026; asymmetric equity band) rather than from the
implementation's own expressions. FIX BodyLength and CheckSum are re-derived inside the
tests straight from the FIX 4.4 spec, so a framing regression is detectable.
"""
import logging
import unittest
from datetime import datetime, timezone

from dubai_financial_market_dfm_api import (
    DubaiFinancialMarketApiEngine, DfmOrderRequest, DfmOrderExecutionReport, SOH,
)

logging.getLogger("dubai_financial_market_dfm_api").setLevel(logging.CRITICAL)

FIXED_TIME = datetime(2026, 8, 23, 10, 30, 0, tzinfo=timezone.utc)


class TestDubaiFinancialMarketApiEngine(unittest.TestCase):

    def setUp(self):
        self.engine = DubaiFinancialMarketApiEngine()

    def order(self, **kw):
        base = dict(
            cl_ord_id="ORD_DFM_01",
            nin_investor_number="1099887766",
            symbol="EMAAR",
            side="BUY",
            order_qty=10000,
            price_aed=7.85,
            prior_settlement_price_aed=7.80,
        )
        base.update(kw)
        return DfmOrderRequest(**base)

    # ------------------------------------------------------------------
    # Baseline behaviour
    # ------------------------------------------------------------------

    def test_valid_dfm_order_accepted(self):
        report = self.engine.process_dfm_order(self.order(), sending_time=FIXED_TIME)

        self.assertTrue(report.is_accepted)
        self.assertEqual(report.status, "STATUS_OK")
        self.assertIsNotNone(report.fix_payload)
        self.assertEqual(report.fix_payload.account_nin, "1099887766")
        self.assertIn(f"15=AED{SOH}", report.fix_payload.fix_raw_string)
        self.assertAlmostEqual(report.required_tick_size, 0.01, places=9)

    def test_off_tick_price_rejected(self):
        report = self.engine.process_dfm_order(self.order(price_aed=7.855))
        self.assertFalse(report.is_accepted)
        self.assertEqual(report.status, "INVALID_TICK_SIZE")

    def test_circuit_breaker_breach_rejected(self):
        # 9.00 vs 7.80 = +15.38%, beyond the +15% limit up.
        report = self.engine.process_dfm_order(self.order(price_aed=9.00))
        self.assertFalse(report.is_accepted)
        self.assertEqual(report.status, "CIRCUIT_BREAKER_BAND_BREACH")

    # ------------------------------------------------------------------
    # Tick structure effective 06 April 2026 (regression: 100+ bracket was absent)
    # ------------------------------------------------------------------

    def test_tick_size_bracket_boundaries(self):
        cases = [
            (0.999, 0.001), (1.00, 0.01), (9.99, 0.01),
            (10.00, 0.02), (49.98, 0.02),
            (50.00, 0.05), (99.95, 0.05),
            (100.00, 0.10), (250.00, 0.10),
        ]
        for price, expected_tick in cases:
            with self.subTest(price=price):
                self.assertAlmostEqual(
                    self.engine.required_tick_size(price), expected_tick, places=9
                )

    def test_securities_at_or_above_100_aed_require_a_010_tick(self):
        # 150.05 is a valid multiple of 0.05 but NOT of 0.10. Before the 100+ bracket
        # existed this order was accepted and DFM would have rejected it.
        rejected = self.engine.process_dfm_order(
            self.order(price_aed=150.05, prior_settlement_price_aed=150.00)
        )
        self.assertEqual(rejected.status, "INVALID_TICK_SIZE")
        self.assertAlmostEqual(rejected.required_tick_size, 0.10, places=9)

        accepted = self.engine.process_dfm_order(
            self.order(price_aed=150.10, prior_settlement_price_aed=150.00),
            sending_time=FIXED_TIME,
        )
        self.assertEqual(accepted.status, "STATUS_OK")

    def test_tick_validation_is_robust_at_band_edges(self):
        # Float modulo misjudges several of these; integer tick counts do not.
        for price in (0.003, 0.999, 1.01, 9.99, 10.02, 49.98, 50.05, 99.95, 100.10):
            with self.subTest(price=price):
                is_valid, _ = self.engine.audit_dfm_tick_size(price)
                self.assertTrue(is_valid, f"{price} should be on-tick")

    def test_engine_rejects_a_tick_table_with_a_finite_top_band(self):
        with self.assertRaises(ValueError):
            DubaiFinancialMarketApiEngine(tick_bands=((1.0, 0.001), (10.0, 0.01)))
        with self.assertRaises(ValueError):
            DubaiFinancialMarketApiEngine(tick_bands=((10.0, 0.01), (1.0, 0.001)))

    # ------------------------------------------------------------------
    # Asymmetric price band (regression: symmetric +/-10% rejected valid orders)
    # ------------------------------------------------------------------

    def test_band_is_asymmetric_minus_10_plus_15(self):
        # Benchmark 7.80 => lower 7.02, upper 8.97.
        for price, expected in [
            (8.58, "STATUS_OK"),                      # +10.00%, was wrongly rejected
            (8.97, "STATUS_OK"),                      # +15.00%, exactly at limit up
            (8.98, "CIRCUIT_BREAKER_BAND_BREACH"),    # +15.13%
            (7.02, "STATUS_OK"),                      # -10.00%, exactly at limit down
            (7.01, "CIRCUIT_BREAKER_BAND_BREACH"),    # -10.13%
        ]:
            with self.subTest(price=price):
                report = self.engine.process_dfm_order(
                    self.order(price_aed=price), sending_time=FIXED_TIME
                )
                self.assertEqual(report.status, expected)

    def test_per_security_band_override(self):
        report = self.engine.process_dfm_order(
            self.order(price_aed=8.58, limit_up_pct=0.05, limit_down_pct=0.05)
        )
        self.assertEqual(report.status, "CIRCUIT_BREAKER_BAND_BREACH")
        self.assertAlmostEqual(report.upper_price_limit, 8.19, places=9)
        self.assertAlmostEqual(report.lower_price_limit, 7.41, places=9)

    def test_missing_reference_price_fails_closed(self):
        # Previously a missing/zero benchmark silently SKIPPED the band check entirely.
        for ref in (None, 0.0, -1.0, float("nan")):
            with self.subTest(ref=ref):
                report = self.engine.process_dfm_order(
                    self.order(price_aed=999.0, prior_settlement_price_aed=ref)
                )
                self.assertFalse(report.is_accepted)
                self.assertEqual(report.status, "MISSING_REFERENCE_PRICE")

    def test_first_trading_session_has_no_band_but_warns(self):
        report = self.engine.process_dfm_order(
            self.order(price_aed=999.00, prior_settlement_price_aed=None,
                       is_first_trading_session=True),
            sending_time=FIXED_TIME,
        )
        self.assertEqual(report.status, "STATUS_OK")
        self.assertIsNone(report.upper_price_limit)
        self.assertTrue(any("First Trading Session" in w for w in report.warnings))

    # ------------------------------------------------------------------
    # Order field validation (regression: unknown side silently became SELL)
    # ------------------------------------------------------------------

    def test_unrecognised_side_is_rejected_not_coerced_to_sell(self):
        for side in ("LONG", "", "SELLL", "S", "1"):
            with self.subTest(side=side):
                report = self.engine.process_dfm_order(self.order(side=side))
                self.assertEqual(report.status, "INVALID_ORDER_FIELD")
                self.assertIsNone(report.fix_payload)

    def test_side_is_case_and_whitespace_tolerant(self):
        for side, tag54 in (("buy", 1), (" Sell ", 2)):
            with self.subTest(side=side):
                report = self.engine.process_dfm_order(
                    self.order(side=side), sending_time=FIXED_TIME
                )
                self.assertEqual(report.status, "STATUS_OK")
                self.assertEqual(report.fix_payload.side, tag54)

    def test_invalid_quantities_rejected(self):
        for qty in (0, -100, 1.5, True):
            with self.subTest(qty=qty):
                self.assertEqual(
                    self.engine.process_dfm_order(self.order(order_qty=qty)).status,
                    "INVALID_ORDER_FIELD",
                )

    def test_invalid_prices_rejected(self):
        for price in (0.0, -5.0, float("nan"), float("inf")):
            with self.subTest(price=price):
                self.assertEqual(
                    self.engine.process_dfm_order(self.order(price_aed=price)).status,
                    "INVALID_ORDER_FIELD",
                )

    def test_currency_is_honoured_and_validated(self):
        usd = self.engine.process_dfm_order(
            self.order(currency="USD"), sending_time=FIXED_TIME
        )
        self.assertEqual(usd.status, "STATUS_OK")
        self.assertEqual(usd.fix_payload.currency, "USD")
        self.assertIn(f"15=USD{SOH}", usd.fix_payload.fix_raw_string)

        self.assertEqual(
            self.engine.process_dfm_order(self.order(currency="GBP")).status,
            "INVALID_ORDER_FIELD",
        )

    def test_nin_validation(self):
        for nin in ("109988776", "10998877660", "abcdefghij", "", "10998877 6", None):
            with self.subTest(nin=nin):
                report = self.engine.process_dfm_order(self.order(nin_investor_number=nin))
                self.assertEqual(report.status, "INVALID_NIN")

    def test_fix_field_injection_is_rejected(self):
        # A cl_ord_id carrying SOH could forge a premature 10= CheckSum field and
        # produce a malformed message; '=' would forge a tag boundary.
        injections = [
            "EVIL" + SOH + "10=000" + SOH,
            "ORD=1",
            "ORD" + SOH + "54=2",
        ]
        for bad in injections:
            with self.subTest(value=bad):
                self.assertEqual(
                    self.engine.process_dfm_order(self.order(cl_ord_id=bad)).status,
                    "INVALID_ORDER_FIELD",
                )
                self.assertEqual(
                    self.engine.process_dfm_order(self.order(symbol=bad)).status,
                    "INVALID_ORDER_FIELD",
                )

    def test_accepted_message_contains_exactly_one_checksum_field(self):
        raw = self.engine.process_dfm_order(
            self.order(), sending_time=FIXED_TIME
        ).fix_payload.fix_raw_string
        self.assertEqual(sum(1 for f in raw.split(SOH) if f.startswith("10=")), 1)

    def test_blank_identifiers_rejected(self):
        self.assertEqual(
            self.engine.process_dfm_order(self.order(cl_ord_id="  ")).status,
            "INVALID_ORDER_FIELD",
        )
        self.assertEqual(
            self.engine.process_dfm_order(self.order(symbol="")).status,
            "INVALID_ORDER_FIELD",
        )

    # ------------------------------------------------------------------
    # FIX 4.4 framing, re-derived from the spec inside the test
    # ------------------------------------------------------------------

    def test_fix_body_length_and_checksum_match_the_spec(self):
        report = self.engine.process_dfm_order(self.order(), sending_time=FIXED_TIME)
        raw = report.fix_payload.fix_raw_string

        body_start = raw.index(SOH, raw.index("9=")) + 1
        tag10_start = raw.index(SOH + "10=") + 1

        expected_len = len(raw[body_start:tag10_start].encode("ascii"))
        expected_sum = sum(raw[:tag10_start].encode("ascii")) % 256

        self.assertEqual(report.fix_payload.body_length, expected_len)
        self.assertEqual(report.fix_payload.check_sum, f"{expected_sum:03d}")
        self.assertIn(f"{SOH}9={expected_len}{SOH}", raw)
        self.assertTrue(raw.endswith(f"10={expected_sum:03d}{SOH}"))

    def test_fix_message_uses_soh_and_carries_session_fields(self):
        engine = DubaiFinancialMarketApiEngine(sender_comp_id="BRK9", target_comp_id="DFMGW")
        raw = engine.process_dfm_order(
            self.order(), sending_time=FIXED_TIME
        ).fix_payload.fix_raw_string

        self.assertIn(SOH, raw)
        self.assertNotIn("|", raw)
        self.assertTrue(raw.startswith(f"8=FIX.4.4{SOH}9="))
        for tag in (f"49=BRK9{SOH}", f"56=DFMGW{SOH}", f"34=1{SOH}",
                    f"52=20260823-10:30:00.000{SOH}", f"40=2{SOH}", f"35=D{SOH}"):
            self.assertIn(tag, raw)

    def test_price_is_rendered_at_tick_precision(self):
        report = self.engine.process_dfm_order(
            self.order(price_aed=7.85), sending_time=FIXED_TIME
        )
        self.assertIn(f"44=7.85{SOH}", report.fix_payload.fix_raw_string)

        sub_dirham = self.engine.process_dfm_order(
            self.order(price_aed=0.755, prior_settlement_price_aed=0.750),
            sending_time=FIXED_TIME,
        )
        self.assertIn(f"44=0.755{SOH}", sub_dirham.fix_payload.fix_raw_string)

    def test_msg_seq_num_increments_per_message(self):
        seqs = []
        for _ in range(3):
            raw = self.engine.process_dfm_order(
                self.order(), sending_time=FIXED_TIME
            ).fix_payload.fix_raw_string
            seqs.append(next(f for f in raw.split(SOH) if f.startswith("34=")))
        self.assertEqual(seqs, ["34=1", "34=2", "34=3"])

    def test_naive_sending_time_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.format_fix_44_payload(
                self.order(), 0.01, sending_time=datetime(2026, 8, 23, 10, 30, 0)
            )

    def test_engine_requires_session_identifiers(self):
        with self.assertRaises(ValueError):
            DubaiFinancialMarketApiEngine(sender_comp_id="")
        with self.assertRaises(ValueError):
            DubaiFinancialMarketApiEngine(target_comp_id="")


if __name__ == "__main__":
    unittest.main()
