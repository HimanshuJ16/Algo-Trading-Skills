import math
import unittest
from single_name_concentration_limiter import SingleNameConcentrationLimiter, OrderCheckResult

class TestSingleNameConcentrationLimiter(unittest.TestCase):

    def setUp(self):
        # 5% NAV limit, 10% ADV limit
        self.limiter = SingleNameConcentrationLimiter(max_nav_pct=0.05, max_adv_pct=0.10, allow_downsizing=True)

    def test_order_approved_in_full(self):
        # NAV = $1,000,000, 5% NAV = $50,000. Current = $10,000 -> $40,000 room (400 shares @ $100)
        # ADV = 100,000 -> 10% ADV = 10,000 shares
        # Order: 200 shares ($20,000) -> Fits both limits
        res = self.limiter.evaluate_order(
            symbol="AAPL", side="BUY", proposed_quantity=200, price=100.0,
            portfolio_nav=1_000_000.0, current_position_value=10_000.0, twenty_day_adv=100_000
        )
        self.assertFalse(res.is_downsized)
        self.assertFalse(res.is_rejected)
        self.assertEqual(res.approved_quantity, 200)

    def test_nav_limit_downsizing(self):
        # NAV = $1,000,000, 5% NAV = $50,000. Current = $0 -> $50,000 room (500 shares @ $100)
        # ADV = 100,000 -> 10% ADV = 10,000 shares
        # Order: 800 shares ($80,000) -> Exceeds NAV limit ($50k)
        # Downsizes to 500 shares
        res = self.limiter.evaluate_order(
            symbol="AAPL", side="BUY", proposed_quantity=800, price=100.0,
            portfolio_nav=1_000_000.0, current_position_value=0.0, twenty_day_adv=100_000
        )
        self.assertTrue(res.is_downsized)
        self.assertTrue(res.nav_limit_breached)
        self.assertEqual(res.approved_quantity, 500)

    def test_adv_limit_downsizing(self):
        # NAV = $10,000,000, 5% NAV = $500,000 -> 5000 shares @ $100
        # ADV = 1,000 -> 10% ADV = 100 shares limit!
        # Order: 500 shares -> Exceeds ADV limit
        # Downsizes to 100 shares
        res = self.limiter.evaluate_order(
            symbol="ILLIQUID", side="BUY", proposed_quantity=500, price=100.0,
            portfolio_nav=10_000_000.0, current_position_value=0.0, twenty_day_adv=1_000
        )
        self.assertTrue(res.is_downsized)
        self.assertTrue(res.adv_limit_breached)
        self.assertEqual(res.approved_quantity, 100)

    def test_hard_rejection_without_downsizing(self):
        strict_limiter = SingleNameConcentrationLimiter(max_nav_pct=0.05, max_adv_pct=0.10, allow_downsizing=False)
        res = strict_limiter.evaluate_order(
            symbol="AAPL", side="BUY", proposed_quantity=800, price=100.0,
            portfolio_nav=1_000_000.0, current_position_value=0.0, twenty_day_adv=100_000
        )
        self.assertTrue(res.is_rejected)
        self.assertEqual(res.approved_quantity, 0)

    # --- Short-side NAV enforcement -------------------------------------------------

    def test_sell_from_flat_is_capped_by_nav_limit(self):
        # Regression: a SELL from a flat position used to bypass the NAV limit
        # entirely, so an unbounded single-name SHORT could be opened.
        # NAV = $1,000,000, 5% NAV = $50,000 -> 500 shares @ $100 short cap.
        # ADV = 10,000,000 -> 1,000,000 share ADV cap, so NAV is the binding limit.
        res = self.limiter.evaluate_order(
            symbol="AAPL", side="SELL", proposed_quantity=5_000, price=100.0,
            portfolio_nav=1_000_000.0, current_position_value=0.0, twenty_day_adv=10_000_000
        )
        self.assertTrue(res.is_downsized)
        self.assertTrue(res.nav_limit_breached)
        self.assertEqual(res.approved_quantity, 500)

    def test_buy_to_cover_is_capped_symmetrically(self):
        # Existing SHORT of $50,000 (-500 shares @ $100), at the 5% cap.
        # Buying back is de-risking: allowed to cover all 500 shares AND
        # re-establish up to 500 long -> 1,000 shares total.
        res = self.limiter.evaluate_order(
            symbol="AAPL", side="BUY", proposed_quantity=2_000, price=100.0,
            portfolio_nav=1_000_000.0, current_position_value=-50_000.0, twenty_day_adv=10_000_000
        )
        self.assertTrue(res.is_downsized)
        self.assertEqual(res.approved_quantity, 1_000)

    def test_adding_to_existing_short_uses_remaining_headroom(self):
        # Existing SHORT of $30,000 against a $50,000 cap -> $20,000 room = 200 shares.
        res = self.limiter.evaluate_order(
            symbol="AAPL", side="SELL", proposed_quantity=900, price=100.0,
            portfolio_nav=1_000_000.0, current_position_value=-30_000.0, twenty_day_adv=10_000_000
        )
        self.assertTrue(res.is_downsized)
        self.assertTrue(res.nav_limit_breached)
        self.assertEqual(res.approved_quantity, 200)

    def test_de_risking_sell_is_never_blocked(self):
        # Position drifted to $80,000 (8% NAV) above the $50,000 cap via price
        # appreciation. A SELL must not be blocked just because the position is
        # already non-compliant.
        res = self.limiter.evaluate_order(
            symbol="AAPL", side="SELL", proposed_quantity=300, price=100.0,
            portfolio_nav=1_000_000.0, current_position_value=80_000.0, twenty_day_adv=10_000_000
        )
        self.assertFalse(res.is_downsized)
        self.assertFalse(res.is_rejected)
        self.assertEqual(res.approved_quantity, 300)

    def test_buy_when_already_over_limit_is_rejected(self):
        # Position at $80,000 vs a $50,000 cap: no additional BUY may be approved.
        res = self.limiter.evaluate_order(
            symbol="AAPL", side="BUY", proposed_quantity=100, price=100.0,
            portfolio_nav=1_000_000.0, current_position_value=80_000.0, twenty_day_adv=10_000_000
        )
        self.assertTrue(res.is_rejected)
        self.assertTrue(res.nav_limit_breached)
        self.assertEqual(res.approved_quantity, 0)

    # --- Pending (unfilled) order exposure ------------------------------------------

    def test_pending_orders_consume_nav_headroom(self):
        # $50,000 cap, flat position, but $40,000 of BUY orders are already
        # working at the venue -> only $10,000 (100 shares) of headroom remains.
        res = self.limiter.evaluate_order(
            symbol="AAPL", side="BUY", proposed_quantity=500, price=100.0,
            portfolio_nav=1_000_000.0, current_position_value=0.0,
            twenty_day_adv=10_000_000, pending_exposure_value=40_000.0
        )
        self.assertTrue(res.is_downsized)
        self.assertEqual(res.approved_quantity, 100)

    def test_pending_exposure_defaults_to_zero(self):
        # Backwards compatibility: omitting pending_exposure_value must behave
        # exactly as passing 0.0.
        kwargs = dict(
            symbol="AAPL", side="BUY", proposed_quantity=800, price=100.0,
            portfolio_nav=1_000_000.0, current_position_value=0.0, twenty_day_adv=100_000
        )
        self.assertEqual(
            self.limiter.evaluate_order(**kwargs).approved_quantity,
            self.limiter.evaluate_order(pending_exposure_value=0.0, **kwargs).approved_quantity,
        )

    # --- Boundary conditions ---------------------------------------------------------

    def test_order_exactly_at_nav_limit_is_approved(self):
        # Exactly 500 shares = exactly $50,000 = exactly 5% NAV. Limit is inclusive.
        res = self.limiter.evaluate_order(
            symbol="AAPL", side="BUY", proposed_quantity=500, price=100.0,
            portfolio_nav=1_000_000.0, current_position_value=0.0, twenty_day_adv=10_000_000
        )
        self.assertFalse(res.is_downsized)
        self.assertEqual(res.approved_quantity, 500)

    def test_order_exactly_at_adv_limit_is_approved(self):
        # 10% of 1,000 ADV = exactly 100 shares.
        res = self.limiter.evaluate_order(
            symbol="ILLIQUID", side="BUY", proposed_quantity=100, price=100.0,
            portfolio_nav=10_000_000.0, current_position_value=0.0, twenty_day_adv=1_000
        )
        self.assertFalse(res.is_downsized)
        self.assertEqual(res.approved_quantity, 100)

    def test_downsizing_floors_partial_shares(self):
        # $50,000 room at $300/share = 166.67 shares -> must floor to 166,
        # never round up past the limit.
        res = self.limiter.evaluate_order(
            symbol="AAPL", side="BUY", proposed_quantity=1_000, price=300.0,
            portfolio_nav=1_000_000.0, current_position_value=0.0, twenty_day_adv=10_000_000
        )
        self.assertEqual(res.approved_quantity, 166)
        self.assertLessEqual(res.approved_quantity * 300.0, 50_000.0)

    # --- Invalid input ---------------------------------------------------------------

    def test_unknown_side_raises(self):
        # Regression: an unrecognised side used to fall through to the SELL
        # branch, which applied no NAV limit at all.
        with self.assertRaises(ValueError):
            self.limiter.evaluate_order(
                symbol="AAPL", side="SEL", proposed_quantity=100, price=100.0,
                portfolio_nav=1_000_000.0, current_position_value=0.0, twenty_day_adv=100_000
            )

    def test_non_string_side_raises_value_error(self):
        for bad_side in (None, 1, ["BUY"]):
            with self.assertRaises(ValueError, msg=repr(bad_side)):
                self.limiter.evaluate_order(
                    symbol="AAPL", side=bad_side, proposed_quantity=100, price=100.0,
                    portfolio_nav=1_000_000.0, current_position_value=0.0, twenty_day_adv=100_000
                )

    def test_side_is_case_and_whitespace_insensitive(self):
        res = self.limiter.evaluate_order(
            symbol="AAPL", side=" buy ", proposed_quantity=100, price=100.0,
            portfolio_nav=1_000_000.0, current_position_value=0.0, twenty_day_adv=100_000
        )
        self.assertFalse(res.is_rejected)
        self.assertEqual(res.approved_quantity, 100)

    def test_percent_supplied_as_whole_number_raises(self):
        # Guards the unit error of passing 5 to mean 5%.
        with self.assertRaises(ValueError):
            SingleNameConcentrationLimiter(max_nav_pct=5.0)
        with self.assertRaises(ValueError):
            SingleNameConcentrationLimiter(max_adv_pct=10.0)

    def test_non_positive_limits_raise(self):
        with self.assertRaises(ValueError):
            SingleNameConcentrationLimiter(max_nav_pct=0.0)
        with self.assertRaises(ValueError):
            SingleNameConcentrationLimiter(max_adv_pct=-0.1)

    def test_missing_adv_fails_closed(self):
        # Missing liquidity data must reject, never be read as "no ADV limit".
        for bad_adv in (0, -100, None):
            res = self.limiter.evaluate_order(
                symbol="NEWLIST", side="BUY", proposed_quantity=10, price=100.0,
                portfolio_nav=1_000_000.0, current_position_value=0.0, twenty_day_adv=bad_adv
            )
            self.assertTrue(res.is_rejected, msg=f"adv={bad_adv!r}")
            self.assertTrue(res.adv_limit_breached, msg=f"adv={bad_adv!r}")
            self.assertEqual(res.approved_quantity, 0, msg=f"adv={bad_adv!r}")

    def test_nan_inputs_raise_rather_than_propagate(self):
        nan = float("nan")
        base = dict(
            symbol="AAPL", side="BUY", proposed_quantity=100, price=100.0,
            portfolio_nav=1_000_000.0, current_position_value=0.0, twenty_day_adv=100_000
        )
        for field in ("price", "portfolio_nav", "current_position_value"):
            with self.assertRaises(ValueError, msg=field):
                self.limiter.evaluate_order(**{**base, field: nan})
        with self.assertRaises(ValueError):
            self.limiter.evaluate_order(pending_exposure_value=nan, **base)

    def test_non_positive_nav_and_price_raise(self):
        base = dict(
            symbol="AAPL", side="BUY", proposed_quantity=100, price=100.0,
            portfolio_nav=1_000_000.0, current_position_value=0.0, twenty_day_adv=100_000
        )
        with self.assertRaises(ValueError):
            self.limiter.evaluate_order(**{**base, "portfolio_nav": 0.0})
        with self.assertRaises(ValueError):
            self.limiter.evaluate_order(**{**base, "price": -1.0})

    def test_non_positive_quantity_is_rejected(self):
        res = self.limiter.evaluate_order(
            symbol="AAPL", side="BUY", proposed_quantity=0, price=100.0,
            portfolio_nav=1_000_000.0, current_position_value=0.0, twenty_day_adv=100_000
        )
        self.assertTrue(res.is_rejected)
        self.assertEqual(res.approved_quantity, 0)

    # --- HHI -------------------------------------------------------------------------

    def test_hhi_calculation(self):
        # 10 equal positions of $100,000 -> Each weight = 0.10
        # HHI = 10 * (0.10^2) = 0.10
        # N_eff = 1 / 0.10 = 10.0
        positions = [100_000.0] * 10
        hhi_res = SingleNameConcentrationLimiter.calculate_hhi(positions)

        self.assertEqual(hhi_res["hhi"], 0.10)
        self.assertEqual(hhi_res["effective_n"], 10.0)

    def test_hhi_single_position_is_fully_concentrated(self):
        # One position -> w = 1, HHI = 1, N_eff = 1.
        hhi_res = SingleNameConcentrationLimiter.calculate_hhi([250_000.0])
        self.assertEqual(hhi_res["hhi"], 1.0)
        self.assertEqual(hhi_res["effective_n"], 1.0)

    def test_hhi_uses_gross_exposure_for_shorts(self):
        # A $100k long and a $100k short are two equal gross positions, not a
        # netted zero: HHI = 0.5, N_eff = 2.
        hhi_res = SingleNameConcentrationLimiter.calculate_hhi([100_000.0, -100_000.0])
        self.assertEqual(hhi_res["hhi"], 0.5)
        self.assertEqual(hhi_res["effective_n"], 2.0)

    def test_hhi_unequal_weights_independent_expectation(self):
        # Weights 0.5 / 0.3 / 0.2 -> HHI = 0.25 + 0.09 + 0.04 = 0.38
        # N_eff = 1 / 0.38 = 2.6315... -> 2.63
        hhi_res = SingleNameConcentrationLimiter.calculate_hhi([500.0, 300.0, 200.0])
        self.assertEqual(hhi_res["hhi"], 0.38)
        self.assertEqual(hhi_res["effective_n"], 2.63)

    def test_hhi_empty_portfolio_is_undefined(self):
        # Concentration is undefined with no exposure; NaN must not read as
        # "maximally diversified" nor trip an `effective_n < threshold` alert.
        for values in ([], [0.0, 0.0]):
            hhi_res = SingleNameConcentrationLimiter.calculate_hhi(values)
            self.assertTrue(math.isnan(hhi_res["hhi"]), msg=repr(values))
            self.assertTrue(math.isnan(hhi_res["effective_n"]), msg=repr(values))
            self.assertFalse(hhi_res["effective_n"] < 3.0, msg=repr(values))

if __name__ == '__main__':
    unittest.main()
