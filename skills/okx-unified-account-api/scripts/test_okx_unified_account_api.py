"""Unit tests for okx-unified-account-api.

Expected values are derived independently of the implementation:

* the HMAC-SHA256 signature is re-derived from RFC 2104 primitives (ipad/opad over
  ``hashlib.sha256``) rather than by calling ``hmac`` again, and is pinned to a
  golden constant so the prehash field order cannot drift silently;
* the tiered-discount expectation is OKX's own published worked example (100 BTC at
  $60,000 -> $5,785,500 adjusted equity).
"""
import hashlib
import logging
import math
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from okx_unified_account_api import (
    CLIENT_ORDER_ID_PATTERN,
    HEADER_SIMULATED_TRADING,
    LIQUIDATION_MARGIN_RATIO_PCT,
    MAX_CLIENT_ORDER_ID_LENGTH,
    RISK_ALERT_MARGIN_RATIO_PCT,
    STATUS_LIQUIDATION_RISK_CALL,
    STATUS_MARGIN_WARNING,
    STATUS_SAFE,
    OKXDiscountTier,
    OKXTokenBalance,
    OKXUnifiedAccountEngine,
    OKXUnifiedAccountError,
    new_client_order_id,
)

# OKX Help Center, "IV. Multi-currency margin mode: cross margin trading" -- the
# published BTC discount schedule used in its own worked example.
OKX_BTC_EXAMPLE_TIERS = (
    OKXDiscountTier(20.0, 0.98),
    OKXDiscountTier(25.0, 0.975),
    OKXDiscountTier(30.0, 0.97),
    OKXDiscountTier(50.0, 0.965),
    OKXDiscountTier(70.0, 0.96),
    OKXDiscountTier(90.0, 0.955),
    OKXDiscountTier(110.0, 0.95),
)


def rfc2104_hmac_sha256_base64(key: str, message: str) -> str:
    """HMAC-SHA256 built from RFC 2104 primitives, independent of ``hmac``."""
    import base64

    block_size = 64
    key_bytes = key.encode("utf-8")
    if len(key_bytes) > block_size:
        key_bytes = hashlib.sha256(key_bytes).digest()
    key_bytes = key_bytes.ljust(block_size, b"\x00")
    inner = bytes(b ^ 0x36 for b in key_bytes)
    outer = bytes(b ^ 0x5C for b in key_bytes)
    inner_digest = hashlib.sha256(inner + message.encode("utf-8")).digest()
    digest = hashlib.sha256(outer + inner_digest).digest()
    return base64.b64encode(digest).decode("utf-8")


