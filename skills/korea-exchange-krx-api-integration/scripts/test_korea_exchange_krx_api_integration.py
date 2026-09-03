import unittest
from decimal import Decimal

from korea_exchange_krx_api_integration import (
    ETF_ETN_TICK_SCHEDULE,
    SECURITY_CLASS_ETF_ETN,
    SECURITY_CLASS_STOCK,
    KoreaExchangeKrxApiEngine,
    KrxOrderPayload,
)


class TestKrxTickSchedule(unittest.TestCase):
    """Tick schedule in force since the 25 January 2023 KRX revision.

    Bands are 「이상 ~ 미만」: the upper bound is EXCLUSIVE, so a price sitting
    exactly on a boundary takes the COARSER tick of the band above.
    """

    def setUp(self):
        self.engine = KoreaExchangeKrxApiEngine()

    def test_tick_size_by_band(self):
        cases = [
            (1, "1"), (999, "1"), (1_999, "1"),
            (2_000, "5"), (4_999, "5"),
            (5_000, "10"), (19_999, "10"),
            (20_000, "50"), (49_999, "50"),
            (50_000, "100"), (150_000, "100"), (199_999, "100"),
            (200_000, "500"), (499_999, "500"),
            (500_000, "1000"), (2_500_000, "1000"),
        ]
        for price, expected in cases:
            with self.subTest(price=price):
                self.assertEqual(
                    self.engine.get_krx_tick_size_krw(price), Decimal(expected)
                )

    def test_boundaries_are_exclusive_upper_bounds(self):
        # Regression guard for the 2023 revision. Under an earlier schedule
        # these three prices took KRW 5, KRW 50 and KRW 500 respectively.
        self.assertEqual(self.engine.get_krx_tick_size_krw(1_500), Decimal("1"))
        self.assertEqual(self.engine.get_krx_tick_size_krw(15_000), Decimal("10"))
        self.assertEqual(self.engine.get_krx_tick_size_krw(150_000), Decimal("100"))

    def test_etf_etn_tick_is_flat_five_krw(self):
        # ETFs, ETNs and ELWs were excluded from the 2023 revision.
        for price in (1_050, 9_995, 15_000, 250_000, 1_000_000):
            with self.subTest(price=price):
                self.assertEqual(
                    self.engine.get_krx_tick_size_krw(price, SECURITY_CLASS_ETF_ETN),
                    Decimal("5"),
                )
        self.assertEqual(len(ETF_ETN_TICK_SCHEDULE), 1)

    def test_non_positive_or_non_finite_price_raises(self):
        for bad in (0, -1, -150_000.0, float("nan"), float("inf"), "abc", None):
            with self.subTest(price=bad):
                with self.assertRaises(ValueError):
                    self.engine.get_krx_tick_size_krw(bad)

    def test_unknown_security_class_raises(self):
        with self.assertRaises(ValueError):
            self.engine.get_krx_tick_size_krw(10_000, "ELW")


class TestKrxShortCode(unittest.TestCase):

    def setUp(self):
        self.engine = KoreaExchangeKrxApiEngine()

    def test_numeric_codes_accepted(self):
        for code in ("005930", "000660", "035420", "005935"):
            with self.subTest(code=code):
                self.assertEqual(self.engine.validate_krx_local_code(code), code)

    def test_codes_with_a_trailing_letter_accepted(self):
        # Listed preferred lines whose short code ends in a letter. An
        # isdigit() validator rejects every one of these pre-trade.
        for code in ("00781K", "03473K", "18064K", "02826K"):
            with self.subTest(code=code):
                self.assertEqual(self.engine.validate_krx_local_code(code), code)

    def test_lowercase_and_whitespace_normalised(self):
        self.assertEqual(self.engine.validate_krx_local_code("  03473k "), "03473K")

    def test_excluded_letters_rejected(self):
        # KRX excludes I, O and U from the short-code alphabet.
        for code in ("00781I", "00781O", "00781U"):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    self.engine.validate_krx_local_code(code)

    def test_letter_outside_the_sixth_position_rejected_for_stocks(self):
        for code in ("K05930", "0K5930", "00K930"):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    self.engine.validate_krx_local_code(code)

    def test_malformed_codes_rejected(self):
        for code in ("", "5930", "0059300", "00-930", 5930, None):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    self.engine.validate_krx_local_code(code)

    def test_zero_padding_is_off_by_default(self):
        # Silently padding '5930' routes a mistyped code to a real but
        # different instrument, so it must be opted into explicitly.
        with self.assertRaises(ValueError):
            self.engine.validate_krx_local_code("5930")
        padding = KoreaExchangeKrxApiEngine(allow_zero_pad=True)
        self.assertEqual(padding.validate_krx_local_code("5930"), "005930")


