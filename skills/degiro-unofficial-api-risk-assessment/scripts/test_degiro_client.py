"""
Unit tests for degiro-unofficial-api-risk-assessment skill.
"""
import unittest
from degiro_client import DEGIROUnofficialRiskManager, RiskLevel


def mock_http_transport(method, url, headers, body):
    """Mock HTTP transport for DEGIRO Web API."""
    if "login/secure/login" in url:
        return 200, {
            "sessionId": "mock_jsession_998877",
            "intAccount": 1234567,
            "clientInfo": {"id": 98765},
        }
    elif "checkOrder" in url:
        return 200, {
            "data": {
                "confirmationId": "conf_abc_123",
                "transactionFee": 1.75,
                "total": 1001.75,
            }
        }
    return 404, {"detail": "Not found"}


class TestDEGIROUnofficialRiskManager(unittest.TestCase):

    def setUp(self):
        self.mgr = DEGIROUnofficialRiskManager(http_fn=mock_http_transport)

    def test_session_login(self):
        session = self.mgr.login_and_extract_session("user", "pass")
        self.assertEqual(session.session_id, "mock_jsession_998877")
        self.assertEqual(session.int_account, 1234567)
        self.assertTrue(session.is_active)

    def test_risk_evaluation_low_risk(self):
        self.mgr.login_and_extract_session("user", "pass")
        eval_res = self.mgr.evaluate_api_risk()
        self.assertEqual(eval_res.risk_level, RiskLevel.LOW)
        self.assertLess(eval_res.risk_score, 0.30)

    def test_check_order_dry_run_success(self):
        self.mgr.login_and_extract_session("user", "pass")
        res = self.mgr.check_order_dry_run(
            product_id=730248,  # ASML Euronext
            buy_sell="BUY",
            order_type=0,  # LIMIT
            price=500.0,
            quantity=2,
        )
        self.assertTrue(res.is_valid)
        self.assertEqual(res.confirmation_id, "conf_abc_123")
        self.assertAlmostEqual(res.estimated_fee, 1.75)
        self.assertAlmostEqual(res.total_transaction_value, 1001.75)

    def test_high_login_attempt_risk_escalation(self):
        for _ in range(5):
            self.mgr.login_and_extract_session("user", "pass")
        eval_res = self.mgr.evaluate_api_risk()
        self.assertIn(eval_res.risk_level, [RiskLevel.HIGH, RiskLevel.CRITICAL_HALT])
        self.assertIn("High login attempt frequency detected (lockout risk).", eval_res.reasons)


if __name__ == "__main__":
    unittest.main()