class TestSignatureAndHeaders(unittest.TestCase):

    def setUp(self):
        self.engine = OKXUnifiedAccountEngine(
            api_key="TESTAPIKEY",
            secret_key="TESTSECRETKEY",
            passphrase="TESTPASSPHRASE",
        )
        # Timestamp and request path from OKX's own signing example.
        self.timestamp = "2020-12-08T09:08:57.715Z"
        self.path = "/api/v5/account/balance?ccy=BTC"

    def test_signature_matches_independent_rfc2104_derivation(self):
        signature = self.engine.generate_signature(self.timestamp, "GET", self.path)
        expected = rfc2104_hmac_sha256_base64(
            "TESTSECRETKEY", self.timestamp + "GET" + self.path)
        self.assertEqual(signature, expected)
        # Golden constant: pins the prehash field order, not just the primitive.
        self.assertEqual(signature, "ywUII5RfhbX1DEVy+t63H12ZB9tV5VwXoi/Mt2V00Wk=")
        self.assertEqual(len(signature), 44)

    def test_prehash_field_order_is_timestamp_method_path_body(self):
        body = '{"instId":"BTC-USDT"}'
        signature = self.engine.generate_signature(
            self.timestamp, "post", "/api/v5/trade/order", body)
        expected = rfc2104_hmac_sha256_base64(
            "TESTSECRETKEY",
            self.timestamp + "POST" + "/api/v5/trade/order" + body)
        self.assertEqual(signature, expected)
        # A permuted prehash must not collide with the correct one.
        permuted = rfc2104_hmac_sha256_base64(
            "TESTSECRETKEY",
            "POST" + self.timestamp + "/api/v5/trade/order" + body)
        self.assertNotEqual(signature, permuted)

    def test_query_string_is_part_of_the_signed_request_path(self):
        with_query = self.engine.generate_signature(self.timestamp, "GET", self.path)
        without_query = self.engine.generate_signature(
            self.timestamp, "GET", "/api/v5/account/balance")
        self.assertNotEqual(with_query, without_query)

    def test_body_must_be_the_exact_transmitted_string(self):
        compact = self.engine.generate_signature(
            self.timestamp, "POST", "/api/v5/trade/order", '{"a":1}')
        spaced = self.engine.generate_signature(
            self.timestamp, "POST", "/api/v5/trade/order", '{"a": 1}')
        self.assertNotEqual(compact, spaced)

    def test_epoch_timestamp_is_rejected_before_it_reaches_okx(self):
        for bad in ("1607418537", "1607418537715", "2020-12-08T09:08:57Z",
                    "2020-12-08 09:08:57.715Z", "2020-12-08T09:08:57.715"):
            with self.subTest(timestamp=bad):
                with self.assertRaises(OKXUnifiedAccountError):
                    self.engine.generate_signature(bad, "GET", self.path)

    def test_shape_valid_but_impossible_timestamp_raises_the_module_error(self):
        with self.assertRaises(OKXUnifiedAccountError):
            OKXUnifiedAccountEngine.parse_timestamp("2020-13-45T09:08:57.715Z")

    def test_request_path_must_be_absolute(self):
        with self.assertRaises(OKXUnifiedAccountError):
            self.engine.generate_signature(
                self.timestamp, "GET", "https://www.okx.com/api/v5/account/balance")

    def test_blank_credentials_are_rejected_at_construction(self):
        for kwargs in ({"api_key": ""}, {"secret_key": "   "}, {"passphrase": ""}):
            with self.subTest(**kwargs):
                args = {"api_key": "K", "secret_key": "S", "passphrase": "P"}
                args.update(kwargs)
                with self.assertRaises(OKXUnifiedAccountError):
                    OKXUnifiedAccountEngine(**args)

    def test_headers_carry_live_environment_flag_by_default(self):
        headers = self.engine.get_auth_headers("GET", self.path, "", self.timestamp)
        self.assertEqual(headers["OK-ACCESS-KEY"], "TESTAPIKEY")
        self.assertEqual(headers["OK-ACCESS-PASSPHRASE"], "TESTPASSPHRASE")
        self.assertEqual(headers["OK-ACCESS-TIMESTAMP"], self.timestamp)
        self.assertEqual(
            headers["OK-ACCESS-SIGN"],
            self.engine.generate_signature(self.timestamp, "GET", self.path))
        self.assertEqual(headers[HEADER_SIMULATED_TRADING], "0")

    def test_demo_engine_sets_simulated_trading_flag(self):
        demo = OKXUnifiedAccountEngine("K", "S", "P", simulated_trading=True)
        headers = demo.get_auth_headers("GET", self.path, "", self.timestamp)
        self.assertEqual(headers[HEADER_SIMULATED_TRADING], "1")

    def test_generated_timestamp_round_trips_and_has_millisecond_precision(self):
        moment = datetime(2026, 7, 31, 13, 39, 0, 123456, tzinfo=timezone.utc)
        stamp = OKXUnifiedAccountEngine.build_timestamp(moment)
        self.assertEqual(stamp, "2026-07-31T13:39:00.123Z")
        self.assertEqual(
            OKXUnifiedAccountEngine.parse_timestamp(stamp),
            moment.replace(microsecond=123000))

    def test_build_timestamp_converts_non_utc_offsets(self):
        moment = datetime(2026, 7, 31, 19, 9, 0, 0,
                          tzinfo=timezone(timedelta(hours=5, minutes=30)))
        self.assertEqual(
            OKXUnifiedAccountEngine.build_timestamp(moment), "2026-07-31T13:39:00.000Z")

    def test_clock_skew_is_measured_against_server_time(self):
        stamp = "2026-07-31T13:39:00.000Z"
        server_ms = OKXUnifiedAccountEngine.parse_timestamp(stamp).timestamp() * 1000.0
        self.assertAlmostEqual(
            self.engine.clock_skew_seconds(server_ms, stamp), 0.0, places=6)
        self.assertAlmostEqual(
            self.engine.clock_skew_seconds(server_ms - 45_000.0, stamp), 45.0, places=6)

    def test_credentials_are_redacted_in_repr(self):
        rendered = repr(self.engine)
        self.assertNotIn("TESTSECRETKEY", rendered)
        self.assertNotIn("TESTPASSPHRASE", rendered)
        self.assertIn("<redacted>", rendered)


