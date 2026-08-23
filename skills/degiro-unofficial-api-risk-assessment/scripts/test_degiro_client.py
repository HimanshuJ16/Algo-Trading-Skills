"""
Unit tests for degiro-unofficial-api-risk-assessment skill.
"""
import unittest

from degiro_client import (
    DEGIROAPIError,
    DEGIROAuthError,
    DEGIRORiskThresholdBreached,
    DEGIROUnofficialRiskManager,
    RiskLevel,
)

LOGIN_OK = {
    "sessionId": "mock_jsession_998877",
    "intAccount": 1234567,
    "clientInfo": {"id": 98765},
}

CHECK_OK = {
    "data": {
        "confirmationId": "conf_abc_123",
        "transactionFee": 1.75,
    }
}


def mock_http_transport(method, url, headers, body):
    """Mock transport returning a fully-populated DEGIRO response set."""
    if "login/secure/login/totp" in url:
        if not body.get("oneTimePassword"):
            return 400, {"status": 6, "statusText": "totpNeeded"}
        return 200, dict(LOGIN_OK)
    if "login/secure/login" in url:
        return 200, dict(LOGIN_OK)
    if "checkOrder" in url:
        return 200, {"data": dict(CHECK_OK["data"])}
    if "/trading/secure/v5/order/" in url:
        return 200, {"data": {"orderId": "order_xyz_789"}}
    return 404, {"detail": "Not found"}


def make_transport(**overrides):
    """Builds a transport whose individual responses can be overridden."""
    def _transport(method, url, headers, body):
        if "login/secure/login" in url and "login" in overrides:
            return overrides["login"]
        if "checkOrder" in url and "check" in overrides:
            return overrides["check"]
        if "/trading/secure/v5/order/" in url and "confirm" in overrides:
            return overrides["confirm"]
        return mock_http_transport(method, url, headers, body)
    return _transport


ORDER = dict(product_id=730248, buy_sell="BUY", order_type=0, price=500.0, quantity=2)


class TestSessionAndAuth(unittest.TestCase):

    def setUp(self):
        self.mgr = DEGIROUnofficialRiskManager(http_fn=mock_http_transport)

    def test_session_login(self):
        session = self.mgr.login_and_extract_session("user", "pass")
        self.assertEqual(session.session_id, "mock_jsession_998877")
        self.assertEqual(session.int_account, 1234567)
        self.assertTrue(session.is_active)

    def test_totp_code_is_routed_to_the_totp_endpoint(self):
        """A TOTP code passed to the plain login endpoint is silently dropped and
        fails on any 2FA-enabled account."""
        seen = {}

        def transport(method, url, headers, body):
            seen["url"] = url
            seen["body"] = dict(body)
            return mock_http_transport(method, url, headers, body)

        mgr = DEGIROUnofficialRiskManager(http_fn=transport)
        mgr.login_and_extract_session("user", "pass", totp_code="123456")

        self.assertTrue(seen["url"].endswith("/login/secure/login/totp"))
        self.assertEqual(seen["body"]["oneTimePassword"], "123456")

    def test_missing_int_account_is_not_defaulted(self):
        """Defaulting intAccount addresses another customer's account on every
        subsequent request."""
        mgr = DEGIROUnofficialRiskManager(
            http_fn=make_transport(login=(200, {"sessionId": "s1", "clientInfo": {"id": 1}}))
        )
        with self.assertRaises(DEGIROAuthError) as ctx:
            mgr.login_and_extract_session("user", "pass")
        self.assertIn("intAccount", str(ctx.exception))
        self.assertIsNone(mgr.session)

    def test_missing_client_id_is_not_defaulted(self):
        mgr = DEGIROUnofficialRiskManager(
            http_fn=make_transport(login=(200, {"sessionId": "s1", "intAccount": 42}))
        )
        with self.assertRaises(DEGIROAuthError):
            mgr.login_and_extract_session("user", "pass")

    def test_two_factor_required_is_surfaced_as_actionable_error(self):
        mgr = DEGIROUnofficialRiskManager(
            http_fn=make_transport(login=(400, {"status": 6, "statusText": "totpNeeded"}))
        )
        with self.assertRaises(DEGIROAuthError) as ctx:
            mgr.login_and_extract_session("user", "pass")
        self.assertIn("2FA", str(ctx.exception))

    def test_auth_error_does_not_echo_response_body(self):
        secret = "sessionId_leak_should_not_appear"
        mgr = DEGIROUnofficialRiskManager(
            http_fn=make_transport(login=(401, {"detail": secret}))
        )
        with self.assertRaises(DEGIROAuthError) as ctx:
            mgr.login_and_extract_session("user", "pass")
        self.assertNotIn(secret, str(ctx.exception))

    def test_transport_is_mandatory(self):
        with self.assertRaises(ValueError):
            DEGIROUnofficialRiskManager()

    def test_blank_credentials_rejected(self):
        with self.assertRaises(ValueError):
            self.mgr.login_and_extract_session("", "pass")
        with self.assertRaises(ValueError):
            self.mgr.login_and_extract_session("user", "")


