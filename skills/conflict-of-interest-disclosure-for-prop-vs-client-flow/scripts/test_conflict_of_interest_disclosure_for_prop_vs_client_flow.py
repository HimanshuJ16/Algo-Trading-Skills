import unittest
from decimal import Decimal

from conflict_of_interest_disclosure_for_prop_vs_client_flow import (
    ClientOrder,
    ExceptionCode,
    OrderSide,
    PropOrder,
    PropVsClientConflictEngine,
    Rule5320ViolationError,
    SecurityType,
    TradingUnitType,
    ViolationCode,
    minimum_price_improvement,
)


def client(**overrides):
    """A 500-share (round lot) retail customer BUY limit at $150.00 on DESK_A."""
    kwargs = dict(
        order_id="C_101", symbol="AAPL", side="BUY", quantity=500,
        limit_price="150.00", info_barrier_id="DESK_A",
    )
    kwargs.update(overrides)
    return ClientOrder(**kwargs)


def prop(**overrides):
    kwargs = dict(
        order_id="P_201", symbol="AAPL", side="BUY", quantity=1000,
        price="150.00", info_barrier_id="DESK_A",
    )
    kwargs.update(overrides)
    return PropOrder(**kwargs)


class TestPriceConflictDirection(unittest.TestCase):
    """
    Rule 5320 prohibits proprietary trading at a price that *would satisfy* the held
    customer order. A customer BUY limit at $150.00 is satisfied by a purchase at
    $150.00 or lower; a customer SELL limit at $150.00 by a sale at $150.00 or higher.
    """

    def test_buy_at_customer_limit_is_trading_ahead(self):
        engine = PropVsClientConflictEngine([client()])
        res = engine.evaluate_prop_order(prop(price="150.00"))
        self.assertFalse(res.is_approved)
        self.assertEqual(res.violation_type, ViolationCode.TRADING_AHEAD_5320.value)
        self.assertEqual([c.client_order_id for c in res.conflicts], ["C_101"])

    def test_buy_below_customer_limit_is_trading_ahead(self):
        # Regression: the previous implementation treated a prop buy *below* the
        # customer's buy limit as non-conflicting. It is the clearest violation there
        # is - the firm took a fill the customer's resting order would have received.
        engine = PropVsClientConflictEngine([client(limit_price="145.00")])
        res = engine.evaluate_prop_order(prop(price="140.00"))
        self.assertFalse(res.is_approved)
        self.assertEqual(res.violation_type, ViolationCode.TRADING_AHEAD_5320.value)
        self.assertIn("satisfy", res.conflicts[0].reason)

    def test_buy_above_customer_limit_by_full_increment_is_permitted(self):
        engine = PropVsClientConflictEngine([client(limit_price="150.00")])
        res = engine.evaluate_prop_order(prop(price="150.01"))
        self.assertTrue(res.is_approved)
        # Clearing the Rule 5320.06 increment means there was never a conflict, so no
        # exception is recorded.
        self.assertIsNone(res.exception_applied)
        self.assertEqual(res.exceptions_applied, [])

    def test_sell_at_or_above_customer_sell_limit_is_trading_ahead(self):
        engine = PropVsClientConflictEngine(
            [client(order_id="C_S", side="SELL", limit_price="400.00")]
        )
        res = engine.evaluate_prop_order(
            prop(order_id="P_S", side="SELL", price="400.50")
        )
        self.assertFalse(res.is_approved)

    def test_sell_below_customer_sell_limit_by_full_increment_is_permitted(self):
        # Regression: previously flagged as a violation. Selling at $399.50 does not
        # satisfy a customer sell limit of $400.00 - the customer wants $400 or better.
        engine = PropVsClientConflictEngine(
            [client(order_id="C_S", side="SELL", limit_price="400.00")]
        )
        res = engine.evaluate_prop_order(
            prop(order_id="P_S", side="SELL", price="399.50")
        )
        self.assertTrue(res.is_approved)
        self.assertIsNone(res.violation_type)


