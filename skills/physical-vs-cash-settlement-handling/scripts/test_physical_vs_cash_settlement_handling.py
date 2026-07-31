import unittest
from physical_vs_cash_settlement_handling import (
    InputData,
    PhysicalVsCashSettlementHandlingEngine,
    ContractSettlementSpec,
    PositionState,
    SettlementReport
)

class TestPhysicalVsCashSettlementHandling(unittest.TestCase):

    def test_process_legacy(self):
        engine = PhysicalVsCashSettlementHandlingEngine()
        res = engine.process(InputData(value=10.0))
        self.assertEqual(res, 20.0)

    def test_process_zero_legacy(self):
        engine = PhysicalVsCashSettlementHandlingEngine()
        res = engine.process(InputData(value=0.0))
        self.assertEqual(res, 0.0)

    def test_cash_settlement_pnl(self):
        engine = PhysicalVsCashSettlementHandlingEngine()
        # ES S&P 500 futures: 10 contracts @ $5,000 entry, settled @ $5,050 (Multiplier 50)
        spec = ContractSettlementSpec("ES", "SP500", "CASH", multiplier=50.0)
        pos = PositionState(position_qty=10.0, entry_price=5000.0, account_cash_balance=100000.0)

        report = engine.evaluate_settlement_risk(spec, pos, settlement_price=5050.0)
        self.assertEqual(report.status, "CASH_SETTLED_OK")
        self.assertEqual(report.cash_settlement_pnl, 25000.0) # 10 * 50 * 50 = $25,000
        self.assertFalse(report.is_fnd_risk_active)

    def test_physical_delivery_fnd_breach(self):
        engine = PhysicalVsCashSettlementHandlingEngine()
        # CL WTI Crude Oil: 5 contracts @ $70.00 (Multiplier 1000 => $350,000 delivery value)
        spec = ContractSettlementSpec("CL", "WTI_CRUDE", "PHYSICAL", multiplier=1000.0, days_to_notice_date=2)
        # Account has $50,000 cash and no delivery facility -> Breach!
        pos = PositionState(position_qty=5.0, entry_price=70.0, account_cash_balance=50000.0, has_delivery_facility=False)

        report = engine.evaluate_settlement_risk(spec, pos, settlement_price=70.0)
        self.assertEqual(report.status, "PHYSICAL_DELIVERY_RISK_BREACH")
        self.assertTrue(report.is_fnd_risk_active)
        self.assertEqual(report.notional_delivery_value, 350000.0)

if __name__ == '__main__':
    unittest.main()