class TestRiskEvaluation(unittest.TestCase):

    def setUp(self):
        self.mgr = DEGIROUnofficialRiskManager(http_fn=mock_http_transport)

    def test_risk_evaluation_low_risk(self):
        self.mgr.login_and_extract_session("user", "pass")
        result = self.mgr.evaluate_api_risk()
        self.assertEqual(result.risk_level, RiskLevel.LOW)
        self.assertLess(result.risk_score, 0.30)

    def test_high_login_attempt_risk_escalation(self):
        for _ in range(5):
            self.mgr.login_and_extract_session("user", "pass")
        result = self.mgr.evaluate_api_risk()
        self.assertIn(result.risk_level, [RiskLevel.HIGH, RiskLevel.CRITICAL_HALT])
        self.assertTrue(any("Login burst" in r for r in result.reasons))

    def test_no_session_is_critical_halt(self):
        result = self.mgr.evaluate_api_risk()
        self.assertEqual(result.risk_level, RiskLevel.CRITICAL_HALT)
        self.assertGreaterEqual(result.risk_score, 0.80)

    def test_stale_session_raises_score(self):
        self.mgr.login_and_extract_session("user", "pass")
        self.mgr.session.created_at -= self.mgr.session_stale_after_s + 1
        result = self.mgr.evaluate_api_risk()
        self.assertTrue(any("Session older than" in r for r in result.reasons))
        self.assertGreaterEqual(result.risk_score, 0.40)

    def test_risk_score_is_capped_at_one(self):
        self.mgr.login_attempts = 99
        result = self.mgr.evaluate_api_risk()
        self.assertLessEqual(result.risk_score, 1.0)

    def test_invalid_threshold_rejected(self):
        with self.assertRaises(ValueError):
            DEGIROUnofficialRiskManager(max_acceptable_risk_score=1.5, http_fn=mock_http_transport)


