import unittest
from exchange_for_physical_efp_transactions import (
    ExchangeForPhysicalEngine, EfpFuturesLeg, EfpPhysicalLeg, EfpAuditReport
)

class TestExchangeForPhysicalEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ExchangeForPhysicalEngine(quantity_tolerance=1e-4)

    def test_valid_gold_efp_transaction_approved(self):
        # 10 Gold Futures contracts (GC_202612, 100 oz multiplier -> 1,000 oz) @ $2,500.00/oz
        # Spot Gold @ $2,490.00/oz (Observed Basis = +$10.00/oz)
        f_leg = EfpFuturesLeg(
            futures_symbol="GC_202612", contract_count=10, contract_multiplier=100.0,
            futures_price_usd=2500.00, side="SELL_FUTURES"
        )
        p_leg = EfpPhysicalLeg(
            physical_symbol="SPOT_GOLD_USD", physical_quantity=1000.0,
            spot_price_usd=2490.00, side="BUY_PHYSICAL"
        )

        report = self.engine.evaluate_efp_transaction(
            efp_id="EFP_GOLD_001", futures_leg=f_leg, physical_leg=p_leg,
            risk_free_rate=0.04, time_to_expiry_years=0.25
        )

        self.assertTrue(report.is_quantity_equivalent)
        self.assertEqual(report.status, "EFP_APPROVED")
        self.assertEqual(report.observed_efp_basis_usd, 10.00)
        self.assertIsNotNone(report.efrp_clearing_payload)
        self.assertEqual(report.efrp_clearing_payload["reporting_status"], "SUBMITTED_TO_CLEARINGHOUSE")

    def test_quantity_mismatch_rejection(self):
        # 10 Gold Futures contracts (1,000 oz) vs 900 oz physical gold -> REJECTED!
        f_leg = EfpFuturesLeg(
            futures_symbol="GC_202612", contract_count=10, contract_multiplier=100.0,
            futures_price_usd=2500.00, side="SELL_FUTURES"
        )
        p_leg = EfpPhysicalLeg(
            physical_symbol="SPOT_GOLD_USD", physical_quantity=900.0,   # Mismatched!
            spot_price_usd=2490.00, side="BUY_PHYSICAL"
        )

        report = self.engine.evaluate_efp_transaction(
            efp_id="EFP_GOLD_002", futures_leg=f_leg, physical_leg=p_leg
        )

        self.assertFalse(report.is_quantity_equivalent)
        self.assertEqual(report.status, "QUANTITY_MISMATCH_REJECTION")
        self.assertIsNone(report.efrp_clearing_payload)

if __name__ == '__main__':
    unittest.main()
