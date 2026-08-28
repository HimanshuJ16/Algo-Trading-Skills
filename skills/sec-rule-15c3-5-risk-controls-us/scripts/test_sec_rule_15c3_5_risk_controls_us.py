import itertools
import logging
import threading
import unittest

from sec_rule_15c3_5_risk_controls_us import (
    SecRule15C35RiskControlsUsEngine, ComplianceResult,
    MarketAccessOrder, SecRule15c35Limits, MarketAccessRuleCode,
    MarketAccessCheckResult, VALID_SIDES,
)

logging.getLogger("sec_rule_15c3_5_risk_controls_us").addHandler(logging.NullHandler())
logging.getLogger("sec_rule_15c3_5_risk_controls_us").propagate = False

RC = MarketAccessRuleCode


def make_limits(**overrides):
    """Test limit set. These are placeholders, not regulatory thresholds."""
    base = dict(
        firm_credit_cap_usd=5000000.0,
        account_credit_cap_usd=500000.0,
        max_single_order_notional_usd=100000.0,
        max_single_order_qty=1000.0,
        max_price_collar_pct=0.05,
        max_order_rate_per_sec=100,
        restricted_symbols={"SANCTIONED_CO", "RESTRICTED_TICKER"},
    )
    base.update(overrides)
    return SecRule15c35Limits(**base)


def make_order(**overrides):
    base = dict(
        order_id="ORD_001",
        account_id="ACC_100",
        symbol="AAPL",
        side="BUY",
        quantity=100.0,
        price=150.0,
        nbbo_mid_price=150.0,
    )
    base.update(overrides)
    return MarketAccessOrder(**base)


class TestSecRule15C35Legacy(unittest.TestCase):
    """The legacy structural helper. Not a 15c3-5 control -- but fail-closed."""

    def setUp(self):
        self.engine = SecRule15C35RiskControlsUsEngine()

    def test_empty(self):
        res = self.engine.run_checks({})
        self.assertFalse(res.is_compliant)

    def test_negative_size(self):
        res = self.engine.run_checks({'size': -10})
        self.assertFalse(res.is_compliant)

    def test_valid(self):
        res = self.engine.run_checks({'size': 100})
        self.assertTrue(res.is_compliant)

    def test_nan_size_is_not_compliant(self):
        # Regression: `float('nan') < 0` is False, so the old implementation returned
        # is_compliant=True for a NaN size.
        self.assertFalse(self.engine.run_checks({'size': float('nan')}).is_compliant)

    def test_zero_and_missing_and_non_dict_size_are_not_compliant(self):
        for payload in ({'size': 0}, {'symbol': 'AAPL'}, {'size': '100'}):
            with self.subTest(payload=payload):
                self.assertFalse(self.engine.run_checks(payload).is_compliant)
        self.assertFalse(self.engine.run_checks(None).is_compliant)


class TestSecRule15c35LimitsValidation(unittest.TestCase):
    """A gate that cannot state its own limits must not issue verdicts."""

    def test_non_positive_or_non_finite_caps_raise(self):
        for name in (
            "firm_credit_cap_usd", "account_credit_cap_usd",
            "max_single_order_notional_usd", "max_single_order_qty",
        ):
            for bad in (0.0, -1.0, float('nan'), float('inf'), None, "1000"):
                with self.subTest(field=name, value=bad):
                    with self.assertRaises(ValueError):
                        make_limits(**{name: bad})

    def test_account_cap_above_firm_cap_raises(self):
        with self.assertRaises(ValueError):
            make_limits(firm_credit_cap_usd=100.0, account_credit_cap_usd=101.0)

    def test_negative_collar_and_bad_rate_and_bad_window_raise(self):
        with self.assertRaises(ValueError):
            make_limits(max_price_collar_pct=-0.01)
        with self.assertRaises(ValueError):
            make_limits(max_price_collar_pct=float('nan'))
        for bad_rate in (0, -1, 1.5, True):
            with self.subTest(rate=bad_rate):
                with self.assertRaises(ValueError):
                    make_limits(max_order_rate_per_sec=bad_rate)
        for name in ("burst_window_sec", "duplicate_window_sec"):
            with self.subTest(field=name):
                with self.assertRaises(ValueError):
                    make_limits(**{name: 0.0})

    def test_bare_string_restricted_list_raises(self):
        # "AAPL" would iterate into {'A', 'P', 'L'} and restrict three one-letter
        # tickers while leaving AAPL tradable.
        with self.assertRaises(TypeError):
            make_limits(restricted_symbols="AAPL")

    def test_restricted_symbols_normalised_at_construction(self):
        limits = make_limits(restricted_symbols=[" restricted_ticker ", "", None])
        self.assertEqual(limits.restricted_symbols, frozenset({"RESTRICTED_TICKER"}))

    def test_limits_are_frozen(self):
        limits = make_limits()
        with self.assertRaises(Exception):
            limits.max_single_order_qty = 1e9

    def test_with_updates_revalidates(self):
        limits = make_limits()
        self.assertEqual(limits.with_updates(max_single_order_qty=2000.0)
                         .max_single_order_qty, 2000.0)
        with self.assertRaises(ValueError):
            limits.with_updates(max_single_order_qty=0.0)

    def test_engine_rejects_a_non_limits_object(self):
        with self.assertRaises(TypeError):
            SecRule15C35RiskControlsUsEngine(limits={"max_single_order_qty": 10})


