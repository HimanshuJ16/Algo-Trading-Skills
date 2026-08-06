import unittest
from transfer_pricing_considerations_for_multi_entity_trading_operations import (
    TPMethodology,
    EntityType,
    LegalEntity,
    IntercompanyTransaction,
    DEMPEContribution,
    TransferPricingEngine,
    TransferPricingError,
)


class TestTransferPricingEngine(unittest.TestCase):
    def setUp(self):
        self.engine = TransferPricingEngine()

        self.us_ip_entity = LegalEntity(
            entity_id="ENT-US",
            name="Alpha Trading Systems LLC",
            jurisdiction="United States",
            entity_type=EntityType.IP_OWNER,
            tax_rate_pct=21.0,
        )
        self.uk_manager = LegalEntity(
            entity_id="ENT-UK",
            name="Alpha Asset Management UK Ltd",
            jurisdiction="United Kingdom",
            entity_type=EntityType.INVESTMENT_MANAGER,
            tax_rate_pct=25.0,
        )
        self.sg_execution = LegalEntity(
            entity_id="ENT-SG",
            name="Alpha Execution Hub SG Pte Ltd",
            jurisdiction="Singapore",
            entity_type=EntityType.EXECUTION_HUB,
            tax_rate_pct=17.0,
        )

        self.engine.register_entity(self.us_ip_entity)
        self.engine.register_entity(self.uk_manager)
        self.engine.register_entity(self.sg_execution)

    def test_entity_registration(self):
        self.assertEqual(len(self.engine.entities), 3)
        self.assertIn("ENT-US", self.engine.entities)

    def test_cost_plus_fee_calculation(self):
        # Base cost = $1,000,000, 12% markup -> $1,120,000 fee, $120,000 markup
        fee, markup = TransferPricingEngine.calculate_cost_plus_fee(1_000_000.0, 12.0)
        self.assertEqual(fee, 1_120_000.0)
        self.assertEqual(markup, 120_000.0)

    def test_cup_fee_calculation(self):
        # 50,000 order executions @ $0.50 per order benchmark = $25,000
        cup_fee = TransferPricingEngine.calculate_cup_fee(50_000.0, 0.50)
        self.assertEqual(cup_fee, 25_000.0)

    def test_berry_ratio_calculation(self):
        # Gross profit = $1,150,000, Operating expenses = $1,000,000 -> Berry Ratio = 1.15
        berry = TransferPricingEngine.calculate_berry_ratio(1_150_000.0, 1_000_000.0)
        self.assertAlmostEqual(berry, 1.15)

    def test_process_intercompany_transaction_cost_plus(self):
        tx = IntercompanyTransaction(
            transaction_id="TX-101",
            provider_entity_id="ENT-UK",
            recipient_entity_id="ENT-US",
            service_description="Quant Research & Alpha Strategy Generation",
            base_cost_usd=500_000.0,
            tp_method=TPMethodology.COST_PLUS,
            markup_pct=10.0,
        )
        settlement = self.engine.process_intercompany_transaction(tx)
        self.assertEqual(settlement.arm_length_fee_usd, 550_000.0)
        self.assertEqual(settlement.intercompany_markup_usd, 50_000.0)
        self.assertAlmostEqual(settlement.berry_ratio, 1.10)

    def test_process_intercompany_transaction_cup(self):
        tx = IntercompanyTransaction(
            transaction_id="TX-102",
            provider_entity_id="ENT-SG",
            recipient_entity_id="ENT-US",
            service_description="Order Execution Routing",
            base_cost_usd=100_000.0,
            tp_method=TPMethodology.CUP,
            benchmark_cup_rate_usd=2.50,
            volume_units=50_000.0,  # 50k orders * $2.50 = $125,000
        )
        settlement = self.engine.process_intercompany_transaction(tx)
        self.assertEqual(settlement.arm_length_fee_usd, 125_000.0)
        self.assertEqual(settlement.intercompany_markup_usd, 25_000.0)

    def test_calculate_profit_split_dempe(self):
        dempe_us = DEMPEContribution(
            entity_id="ENT-US",
            development_weight=1.0,  # High IP dev
            enhancement_weight=0.9,
            maintenance_weight=0.8,
            protection_weight=1.0,
            exploitation_weight=0.7,  # Score = 4.4 / 5 = 0.88
        )
        dempe_uk = DEMPEContribution(
            entity_id="ENT-UK",
            development_weight=0.2,
            enhancement_weight=0.3,
            maintenance_weight=0.4,
            protection_weight=0.1,
            exploitation_weight=0.5,  # Score = 1.5 / 5 = 0.30
        )

        split = self.engine.calculate_profit_split(10_000_000.0, [dempe_us, dempe_uk])
        self.assertEqual(split.total_global_pnl_usd, 10_000_000.0)
        # Total DEMPE score = 0.88 + 0.30 = 1.18
        # US Share = (0.88 / 1.18) ~ 74.576% -> $7,457,627.12
        self.assertTrue(split.entity_allocations_usd["ENT-US"] > 7_000_000.0)
        self.assertTrue(split.entity_allocations_usd["ENT-UK"] < 3_000_000.0)

    def test_unregistered_entity_raises_error(self):
        bad_tx = IntercompanyTransaction(
            transaction_id="TX-BAD",
            provider_entity_id="ENT-UNKNOWN",
            recipient_entity_id="ENT-US",
            service_description="Test",
            base_cost_usd=10_000.0,
            tp_method=TPMethodology.COST_PLUS,
        )
        with self.assertRaises(TransferPricingError):
            self.engine.process_intercompany_transaction(bad_tx)


if __name__ == "__main__":
    unittest.main()

