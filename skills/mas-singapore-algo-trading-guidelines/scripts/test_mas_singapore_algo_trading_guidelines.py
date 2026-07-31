import unittest
from mas_singapore_algo_trading_guidelines import (
    MasSingaporeAlgoComplianceEngine, MasAlgoConfig, SgxOrderRequest, MasComplianceReport
)

class TestMasSingaporeAlgoComplianceEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MasSingaporeAlgoComplianceEngine()
        self.valid_config = MasAlgoConfig(
            algo_id="SG_QUANT_01",
            mas_reg_number="SG_MAS_ALGO_9812",
            is_sandbox_tested=True,
            is_kill_switch_ready=True,
            max_order_notional_sgd=1000000.0,
            max_order_rate_per_sec=50,
            max_price_collar_pct=10.0
        )

    def test_successful_sgx_order_compliance(self):
        # DBS (D05) @ $30.00 SGD, Ref = $30.00 SGD, 1,000 shares -> Notional = $30,000 SGD -> APPROVED!
        order = SgxOrderRequest("SG_QUANT_01", "D05", "BUY", price_sgd=30.0, quantity=1000, sgx_ref_price_sgd=30.0)
        report = self.engine.validate_sgx_order_compliance(self.valid_config, order)

        self.assertEqual(report.status, "MAS_ORDER_APPROVED")
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.price_deviation_pct, 0.0)
        self.assertEqual(report.order_notional_sgd, 30000.0)

    def test_governance_breach_missing_mas_reg(self):
        # Missing MAS Registration Number -> REJECT!
        bad_config = MasAlgoConfig("UNREG_ALGO", mas_reg_number="", is_sandbox_tested=True, is_kill_switch_ready=True)
        order = SgxOrderRequest("UNREG_ALGO", "D05", "BUY", price_sgd=30.0, quantity=100, sgx_ref_price_sgd=30.0)
        report = self.engine.validate_sgx_order_compliance(bad_config, order)

        self.assertEqual(report.status, "MAS_REJECT_GOVERNANCE_BREACH")
        self.assertFalse(report.is_compliant)

    def test_sgx_price_collar_breach(self):
        # Price $36.00 SGD vs Ref $30.00 SGD (+20.0% deviation > 10% collar limit) -> REJECT!
        order = SgxOrderRequest("SG_QUANT_01", "D05", "BUY", price_sgd=36.0, quantity=1000, sgx_ref_price_sgd=30.0)
        report = self.engine.validate_sgx_order_compliance(self.valid_config, order)

        self.assertEqual(report.status, "MAS_REJECT_PRICE_COLLAR_BREACH")
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.price_deviation_pct, 20.0)

if __name__ == '__main__':
    unittest.main()