class TestKrxDailyPriceLimit(unittest.TestCase):
    """가격제한폭: truncate(base x pct, tick of the BASE price), then +/-."""

    def setUp(self):
        self.engine = KoreaExchangeKrxApiEngine()

    def test_krx_published_worked_example(self):
        # KRX regulation portal, 기준가격/가격제한폭/상하한가:
        # base KRW 9,940 -> 9,940 x 0.3 = 2,982 -> truncated to the KRW 10
        # tick = 2,980 -> upper 12,920, lower 6,960.
        amount, lower, upper = self.engine.get_daily_price_limit_bounds(9_940)
        self.assertEqual(amount, Decimal("2980"))
        self.assertEqual(lower, Decimal("6960"))
        self.assertEqual(upper, Decimal("12920"))

    def test_truncation_is_material(self):
        # Hand-derived: base 9,940 sits in the KRW 10 band, so the raw
        # amount 2,982 loses 2 KRW. A naive abs(P-base)/base <= 0.30 test
        # accepts 12,922 (+30.00%); KRX's own upper limit is 12,920.
        _, _, upper = self.engine.get_daily_price_limit_bounds(9_940)
        self.assertLess(upper, Decimal("9940") * Decimal("1.3"))
        report = self.engine.validate_and_route_order(
            KrxOrderPayload("005930", "BUY", 12_922, 1, 9_940)
        )
        self.assertEqual(report.status, "INVALID_TICK_SIZE")
        report = self.engine.validate_and_route_order(
            KrxOrderPayload("005930", "BUY", 12_930, 1, 9_940)
        )
        self.assertEqual(report.status, "PRICE_LIMIT_EXCEEDED")

    def test_band_bounds_are_inclusive(self):
        # 12,920 is exactly 상한가 and is tradeable; 6,960 is exactly 하한가.
        for price in (12_920, 6_960):
            with self.subTest(price=price):
                report = self.engine.validate_and_route_order(
                    KrxOrderPayload("005930", "BUY", price, 1, 9_940)
                )
                self.assertEqual(report.status, "KRX_ORDER_VALIDATED")
                self.assertTrue(report.is_price_limit_valid)

    def test_band_is_symmetric_about_the_base_price(self):
        # Truncation is applied to the AMOUNT, not to each bound, so the two
        # bounds are equidistant and both stay inside the nominal 30%.
        amount, lower, upper = self.engine.get_daily_price_limit_bounds(21_050)
        base = Decimal("21050")
        self.assertEqual(upper - base, base - lower)
        self.assertLessEqual(upper, base * Decimal("1.3"))
        self.assertGreaterEqual(lower, base * Decimal("0.7"))
        # Hand-derived: 21,050 is in the KRW 50 band; 21,050 x 0.3 = 6,315;
        # truncated to 50 -> 6,300.
        self.assertEqual(amount, Decimal("6300"))

    def test_konex_fifteen_percent_override(self):
        # Hand-derived: 10,000 is in the KRW 10 band; 10,000 x 0.15 = 1,500,
        # already a multiple of 10.
        amount, lower, upper = self.engine.get_daily_price_limit_bounds(
            10_000, limit_pct_override=15
        )
        self.assertEqual((amount, lower, upper), (Decimal("1500"), Decimal("8500"), Decimal("11500")))

    def test_zero_or_negative_base_price_raises_not_divides_by_zero(self):
        for bad in (0, 0.0, -9_940, float("nan"), float("inf")):
            with self.subTest(base=bad):
                with self.assertRaises(ValueError):
                    self.engine.get_daily_price_limit_bounds(bad)

    def test_non_positive_override_raises(self):
        for bad in (0, -15):
            with self.subTest(pct=bad):
                with self.assertRaises(ValueError):
                    self.engine.get_daily_price_limit_bounds(10_000, limit_pct_override=bad)


