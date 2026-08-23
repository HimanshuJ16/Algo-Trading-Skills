import unittest
from otc_counterparty_risk import (
    OtcCounterpartyRiskEngine, OtcContract, CsaTerms, SA_CCR_SUPERVISORY_FACTORS
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

        # 3 trades under one netting set, canonical BCBS 279 Table 2 factors:
        # Contract 1: Equity swap +$500k MTM, $1M notional @ 32% SF = $320k add-on
        # Contract 2: FX forward  -$200k MTM, $500k notional @ 4% SF  = $20k add-on
        # Contract 3: Rates swap  +$100k MTM, $2M notional @ 0.5% SF  = $10k add-on
        # Net MTM = $500k - $200k + $100k = $400k; AddOn aggregate = $350k
        self.contracts = [
            OtcContract("SWAP_1", "EQUITY", 1_000_000.0, 500_000.0, 0.32),
            OtcContract("SWAP_2", "FX", 500_000.0, -200_000.0, 0.04),
            OtcContract("SWAP_3", "RATES", 2_000_000.0, 100_000.0, 0.005)
        ]

    def test_current_exposure_uses_sa_ccr_margined_rc(self):
        # BCBS 279 para 144: RC = max(V - C, TH + MTA - NICA, 0)
        # V - C = 400k - 300k = 100k; TH + MTA = 150k; RC = 150k (floor binds)
        rc = self.engine.calculate_current_exposure(self.contracts, self.csa)
        self.assertEqual(rc, 150_000.0)

    def test_unmargined_rc_degenerates_without_csa_terms(self):
        # TH = MTA = NICA = 0 collapses para 144 to para 136: RC = max(V - C, 0)
        unmargined = CsaTerms(
            netting_set_id="ISDA_BANK_ALPHA_UNMARGINED",
            threshold_usd=0.0, minimum_transfer_amount=0.0,
            posted_collateral_usd=0.0, counterparty_pd=0.02, recovery_rate=0.40
        )
        self.assertEqual(
            self.engine.calculate_current_exposure(self.contracts, unmargined), 400_000.0
        )
        negative_net = [OtcContract("SWAP_4", "FX", 500_000.0, -900_000.0, 0.04)]
        self.assertEqual(
            self.engine.calculate_current_exposure(negative_net, unmargined), 0.0
        )

    def test_pfe_add_on_aggregate(self):
        # AddOn = 320k + 20k + 10k = 350k (sum of notional x supervisory factor)
        self.assertEqual(self.engine.calculate_pfe(self.contracts), 350_000.0)

    def test_pfe_multiplier_is_one_when_net_collateral_positive(self):
        # V - C = +100k >= 0: multiplier caps at exactly 1.0 (para 149)
        m = self.engine.calculate_pfe_multiplier(400_000.0, 300_000.0, 350_000.0)
        self.assertEqual(m, 1.0)
        self.assertEqual(self.engine.calculate_pfe_multiplier(400_000.0, 400_000.0, 350_000.0), 1.0)

    def test_pfe_multiplier_exact_value_when_over_collateralized(self):
        # Exponent = -190k / (2 * 0.95 * 200k) = -0.5 exactly.
        # multiplier = 0.05 + 0.95 * e^-0.5, e^-0.5 = 0.6065306597126334 (known constant)
        expected = 0.05 + 0.95 * 0.6065306597126334
        m = self.engine.calculate_pfe_multiplier(10_000.0, 200_000.0, 200_000.0)
        self.assertAlmostEqual(m, expected, places=12)

    def test_pfe_multiplier_floor_and_zero_add_on(self):
        # Deeply over-collateralized: multiplier approaches the 5% floor
        m = self.engine.calculate_pfe_multiplier(-1e12, 0.0, 200_000.0)
        self.assertAlmostEqual(m, 0.05, places=6)
        # Zero add-on must not divide by zero
        self.assertEqual(self.engine.calculate_pfe_multiplier(400_000.0, 300_000.0, 0.0), 1.0)

    def test_cva_calculation(self):
        # LGD = 1 - 0.40 = 0.60; CVA = 0.60 * $100,000 * 0.02 = $1,200.0
        cva = self.engine.calculate_cva(ead=100_000.0, pd=0.02, recovery_rate=0.40)
        self.assertEqual(cva, 1200.0)

    def test_cva_boundaries(self):
        self.assertEqual(self.engine.calculate_cva(100_000.0, 0.0, 0.40), 0.0)
        self.assertEqual(self.engine.calculate_cva(100_000.0, 0.02, 1.0), 0.0)

    def test_full_netting_set_audit(self):
        # RC = 150k (floor binds); AddOn = 350k; multiplier = 1 (V - C > 0);
        # EAD = 1.4 * (150k + 350k) = 700k; CVA = 0.6 * 700k * 0.02 = $8,400
        # Margin: V - C - TH = 0 < MTA 50k -> no call; EAD 700k < limit 1M
        report = self.engine.analyze_netting_set(self.contracts, self.csa)

        self.assertEqual(report.gross_mtm_usd, 800_000.0)
        self.assertEqual(report.net_mtm_usd, 400_000.0)
        self.assertEqual(report.netted_current_exposure_usd, 150_000.0)
        self.assertEqual(report.potential_future_exposure_usd, 350_000.0)
        self.assertEqual(report.exposure_at_default_usd, 700_000.0)
        self.assertEqual(report.cva_usd, 8400.0)
        self.assertEqual(report.pfe_multiplier, 1.0)
        self.assertFalse(report.is_margin_call_triggered)
        self.assertEqual(report.margin_call_amount_usd, 0.0)
        self.assertFalse(report.is_credit_limit_breached)

    def test_over_collateralized_audit_applies_multiplier(self):
        # C = 800k: V - C = -400k < 0 so the multiplier activates;
        # RC floor = TH + MTA = 150k still binds; PFE = multiplier * 350k < 350k
        csa = CsaTerms(
            netting_set_id="ISDA_BANK_ALPHA",
            threshold_usd=100_000.0, minimum_transfer_amount=50_000.0,
            posted_collateral_usd=800_000.0, counterparty_pd=0.02, recovery_rate=0.40
        )
        report = self.engine.analyze_netting_set(self.contracts, csa)
        self.assertGreater(report.pfe_multiplier, 0.05)
        self.assertLess(report.pfe_multiplier, 1.0)
        self.assertEqual(report.netted_current_exposure_usd, 150_000.0)
        self.assertLess(report.potential_future_exposure_usd, 350_000.0)
        # EAD must equal alpha * (RC + multiplier * AddOn); tolerance covers the
        # report rounding pfe_multiplier to 6dp while EAD uses the exact value
        expected_ead = 1.4 * (150_000.0 + report.pfe_multiplier * 350_000.0)
        self.assertAlmostEqual(report.exposure_at_default_usd, expected_ead, delta=0.5)

    def test_negative_net_mtm_still_carries_threshold_floor(self):
        # Even with deeply negative MTM, RC cannot fall below TH + MTA = 150k
        contracts = [OtcContract("SWAP_LOSS", "EQUITY", 1_000_000.0, -500_000.0, 0.32)]
        report = self.engine.analyze_netting_set(contracts, CsaTerms(
            netting_set_id="ISDA_BANK_ALPHA", threshold_usd=100_000.0,
            minimum_transfer_amount=50_000.0, posted_collateral_usd=0.0,
            counterparty_pd=0.02, recovery_rate=0.40
        ))
        self.assertEqual(report.netted_current_exposure_usd, 150_000.0)
        self.assertFalse(report.is_margin_call_triggered)

    def test_margin_call_triggered_when_delivery_amount_exceeds_mta(self):
        # No collateral: delivery amount = 400k - 100k TH = 300k >= MTA 50k
        csa = CsaTerms(
            netting_set_id="ISDA_BANK_ALPHA", threshold_usd=100_000.0,
            minimum_transfer_amount=50_000.0, posted_collateral_usd=0.0,
            counterparty_pd=0.02, recovery_rate=0.40
        )
        report = self.engine.analyze_netting_set(self.contracts, csa)
        self.assertTrue(report.is_margin_call_triggered)
        self.assertEqual(report.margin_call_amount_usd, 300_000.0)

    def test_margin_call_mta_boundary_is_inclusive(self):
        # C = 250k: delivery amount = 400k - 250k - 100k = 50k == MTA -> triggers
        csa_at = CsaTerms(
            netting_set_id="ISDA_BANK_ALPHA", threshold_usd=100_000.0,
            minimum_transfer_amount=50_000.0, posted_collateral_usd=250_000.0,
            counterparty_pd=0.02, recovery_rate=0.40
        )
        report = self.engine.analyze_netting_set(self.contracts, csa_at)
        self.assertTrue(report.is_margin_call_triggered)
        self.assertEqual(report.margin_call_amount_usd, 50_000.0)

        # C = 260k: delivery amount = 40k < MTA -> suppressed, amount reports 0
        csa_below = CsaTerms(
            netting_set_id="ISDA_BANK_ALPHA", threshold_usd=100_000.0,
            minimum_transfer_amount=50_000.0, posted_collateral_usd=260_000.0,
            counterparty_pd=0.02, recovery_rate=0.40
        )
        report = self.engine.analyze_netting_set(self.contracts, csa_below)
        self.assertFalse(report.is_margin_call_triggered)
        self.assertEqual(report.margin_call_amount_usd, 0.0)

    def test_credit_limit_breach_and_boundary(self):
        strict = OtcCounterpartyRiskEngine(max_ead_limit_usd=500_000.0)
        self.assertTrue(strict.analyze_netting_set(self.contracts, self.csa).is_credit_limit_breached)
        # EAD == limit is NOT a breach (strict inequality)
        at_boundary = OtcCounterpartyRiskEngine(max_ead_limit_usd=700_000.0)
        self.assertFalse(
            at_boundary.analyze_netting_set(self.contracts, self.csa).is_credit_limit_breached
        )

    def test_supervisory_factors_match_bcbs279_table2(self):
        # Independently transcribed from BCBS 279 (March 2014) Table 2
        self.assertEqual(SA_CCR_SUPERVISORY_FACTORS["INTEREST_RATE"], 0.005)
        self.assertEqual(SA_CCR_SUPERVISORY_FACTORS["FX"], 0.04)
        self.assertEqual(SA_CCR_SUPERVISORY_FACTORS["EQUITY_SINGLE"], 0.32)
        self.assertEqual(SA_CCR_SUPERVISORY_FACTORS["EQUITY_INDEX"], 0.20)
        self.assertEqual(SA_CCR_SUPERVISORY_FACTORS["COMMODITY_ELECTRICITY"], 0.40)
        self.assertEqual(SA_CCR_SUPERVISORY_FACTORS["COMMODITY_OTHER"], 0.18)
        self.assertEqual(SA_CCR_SUPERVISORY_FACTORS["CREDIT_INDEX_IG"], 0.0038)
        self.assertEqual(SA_CCR_SUPERVISORY_FACTORS["CREDIT_INDEX_SG"], 0.0106)

    def test_contract_input_validation(self):
        with self.assertRaises(ValueError):
            OtcContract("", "EQUITY", 1_000_000.0, 0.0, 0.32)
        with self.assertRaises(ValueError):
            OtcContract("SWAP_1", "  ", 1_000_000.0, 0.0, 0.32)
        with self.assertRaises(ValueError):
            OtcContract("SWAP_1", "EQUITY", -1.0, 0.0, 0.32)
        with self.assertRaises(ValueError):
            OtcContract("SWAP_1", "EQUITY", 1_000_000.0, 0.0, -0.01)
        with self.assertRaises(ValueError):
            OtcContract("SWAP_1", "EQUITY", 1_000_000.0, 0.0, 1.5)
        with self.assertRaises(ValueError):
            OtcContract("SWAP_1", "EQUITY", float("nan"), 0.0, 0.32)
        with self.assertRaises(ValueError):
            OtcContract("SWAP_1", "EQUITY", 1_000_000.0, float("inf"), 0.32)

    def test_csa_input_validation(self):
        base = dict(netting_set_id="X", threshold_usd=0.0, minimum_transfer_amount=0.0,
                    posted_collateral_usd=0.0, counterparty_pd=0.02, recovery_rate=0.40)
        for bad in (
            dict(netting_set_id=""), dict(threshold_usd=-1.0), dict(minimum_transfer_amount=-1.0),
            dict(posted_collateral_usd=-1.0), dict(counterparty_pd=-0.1), dict(counterparty_pd=1.5),
            dict(counterparty_pd=float("nan")), dict(recovery_rate=-1.0), dict(recovery_rate=2.0),
        ):
            with self.assertRaises(ValueError):
                CsaTerms(**{**base, **bad})

    def test_engine_and_method_input_validation(self):
        with self.assertRaises(ValueError):
            OtcCounterpartyRiskEngine(max_ead_limit_usd=0.0)
        with self.assertRaises(ValueError):
            OtcCounterpartyRiskEngine(max_ead_limit_usd=-5.0)
        with self.assertRaises(ValueError):
            OtcCounterpartyRiskEngine(alpha=0.0)
        with self.assertRaises(ValueError):
            self.engine.analyze_netting_set([], self.csa)
        with self.assertRaises(ValueError):
            self.engine.calculate_cva(100_000.0, 2.0, 0.40)
        with self.assertRaises(ValueError):
            self.engine.calculate_cva(100_000.0, 0.02, -1.0)
        with self.assertRaises(ValueError):
            self.engine.calculate_cva(-1.0, 0.02, 0.40)
        with self.assertRaises(ValueError):
            self.engine.calculate_pfe_multiplier(float("nan"), 0.0, 100_000.0)


if __name__ == '__main__':
    unittest.main()