class TestPreTradeCheck(unittest.TestCase):

    def setUp(self):
        self.mgr = DEGIROUnofficialRiskManager(http_fn=mock_http_transport)
        self.mgr.login_and_extract_session("user", "pass")

    def test_check_order_dry_run_success(self):
        res = self.mgr.check_order_dry_run(**ORDER)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.confirmation_id, "conf_abc_123")
        self.assertAlmostEqual(res.estimated_fee, 1.75)
        self.assertAlmostEqual(res.gross_notional, 1000.0)
        self.assertAlmostEqual(res.total_cost, 1001.75)
        self.assertTrue(res.cost_fields_complete)

    def test_absent_fee_fields_are_unknown_not_zero(self):
        """checkOrder has been observed returning only confirmationId; reporting
        a 0.00 fee understates the cost of every trade."""
        mgr = DEGIROUnofficialRiskManager(
            http_fn=make_transport(check=(200, {"data": {"confirmationId": "c1"}}))
        )
        mgr.login_and_extract_session("user", "pass")
        res = mgr.check_order_dry_run(**ORDER)

        self.assertIsNone(res.estimated_fee)
        self.assertIsNone(res.total_cost)
        self.assertFalse(res.cost_fields_complete)
        self.assertFalse(res.is_valid)
        self.assertIn("not zero", res.error_message)

    def test_absent_fee_fields_may_be_accepted_when_explicitly_opted_in(self):
        mgr = DEGIROUnofficialRiskManager(
            http_fn=make_transport(check=(200, {"data": {"confirmationId": "c1"}})),
            require_complete_cost_fields=False,
        )
        mgr.login_and_extract_session("user", "pass")
        res = mgr.check_order_dry_run(**ORDER)

        self.assertTrue(res.is_valid)
        self.assertIsNone(res.estimated_fee)
        self.assertFalse(res.cost_fields_complete)

    def test_list_cost_blocks_are_aggregated_into_the_fee(self):
        """transactionFees/taxes/FX surcharges are separate list blocks; summing
        only the scalar transactionFee understates total cost."""
        mgr = DEGIROUnofficialRiskManager(
            http_fn=make_transport(check=(200, {"data": {
                "confirmationId": "c1",
                "transactionFee": 2.00,
                "transactionFees": [{"amount": 0.50}, {"amount": 0.25}],
                "transactionTaxes": [{"amount": 1.00}],
                "transactionAutoFxSurcharges": [{"amount": 0.10}],
            }}))
        )
        mgr.login_and_extract_session("user", "pass")
        res = mgr.check_order_dry_run(**ORDER)

        # 2.00 + 0.50 + 0.25 + 1.00 + 0.10, derived independently of the summing loop
        self.assertAlmostEqual(res.estimated_fee, 3.85)
        self.assertAlmostEqual(res.total_cost, 1003.85)

    def test_missing_confirmation_id_is_invalid(self):
        mgr = DEGIROUnofficialRiskManager(
            http_fn=make_transport(check=(200, {"data": {"transactionFee": 1.0}}))
        )
        mgr.login_and_extract_session("user", "pass")
        res = mgr.check_order_dry_run(**ORDER)
        self.assertFalse(res.is_valid)
        self.assertIsNone(res.confirmation_id)

    def test_http_failure_is_reported_not_raised(self):
        mgr = DEGIROUnofficialRiskManager(
            http_fn=make_transport(check=(503, {"detail": "unavailable"}))
        )
        mgr.login_and_extract_session("user", "pass")
        res = mgr.check_order_dry_run(**ORDER)
        self.assertFalse(res.is_valid)
        self.assertIn("503", res.error_message)

    def test_dry_run_without_session_raises_even_with_loose_threshold(self):
        """The old guard relied on the risk score; loosening the threshold turned
        a missing session into an AttributeError."""
        mgr = DEGIROUnofficialRiskManager(
            http_fn=mock_http_transport, max_acceptable_risk_score=1.0
        )
        with self.assertRaises(DEGIROAPIError):
            mgr.check_order_dry_run(**ORDER)

    def test_risk_gate_blocks_when_threshold_exceeded(self):
        mgr = DEGIROUnofficialRiskManager(
            http_fn=mock_http_transport, max_acceptable_risk_score=0.05
        )
        mgr.login_and_extract_session("user", "pass")
        res = mgr.check_order_dry_run(**ORDER)
        self.assertFalse(res.is_valid)
        self.assertIn("risk gate", res.error_message)

    def test_invalid_order_parameters_rejected(self):
        bad = [
            (dict(ORDER, quantity=0), ValueError),
            (dict(ORDER, quantity=-5), ValueError),
            (dict(ORDER, price=0.0), ValueError),
            (dict(ORDER, buy_sell="HOLD"), ValueError),
            (dict(ORDER, order_type=99), ValueError),
            (dict(ORDER, product_id=0), ValueError),
            (dict(ORDER, product_id="ASML"), ValueError),
            (dict(ORDER, order_type="LIMIT"), TypeError),
        ]
        for kwargs, expected in bad:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(expected):
                    self.mgr.check_order_dry_run(**kwargs)

    def test_buy_sell_is_normalized(self):
        res = self.mgr.check_order_dry_run(**dict(ORDER, buy_sell=" buy "))
        self.assertTrue(res.is_valid)

    def test_market_order_allows_zero_price(self):
        res = self.mgr.check_order_dry_run(**dict(ORDER, order_type=2, price=0.0))
        self.assertTrue(res.is_valid)


