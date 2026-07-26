import unittest
from otc_counterparty_risk import (
    OtcCounterpartyRiskEngine, OtcContract, CsaTerms
)

class TestOtcCounterpartyRiskEngine(unittest.TestCase):

    def setUp(self):
        self.engine = OtcCounterpartyRiskEngine(max_ead_limit_usd=1_000_000.0)
        
        self.csa = CsaTerms(
            netting_set_id="ISDA_BANK_ALPHA",
            threshold_usd=100_000.0,
            minimum_transfer_amount=50_000.0,
            posted_collateral_usd=300_000.0,
            counterparty_pd=0.02,        # 2% default probability
            recovery_rate=0.40           # 40% recovery
        )
        
        # 3 Swaps under same netting set:
        # Contract 1: Equity Swap +$500k MTM ($1M Notional @ 6% Add-on = $60k PFE)
        # Contract 2: FX Forward -$200k MTM ($500k Notional @ 4% Add-on = $20k PFE)
        # Contract 3: Rates Swap  +$100k MTM ($2M Notional @ 1% Add-on = $20k PFE)
        # Net MTM = $500k - $200k + $100k = $400k
        self.contracts = [
            OtcContract("SWAP_1", "EQUITY", 1_000_000.0, 500_000.0, 0.06),
            OtcContract("SWAP_2", "FX", 500_000.0, -200_000.0, 0.04),
            OtcContract("SWAP_3", "RATES", 2_000_000.0, 100_000.0, 0.01)
        ]

    def test_current_exposure_and_netting(self):
        # Net MTM = $400k. Posted Collateral = $300k. Threshold = $100k
        # CE = max(0, $400k - $300k - $100k) = $0.0
        ce = self.engine.calculate_current_exposure(self.contracts, self.csa)
        self.assertEqual(ce, 0.0)

    def test_pfe_and_ead_calculation(self):
        # Total PFE = $60k + $20k + $20k = $100k
        pfe = self.engine.calculate_pfe(self.contracts)
        self.assertEqual(pfe, 100_000.0)

    def test_cva_calculation(self):
        # EAD = $100k PFE + $0 CE = $100k
        # LGD = 1 - 0.40 = 0.60
        # CVA = 0.60 * $100,000 * 0.02 = $1,200.0
        cva = self.engine.calculate_cva(ead=100_000.0, pd=0.02, recovery_rate=0.40)
        self.assertEqual(cva, 1200.0)

    def test_full_netting_set_audit(self):
        report = self.engine.analyze_netting_set(self.contracts, self.csa)
        
        self.assertEqual(report.gross_mtm_usd, 800_000.0)
        self.assertEqual(report.net_mtm_usd, 400_000.0)
        self.assertEqual(report.potential_future_exposure_usd, 100_000.0)
        self.assertEqual(report.cva_usd, 1200.0)
        self.assertFalse(report.is_credit_limit_breached)

if __name__ == '__main__':
    unittest.main()