class TestMultiCurrencyMargin(unittest.TestCase):

    def setUp(self):
        self.engine = OKXUnifiedAccountEngine("K", "S", "P")
        # 1 BTC at $60,000 with a 0.95 haircut = $57,000, plus 10,000 USDT at 1.0.
        self.balances = [
            OKXTokenBalance("BTC", 1.0, 60000.0, 0.95),
            OKXTokenBalance("USDT", 10000.0, 1.0, 1.0),
        ]

    def test_flat_discount_equity_and_safe_status(self):
        report = self.engine.compute_multi_currency_margin(self.balances, 10000.0)
        self.assertEqual(report.total_usd_equity, 70000.0)
        self.assertEqual(report.discounted_usd_equity, 67000.0)
        self.assertEqual(report.adjusted_usd_equity, 67000.0)
        self.assertAlmostEqual(report.margin_ratio_pct, 670.0, delta=0.01)
        self.assertEqual(report.status, STATUS_SAFE)

    def test_liquidation_status_below_one_hundred_percent(self):
        report = self.engine.compute_multi_currency_margin(self.balances, 70000.0)
        self.assertEqual(report.status, STATUS_LIQUIDATION_RISK_CALL)
        self.assertLessEqual(report.margin_ratio_pct, LIQUIDATION_MARGIN_RATIO_PCT)

    def test_threshold_boundaries_are_inclusive_on_the_risk_side(self):
        # OKX alerts at <= 300% and pre-liquidates at <= 100%, so a ratio landing
        # exactly on a threshold belongs to the riskier bucket. Equity is chosen so
        # each ratio is exact in binary floating point.
        equity = [OKXTokenBalance("USDT", 30000.0, 1.0)]
        at_alert = self.engine.compute_multi_currency_margin(equity, 10000.0)
        self.assertEqual(at_alert.margin_ratio_pct, RISK_ALERT_MARGIN_RATIO_PCT)
        self.assertEqual(at_alert.status, STATUS_MARGIN_WARNING)

        just_above_alert = self.engine.compute_multi_currency_margin(equity, 9900.0)
        self.assertGreater(just_above_alert.margin_ratio_pct, RISK_ALERT_MARGIN_RATIO_PCT)
        self.assertEqual(just_above_alert.status, STATUS_SAFE)

        at_liquidation = self.engine.compute_multi_currency_margin(equity, 30000.0)
        self.assertEqual(at_liquidation.margin_ratio_pct, LIQUIDATION_MARGIN_RATIO_PCT)
        self.assertEqual(at_liquidation.status, STATUS_LIQUIDATION_RISK_CALL)

        just_above_liquidation = self.engine.compute_multi_currency_margin(
            equity, 29000.0)
        self.assertGreater(
            just_above_liquidation.margin_ratio_pct, LIQUIDATION_MARGIN_RATIO_PCT)
        self.assertEqual(just_above_liquidation.status, STATUS_MARGIN_WARNING)

    def test_liability_receives_no_haircut_benefit(self):
        # Borrowing 10,000 USDT is a $10,000 liability. A haircut applied to it
        # would report only -$9,000 and inflate the margin ratio.
        borrowed = OKXTokenBalance("USDT", -10000.0, 1.0, 0.9)
        self.assertEqual(borrowed.discounted_usd_value(), -10000.0)

        report = self.engine.compute_multi_currency_margin(
            [OKXTokenBalance("BTC", 1.0, 60000.0, 1.0), borrowed], 10000.0)
        self.assertEqual(report.discounted_usd_equity, 50000.0)
        self.assertAlmostEqual(report.margin_ratio_pct, 500.0, delta=0.01)

    def test_liability_can_drive_the_account_into_liquidation_status(self):
        report = self.engine.compute_multi_currency_margin(
            [OKXTokenBalance("BTC", 1.0, 60000.0, 0.95),
             OKXTokenBalance("USDT", -60000.0, 1.0, 1.0)],
            5000.0)
        self.assertEqual(report.discounted_usd_equity, -3000.0)
        self.assertEqual(report.status, STATUS_LIQUIDATION_RISK_CALL)

    def test_tiered_discount_matches_okx_published_worked_example(self):
        # OKX example: 100 BTC at $60,000 -> $5,785,500 discounted equity.
        holding = OKXTokenBalance(
            "BTC", 100.0, 60000.0, discount_tiers=OKX_BTC_EXAMPLE_TIERS)
        self.assertAlmostEqual(holding.gross_usd_value(), 6_000_000.0, places=2)
        self.assertAlmostEqual(holding.discounted_usd_value(), 5_785_500.0, places=2)

    def test_tiers_are_applied_marginally_not_as_a_single_rate(self):
        holding = OKXTokenBalance(
            "BTC", 100.0, 60000.0, discount_tiers=OKX_BTC_EXAMPLE_TIERS)
        top_rate_only = 100.0 * 60000.0 * 0.95
        first_rate_only = 100.0 * 60000.0 * 0.98
        self.assertGreater(holding.discounted_usd_value(), top_rate_only)
        self.assertLess(holding.discounted_usd_value(), first_rate_only)

    def test_holding_inside_the_first_tier_uses_only_that_rate(self):
        holding = OKXTokenBalance(
            "BTC", 10.0, 60000.0, discount_tiers=OKX_BTC_EXAMPLE_TIERS)
        self.assertAlmostEqual(
            holding.discounted_usd_value(), 10.0 * 60000.0 * 0.98, places=2)

    def test_balance_beyond_the_tier_schedule_is_refused_not_valued(self):
        holding = OKXTokenBalance(
            "BTC", 200.0, 60000.0, discount_tiers=OKX_BTC_EXAMPLE_TIERS)
        with self.assertRaises(OKXUnifiedAccountError):
            holding.discounted_usd_value()

    def test_unbounded_final_tier_absorbs_the_remainder(self):
        holding = OKXTokenBalance(
            "BTC", 200.0, 60000.0,
            discount_tiers=(OKXDiscountTier(20.0, 0.98), OKXDiscountTier(None, 0.5)))
        expected = 20.0 * 60000.0 * 0.98 + 180.0 * 60000.0 * 0.5
        self.assertAlmostEqual(holding.discounted_usd_value(), expected, places=2)

    def test_deductions_and_liquidation_fees_lower_the_reported_ratio(self):
        base = self.engine.compute_multi_currency_margin(self.balances, 10000.0)
        loaded = self.engine.compute_multi_currency_margin(
            self.balances, 10000.0,
            equity_deductions_usd=7000.0, liquidation_fee_usd=1000.0)
        self.assertEqual(loaded.adjusted_usd_equity, 60000.0)
        self.assertAlmostEqual(loaded.margin_ratio_pct, 60000.0 / 11000.0 * 100.0,
                               delta=0.01)
        self.assertLess(loaded.margin_ratio_pct, base.margin_ratio_pct)

    def test_zero_maintenance_margin_reports_infinity_not_a_sentinel(self):
        report = self.engine.compute_multi_currency_margin(self.balances, 0.0)
        self.assertTrue(math.isinf(report.margin_ratio_pct))
        self.assertEqual(report.status, STATUS_SAFE)
        self.assertTrue(any("undefined" in w for w in report.warnings))

    def test_negative_equity_with_no_positions_is_not_reported_safe(self):
        report = self.engine.compute_multi_currency_margin(
            [OKXTokenBalance("USDT", -500.0, 1.0)], 0.0)
        self.assertEqual(report.status, STATUS_LIQUIDATION_RISK_CALL)

    def test_negative_maintenance_margin_raises_instead_of_reporting_safe(self):
        with self.assertRaises(OKXUnifiedAccountError):
            self.engine.compute_multi_currency_margin(self.balances, -10000.0)

    def test_non_finite_inputs_raise_rather_than_being_scored(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad):
                with self.assertRaises(OKXUnifiedAccountError):
                    OKXTokenBalance("BTC", 1.0, bad)
                with self.assertRaises(OKXUnifiedAccountError):
                    self.engine.compute_multi_currency_margin(self.balances, bad)

    def test_overflowing_equity_is_refused_not_reported_safe(self):
        # Each input is finite, but the product and sum overflow to +inf, which
        # would otherwise sail through the threshold ladder as SAFE.
        huge = [OKXTokenBalance("BTC", 1e308, 1e308)]
        with self.assertRaises(OKXUnifiedAccountError):
            self.engine.compute_multi_currency_margin(huge, 10000.0)

    def test_discount_factor_outside_zero_to_one_is_rejected(self):
        for bad in (-0.1, 1.5):
            with self.subTest(value=bad):
                with self.assertRaises(OKXUnifiedAccountError):
                    OKXTokenBalance("BTC", 1.0, 60000.0, bad)

    def test_non_ascending_tier_bounds_are_rejected(self):
        with self.assertRaises(OKXUnifiedAccountError):
            OKXTokenBalance("BTC", 1.0, 60000.0, discount_tiers=(
                OKXDiscountTier(20.0, 0.98), OKXDiscountTier(10.0, 0.97)))

    def test_unbounded_tier_must_be_last(self):
        with self.assertRaises(OKXUnifiedAccountError):
            OKXTokenBalance("BTC", 1.0, 60000.0, discount_tiers=(
                OKXDiscountTier(None, 0.98), OKXDiscountTier(50.0, 0.97)))

    def test_empty_balances_report_zero_equity_with_a_warning(self):
        report = self.engine.compute_multi_currency_margin([], 1000.0)
        self.assertEqual(report.total_usd_equity, 0.0)
        self.assertEqual(report.status, STATUS_LIQUIDATION_RISK_CALL)
        self.assertTrue(any("No balances" in w for w in report.warnings))

    def test_unmodelled_terms_are_flagged_as_an_upper_bound(self):
        report = self.engine.compute_multi_currency_margin(self.balances, 10000.0)
        self.assertTrue(any("upper bound" in w for w in report.warnings))