class TestKrxOrderRouting(unittest.TestCase):

    def setUp(self):
        self.engine = KoreaExchangeKrxApiEngine()

    def test_valid_stock_order(self):
        # Samsung Electronics at KRW 150,000: the 50,000-200,000 band, so the
        # tick is KRW 100 (it was KRW 500 before 25 January 2023).
        report = self.engine.validate_and_route_order(
            KrxOrderPayload("005930", "BUY", 150_000, 10, 150_000)
        )
        self.assertEqual(report.status, "KRX_ORDER_VALIDATED")
        self.assertEqual(report.local_code, "005930")
        self.assertEqual(report.security_class, SECURITY_CLASS_STOCK)
        self.assertEqual(report.applicable_tick_size_krw, Decimal("100"))
        self.assertTrue(report.is_price_tick_valid)
        self.assertTrue(report.is_price_limit_valid)
        # 150,000 x 0.3 = 45,000, already a multiple of the KRW 100 tick.
        self.assertEqual(report.daily_price_limit_amount_krw, Decimal("45000"))
        self.assertEqual(report.lower_limit_price_krw, Decimal("105000"))
        self.assertEqual(report.upper_limit_price_krw, Decimal("195000"))

    def test_regression_krw_150200_is_valid_under_the_current_schedule(self):
        # Fails against an earlier schedule, which put 150,000-500,000 on a
        # KRW 500 tick and rejected this price.
        report = self.engine.validate_and_route_order(
            KrxOrderPayload("005930", "BUY", 150_200, 10, 150_000)
        )
        self.assertEqual(report.status, "KRX_ORDER_VALIDATED")
        self.assertEqual(report.applicable_tick_size_krw, Decimal("100"))

    def test_invalid_tick_size_rejected(self):
        # KRW 150,250 is not a multiple of the KRW 100 tick.
        report = self.engine.validate_and_route_order(
            KrxOrderPayload("005930", "BUY", 150_250, 10, 150_000)
        )
        self.assertEqual(report.status, "INVALID_TICK_SIZE")
        self.assertFalse(report.is_price_tick_valid)

    def test_fractional_price_rejected_without_float_tolerance(self):
        # A binary-float tolerance test can wave this through; Decimal modulo
        # does not.
        report = self.engine.validate_and_route_order(
            KrxOrderPayload("005930", "BUY", 150_000.5, 10, 150_000)
        )
        self.assertEqual(report.status, "INVALID_TICK_SIZE")

    def test_price_limit_exceeded_rejected(self):
        # KRW 210,000 against a KRW 150,000 base is +40%; the band tops out
        # at 195,000. 210,000 is tick-aligned (KRW 500 band), so the tick
        # check passes and the limit check is what rejects it.
        report = self.engine.validate_and_route_order(
            KrxOrderPayload("005930", "SELL", 210_000, 10, 150_000)
        )
        self.assertEqual(report.status, "PRICE_LIMIT_EXCEEDED")
        self.assertTrue(report.is_price_tick_valid)
        self.assertFalse(report.is_price_limit_valid)

    def test_etf_class_uses_the_flat_five_krw_tick(self):
        # KRW 15,005 is a legal ETF price but not a legal stock price: the
        # stock schedule puts 15,005 on a KRW 10 tick.
        etf = self.engine.validate_and_route_order(
            KrxOrderPayload("069500", "BUY", 15_005, 100, 15_000,
                            security_class=SECURITY_CLASS_ETF_ETN)
        )
        self.assertEqual(etf.status, "KRX_ORDER_VALIDATED")
        self.assertEqual(etf.applicable_tick_size_krw, Decimal("5"))
        stock = self.engine.validate_and_route_order(
            KrxOrderPayload("069500", "BUY", 15_005, 100, 15_000)
        )
        self.assertEqual(stock.status, "INVALID_TICK_SIZE")

    def test_price_limit_exempt_instrument_skips_the_band(self):
        # 정리매매 and 신주인수권증권·증서 carry no daily price limit; the tick
        # check still applies.
        report = self.engine.validate_and_route_order(
            KrxOrderPayload("005930", "SELL", 15_000, 10, 150_000,
                            price_limit_exempt=True)
        )
        self.assertEqual(report.status, "KRX_ORDER_VALIDATED")
        self.assertIsNone(report.upper_limit_price_krw)
        self.assertIsNone(report.lower_limit_price_krw)
        self.assertEqual(report.daily_price_limit_amount_krw, Decimal("0"))

    def test_truthy_non_bool_exempt_flag_raises(self):
        # A truthy string must not silently disable the band check.
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(
                KrxOrderPayload("005930", "SELL", 15_000, 10, 150_000,
                                price_limit_exempt="no")
            )

    def test_invalid_side_raises(self):
        for side in ("BYU", "", "buy sell", None, 1):
            with self.subTest(side=side):
                with self.assertRaises(ValueError):
                    self.engine.validate_and_route_order(
                        KrxOrderPayload("005930", side, 150_000, 10, 150_000)
                    )

    def test_side_is_normalised(self):
        report = self.engine.validate_and_route_order(
            KrxOrderPayload("005930", " sell ", 150_000, 10, 150_000)
        )
        self.assertEqual(report.side, "SELL")

    def test_invalid_quantity_raises(self):
        for qty in (0, -10, 10.5, "10", True, None):
            with self.subTest(quantity=qty):
                with self.assertRaises(ValueError):
                    self.engine.validate_and_route_order(
                        KrxOrderPayload("005930", "BUY", 150_000, qty, 150_000)
                    )

    def test_zero_base_price_raises_rather_than_dividing_by_zero(self):
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(
                KrxOrderPayload("005930", "BUY", 150_000, 10, 0)
            )

    def test_nan_price_raises_rather_than_silently_failing_comparisons(self):
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(
                KrxOrderPayload("005930", "BUY", float("nan"), 10, 150_000)
            )

    def test_status_never_reports_a_code_error(self):
        # 'INVALID_STOCK_CODE' is not a status: a bad code is a caller bug and
        # is raised, so it can never be mistaken for an exchange rejection.
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(
                KrxOrderPayload("00593", "BUY", 150_000, 10, 150_000)
            )

    def test_report_carries_the_band_so_a_rejection_can_be_repriced(self):
        report = self.engine.validate_and_route_order(
            KrxOrderPayload("005930", "BUY", 210_000, 10, 150_000)
        )
        self.assertEqual(report.upper_limit_price_krw, Decimal("195000"))
        self.assertIn("195,000", report.audit_notes)


if __name__ == "__main__":
    unittest.main()