class TestSecRule15C35MarketAccessEngine(unittest.TestCase):

    def setUp(self):
        # A deterministic clock advancing a minute per evaluation, so the order-by-order
        # tests are never perturbed by the burst or duplicate windows. Those two
        # controls are exercised against their own engines further down. (A real
        # monotonic clock would not do: its Windows resolution is coarse enough that
        # two consecutive evaluations can read the same value.)
        self.limits = make_limits()
        self.engine = SecRule15C35RiskControlsUsEngine(
            limits=self.limits, clock=itertools.count(1000.0, 60.0).__next__
        )

    def codes(self, order):
        return set(self.engine.evaluate_market_access_order(order).triggered_violations)

    # ------------------------------------------------------------- happy path

    def test_valid_order_allowed(self):
        res = self.engine.evaluate_market_access_order(make_order())
        self.assertTrue(res.is_allowed)
        self.assertEqual(len(res.triggered_violations), 0)
        self.assertEqual(res.notional_usd, 15000.0)

    def test_returns_a_market_access_check_result(self):
        res = self.engine.evaluate_market_access_order(make_order())
        self.assertIsInstance(res, MarketAccessCheckResult)
        self.assertEqual(res.order_id, "ORD_001")
        self.assertGreaterEqual(res.latency_microseconds, 0.0)

    def test_non_order_argument_raises(self):
        with self.assertRaises(TypeError):
            self.engine.evaluate_market_access_order({"order_id": "X"})

    # -------------------------------------------------- (c)(1)(ii) size limbs

    def test_notional_cap_and_qty_cap_breach(self):
        # Qty 2000 > 1000, Notional $400k > $100k cap.
        order = make_order(order_id="ORD_002", quantity=2000.0, price=200.0,
                           nbbo_mid_price=200.0)
        self.assertEqual(
            self.codes(order),
            {RC.SINGLE_ORDER_QTY_CAP, RC.SINGLE_ORDER_NOTIONAL_CAP},
        )

    def test_quantity_exactly_at_cap_is_allowed_and_one_more_is_not(self):
        # Pins `>` against `>=`. Notional held under its own cap: 1000 x 50 = $50k.
        at_cap = make_order(quantity=1000.0, price=50.0, nbbo_mid_price=50.0)
        self.assertEqual(self.codes(at_cap), set())
        over = make_order(order_id="ORD_B", quantity=1000.01, price=50.0,
                          nbbo_mid_price=50.0)
        self.assertEqual(self.codes(over), {RC.SINGLE_ORDER_QTY_CAP})

    def test_notional_exactly_at_cap_is_allowed_and_one_cent_more_is_not(self):
        # 500 x 200 = $100,000 exactly, against a $100,000 cap.
        at_cap = make_order(quantity=500.0, price=200.0, nbbo_mid_price=200.0)
        self.assertEqual(self.codes(at_cap), set())
        over = make_order(order_id="ORD_B", quantity=500.0, price=200.01,
                          nbbo_mid_price=200.0)
        self.assertEqual(self.codes(over), {RC.SINGLE_ORDER_NOTIONAL_CAP})

    # -------------------------------------------------- (c)(1)(i) credit limbs

    def test_account_credit_cap_boundary(self):
        # Committed $400k + $100k order = $500k exactly, against a $500k account cap.
        at_cap = make_order(quantity=500.0, price=200.0, nbbo_mid_price=200.0,
                            accumulated_credit_used_usd=400000.0)
        self.assertEqual(self.codes(at_cap), set())
        over = make_order(order_id="ORD_B", quantity=500.0, price=200.0,
                          nbbo_mid_price=200.0,
                          accumulated_credit_used_usd=400000.01)
        self.assertEqual(self.codes(over), {RC.CREDIT_CAP_EXCEEDED})

    def test_firm_credit_cap_is_enforced_independently_of_the_account_cap(self):
        # (c)(1)(i) requires the threshold "in the aggregate for each customer and the
        # broker or dealer". This account is well inside its own limit; the firm is not.
        order = make_order(quantity=500.0, price=200.0, nbbo_mid_price=200.0,
                           accumulated_credit_used_usd=0.0,
                           accumulated_firm_credit_used_usd=4999999.0)
        self.assertEqual(self.codes(order), {RC.FIRM_CREDIT_CAP_EXCEEDED})

    def test_firm_credit_cap_boundary_is_allowed(self):
        order = make_order(quantity=500.0, price=200.0, nbbo_mid_price=200.0,
                           accumulated_firm_credit_used_usd=4900000.0)
        self.assertEqual(self.codes(order), set())

    # ---------------------------------------------- (c)(1)(ii) price parameter

    def test_fat_finger_price_collar_breach(self):
        # Price $200 vs NBBO $150 (33.3% deviation > 5% collar).
        order = make_order(order_id="ORD_003", price=200.0, nbbo_mid_price=150.0)
        self.assertEqual(self.codes(order), {RC.PRICE_COLLAR_FAT_FINGER})

    def test_price_exactly_at_the_collar_is_allowed(self):
        # Regression: the division form `abs(p - m) / m > 0.05` evaluates
        # 20.1345 / 402.69 as 0.05000000000000001 and rejects a compliant order.
        mid = 402.69
        order = make_order(quantity=1.0, price=422.8245, nbbo_mid_price=mid)
        self.assertEqual(abs(422.8245 - mid) / mid, 0.05000000000000001)
        self.assertEqual(self.codes(order), set())

    def test_collar_breach_just_past_the_boundary(self):
        order = make_order(quantity=1.0, price=157.51, nbbo_mid_price=150.0)
        self.assertEqual(self.codes(order), {RC.PRICE_COLLAR_FAT_FINGER})
        allowed = make_order(order_id="ORD_B", quantity=1.0, price=157.5,
                             nbbo_mid_price=150.0)
        self.assertEqual(self.codes(allowed), set())

    def test_collar_applies_symmetrically_below_the_mid(self):
        order = make_order(quantity=1.0, price=142.49, nbbo_mid_price=150.0)
        self.assertEqual(self.codes(order), {RC.PRICE_COLLAR_FAT_FINGER})

    # ------------------------------------------------- fail-closed on bad data

    def test_unusable_reference_price_blocks_rather_than_skipping_the_collar(self):
        # Regression: `if nbbo_mid_price > 0` silently disabled the collar for a zero,
        # negative, NaN or absent mid, letting a $99,999 price through untested.
        for mid in (0.0, -1.0, float('nan'), float('inf'), None, "150"):
            with self.subTest(mid=mid):
                order = make_order(quantity=1.0, price=99999.0, nbbo_mid_price=mid)
                self.assertEqual(self.codes(order), {RC.REFERENCE_PRICE_UNAVAILABLE})

    def test_missing_reference_price_is_not_defaulted_to_a_made_up_value(self):
        # Regression: nbbo_mid_price used to default to 100.0, so an order priced at
        # 98.0 with no reference price at all "passed" a collar against a fiction.
        order = MarketAccessOrder(
            order_id="ORD_X", account_id="ACC_100", symbol="AAPL",
            side="BUY", quantity=10.0, price=98.0,
        )
        self.assertEqual(self.codes(order), {RC.REFERENCE_PRICE_UNAVAILABLE})

    def test_unusable_reference_price_does_not_mask_a_size_breach(self):
        order = make_order(quantity=5000.0, price=1.0, nbbo_mid_price=0.0)
        self.assertEqual(
            self.codes(order),
            {RC.REFERENCE_PRICE_UNAVAILABLE, RC.SINGLE_ORDER_QTY_CAP},
        )

    def test_nan_and_infinite_quantity_are_rejected(self):
        # Regression: every comparison against NaN is False, so a NaN quantity
        # breached no cap and the order was ALLOWED.
        for qty in (float('nan'), float('inf'), float('-inf'), None, "100", True):
            with self.subTest(quantity=qty):
                self.assertEqual(self.codes(make_order(quantity=qty)),
                                 {RC.INVALID_ORDER})

    def test_zero_and_negative_quantity_are_rejected(self):
        # Regression: quantity=-1e9 at $150 gave a notional of -$150bn, which is under
        # every positive cap, so a nine-figure order was ALLOWED.
        for qty in (0.0, -1.0, -1e9):
            with self.subTest(quantity=qty):
                self.assertEqual(self.codes(make_order(quantity=qty)),
                                 {RC.INVALID_ORDER})

    def test_non_positive_or_non_finite_price_is_rejected(self):
        for price in (0.0, -150.0, float('nan'), float('inf'), None, "150"):
            with self.subTest(price=price):
                self.assertEqual(self.codes(make_order(price=price)),
                                 {RC.INVALID_ORDER})

    def test_nan_or_negative_accumulated_credit_is_rejected(self):
        for field_name in ("accumulated_credit_used_usd",
                           "accumulated_firm_credit_used_usd"):
            for bad in (float('nan'), float('inf'), -1.0, None):
                with self.subTest(field=field_name, value=bad):
                    self.assertEqual(self.codes(make_order(**{field_name: bad})),
                                     {RC.INVALID_ORDER})

    def test_blank_identifiers_are_rejected(self):
        for field_name in ("order_id", "account_id", "symbol"):
            for bad in ("", "   ", None, 42):
                with self.subTest(field=field_name, value=bad):
                    self.assertEqual(self.codes(make_order(**{field_name: bad})),
                                     {RC.INVALID_ORDER})

    def test_invalid_order_stops_evaluation_and_reports_zero_notional(self):
        res = self.engine.evaluate_market_access_order(
            make_order(symbol="RESTRICTED_TICKER", quantity=float('nan'))
        )
        self.assertFalse(res.is_allowed)
        self.assertEqual(res.triggered_violations, [RC.INVALID_ORDER])
        self.assertEqual(res.notional_usd, 0.0)

    # ------------------------------------------------- (c)(2)(i) Reg SHO locate

    def test_short_sale_missing_locate_breach(self):
        order = make_order(order_id="ORD_004", side="SELL_SHORT",
                           short_locate_id=None)
        self.assertEqual(self.codes(order), {RC.SHORT_SALE_LOCATE_MISSING})

    def test_short_sale_with_locate_is_allowed(self):
        order = make_order(side="SELL_SHORT", short_locate_id="LOC-88213")
        self.assertEqual(self.codes(order), set())

    def test_blank_or_whitespace_locate_id_is_not_a_locate(self):
        # Regression: "   " is truthy, so a whitespace-only locate id passed.
        for locate in ("", "   ", "\t\n", 12345):
            with self.subTest(locate=locate):
                order = make_order(side="SELL_SHORT", short_locate_id=locate)
                self.assertEqual(self.codes(order), {RC.SHORT_SALE_LOCATE_MISSING})

    def test_unrecognised_side_is_rejected_not_treated_as_a_long_sale(self):
        # Regression: only the literal "SELL_SHORT" triggered the locate check, so
        # "SHORT", "sell short" and "SS" routed a naked short straight through.
        for side in ("SHORT", "sell short", "SELL-SHORT", "SS", "BUYY", "", None, 1):
            with self.subTest(side=side):
                self.assertEqual(self.codes(make_order(side=side)),
                                 {RC.INVALID_ORDER})

    def test_side_is_case_and_whitespace_insensitive_within_the_whitelist(self):
        for side in ("sell_short", " SELL_SHORT ", "Sell_Short"):
            with self.subTest(side=side):
                order = make_order(side=side, short_locate_id=None)
                self.assertEqual(self.codes(order), {RC.SHORT_SALE_LOCATE_MISSING})

    def test_every_whitelisted_side_is_accepted(self):
        for side in sorted(VALID_SIDES):
            with self.subTest(side=side):
                order = make_order(side=side, short_locate_id="LOC-1")
                self.assertEqual(self.codes(order), set())

    def test_market_maker_locate_exception_requires_the_firm_to_enable_it(self):
        # Rule 203(b)(2)(iii) is a firm determination, so the engine defaults closed.
        order = make_order(side="SELL_SHORT", short_locate_id=None,
                           is_bona_fide_market_making=True)
        self.assertEqual(self.codes(order), {RC.SHORT_SALE_LOCATE_MISSING})

        mm_engine = SecRule15C35RiskControlsUsEngine(
            limits=make_limits(allow_market_maker_locate_exception=True)
        )
        with self.assertLogs("sec_rule_15c3_5_risk_controls_us", level="WARNING"):
            res = mm_engine.evaluate_market_access_order(order)
        self.assertTrue(res.is_allowed)

    def test_market_maker_flag_must_be_a_bool(self):
        self.assertEqual(self.codes(make_order(is_bona_fide_market_making="yes")),
                         {RC.INVALID_ORDER})

    # ------------------------------------------ (c)(2)(ii) restricted securities

    def test_restricted_security_breach(self):
        order = make_order(order_id="ORD_005", symbol="RESTRICTED_TICKER",
                           quantity=100.0, price=50.0, nbbo_mid_price=50.0)
        self.assertEqual(self.codes(order), {RC.RESTRICTED_SECURITY})

    def test_restricted_check_is_case_and_whitespace_insensitive_both_ways(self):
        # Regression: the list was matched with `symbol.upper() in restricted_symbols`,
        # so a lowercase-configured entry never matched anything.
        engine = SecRule15C35RiskControlsUsEngine(
            limits=make_limits(restricted_symbols={"restricted_ticker"})
        )
        for symbol in ("RESTRICTED_TICKER", "restricted_ticker", " Restricted_Ticker "):
            with self.subTest(symbol=symbol):
                res = engine.evaluate_market_access_order(
                    make_order(symbol=symbol, quantity=1.0, price=50.0,
                               nbbo_mid_price=50.0)
                )
                self.assertIn(RC.RESTRICTED_SECURITY, res.triggered_violations)

    # ---------------------------- (c)(1)(ii) "over a short period of time" limb

    def test_message_rate_over_the_window_trips_the_burst_control(self):
        engine = SecRule15C35RiskControlsUsEngine(
            limits=make_limits(max_order_rate_per_sec=3, burst_window_sec=1.0,
                               duplicate_window_sec=0.0001)
        )
        codes = []
        for i in range(5):
            order = make_order(order_id=f"ORD_{i}", quantity=float(i + 1),
                               timestamp_sec=1000.0 + i * 0.1)
            codes.append(set(
                engine.evaluate_market_access_order(order).triggered_violations
            ))
        self.assertEqual(codes[:3], [set(), set(), set()])
        self.assertEqual(codes[3], {RC.RAPID_ORDER_BURST})
        self.assertEqual(codes[4], {RC.RAPID_ORDER_BURST})

    def test_burst_window_rolls_forward(self):
        engine = SecRule15C35RiskControlsUsEngine(
            limits=make_limits(max_order_rate_per_sec=2, burst_window_sec=1.0,
                               duplicate_window_sec=0.0001)
        )
        for i in range(2):
            engine.evaluate_market_access_order(
                make_order(order_id=f"A{i}", quantity=float(i + 1),
                           timestamp_sec=1000.0 + i * 0.1)
            )
        blocked = engine.evaluate_market_access_order(
            make_order(order_id="A2", quantity=9.0, timestamp_sec=1000.5)
        )
        self.assertIn(RC.RAPID_ORDER_BURST, blocked.triggered_violations)
        # Two seconds later the earlier messages have aged out of the window.
        cleared = engine.evaluate_market_access_order(
            make_order(order_id="A3", quantity=10.0, timestamp_sec=1002.5)
        )
        self.assertTrue(cleared.is_allowed)

    def test_burst_counter_is_per_account(self):
        engine = SecRule15C35RiskControlsUsEngine(
            limits=make_limits(max_order_rate_per_sec=2, burst_window_sec=1.0,
                               duplicate_window_sec=0.0001)
        )
        for i in range(2):
            engine.evaluate_market_access_order(
                make_order(order_id=f"A{i}", account_id="ACC_A",
                           quantity=float(i + 1), timestamp_sec=1000.0)
            )
        other = engine.evaluate_market_access_order(
            make_order(order_id="B0", account_id="ACC_B", quantity=7.0,
                       timestamp_sec=1000.0)
        )
        self.assertTrue(other.is_allowed)

    def test_rejected_orders_still_consume_the_message_budget(self):
        # A rejected order was still a message sent to the gate.
        engine = SecRule15C35RiskControlsUsEngine(
            limits=make_limits(max_order_rate_per_sec=2, burst_window_sec=1.0)
        )
        for i in range(2):
            engine.evaluate_market_access_order(
                make_order(order_id=f"BAD{i}", quantity=99999.0,
                           timestamp_sec=1000.0)
            )
        res = engine.evaluate_market_access_order(
            make_order(order_id="GOOD", quantity=1.0, timestamp_sec=1000.0)
        )
        self.assertEqual(set(res.triggered_violations), {RC.RAPID_ORDER_BURST})

    # ---------------------------------- (c)(1)(ii) duplicative-orders limb

    def test_identical_resubmission_within_the_window_is_a_duplicate(self):
        engine = SecRule15C35RiskControlsUsEngine(limits=make_limits())
        first = engine.evaluate_market_access_order(
            make_order(order_id="ORD_A", timestamp_sec=1000.0)
        )
        self.assertTrue(first.is_allowed)
        second = engine.evaluate_market_access_order(
            make_order(order_id="ORD_B", timestamp_sec=1000.2)
        )
        self.assertEqual(set(second.triggered_violations),
                         {RC.DUPLICATE_ORDER_DETECTED})

    def test_duplicate_window_expires(self):
        engine = SecRule15C35RiskControlsUsEngine(
            limits=make_limits(duplicate_window_sec=0.5)
        )
        engine.evaluate_market_access_order(make_order(order_id="A",
                                                       timestamp_sec=1000.0))
        later = engine.evaluate_market_access_order(
            make_order(order_id="B", timestamp_sec=1001.0)
        )
        self.assertTrue(later.is_allowed)

    def test_orders_differing_in_any_economic_field_are_not_duplicates(self):
        for change in ({"quantity": 101.0}, {"price": 151.0}, {"symbol": "MSFT"},
                       {"side": "SELL"}, {"account_id": "ACC_200"}):
            with self.subTest(change=change):
                engine = SecRule15C35RiskControlsUsEngine(limits=make_limits())
                engine.evaluate_market_access_order(
                    make_order(order_id="A", timestamp_sec=1000.0)
                )
                second = make_order(order_id="B", timestamp_sec=1000.1, **change)
                res = engine.evaluate_market_access_order(second)
                self.assertTrue(res.is_allowed, res.rejection_reasons)

    def test_account_ids_differing_only_in_case_are_distinct_accounts(self):
        # An account id is an opaque system key. Case-folding it in the fingerprint
        # would collide two real accounts, and would disagree with the burst counter,
        # which keys on the raw id.
        engine = SecRule15C35RiskControlsUsEngine(limits=make_limits())
        engine.evaluate_market_access_order(
            make_order(order_id="A", account_id="acct1", timestamp_sec=1000.0)
        )
        other = engine.evaluate_market_access_order(
            make_order(order_id="B", account_id="ACCT1", timestamp_sec=1000.1)
        )
        self.assertTrue(other.is_allowed, other.rejection_reasons)

    def test_symbol_case_does_not_defeat_duplicate_detection(self):
        engine = SecRule15C35RiskControlsUsEngine(limits=make_limits())
        engine.evaluate_market_access_order(
            make_order(order_id="A", symbol="AAPL", timestamp_sec=1000.0)
        )
        again = engine.evaluate_market_access_order(
            make_order(order_id="B", symbol="aapl", timestamp_sec=1000.1)
        )
        self.assertEqual(set(again.triggered_violations),
                         {RC.DUPLICATE_ORDER_DETECTED})

    def test_a_rejected_order_does_not_seed_the_duplicate_window(self):
        # The first submission never reached the venue, so the corrected resubmission
        # is not a duplicate of anything.
        engine = SecRule15C35RiskControlsUsEngine(limits=make_limits())
        rejected = engine.evaluate_market_access_order(
            make_order(order_id="A", symbol="RESTRICTED_TICKER", timestamp_sec=1000.0)
        )
        self.assertFalse(rejected.is_allowed)
        retried = engine.evaluate_market_access_order(
            make_order(order_id="A2", symbol="RESTRICTED_TICKER", timestamp_sec=1000.1)
        )
        self.assertEqual(set(retried.triggered_violations), {RC.RESTRICTED_SECURITY})

    def test_window_state_does_not_grow_without_bound(self):
        engine = SecRule15C35RiskControlsUsEngine(
            limits=make_limits(burst_window_sec=1.0, duplicate_window_sec=1.0)
        )
        for i in range(500):
            engine.evaluate_market_access_order(
                make_order(order_id=f"O{i}", account_id=f"ACC_{i}",
                           quantity=float(i + 1), timestamp_sec=1000.0 + i)
            )
        self.assertLessEqual(len(engine._recent_order_times), 2)
        self.assertLessEqual(len(engine._accepted_fingerprints), 2)

    # ------------------------------------------------------ combined behaviour

    def test_multiple_independent_violations_are_all_reported(self):
        order = make_order(order_id="ORD_ALL", symbol="SANCTIONED_CO", side="SELL_SHORT",
                           quantity=2000.0, price=300.0, nbbo_mid_price=150.0,
                           accumulated_credit_used_usd=490000.0,
                           accumulated_firm_credit_used_usd=4999000.0)
        self.assertEqual(
            self.codes(order),
            {
                RC.SINGLE_ORDER_QTY_CAP, RC.SINGLE_ORDER_NOTIONAL_CAP,
                RC.CREDIT_CAP_EXCEEDED, RC.FIRM_CREDIT_CAP_EXCEEDED,
                RC.PRICE_COLLAR_FAT_FINGER, RC.SHORT_SALE_LOCATE_MISSING,
                RC.RESTRICTED_SECURITY,
            },
        )

    def test_rejection_is_logged_at_warning_with_the_rule_codes(self):
        with self.assertLogs("sec_rule_15c3_5_risk_controls_us", level="WARNING") as cm:
            self.engine.evaluate_market_access_order(
                make_order(symbol="RESTRICTED_TICKER")
            )
        self.assertIn("RESTRICTED_SECURITY", "".join(cm.output))

    def test_concurrent_evaluation_does_not_lose_burst_accounting(self):
        engine = SecRule15C35RiskControlsUsEngine(
            limits=make_limits(max_order_rate_per_sec=1000000, burst_window_sec=60.0,
                               duplicate_window_sec=0.0001)
        )
        counter = itertools.count()

        def submit():
            for _ in range(50):
                i = next(counter)
                engine.evaluate_market_access_order(
                    make_order(order_id=f"T{i}", quantity=float(i + 1),
                               timestamp_sec=2000.0)
                )

        threads = [threading.Thread(target=submit) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(engine._recent_order_times["ACC_100"]), 200)