class TestOrderPayload(unittest.TestCase):

    def setUp(self):
        self.engine = OKXUnifiedAccountEngine("K", "S", "P")

    def _payload(self, **overrides):
        kwargs = dict(
            inst_id="BTC-USDT-SWAP", td_mode="cross", side="buy", ord_type="limit",
            size=1.0, price=60000.0, cl_ord_id="algo0001")
        kwargs.update(overrides)
        return self.engine.build_order_payload(**kwargs)

    def test_limit_order_payload_fields(self):
        payload = self._payload()
        self.assertEqual(payload["instId"], "BTC-USDT-SWAP")
        self.assertEqual(payload["tdMode"], "cross")
        self.assertEqual(payload["side"], "buy")
        self.assertEqual(payload["ordType"], "limit")
        self.assertEqual(payload["sz"], "1.0")
        self.assertEqual(payload["px"], "60000.0")
        self.assertEqual(payload["clOrdId"], "algo0001")

    def test_client_order_id_is_mandatory(self):
        with self.assertRaises(TypeError):
            self.engine.build_order_payload(
                "BTC-USDT-SWAP", "cross", "buy", "limit", 1.0, 60000.0)

    def test_non_alphanumeric_client_order_id_is_rejected(self):
        for bad in ("11111111-2222-3333-4444-555555555555", "algo_001", "algo-001",
                    "a" * 33, "  "):
            with self.subTest(cl_ord_id=bad):
                with self.assertRaises(OKXUnifiedAccountError):
                    self._payload(cl_ord_id=bad)

    def test_new_client_order_id_is_okx_compliant(self):
        generated = new_client_order_id()
        self.assertTrue(CLIENT_ORDER_ID_PATTERN.match(generated))
        self.assertEqual(len(generated), MAX_CLIENT_ORDER_ID_LENGTH)

        prefixed = new_client_order_id("mmbot")
        self.assertTrue(prefixed.startswith("mmbot"))
        self.assertTrue(CLIENT_ORDER_ID_PATTERN.match(prefixed))
        self.assertNotEqual(new_client_order_id(), new_client_order_id())

    def test_new_client_order_id_rejects_non_alphanumeric_prefix(self):
        with self.assertRaises(OKXUnifiedAccountError):
            new_client_order_id("mm-bot")

    def test_small_size_is_not_emitted_in_scientific_notation(self):
        self.assertEqual(self._payload(size=1e-8)["sz"], "0.00000001")
        self.assertEqual(self._payload(price=1e-8)["px"], "0.00000001")

    def test_decimal_and_string_inputs_preserve_exact_digits(self):
        self.assertEqual(self._payload(size=Decimal("0.010"))["sz"], "0.010")
        self.assertEqual(self._payload(price="59999.50")["px"], "59999.50")
        self.assertEqual(self._payload(size=3)["sz"], "3")

    def test_non_positive_or_unparsable_sizes_are_rejected(self):
        for bad in (0, -1.0, float("nan"), float("inf"), "abc", True, None):
            with self.subTest(size=bad):
                with self.assertRaises(OKXUnifiedAccountError):
                    self._payload(size=bad)

    def test_limit_order_without_price_is_rejected(self):
        with self.assertRaises(OKXUnifiedAccountError):
            self._payload(price=None)

    def test_market_order_must_not_carry_a_price(self):
        with self.assertRaises(OKXUnifiedAccountError):
            self._payload(ord_type="market")
        payload = self._payload(ord_type="market", price=None)
        self.assertNotIn("px", payload)

    def test_priced_order_types_all_require_a_price(self):
        for ord_type in ("limit", "post_only", "fok", "ioc"):
            with self.subTest(ord_type=ord_type):
                self.assertIn("px", self._payload(ord_type=ord_type))
                with self.assertRaises(OKXUnifiedAccountError):
                    self._payload(ord_type=ord_type, price=None)

    def test_invalid_enums_are_rejected(self):
        with self.assertRaises(OKXUnifiedAccountError):
            self._payload(td_mode="margin")
        with self.assertRaises(OKXUnifiedAccountError):
            self._payload(side="long")
        with self.assertRaises(OKXUnifiedAccountError):
            self._payload(ord_type="stop")
        with self.assertRaises(OKXUnifiedAccountError):
            self._payload(pos_side="flat")
        with self.assertRaises(OKXUnifiedAccountError):
            self._payload(tgt_ccy="usd")

    def test_enum_inputs_are_case_normalised(self):
        payload = self._payload(td_mode="CROSS", side="BUY", ord_type="LIMIT",
                                pos_side="LONG")
        self.assertEqual(payload["tdMode"], "cross")
        self.assertEqual(payload["side"], "buy")
        self.assertEqual(payload["ordType"], "limit")
        self.assertEqual(payload["posSide"], "long")

    def test_instrument_id_case_is_preserved(self):
        # instId is case-sensitive on OKX; lowercasing it would break the request.
        self.assertEqual(self._payload()["instId"], "BTC-USDT-SWAP")

    def test_optional_fields_are_omitted_when_not_supplied(self):
        payload = self._payload()
        self.assertNotIn("posSide", payload)
        self.assertNotIn("tgtCcy", payload)

    def test_spot_market_buy_can_declare_the_size_currency(self):
        payload = self._payload(
            inst_id="BTC-USDT", td_mode="cash", ord_type="market", price=None,
            size=100.0, tgt_ccy="quote_ccy")
        self.assertEqual(payload["tgtCcy"], "quote_ccy")
        self.assertEqual(payload["sz"], "100.0")


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main()