class TestMinimumPriceImprovement(unittest.TestCase):

    def test_subpenny_improvement_does_not_clear_the_5320_06_increment(self):
        # $150.005 does not satisfy a $150.00 buy limit, but it improves on it by less
        # than the $0.01 minimum, so Rule 5320.06 still requires the customer's fill.
        engine = PropVsClientConflictEngine([client()])
        res = engine.evaluate_prop_order(prop(price="150.005"))
        self.assertFalse(res.is_approved)
        self.assertIn("5320.06", res.conflicts[0].reason)

    def test_nms_stock_above_one_dollar_is_a_flat_penny(self):
        self.assertEqual(
            minimum_price_improvement(
                Decimal("150.00"), SecurityType.NMS_STOCK, inside_spread=Decimal("0.01")
            ),
            Decimal("0.01"),
        )

    def test_otc_uses_half_the_inside_spread_when_narrower(self):
        self.assertEqual(
            minimum_price_improvement(
                Decimal("5.00"), SecurityType.OTC_EQUITY, inside_spread=Decimal("0.01")
            ),
            Decimal("0.005"),
        )

    def test_sub_dollar_tiers(self):
        for price, expected in [
            ("0.50", "0.01"), ("0.005", "0.001"),
            ("0.0005", "0.0001"), ("0.00005", "0.00001"), ("0.000001", "0.000001"),
        ]:
            with self.subTest(price=price):
                self.assertEqual(
                    minimum_price_improvement(Decimal(price), SecurityType.NMS_STOCK),
                    Decimal(expected),
                )

    def test_missing_inside_spread_uses_the_stricter_tier_increment(self):
        self.assertEqual(
            minimum_price_improvement(Decimal("0.50"), SecurityType.OTC_EQUITY),
            Decimal("0.01"),
        )


class TestNoKnowledgeException(unittest.TestCase):

    def test_distinct_effective_barrier_permits_prop_order(self):
        engine = PropVsClientConflictEngine([client(info_barrier_id="BARRIER_AGENCY")])
        res = engine.evaluate_prop_order(prop(info_barrier_id="BARRIER_PROP_HFT"))
        self.assertTrue(res.is_approved)
        self.assertEqual(res.exception_applied, ExceptionCode.NO_KNOWLEDGE_BARRIER.value)

    def test_barrier_flagged_ineffective_does_not_except(self):
        engine = PropVsClientConflictEngine([client(info_barrier_id="BARRIER_AGENCY")])
        res = engine.evaluate_prop_order(
            prop(info_barrier_id="BARRIER_PROP_HFT", barriers_effective=False)
        )
        self.assertFalse(res.is_approved)

    def test_otc_market_making_desk_cannot_use_the_no_knowledge_exception(self):
        engine = PropVsClientConflictEngine([client(info_barrier_id="BARRIER_AGENCY")])
        res = engine.evaluate_prop_order(
            prop(
                info_barrier_id="BARRIER_PROP_MM",
                security_type=SecurityType.OTC_EQUITY,
                trading_unit_type=TradingUnitType.MARKET_MAKING,
            )
        )
        self.assertFalse(res.is_approved)

    def test_otc_non_market_making_desk_may_use_it(self):
        engine = PropVsClientConflictEngine([client(info_barrier_id="BARRIER_AGENCY")])
        res = engine.evaluate_prop_order(
            prop(
                info_barrier_id="BARRIER_PROP_STAT_ARB",
                security_type=SecurityType.OTC_EQUITY,
                trading_unit_type=TradingUnitType.NON_MARKET_MAKING,
            )
        )
        self.assertTrue(res.is_approved)
        self.assertEqual(res.exception_applied, ExceptionCode.NO_KNOWLEDGE_BARRIER.value)


