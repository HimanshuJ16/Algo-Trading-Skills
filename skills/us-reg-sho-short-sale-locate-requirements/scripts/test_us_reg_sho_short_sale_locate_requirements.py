import datetime
import unittest

from us_reg_sho_short_sale_locate_requirements import (
    NBB_PRICE_EPSILON,
    LocateRecord,
    LocateStatus,
    OrderIntent,
    OrderMarking,
    RegSHOError,
    RegSHOShortSaleEngine,
    RegSHOValidationResult,
    ShortExemptReason,
)

# A fixed clock so locate-expiry assertions cannot start failing as real time passes.
NOW = datetime.datetime(2026, 6, 1, 15, 0, tzinfo=datetime.timezone.utc)


class TestRegSHOShortSaleEngine(unittest.TestCase):
    def setUp(self):
        self.engine = RegSHOShortSaleEngine()
        # LOC-1001: 1,000 shares of TSLA, granted at NOW, good for 8 hours.
        self.locate = self.engine.grant_locate(
            locate_id="LOC-1001",
            symbol="TSLA",
            quantity=1000,
            lender_id="GOLDMAN_PRIME",
            granted_at=NOW,
        )

    def _order(self, **overrides) -> OrderIntent:
        params = dict(
            order_id="ORD-01",
            symbol="TSLA",
            marking=OrderMarking.SHORT,
            quantity=100,
            price=200.00,
            nbb_price=199.90,
            nbo_price=200.00,
            locate_id="LOC-1001",
        )
        params.update(overrides)
        return OrderIntent(**params)

    def _validate(self, **overrides) -> RegSHOValidationResult:
        return self.engine.validate_order_intent(self._order(**overrides), as_of=NOW)

    # --- Rule 200(g) order marking ------------------------------------

    def test_long_order_needs_no_locate(self):
        res = self._validate(
            order_id="ORD-LONG", marking=OrderMarking.LONG, locate_id=None
        )
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.marking, OrderMarking.LONG)
        self.assertIsNone(res.locate_id)
        self.assertEqual(self.locate.remaining_quantity, 1000)

    def test_short_exempt_without_statutory_basis_is_rejected(self):
        # Rule 200(g)(2) permits the "short exempt" marking only where 242.201(c) or
        # (d) is met. An unqualified SHORT_EXEMPT would be an unaudited price-test
        # bypass, so it must not be accepted.
        self.engine.trigger_rule_201_ssr("TSLA")
        res = self._validate(
            order_id="ORD-EXEMPT-BARE",
            marking=OrderMarking.SHORT_EXEMPT,
            price=199.90,
            short_exempt_reason=None,
        )
        self.assertFalse(res.is_compliant)
        self.assertIn("Rule 200(g)(2)", res.rejection_reason)
        self.assertEqual(self.locate.remaining_quantity, 1000)

    def test_short_marking_rejects_stray_exempt_reason(self):
        res = self._validate(
            order_id="ORD-CONFUSED",
            marking=OrderMarking.SHORT,
            short_exempt_reason=ShortExemptReason.VWAP,
        )
        self.assertFalse(res.is_compliant)
        self.assertIn("short_exempt_reason", res.rejection_reason)

    def test_unknown_marking_object_is_rejected(self):
        res = self._validate(order_id="ORD-BAD-MARK", marking="SHORT")
        self.assertFalse(res.is_compliant)
        self.assertIn("Rule 200(g)", res.rejection_reason)

    # --- Rule 203(b)(1) locate requirement ----------------------------

    def test_naked_short_without_locate_is_rejected(self):
        res = self._validate(order_id="ORD-NAKED", locate_id=None)
        self.assertFalse(res.is_compliant)
        self.assertIn("Rule 203(b)(1)", res.rejection_reason)
        self.assertEqual(res.locate_status, LocateStatus.LOCATE_NOT_FOUND)

    def test_unknown_locate_id_is_rejected(self):
        res = self._validate(order_id="ORD-GHOST", locate_id="LOC-DOES-NOT-EXIST")
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.locate_status, LocateStatus.LOCATE_NOT_FOUND)

    def test_locate_symbol_mismatch_is_rejected(self):
        self.engine.grant_locate("LOC-AAPL", "AAPL", 500, granted_at=NOW)
        res = self._validate(order_id="ORD-XSYM", locate_id="LOC-AAPL")
        self.assertFalse(res.is_compliant)
        self.assertIn("does not match", res.rejection_reason)
        self.assertEqual(res.locate_status, LocateStatus.LOCATE_NOT_FOUND)

    def test_valid_short_reserves_locate_capacity(self):
        res = self._validate(order_id="ORD-SHORT", quantity=400)
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.locate_status, LocateStatus.LOCATE_GRANTED)
        self.assertEqual(res.reserved_quantity, 400)
        self.assertEqual(self.locate.remaining_quantity, 600)

    def test_insufficient_locate_quantity_is_rejected(self):
        res = self._validate(order_id="ORD-TOO-BIG", quantity=1500)
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.locate_status, LocateStatus.LOCATE_EXHAUSTED)
        self.assertEqual(self.locate.remaining_quantity, 1000)

    def test_locate_capacity_is_exact_at_the_boundary(self):
        self.assertTrue(self._validate(order_id="ORD-EXACT", quantity=1000).is_compliant)
        self.assertEqual(self.locate.remaining_quantity, 0)
        follow_up = self._validate(order_id="ORD-ONE-MORE", quantity=1)
        self.assertFalse(follow_up.is_compliant)
        self.assertEqual(follow_up.locate_status, LocateStatus.LOCATE_EXHAUSTED)

    def test_expired_locate_is_rejected(self):
        expired_at = NOW + datetime.timedelta(hours=9)  # ttl is 8 hours
        res = self.engine.validate_order_intent(
            self._order(order_id="ORD-STALE"), as_of=expired_at
        )
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.locate_status, LocateStatus.LOCATE_EXPIRED)

    def test_naive_expiry_timestamp_does_not_raise(self):
        # Prime broker feeds routinely supply naive timestamps; comparing one against
        # an aware clock would otherwise raise TypeError on the order path.
        self.engine.locate_registry["LOC-NAIVE"] = LocateRecord(
            locate_id="LOC-NAIVE",
            symbol="TSLA",
            quantity_allocated=100,
            expires_at=datetime.datetime(2026, 6, 1, 20, 0),  # naive, treated as UTC
        )
        res = self._validate(order_id="ORD-NAIVE", locate_id="LOC-NAIVE")
        self.assertTrue(res.is_compliant)

    def test_grant_locate_rejects_non_positive_quantity(self):
        # A negative quantity previously credited the pool instead of debiting it.
        with self.assertRaises(RegSHOError):
            self.engine.grant_locate("LOC-NEG", "TSLA", -100)
        with self.assertRaises(RegSHOError):
            self.engine.grant_locate("LOC-ZERO", "TSLA", 0)

    def test_regranting_an_existing_locate_id_is_refused(self):
        self._validate(order_id="ORD-USE", quantity=600)
        with self.assertRaises(RegSHOError):
            self.engine.grant_locate("LOC-1001", "TSLA", 1000)
        # Consumed capacity survives the refused re-grant.
        self.assertEqual(self.engine.locate_registry["LOC-1001"].quantity_used, 600)

    # --- Order-level input validation ---------------------------------

    def test_negative_quantity_cannot_credit_the_locate_pool(self):
        res = self._validate(order_id="ORD-NEGQTY", quantity=-500)
        self.assertFalse(res.is_compliant)
        self.assertEqual(self.locate.remaining_quantity, 1000)

    def test_zero_quantity_is_rejected(self):
        self.assertFalse(self._validate(order_id="ORD-ZERO", quantity=0).is_compliant)

    def test_nan_price_cannot_pass_the_price_test(self):
        # float("nan") <= x is False, so an unvalidated NaN price silently satisfied
        # the Rule 201 comparison.
        self.engine.trigger_rule_201_ssr("TSLA")
        res = self._validate(order_id="ORD-NAN", price=float("nan"))
        self.assertFalse(res.is_compliant)
        self.assertEqual(self.locate.remaining_quantity, 1000)

    # --- Rule 201 price test ------------------------------------------

    def test_price_test_does_not_apply_when_no_restriction_is_in_force(self):
        self.assertFalse(self.engine.is_ssr_active("TSLA"))
        res = self._validate(order_id="ORD-NOSSR", price=100.00, nbb_price=199.90)
        self.assertTrue(res.is_compliant)
        self.assertFalse(res.ssr_active)

    def test_short_at_or_below_nbb_is_rejected_under_the_price_test(self):
        self.engine.trigger_rule_201_ssr("TSLA")
        self.assertTrue(self.engine.is_ssr_active("TSLA"))
        res = self._validate(order_id="ORD-SSR-AT-BID", price=199.90, nbb_price=199.90)
        self.assertFalse(res.is_compliant)
        self.assertTrue(res.ssr_active)
        self.assertIn("Rule 201(b)(1)(i)", res.rejection_reason)
        self.assertEqual(self.locate.remaining_quantity, 1000)

    def test_short_strictly_above_nbb_passes_the_price_test(self):
        self.engine.trigger_rule_201_ssr("TSLA")
        res = self._validate(order_id="ORD-SSR-ABOVE", price=199.91, nbb_price=199.90)
        self.assertTrue(res.is_compliant)

    def test_price_a_hair_above_nbb_is_treated_as_at_the_bid(self):
        # The epsilon leans toward rejection: executing a prohibited short is the
        # unrecoverable error, a spurious rejection is not.
        self.engine.trigger_rule_201_ssr("TSLA")
        res = self._validate(
            order_id="ORD-EPS",
            price=199.90 + NBB_PRICE_EPSILON / 2,
            nbb_price=199.90,
        )
        self.assertFalse(res.is_compliant)

    def test_missing_nbb_under_restriction_fails_closed(self):
        # A market data outage must not become a silent price-test bypass.
        self.engine.trigger_rule_201_ssr("TSLA")
        for bad_nbb in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(nbb=bad_nbb):
                res = self._validate(
                    order_id=f"ORD-NOBID-{bad_nbb}", price=500.00, nbb_price=bad_nbb
                )
                self.assertFalse(res.is_compliant)
                self.assertIn("national best bid", res.rejection_reason)
        self.assertEqual(self.locate.remaining_quantity, 1000)

    def test_restriction_is_symbol_scoped(self):
        self.engine.trigger_rule_201_ssr("AAPL")
        self.assertFalse(self.engine.is_ssr_active("TSLA"))
        self.assertTrue(self._validate(order_id="ORD-OTHER", price=1.00).is_compliant)

    def test_restriction_lookup_is_case_insensitive(self):
        self.engine.trigger_rule_201_ssr("tsla")
        self.assertTrue(self.engine.is_ssr_active("TSLA"))

    def test_restriction_stays_in_force_until_explicitly_cleared(self):
        self.engine.trigger_rule_201_ssr("TSLA", triggered_at=NOW)
        far_future = NOW + datetime.timedelta(days=30)
        self.assertTrue(self.engine.is_ssr_active("TSLA", as_of=far_future))
        self.engine.deactivate_rule_201_ssr("TSLA")
        self.assertFalse(self.engine.is_ssr_active("TSLA"))

    def test_restriction_expires_at_an_explicit_effective_through(self):
        end = NOW + datetime.timedelta(hours=6)
        self.engine.trigger_rule_201_ssr("TSLA", effective_through=end, triggered_at=NOW)
        self.assertTrue(self.engine.is_ssr_active("TSLA", as_of=end - datetime.timedelta(minutes=1)))
        self.assertFalse(self.engine.is_ssr_active("TSLA", as_of=end))

    # --- Rule 201(c)/(d) short exempt handling ------------------------

    def test_rule_201d_basis_bypasses_the_price_test(self):
        self.engine.trigger_rule_201_ssr("TSLA")
        res = self._validate(
            order_id="ORD-VWAP",
            marking=OrderMarking.SHORT_EXEMPT,
            price=199.90,
            short_exempt_reason=ShortExemptReason.VWAP,
        )
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.marking, OrderMarking.SHORT_EXEMPT)
        self.assertEqual(res.short_exempt_reason, ShortExemptReason.VWAP)
        # Rule 201 relief does not reach Rule 203: the locate is still consumed.
        self.assertEqual(self.locate.remaining_quantity, 900)

    def test_rule_201c_basis_is_verified_against_the_bid(self):
        # 242.201(c) requires policies preventing an incorrect "priced above the NBB"
        # identification, so the claim is checked rather than trusted.
        self.engine.trigger_rule_201_ssr("TSLA")
        bad = self._validate(
            order_id="ORD-201C-BAD",
            marking=OrderMarking.SHORT_EXEMPT,
            price=199.80,
            short_exempt_reason=ShortExemptReason.PRICED_ABOVE_NBB_AT_SUBMISSION,
        )
        self.assertFalse(bad.is_compliant)
        self.assertIn("Rule 201(c)", bad.rejection_reason)

        good = self._validate(
            order_id="ORD-201C-GOOD",
            marking=OrderMarking.SHORT_EXEMPT,
            price=199.95,
            short_exempt_reason=ShortExemptReason.PRICED_ABOVE_NBB_AT_SUBMISSION,
        )
        self.assertTrue(good.is_compliant)

    def test_short_exempt_still_requires_a_locate(self):
        self.engine.trigger_rule_201_ssr("TSLA")
        res = self._validate(
            order_id="ORD-EXEMPT-NAKED",
            marking=OrderMarking.SHORT_EXEMPT,
            locate_id=None,
            short_exempt_reason=ShortExemptReason.VWAP,
        )
        self.assertFalse(res.is_compliant)
        self.assertIn("Rule 203(b)(1)", res.rejection_reason)

    # --- Idempotency and reservation lifecycle ------------------------

    def test_repeated_validation_reserves_locate_capacity_once(self):
        order = self._order(order_id="ORD-RETRY", quantity=400)
        first = self.engine.validate_order_intent(order, as_of=NOW)
        second = self.engine.validate_order_intent(order, as_of=NOW)
        third = self.engine.validate_order_intent(order, as_of=NOW)
        self.assertTrue(first.is_compliant)
        self.assertIs(first, second)
        self.assertIs(first, third)
        self.assertEqual(self.locate.quantity_used, 400)

    def test_retry_carrying_a_fresher_nbbo_tick_is_the_same_order(self):
        # A post-timeout retry legitimately re-reads the quote before re-sending, so
        # the NBB (and the optional NBO) will have moved. Market data is not part of
        # the order's identity: the retry must return the original decision and must
        # not reserve locate capacity a second time.
        first = self.engine.validate_order_intent(
            self._order(order_id="ORD-NBB-RETRY", quantity=400, nbb_price=199.90),
            as_of=NOW,
        )
        retry = self.engine.validate_order_intent(
            self._order(
                order_id="ORD-NBB-RETRY", quantity=400, nbb_price=205.25, nbo_price=None
            ),
            as_of=NOW,
        )
        self.assertTrue(first.is_compliant)
        self.assertIs(retry, first)
        self.assertIsNone(retry.rejection_reason)
        self.assertEqual(self.locate.quantity_used, 400)

    def test_changed_quantity_is_still_rejected_even_with_a_moved_nbb(self):
        # The converse of the retry case: dropping market data from the fingerprint
        # must not weaken it against a genuine order_id reuse.
        self._validate(order_id="ORD-DUP-NBB", quantity=100, nbb_price=199.90)
        res = self._validate(order_id="ORD-DUP-NBB", quantity=900, nbb_price=205.25)
        self.assertFalse(res.is_compliant)
        self.assertIn("Duplicate order_id", res.rejection_reason)
        self.assertEqual(self.locate.quantity_used, 100)

    def test_reused_order_id_with_different_terms_is_rejected(self):
        self._validate(order_id="ORD-DUP", quantity=100)
        res = self._validate(order_id="ORD-DUP", quantity=900)
        self.assertFalse(res.is_compliant)
        self.assertIn("Duplicate order_id", res.rejection_reason)
        self.assertEqual(self.locate.quantity_used, 100)

    def test_rejected_order_may_be_resubmitted_once_its_cause_is_fixed(self):
        # A rejection reserves nothing, so caching it would permanently freeze an
        # order ID whose problem was subsequently resolved.
        first = self._validate(order_id="ORD-FIXABLE", locate_id="LOC-LATE")
        self.assertFalse(first.is_compliant)
        self.engine.grant_locate("LOC-LATE", "TSLA", 500, granted_at=NOW)
        second = self._validate(order_id="ORD-FIXABLE", locate_id="LOC-LATE")
        self.assertTrue(second.is_compliant)
        self.assertEqual(self.engine.locate_registry["LOC-LATE"].quantity_used, 100)

    def test_blank_order_id_is_rejected(self):
        for bad_id in ("", "   ", None, 12345):
            with self.subTest(order_id=bad_id):
                res = self._validate(order_id=bad_id)
                self.assertFalse(res.is_compliant)
                self.assertIn("order_id", res.rejection_reason)
        self.assertEqual(self.locate.remaining_quantity, 1000)

    def test_releasing_a_reservation_returns_capacity(self):
        self._validate(order_id="ORD-CXL", quantity=700)
        self.assertEqual(self.locate.remaining_quantity, 300)
        reservation = self.engine.release_locate_reservation("ORD-CXL")
        self.assertTrue(reservation.is_released)
        self.assertEqual(self.locate.remaining_quantity, 1000)

    def test_double_release_is_refused(self):
        self._validate(order_id="ORD-CXL2", quantity=700)
        self.engine.release_locate_reservation("ORD-CXL2")
        with self.assertRaises(RegSHOError):
            self.engine.release_locate_reservation("ORD-CXL2")
        self.assertEqual(self.locate.remaining_quantity, 1000)

    def test_releasing_an_unknown_order_is_refused(self):
        with self.assertRaises(RegSHOError):
            self.engine.release_locate_reservation("ORD-NEVER-SEEN")

    def test_rejected_order_reserves_nothing_to_release(self):
        self._validate(order_id="ORD-REJ", quantity=5000)
        with self.assertRaises(RegSHOError):
            self.engine.release_locate_reservation("ORD-REJ")

    def test_resubmitting_after_release_does_not_re_reserve(self):
        order = self._order(order_id="ORD-REPLAY", quantity=400)
        self.engine.validate_order_intent(order, as_of=NOW)
        self.engine.release_locate_reservation("ORD-REPLAY")
        self.engine.validate_order_intent(order, as_of=NOW)
        self.assertEqual(self.locate.quantity_used, 0)

    # --- Audit trail ---------------------------------------------------

    def test_every_decision_is_appended_to_the_audit_log(self):
        self._validate(order_id="ORD-A", quantity=100)
        self._validate(order_id="ORD-B", locate_id=None)
        self.assertEqual([r.order_id for r in self.engine.audit_log], ["ORD-A", "ORD-B"])
        self.assertEqual(
            [r.is_compliant for r in self.engine.audit_log], [True, False]
        )
        for record in self.engine.audit_log:
            self.assertIsNotNone(record.audit_timestamp.tzinfo)

    # --- Advisory local trigger ---------------------------------------

    def test_local_trigger_fires_at_exactly_ten_percent(self):
        self.assertTrue(self.engine.evaluate_local_trigger(100.0, 90.0))
        self.assertTrue(self.engine.evaluate_local_trigger(100.0, 89.99))
        self.assertFalse(self.engine.evaluate_local_trigger(100.0, 90.01))

    def test_local_trigger_rejects_unusable_reference_prices(self):
        for prior_close in (0.0, -10.0, float("nan")):
            with self.subTest(prior_close=prior_close):
                with self.assertRaises(RegSHOError):
                    self.engine.evaluate_local_trigger(prior_close, 50.0)
        with self.assertRaises(RegSHOError):
            self.engine.evaluate_local_trigger(100.0, float("nan"))

    def test_local_trigger_does_not_itself_restrict_orders(self):
        # The listing market makes the 242.201(b)(3) determination; a local signal
        # must not silently change the gate's behaviour.
        self.assertTrue(self.engine.evaluate_local_trigger(100.0, 85.0))
        self.assertFalse(self.engine.is_ssr_active("TSLA"))
        self.assertTrue(self._validate(order_id="ORD-ADVISORY", price=1.00).is_compliant)


if __name__ == "__main__":
    unittest.main()
