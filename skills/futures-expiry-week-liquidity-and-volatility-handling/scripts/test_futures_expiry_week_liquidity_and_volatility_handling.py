import math
import unittest

from futures_expiry_week_liquidity_and_volatility_handling import (
    FuturesExpiryRiskHandlerEngine,
    FuturesOrderBookState,
    REASON_EXPIRATION_CUTOFF,
    REASON_PAST_LAST_TRADING_DAY,
    REASON_QUAD_WITCHING,
    REASON_THIN_DEPTH,
    REASON_WIDE_SPREAD,
    STATUS_EXPIRED_ESCALATE,
    STATUS_MANDATORY_ROLL,
    STATUS_NORMAL,
    STATUS_RESTRICTED,
)


def make_book(**overrides):
    """A liquid, far-from-expiry snapshot; override one field per test."""
    defaults = dict(
        symbol="ESH6",
        days_to_expiration=15,
        bid_ask_spread_ticks=1.0,
        top_of_book_depth_qty=1000,
        baseline_average_depth_qty=1000,
        is_quadruple_witching_week=False,
    )
    defaults.update(overrides)
    return FuturesOrderBookState(**defaults)


class TestFuturesExpiryRiskHandlerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = FuturesExpiryRiskHandlerEngine(
            max_spread_ticks_threshold=2.0,
            min_depth_ratio_threshold=0.30,
            mandatory_roll_dbe_cutoff=2,
        )

    # --- Baseline behaviour ------------------------------------------------

    def test_normal_market_execution_allows_full_size(self):
        book = FuturesOrderBookState(
            symbol="ESH6", days_to_expiration=15, bid_ask_spread_ticks=1.0,
            top_of_book_depth_qty=1000, baseline_average_depth_qty=1000,
            is_quadruple_witching_week=False
        )
        report = self.engine.audit_expiry_execution_safeguards(book, base_order_qty=100)

        self.assertEqual(report.status, STATUS_NORMAL)
        self.assertTrue(report.is_market_orders_allowed)
        self.assertEqual(report.size_haircut_factor, 1.0)
        self.assertEqual(report.adjusted_max_order_qty, 100)
        self.assertTrue(report.is_new_entry_allowed)
        self.assertEqual(report.restriction_reasons, [])
        self.assertFalse(report.is_order_size_suppressed)
        self.assertFalse(report.requires_manual_escalation)
        self.assertEqual(report.depth_ratio, 1.0)

    def test_quad_witching_and_wide_spread_applies_haircut_and_blocks_market(self):
        # Quad witching + Spread = 3.5 ticks + Depth = 200 (20% baseline)
        # -> Haircut 50% & Block Market Orders.
        book = FuturesOrderBookState(
            symbol="ESH6", days_to_expiration=4, bid_ask_spread_ticks=3.5,
            top_of_book_depth_qty=200, baseline_average_depth_qty=1000,
            is_quadruple_witching_week=True
        )
        report = self.engine.audit_expiry_execution_safeguards(book, base_order_qty=100)

        self.assertEqual(report.status, STATUS_RESTRICTED)
        self.assertFalse(report.is_market_orders_allowed)
        self.assertEqual(report.size_haircut_factor, 0.50)
        self.assertEqual(report.adjusted_max_order_qty, 50)
        self.assertTrue(report.is_new_entry_allowed)
        self.assertEqual(report.depth_ratio, 0.20)
        self.assertEqual(
            report.restriction_reasons,
            [REASON_WIDE_SPREAD, REASON_THIN_DEPTH, REASON_QUAD_WITCHING],
        )

    def test_critical_dbe_triggers_mandatory_roll(self):
        # DBE = 1d <= 2d cutoff -> MANDATORY ROLL REQUIRED.
        book = FuturesOrderBookState(
            symbol="ESH6", days_to_expiration=1, bid_ask_spread_ticks=1.5,
            top_of_book_depth_qty=500, baseline_average_depth_qty=1000,
            is_quadruple_witching_week=False
        )
        report = self.engine.audit_expiry_execution_safeguards(book, base_order_qty=100)

        self.assertEqual(report.status, STATUS_MANDATORY_ROLL)
        self.assertTrue(report.is_mandatory_roll_required)
        self.assertFalse(report.is_new_entry_allowed)
        self.assertEqual(report.restriction_reasons, [REASON_EXPIRATION_CUTOFF])

    def test_quad_witching_alone_haircuts_a_tight_deep_book(self):
        # The flag is a policy override, not a measurement: it halves size even
        # when spread and depth are both normal.
        book = make_book(days_to_expiration=4, is_quadruple_witching_week=True)
        report = self.engine.audit_expiry_execution_safeguards(book, base_order_qty=100)

        self.assertEqual(report.status, STATUS_RESTRICTED)
        self.assertTrue(report.is_market_orders_allowed)  # spread is still tight
        self.assertFalse(report.is_depth_thinned)
        self.assertEqual(report.adjusted_max_order_qty, 50)
        self.assertEqual(report.restriction_reasons, [REASON_QUAD_WITCHING])

    # --- Fail-open regressions (each of these previously produced the *least*
    # --- restrictive report from data the engine could not read) -----------

    def test_non_finite_spread_raises_instead_of_permitting_market_orders(self):
        # float('nan') > 2.0 is False, so an unvalidated engine reports
        # is_market_orders_allowed=True on a spread it cannot read.
        for bad_spread in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(spread=bad_spread):
                book = make_book(bid_ask_spread_ticks=bad_spread)
                with self.assertRaises(ValueError):
                    self.engine.audit_expiry_execution_safeguards(book, 100)

    def test_non_finite_depth_raises_instead_of_cancelling_the_haircut(self):
        # NaN / NaN < 0.30 is False, so an unvalidated engine skips the haircut.
        book = make_book(top_of_book_depth_qty=float("nan"))
        with self.assertRaises(ValueError):
            self.engine.audit_expiry_execution_safeguards(book, 100)

    def test_zero_baseline_depth_raises_instead_of_being_clamped_to_one(self):
        # Clamping the baseline to 1 turns a depth of 200 into a ratio of 200.0,
        # which clears the 0.30 threshold and cancels the haircut.
        for bad_baseline in (0, -1000):
            with self.subTest(baseline=bad_baseline):
                book = make_book(
                    top_of_book_depth_qty=200, baseline_average_depth_qty=bad_baseline
                )
                with self.assertRaises(ValueError):
                    self.engine.audit_expiry_execution_safeguards(book, 100)

    def test_crossed_book_raises_instead_of_reading_as_tight(self):
        book = make_book(bid_ask_spread_ticks=-0.5)
        with self.assertRaises(ValueError):
            self.engine.audit_expiry_execution_safeguards(book, 100)

    def test_negative_depth_raises(self):
        book = make_book(top_of_book_depth_qty=-10)
        with self.assertRaises(ValueError):
            self.engine.audit_expiry_execution_safeguards(book, 100)

    # --- Input validation ---------------------------------------------------

    def test_invalid_snapshot_fields_raise(self):
        bad_books = {
            "empty symbol": make_book(symbol="   "),
            "non-string symbol": make_book(symbol=None),
            "float dbe": make_book(days_to_expiration=2.0),
            "nan dbe": make_book(days_to_expiration=float("nan")),
            "bool dbe": make_book(days_to_expiration=True),
            "string spread": make_book(bid_ask_spread_ticks="1.0"),
            "non-bool quad flag": make_book(is_quadruple_witching_week=1),
        }
        for label, book in bad_books.items():
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    self.engine.audit_expiry_execution_safeguards(book, 100)

    def test_invalid_base_order_qty_raises(self):
        book = make_book()
        for bad_qty in (0, -5, 10.0, True, None, "10"):
            with self.subTest(qty=bad_qty):
                with self.assertRaises(ValueError):
                    self.engine.audit_expiry_execution_safeguards(book, bad_qty)

    def test_non_snapshot_argument_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_expiry_execution_safeguards(
                {"symbol": "ESH6", "days_to_expiration": 15}, 100
            )

    def test_invalid_constructor_thresholds_raise(self):
        bad_kwargs = {
            "negative spread threshold": dict(max_spread_ticks_threshold=-1.0),
            "nan spread threshold": dict(max_spread_ticks_threshold=float("nan")),
            "zero depth ratio": dict(min_depth_ratio_threshold=0.0),
            "depth ratio above 1": dict(min_depth_ratio_threshold=1.5),
            "negative dbe cutoff": dict(mandatory_roll_dbe_cutoff=-1),
            "float dbe cutoff": dict(mandatory_roll_dbe_cutoff=2.0),
            "zero haircut": dict(size_haircut_factor=0.0),
            "haircut above 1": dict(size_haircut_factor=1.5),
        }
        for label, kwargs in bad_kwargs.items():
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    FuturesExpiryRiskHandlerEngine(**kwargs)

    # --- Threshold boundaries ----------------------------------------------

    def test_spread_exactly_at_threshold_is_not_wide(self):
        report = self.engine.audit_expiry_execution_safeguards(
            make_book(bid_ask_spread_ticks=2.0), 100
        )
        self.assertFalse(report.is_spread_wide)
        self.assertTrue(report.is_market_orders_allowed)
        self.assertEqual(report.status, STATUS_NORMAL)

    def test_spread_just_above_threshold_blocks_market_orders(self):
        report = self.engine.audit_expiry_execution_safeguards(
            make_book(bid_ask_spread_ticks=2.25), 100
        )
        self.assertTrue(report.is_spread_wide)
        self.assertFalse(report.is_market_orders_allowed)
        self.assertEqual(report.status, STATUS_RESTRICTED)
        # A wide spread alone does not cut size — it changes the order type.
        self.assertEqual(report.size_haircut_factor, 1.0)
        self.assertEqual(report.adjusted_max_order_qty, 100)

    def test_depth_ratio_exactly_at_threshold_is_not_thinned(self):
        # 300 / 1000 == 0.30 exactly; the test is strictly-less-than.
        report = self.engine.audit_expiry_execution_safeguards(
            make_book(top_of_book_depth_qty=300), 100
        )
        self.assertEqual(report.depth_ratio, 0.30)
        self.assertFalse(report.is_depth_thinned)
        self.assertEqual(report.adjusted_max_order_qty, 100)
        self.assertEqual(report.status, STATUS_NORMAL)

    def test_depth_ratio_just_below_threshold_triggers_haircut(self):
        report = self.engine.audit_expiry_execution_safeguards(
            make_book(top_of_book_depth_qty=299), 100
        )
        self.assertTrue(report.is_depth_thinned)
        self.assertEqual(report.size_haircut_factor, 0.50)
        self.assertEqual(report.adjusted_max_order_qty, 50)
        self.assertEqual(report.restriction_reasons, [REASON_THIN_DEPTH])

    def test_dbe_cutoff_is_inclusive_and_zero_is_still_tradable(self):
        for dbe, expected in ((3, STATUS_NORMAL), (2, STATUS_MANDATORY_ROLL),
                              (0, STATUS_MANDATORY_ROLL)):
            with self.subTest(dbe=dbe):
                report = self.engine.audit_expiry_execution_safeguards(
                    make_book(days_to_expiration=dbe), 100
                )
                self.assertEqual(report.status, expected)
                # DBE == 0 is the final session: still tradable, so a roll is
                # mandated rather than escalated.
                self.assertEqual(
                    report.is_mandatory_roll_required, expected == STATUS_MANDATORY_ROLL
                )
                self.assertFalse(report.requires_manual_escalation)

    # --- Past last trading day ---------------------------------------------

    def test_expired_contract_escalates_and_does_not_mandate_a_roll(self):
        book = make_book(days_to_expiration=-1, bid_ask_spread_ticks=1.0)
        with self.assertLogs(
            "futures_expiry_week_liquidity_and_volatility_handling", level="CRITICAL"
        ):
            report = self.engine.audit_expiry_execution_safeguards(book, 100)

        self.assertEqual(report.status, STATUS_EXPIRED_ESCALATE)
        self.assertTrue(report.requires_manual_escalation)
        # No size is executable in a contract that has stopped trading, and the
        # leg cannot be lifted, so a roll must not be reported as the remedy.
        self.assertEqual(report.adjusted_max_order_qty, 0)
        self.assertFalse(report.is_market_orders_allowed)
        self.assertFalse(report.is_new_entry_allowed)
        self.assertFalse(report.is_mandatory_roll_required)
        self.assertTrue(report.is_order_size_suppressed)
        self.assertIn(REASON_PAST_LAST_TRADING_DAY, report.restriction_reasons)
        self.assertNotIn(REASON_EXPIRATION_CUTOFF, report.restriction_reasons)

    # --- Cross-branch reporting --------------------------------------------

    def test_mandatory_roll_still_reports_spread_and_depth_state(self):
        # Regression: the roll branch used to drop the microstructure findings,
        # so the report told the caller to roll without saying that the roll
        # could not be executed with a market order.
        book = make_book(
            days_to_expiration=1,
            bid_ask_spread_ticks=4.0,
            top_of_book_depth_qty=100,
            is_quadruple_witching_week=True,
        )
        report = self.engine.audit_expiry_execution_safeguards(book, base_order_qty=100)

        self.assertEqual(report.status, STATUS_MANDATORY_ROLL)
        self.assertFalse(report.is_market_orders_allowed)
        self.assertTrue(report.is_spread_wide)
        self.assertTrue(report.is_depth_thinned)
        self.assertEqual(report.adjusted_max_order_qty, 50)
        self.assertEqual(
            report.restriction_reasons,
            [
                REASON_EXPIRATION_CUTOFF,
                REASON_WIDE_SPREAD,
                REASON_THIN_DEPTH,
                REASON_QUAD_WITCHING,
            ],
        )
        self.assertIn("BLOCKED", report.audit_notes)

    # --- Sizing arithmetic --------------------------------------------------

    def test_haircut_floors_rather_than_rounds(self):
        # floor(7 * 0.50) == 3, not 4 — the cap can never exceed the budget.
        report = self.engine.audit_expiry_execution_safeguards(
            make_book(top_of_book_depth_qty=100), base_order_qty=7
        )
        self.assertEqual(report.adjusted_max_order_qty, 3)
        self.assertFalse(report.is_order_size_suppressed)

    def test_haircut_that_floors_to_zero_is_flagged_as_suppressed(self):
        report = self.engine.audit_expiry_execution_safeguards(
            make_book(top_of_book_depth_qty=100), base_order_qty=1
        )
        self.assertEqual(report.adjusted_max_order_qty, 0)
        self.assertTrue(report.is_order_size_suppressed)
        self.assertIn("SIZE SUPPRESSED", report.audit_notes)

    def test_configured_haircut_factor_is_honoured(self):
        engine = FuturesExpiryRiskHandlerEngine(size_haircut_factor=0.25)
        report = engine.audit_expiry_execution_safeguards(
            make_book(top_of_book_depth_qty=100), base_order_qty=100
        )
        self.assertEqual(report.size_haircut_factor, 0.25)
        self.assertEqual(report.adjusted_max_order_qty, 25)

    def test_depth_ratio_is_reported_unclamped(self):
        # 150 / 1200 == 0.125, computed independently of the engine.
        report = self.engine.audit_expiry_execution_safeguards(
            make_book(top_of_book_depth_qty=150, baseline_average_depth_qty=1200), 100
        )
        self.assertTrue(math.isclose(report.depth_ratio, 0.125))

    def test_engine_holds_no_state_between_calls(self):
        restricted = self.engine.audit_expiry_execution_safeguards(
            make_book(top_of_book_depth_qty=100), 100
        )
        normal = self.engine.audit_expiry_execution_safeguards(make_book(), 100)
        self.assertEqual(restricted.status, STATUS_RESTRICTED)
        self.assertEqual(normal.status, STATUS_NORMAL)
        self.assertEqual(normal.adjusted_max_order_qty, 100)
        self.assertEqual(normal.restriction_reasons, [])


if __name__ == '__main__':
    unittest.main()
