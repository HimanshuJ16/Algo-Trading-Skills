import math
import unittest
from credit_card_transaction_data_signal_construction import (
    CreditCardTransactionSignalEngine, QuarterlyPanelData, validate_yoy_alignment
)

class TestCreditCardTransactionSignalEngine(unittest.TestCase):

    def setUp(self):
        # Panel scaling factor = 50.0 (e.g. 2% panel sampling)
        self.engine = CreditCardTransactionSignalEngine(panel_scaling_multiplier=50.0, surprise_threshold_pct=2.5)

    def test_beat_buy_signal(self):
        # Panel spend = $40M -> Implied Revenue = $2.0B
        # Consensus = $1.90B -> Surprise = +5.26% (>= +2.5% -> BEAT_BUY)
        current = QuarterlyPanelData(
            ticker="CMG", fiscal_quarter="2025-Q1",
            panel_spend_usd=40_000_000.0, panel_transaction_count=2_000_000,
            consensus_revenue_usd=1_900_000_000.0
        )
        res = self.engine.generate_signal(current)

        self.assertEqual(res.signal, "BEAT_BUY")
        self.assertEqual(res.implied_revenue_usd, 2_000_000_000.0)
        self.assertAlmostEqual(res.predicted_surprise_pct, 5.26, places=2)
        # Confidence heuristic: 0.50 + 5.26/20 = 0.76 (rank, not a probability)
        self.assertEqual(res.confidence_score, 0.76)

    def test_miss_sell_signal(self):
        # Panel spend = $35M -> Implied Revenue = $1.75B
        # Consensus = $1.90B -> Surprise = -7.89% (<= -2.5% -> MISS_SELL)
        current = QuarterlyPanelData(
            ticker="CMG", fiscal_quarter="2025-Q1",
            panel_spend_usd=35_000_000.0, panel_transaction_count=1_750_000,
            consensus_revenue_usd=1_900_000_000.0
        )
        res = self.engine.generate_signal(current)

        self.assertEqual(res.signal, "MISS_SELL")
        self.assertAlmostEqual(res.predicted_surprise_pct, -7.89, places=2)

    def test_threshold_boundary_is_inclusive(self):
        # Implied = consensus * 1.025 -> surprise exactly +2.5% -> BEAT_BUY
        at_threshold = QuarterlyPanelData(
            ticker="CMG", fiscal_quarter="2025-Q1",
            panel_spend_usd=38_950_000.0, panel_transaction_count=1_950_000,
            consensus_revenue_usd=1_900_000_000.0
        )
        self.assertEqual(self.engine.generate_signal(at_threshold).signal, "BEAT_BUY")

        # Implied = consensus * 0.975 -> surprise exactly -2.5% -> MISS_SELL
        at_threshold_down = QuarterlyPanelData(
            ticker="CMG", fiscal_quarter="2025-Q1",
            panel_spend_usd=37_050_000.0, panel_transaction_count=1_850_000,
            consensus_revenue_usd=1_900_000_000.0
        )
        self.assertEqual(self.engine.generate_signal(at_threshold_down).signal, "MISS_SELL")

        # Surprise +2.49% stays NEUTRAL
        just_inside = QuarterlyPanelData(
            ticker="CMG", fiscal_quarter="2025-Q1",
            panel_spend_usd=38_946_200.0, panel_transaction_count=1_950_000,
            consensus_revenue_usd=1_900_000_000.0
        )
        res = self.engine.generate_signal(just_inside)
        self.assertEqual(res.signal, "NEUTRAL")
        self.assertEqual(res.confidence_score, 0.50)

    def test_confidence_heuristic_caps_at_one(self):
        # Surprise +20% -> 0.50 + 20/20 = 1.50 capped at 1.0
        current = QuarterlyPanelData(
            ticker="CMG", fiscal_quarter="2025-Q1",
            panel_spend_usd=24_000_000.0, panel_transaction_count=1_200_000,
            consensus_revenue_usd=1_000_000_000.0
        )
        res = self.engine.generate_signal(current)
        self.assertEqual(res.confidence_score, 1.0)

    def test_yoy_growth_calculation(self):
        prior = QuarterlyPanelData(
            ticker="SBUX", fiscal_quarter="2024-Q1",
            panel_spend_usd=30_000_000.0, panel_transaction_count=1_500_000,
            consensus_revenue_usd=1_500_000_000.0
        )
        current = QuarterlyPanelData(
            ticker="SBUX", fiscal_quarter="2025-Q1",
            panel_spend_usd=33_000_000.0, panel_transaction_count=1_600_000,
            consensus_revenue_usd=1_650_000_000.0
        )

        res = self.engine.generate_signal(current, prior_year_data=prior)
        # Prior implied = $1.5B, Current implied = $1.65B -> YoY growth = +10.0%
        self.assertEqual(res.yoy_growth_pct, 10.0)
        self.assertEqual(res.signal, "NEUTRAL") # Matches consensus exactly -> Surprise 0%

    def test_yoy_growth_decomposition_identity(self):
        # Ticket: (33M/1.6M) / (30M/1.5M) = 20.625/20 -> +3.125%
        # Volume: 1.6M/1.5M -> +6.6667%
        # Identity: 1.03125 * 1.066667 = 1.10 -> matches +10.0% YoY
        prior = QuarterlyPanelData(
            ticker="SBUX", fiscal_quarter="2024-Q1",
            panel_spend_usd=30_000_000.0, panel_transaction_count=1_500_000,
            consensus_revenue_usd=1_500_000_000.0
        )
        current = QuarterlyPanelData(
            ticker="SBUX", fiscal_quarter="2025-Q1",
            panel_spend_usd=33_000_000.0, panel_transaction_count=1_600_000,
            consensus_revenue_usd=1_650_000_000.0
        )
        decomposition = self.engine.decompose_growth(current, prior)
        self.assertAlmostEqual(decomposition.ticket_size_growth_pct, 3.125, places=4)
        self.assertAlmostEqual(decomposition.transaction_volume_growth_pct, 6.6667, places=4)

        res = self.engine.generate_signal(current, prior_year_data=prior)
        self.assertEqual(res.ticket_size_growth_pct, decomposition.ticket_size_growth_pct)
        self.assertEqual(res.transaction_volume_growth_pct, decomposition.transaction_volume_growth_pct)
        # Multiplicative identity holds against reported YoY
        recomposed = ((1 + decomposition.ticket_size_growth_pct / 100.0)
                      * (1 + decomposition.transaction_volume_growth_pct / 100.0) - 1.0) * 100.0
        self.assertAlmostEqual(recomposed, res.yoy_growth_pct, places=3)

    def test_missing_prior_year_yields_nan_not_zero(self):
        # No prior-year data: YoY is unknown, NOT flat growth
        current = QuarterlyPanelData(
            ticker="SBUX", fiscal_quarter="2025-Q1",
            panel_spend_usd=33_000_000.0, panel_transaction_count=1_600_000,
            consensus_revenue_usd=1_650_000_000.0
        )
        res = self.engine.generate_signal(current)
        self.assertTrue(math.isnan(res.yoy_growth_pct))
        self.assertTrue(math.isnan(res.ticket_size_growth_pct))

    def test_decomposition_unavailable_with_zero_transaction_count(self):
        # Zero current-transaction count is valid panel data but makes the
        # decomposition undefined: fields stay NaN, signal still produced
        prior = QuarterlyPanelData(
            ticker="SBUX", fiscal_quarter="2024-Q1",
            panel_spend_usd=30_000_000.0, panel_transaction_count=1_500_000,
            consensus_revenue_usd=1_500_000_000.0
        )
        current = QuarterlyPanelData(
            ticker="SBUX", fiscal_quarter="2025-Q1",
            panel_spend_usd=33_000_000.0, panel_transaction_count=0,
            consensus_revenue_usd=1_650_000_000.0
        )
        res = self.engine.generate_signal(current, prior_year_data=prior)
        self.assertEqual(res.yoy_growth_pct, 10.0)
        self.assertTrue(math.isnan(res.ticket_size_growth_pct))

    def test_non_positive_prior_base_raises_instead_of_silent_zero(self):
        # Regression: old behaviour returned 0.0 (false "flat growth")
        with self.assertRaises(ValueError):
            self.engine.calculate_yoy_growth(1_600_000_000.0, 0.0)
        prior = QuarterlyPanelData(
            ticker="SBUX", fiscal_quarter="2024-Q1",
            panel_spend_usd=0.0, panel_transaction_count=1_500_000,
            consensus_revenue_usd=1_500_000_000.0
        )
        current = QuarterlyPanelData(
            ticker="SBUX", fiscal_quarter="2025-Q1",
            panel_spend_usd=33_000_000.0, panel_transaction_count=1_600_000,
            consensus_revenue_usd=1_650_000_000.0
        )
        with self.assertRaises(ValueError):
            self.engine.generate_signal(current, prior_year_data=prior)

    def test_panel_data_input_validation(self):
        base = dict(ticker="CMG", fiscal_quarter="2025-Q1", panel_spend_usd=40_000_000.0,
                    panel_transaction_count=2_000_000, consensus_revenue_usd=1_900_000_000.0)
        for bad in (
            dict(ticker=""), dict(fiscal_quarter=" "), dict(panel_spend_usd=-1.0),
            dict(panel_spend_usd=float("nan")), dict(consensus_revenue_usd=0.0),
            dict(consensus_revenue_usd=float("inf")), dict(panel_transaction_count=-5),
        ):
            with self.assertRaises(ValueError):
                QuarterlyPanelData(**{**base, **bad})

    def test_engine_and_method_input_validation(self):
        with self.assertRaises(ValueError):
            CreditCardTransactionSignalEngine(panel_scaling_multiplier=0.0)
        with self.assertRaises(ValueError):
            CreditCardTransactionSignalEngine(panel_scaling_multiplier=-45.0)
        with self.assertRaises(ValueError):
            CreditCardTransactionSignalEngine(surprise_threshold_pct=-1.0)
        with self.assertRaises(ValueError):
            self.engine.estimate_implied_revenue(-1.0)
        with self.assertRaises(ValueError):
            self.engine.calculate_yoy_growth(-5.0, 100.0)

    def test_mismatched_tickers_rejected(self):
        prior = QuarterlyPanelData(
            ticker="SBUX", fiscal_quarter="2024-Q1",
            panel_spend_usd=30_000_000.0, panel_transaction_count=1_500_000,
            consensus_revenue_usd=1_500_000_000.0
        )
        current = QuarterlyPanelData(
            ticker="CMG", fiscal_quarter="2025-Q1",
            panel_spend_usd=33_000_000.0, panel_transaction_count=1_600_000,
            consensus_revenue_usd=1_650_000_000.0
        )
        with self.assertRaises(ValueError):
            self.engine.generate_signal(current, prior_year_data=prior)

    def test_decompose_growth_validation(self):
        prior = QuarterlyPanelData(
            ticker="SBUX", fiscal_quarter="2024-Q1",
            panel_spend_usd=0.0, panel_transaction_count=1_500_000,
            consensus_revenue_usd=1_500_000_000.0
        )
        current = QuarterlyPanelData(
            ticker="SBUX", fiscal_quarter="2025-Q1",
            panel_spend_usd=33_000_000.0, panel_transaction_count=1_600_000,
            consensus_revenue_usd=1_650_000_000.0
        )
        with self.assertRaises(ValueError):
            self.engine.decompose_growth(current, prior)  # prior spend <= 0

        prior2 = QuarterlyPanelData(
            ticker="SBUX", fiscal_quarter="2024-Q1",
            panel_spend_usd=30_000_000.0, panel_transaction_count=0,
            consensus_revenue_usd=1_500_000_000.0
        )
        with self.assertRaises(ValueError):
            self.engine.decompose_growth(current, prior2)  # prior count <= 0

    def test_near_threshold_surprise_is_not_rounded_up_into_a_signal(self):
        # Regression: classification used the 2-dp rounded surprise, so a
        # +2.496% surprise rounded to 2.50 and was emitted as BEAT_BUY even
        # though it never reached the documented +2.5% threshold.
        consensus = 1_900_000_000.0
        # Implied revenue = consensus * 1.02496 -> panel spend at gamma = 50.0
        spend = consensus * 1.02496 / 50.0
        current = QuarterlyPanelData(
            ticker="CMG", fiscal_quarter="2025-Q1",
            panel_spend_usd=spend, panel_transaction_count=1_950_000,
            consensus_revenue_usd=consensus
        )
        res = self.engine.generate_signal(current)
        self.assertEqual(res.predicted_surprise_pct, 2.5)   # reported, rounded
        self.assertEqual(res.signal, "NEUTRAL")             # classified, unrounded
        self.assertEqual(res.confidence_score, 0.50)

        # Symmetric on the downside
        down = QuarterlyPanelData(
            ticker="CMG", fiscal_quarter="2025-Q1",
            panel_spend_usd=consensus * 0.97504 / 50.0, panel_transaction_count=1_850_000,
            consensus_revenue_usd=consensus
        )
        self.assertEqual(self.engine.generate_signal(down).signal, "NEUTRAL")

    def test_misaligned_quarters_rejected(self):
        # t vs t-1 is sequential growth, not YoY: must not be reported as YoY
        prior = QuarterlyPanelData(
            ticker="SBUX", fiscal_quarter="2024-Q4",
            panel_spend_usd=30_000_000.0, panel_transaction_count=1_500_000,
            consensus_revenue_usd=1_500_000_000.0
        )
        current = QuarterlyPanelData(
            ticker="SBUX", fiscal_quarter="2025-Q1",
            panel_spend_usd=33_000_000.0, panel_transaction_count=1_600_000,
            consensus_revenue_usd=1_650_000_000.0
        )
        with self.assertRaises(ValueError):
            self.engine.generate_signal(current, prior_year_data=prior)
        with self.assertRaises(ValueError):
            self.engine.decompose_growth(current, prior)

        # Two years apart is equally misaligned
        two_years = QuarterlyPanelData(
            ticker="SBUX", fiscal_quarter="2023-Q1",
            panel_spend_usd=30_000_000.0, panel_transaction_count=1_500_000,
            consensus_revenue_usd=1_500_000_000.0
        )
        with self.assertRaises(ValueError):
            self.engine.generate_signal(current, prior_year_data=two_years)

    def test_aligned_quarter_label_variants_accepted(self):
        validate_yoy_alignment("2025-Q1", "2024-Q1")
        validate_yoy_alignment("FY2025-Q4", "FY2024-Q4")
        validate_yoy_alignment("2025 q2", "2024 q2")
        with self.assertRaises(ValueError):
            validate_yoy_alignment("2025-Q2", "2024-Q1")

    def test_unrecognised_quarter_labels_warn_but_do_not_block(self):
        # Non-'YYYY-Qn' schemes cannot be checked from the label alone; the
        # engine warns and proceeds rather than rejecting valid callers.
        prior = QuarterlyPanelData(
            ticker="SBUX", fiscal_quarter="FQ1-prior",
            panel_spend_usd=30_000_000.0, panel_transaction_count=1_500_000,
            consensus_revenue_usd=1_500_000_000.0
        )
        current = QuarterlyPanelData(
            ticker="SBUX", fiscal_quarter="FQ1-current",
            panel_spend_usd=33_000_000.0, panel_transaction_count=1_600_000,
            consensus_revenue_usd=1_650_000_000.0
        )
        with self.assertLogs(
            "credit_card_transaction_data_signal_construction", level="WARNING"
        ) as captured:
            res = self.engine.generate_signal(current, prior_year_data=prior)
        self.assertTrue(any("alignment" in m for m in captured.output))
        self.assertEqual(res.yoy_growth_pct, 10.0)

if __name__ == '__main__':
    unittest.main()