class TestLimitChangeControl(unittest.TestCase):

    def setUp(self):
        self.engine = SecRule15C35RiskControlsUsEngine(limits=make_limits())

    def test_replace_limits_requires_an_authoriser_and_a_reason(self):
        new_limits = make_limits(max_single_order_qty=2000.0)
        for authorised_by, reason in (("", "r"), ("  ", "r"), ("a", ""), (None, "r"),
                                      ("a", None)):
            with self.subTest(authorised_by=authorised_by, reason=reason):
                with self.assertRaises(ValueError):
                    self.engine.replace_limits(new_limits, authorised_by, reason)
        with self.assertRaises(TypeError):
            self.engine.replace_limits({"max_single_order_qty": 2000.0}, "a", "r")

    def test_replace_limits_applies_and_logs_the_change(self):
        with self.assertLogs("sec_rule_15c3_5_risk_controls_us", level="WARNING") as cm:
            self.engine.replace_limits(
                make_limits(max_single_order_qty=2000.0),
                authorised_by="risk.officer@firm.example",
                reason="Approved intraday increase, ticket RISK-4412",
            )
        self.assertIn("LIMIT CHANGE", "".join(cm.output))
        self.assertIn("RISK-4412", "".join(cm.output))
        res = self.engine.evaluate_market_access_order(
            make_order(quantity=1500.0, price=50.0, nbbo_mid_price=50.0)
        )
        self.assertTrue(res.is_allowed)


if __name__ == '__main__':
    unittest.main()
