import unittest
from crypto_transaction_tax_lot_tracking import (
    CryptoTaxLotTrackerEngine, CryptoTaxLot, CryptoDispositionResult
)

class TestCryptoTaxLotTrackerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = CryptoTaxLotTrackerEngine(default_matching_method="HIFO")
        
        # 2 ETH Tax Lots
        # Lot 1: 10 ETH @ $1,500 basis ($15,000 total) bought 2025-01-01
        # Lot 2: 5 ETH @ $3,000 basis ($15,000 total) bought 2025-06-01 (High Basis)
        self.engine.register_acquisition(CryptoTaxLot("LOT_1", "ETH", "2025-01-01 10:00:00", days_held=200, quantity=10.0, unit_cost_basis_usd=1500.0))
        self.engine.register_acquisition(CryptoTaxLot("LOT_2", "ETH", "2025-06-01 10:00:00", days_held=50, quantity=5.0, unit_cost_basis_usd=3000.0))

    def test_hifo_swap_disposition_and_gas_fee(self):
        # Swap 4 ETH for 12,000 USDC (Gross Proceeds = $12,000) with $50 Gas Fee
        # HIFO matches Lot 2 (4 ETH @ $3,000 basis = $12,000 cost basis)
        # Net Proceeds = $12,000 - $50 = $11,950
        # Realized PnL = $11,950 - $12,000 = -$50 loss
        res = self.engine.process_crypto_disposition(
            disposal_id="DISP_01", asset_sold="ETH", quantity_sold=4.0,
            asset_received="USDC", gross_proceeds_usd=12000.0, gas_fee_usd=50.0,
            matching_method="HIFO"
        )
        
        self.assertEqual(res.asset_sold, "ETH")
        self.assertEqual(res.asset_received, "USDC")
        self.assertEqual(res.total_cost_basis_usd, 12000.0)
        self.assertEqual(res.net_proceeds_usd, 11950.0)
        self.assertEqual(res.realized_gain_loss_usd, -50.0)

    def test_fifo_swap_disposition(self):
        # Swap 4 ETH for 12,000 USDC using FIFO
        # FIFO matches Lot 1 (4 ETH @ $1,500 basis = $6,000 cost basis)
        # Net Proceeds = $12,000 - $0 = $12,000
        # Realized PnL = $12,000 - $6,000 = +$6,000 gain
        res = self.engine.process_crypto_disposition(
            disposal_id="DISP_02", asset_sold="ETH", quantity_sold=4.0,
            asset_received="USDC", gross_proceeds_usd=12000.0, gas_fee_usd=0.0,
            matching_method="FIFO"
        )
        
        self.assertEqual(res.total_cost_basis_usd, 6000.0)
        self.assertEqual(res.realized_gain_loss_usd, 6000.0)

if __name__ == '__main__':
    unittest.main()