class TestLargeOrderAndInstitutionalException(unittest.TestCase):

    def test_institutional_account_on_negative_consent_is_excepted(self):
        engine = PropVsClientConflictEngine([
            client(
                quantity=500, is_institutional_account=True,
                negative_consent_disclosed=True, opted_in_5320=False,
            )
        ])
        res = engine.evaluate_prop_order(prop())
        self.assertTrue(res.is_approved)
        self.assertEqual(
            res.exception_applied, ExceptionCode.INSTITUTIONAL_NEGATIVE_CONSENT.value
        )

    def test_institutional_customer_who_opted_in_keeps_5320_protection(self):
        engine = PropVsClientConflictEngine([
            client(
                quantity=500, is_institutional_account=True,
                negative_consent_disclosed=True, opted_in_5320=True,
            )
        ])
        res = engine.evaluate_prop_order(prop())
        self.assertFalse(res.is_approved)

    def test_exception_requires_the_written_disclosure(self):
        engine = PropVsClientConflictEngine([
            client(quantity=500, is_institutional_account=True,
                   negative_consent_disclosed=False)
        ])
        res = engine.evaluate_prop_order(prop())
        self.assertFalse(res.is_approved)

    def test_large_order_needs_both_ten_thousand_shares_and_one_hundred_thousand_dollars(self):
        # 10,000 shares at $5.00 is $50,000 - under the value carve-out, so the
        # exception does not apply even though the share count is met.
        engine = PropVsClientConflictEngine([
            client(quantity=10_000, limit_price="5.00", negative_consent_disclosed=True)
        ])
        res = engine.evaluate_prop_order(prop(price="5.00"))
        self.assertFalse(res.is_approved)

    def test_large_order_needs_both_share_count_and_value(self):
        # $500,000 of stock but only 1,000 shares - the share threshold is not met.
        engine = PropVsClientConflictEngine([
            client(quantity=1_000, limit_price="500.00", negative_consent_disclosed=True)
        ])
        res = engine.evaluate_prop_order(prop(price="500.00"))
        self.assertFalse(res.is_approved)

    def test_large_order_meeting_both_thresholds_is_excepted(self):
        engine = PropVsClientConflictEngine([
            client(quantity=10_000, limit_price="150.00", negative_consent_disclosed=True)
        ])
        res = engine.evaluate_prop_order(prop(price="150.00"))
        self.assertTrue(res.is_approved)
        self.assertEqual(
            res.exception_applied, ExceptionCode.LARGE_ORDER_NEGATIVE_CONSENT.value
        )

    def test_exactly_at_both_thresholds_is_excepted(self):
        # 10,000 shares x $10.00 = exactly $100,000.
        engine = PropVsClientConflictEngine([
            client(quantity=10_000, limit_price="10.00", negative_consent_disclosed=True)
        ])
        res = engine.evaluate_prop_order(prop(price="10.00"))
        self.assertTrue(res.is_approved)

    def test_one_cent_of_value_below_the_threshold_is_not_excepted(self):
        # 10,000 x $9.9999 = $99,999.00. Exact decimal arithmetic matters here.
        engine = PropVsClientConflictEngine([
            client(quantity=10_000, limit_price="9.9999", negative_consent_disclosed=True)
        ])
        res = engine.evaluate_prop_order(prop(price="9.9999"))
        self.assertFalse(res.is_approved)


class TestOddLotException(unittest.TestCase):

    def test_odd_lot_customer_order_does_not_block(self):
        engine = PropVsClientConflictEngine([client(quantity=99)])
        res = engine.evaluate_prop_order(prop())
        self.assertTrue(res.is_approved)
        self.assertEqual(res.exception_applied, ExceptionCode.ODD_LOT.value)

    def test_one_round_lot_does_block(self):
        engine = PropVsClientConflictEngine([client(quantity=100)])
        res = engine.evaluate_prop_order(prop())
        self.assertFalse(res.is_approved)


class TestEveryClientOrderIsEvaluated(unittest.TestCase):

    def test_exception_on_one_order_does_not_approve_past_another(self):
        # Regression: the previous implementation returned APPROVED on the first
        # client order that qualified for an exception and never examined the rest,
        # so C_2 - same desk, no exception - was silently traded ahead of.
        engine = PropVsClientConflictEngine([
            client(order_id="C_1", info_barrier_id="BARRIER_AGENCY"),
            client(order_id="C_2", info_barrier_id="DESK_A"),
        ])
        res = engine.evaluate_prop_order(prop(info_barrier_id="DESK_A"))
        self.assertFalse(res.is_approved)
        self.assertEqual([c.client_order_id for c in res.conflicts], ["C_2"])
        self.assertIn(ExceptionCode.NO_KNOWLEDGE_BARRIER.value, res.exceptions_applied)

    def test_all_conflicting_orders_are_reported(self):
        engine = PropVsClientConflictEngine([
            client(order_id="C_1"), client(order_id="C_2", limit_price="151.00"),
        ])
        res = engine.evaluate_prop_order(prop())
        self.assertEqual([c.client_order_id for c in res.conflicts], ["C_1", "C_2"])