class TestOrderConfirmation(unittest.TestCase):

    def setUp(self):
        self.mgr = DEGIROUnofficialRiskManager(http_fn=mock_http_transport)
        self.mgr.login_and_extract_session("user", "pass")

    def test_confirm_order_uses_confirmation_id_in_the_path(self):
        seen = {}

        def transport(method, url, headers, body):
            if "/trading/secure/v5/order/" in url:
                seen["url"] = url
            return mock_http_transport(method, url, headers, body)

        mgr = DEGIROUnofficialRiskManager(http_fn=transport)
        mgr.login_and_extract_session("user", "pass")
        check = mgr.check_order_dry_run(**ORDER)
        result = mgr.confirm_order(check, **ORDER)

        self.assertTrue(result.is_submitted)
        self.assertEqual(result.order_id, "order_xyz_789")
        self.assertIn("/order/conf_abc_123;jsessionid=", seen["url"])

    def test_confirmation_id_cannot_be_replayed(self):
        """A retry after a lost response must not become a duplicate order."""
        check = self.mgr.check_order_dry_run(**ORDER)
        self.mgr.confirm_order(check, **ORDER)
        with self.assertRaises(DEGIRORiskThresholdBreached):
            self.mgr.confirm_order(check, **ORDER)

    def test_confirmation_id_is_consumed_even_when_transport_fails(self):
        def flaky(method, url, headers, body):
            if "/trading/secure/v5/order/" in url:
                raise TimeoutError("connection lost after dispatch")
            return mock_http_transport(method, url, headers, body)

        mgr = DEGIROUnofficialRiskManager(http_fn=flaky)
        mgr.login_and_extract_session("user", "pass")
        check = mgr.check_order_dry_run(**ORDER)

        with self.assertRaises(TimeoutError):
            mgr.confirm_order(check, **ORDER)
        # The order may already have reached DEGIRO: the id must not be replayable.
        with self.assertRaises(DEGIRORiskThresholdBreached):
            mgr.confirm_order(check, **ORDER)

    def test_confirm_refuses_a_failed_pre_trade_check(self):
        mgr = DEGIROUnofficialRiskManager(
            http_fn=make_transport(check=(503, {"detail": "down"}))
        )
        mgr.login_and_extract_session("user", "pass")
        check = mgr.check_order_dry_run(**ORDER)
        with self.assertRaises(DEGIROAPIError):
            mgr.confirm_order(check, **ORDER)

    def test_confirm_refuses_an_order_with_unknown_fees(self):
        mgr = DEGIROUnofficialRiskManager(
            http_fn=make_transport(check=(200, {"data": {"confirmationId": "c1"}})),
            require_complete_cost_fields=False,
        )
        mgr.login_and_extract_session("user", "pass")
        check = mgr.check_order_dry_run(**ORDER)
        self.assertTrue(check.is_valid)

        mgr.require_complete_cost_fields = True
        with self.assertRaises(DEGIROAPIError):
            mgr.confirm_order(check, **ORDER)

    def test_missing_order_id_is_reported_as_unconfirmed(self):
        mgr = DEGIROUnofficialRiskManager(
            http_fn=make_transport(confirm=(200, {"data": {}}))
        )
        mgr.login_and_extract_session("user", "pass")
        check = mgr.check_order_dry_run(**ORDER)
        result = mgr.confirm_order(check, **ORDER)

        self.assertFalse(result.is_submitted)
        self.assertIn("reconcile", result.error_message)

    def test_http_error_on_confirm_warns_about_possible_acceptance(self):
        mgr = DEGIROUnofficialRiskManager(
            http_fn=make_transport(confirm=(500, {"detail": "boom"}))
        )
        mgr.login_and_extract_session("user", "pass")
        check = mgr.check_order_dry_run(**ORDER)
        result = mgr.confirm_order(check, **ORDER)

        self.assertFalse(result.is_submitted)
        self.assertIn("may still have been accepted", result.error_message)


if __name__ == "__main__":
    unittest.main()