class TestScopeAndNonConflict(unittest.TestCase):

    def test_opposite_side_client_order_is_out_of_scope(self):
        engine = PropVsClientConflictEngine([client(side="SELL", limit_price="1.00")])
        res = engine.evaluate_prop_order(prop(price="0.50"))
        self.assertTrue(res.is_approved)
        self.assertEqual(res.exceptions_applied, [])

    def test_different_symbol_is_out_of_scope(self):
        engine = PropVsClientConflictEngine([client(symbol="MSFT")])
        res = engine.evaluate_prop_order(prop(symbol="AAPL", price="1.00"))
        self.assertTrue(res.is_approved)

    def test_empty_book_approves(self):
        res = PropVsClientConflictEngine().evaluate_prop_order(prop())
        self.assertTrue(res.is_approved)
        self.assertIsNone(res.violation_type)


class TestFailClosedOnBadInput(unittest.TestCase):

    def test_unknown_prop_side_is_rejected_not_approved(self):
        engine = PropVsClientConflictEngine([client()])
        res = engine.evaluate_prop_order(prop(side="B"))
        self.assertFalse(res.is_approved)
        self.assertEqual(res.violation_type, ViolationCode.INVALID_ORDER_PARAMETERS.value)

    def test_lowercase_side_is_normalised_and_still_evaluated(self):
        engine = PropVsClientConflictEngine([client(side="buy")])
        res = engine.evaluate_prop_order(prop(side="buy"))
        self.assertFalse(res.is_approved)
        self.assertEqual(res.violation_type, ViolationCode.TRADING_AHEAD_5320.value)

    def test_order_side_enum_is_accepted(self):
        engine = PropVsClientConflictEngine([client(side=OrderSide.BUY)])
        res = engine.evaluate_prop_order(prop(side=OrderSide.BUY))
        self.assertFalse(res.is_approved)

    def test_nan_price_is_rejected(self):
        engine = PropVsClientConflictEngine([client()])
        res = engine.evaluate_prop_order(prop(price=float("nan")))
        self.assertFalse(res.is_approved)
        self.assertEqual(res.violation_type, ViolationCode.INVALID_ORDER_PARAMETERS.value)

    def test_unparseable_client_price_fails_closed(self):
        engine = PropVsClientConflictEngine([client(limit_price="not-a-price")])
        res = engine.evaluate_prop_order(prop())
        self.assertFalse(res.is_approved)
        self.assertEqual(res.violation_type, ViolationCode.INVALID_ORDER_PARAMETERS.value)

    def test_non_positive_quantity_is_rejected(self):
        res = PropVsClientConflictEngine([client()]).evaluate_prop_order(prop(quantity=0))
        self.assertFalse(res.is_approved)

    def test_float_prices_compare_at_the_exact_threshold(self):
        # 150.10 has no exact binary representation; routing through str() keeps the
        # comparison on the price a human wrote.
        engine = PropVsClientConflictEngine([client(limit_price=150.10)])
        self.assertFalse(engine.evaluate_prop_order(prop(price=150.10)).is_approved)
        self.assertTrue(engine.evaluate_prop_order(prop(price=150.11)).is_approved)


class TestEnforcement(unittest.TestCase):

    def test_enforce_raises_on_violation(self):
        engine = PropVsClientConflictEngine([client()])
        with self.assertRaises(Rule5320ViolationError) as ctx:
            engine.enforce_prop_order(prop())
        self.assertEqual(
            ctx.exception.result.violation_type, ViolationCode.TRADING_AHEAD_5320.value
        )

    def test_enforce_returns_result_when_clean(self):
        engine = PropVsClientConflictEngine()
        self.assertTrue(engine.enforce_prop_order(prop()).is_approved)


if __name__ == '__main__':
    unittest.main()
