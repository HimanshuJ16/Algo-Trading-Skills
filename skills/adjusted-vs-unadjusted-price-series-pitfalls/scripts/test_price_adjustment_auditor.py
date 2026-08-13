"""Behavioral tests for adjusted and unadjusted price-series auditing."""

import math
import unittest

from price_adjustment_auditor import (
    CorporateAction,
    PriceAdjustmentAuditor,
    SeriesAdjustmentMode,
)


class TestPriceAdjustmentAuditor(unittest.TestCase):
    def test_detects_known_split_with_volume_consistency(self):
        dates = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
        closes = [200.0, 198.0, 100.0, 101.0]
        volumes = [1000.0, 1100.0, 2200.0, 2100.0]
        actions = [CorporateAction("2024-01-03", "SPLIT", 2.0)]

        report = PriceAdjustmentAuditor().detect_discontinuities(
            "AAPL", closes, volumes, dates, actions
        )

        self.assertFalse(report.is_consistent)
        self.assertEqual(len(report.discontinuities_found), 1)
        event = report.discontinuities_found[0]
        self.assertTrue(event.volume_consistent)
        self.assertTrue(event.action_match)
        self.assertEqual(event.next_open, 100.0)
        self.assertEqual(report.unexplained_discontinuities, 0)
        self.assertFalse(report.has_look_ahead_bias_risk)

    def test_uses_next_open_when_supplied(self):
        report = PriceAdjustmentAuditor().detect_discontinuities(
            "AAPL",
            closes=[100.0, 100.0],
            volumes=[1000.0, 2000.0],
            dates=["2024-01-01", "2024-01-02"],
            known_actions=[CorporateAction("2024-01-02", "SPLIT", 2.0)],
            opens=[100.0, 50.0],
        )

        self.assertEqual(report.discontinuities_found[0].next_open, 50.0)
        self.assertAlmostEqual(report.discontinuities_found[0].observed_price_ratio, 0.5)

    def test_mismatched_known_split_is_unexplained(self):
        report = PriceAdjustmentAuditor().detect_discontinuities(
            "AAPL",
            [100.0, 70.0],
            [1000.0, 1400.0],
            ["2024-01-01", "2024-01-02"],
            [CorporateAction("2024-01-02", "SPLIT", 2.0)],
        )

        self.assertFalse(report.discontinuities_found[0].action_match)
        self.assertEqual(report.unexplained_discontinuities, 1)

    def test_dividend_drop_is_cash_event_not_automatically_lookahead(self):
        report = PriceAdjustmentAuditor().detect_discontinuities(
            "SPY",
            closes=[100.0, 60.0, 61.0],
            volumes=[1000.0, 1000.0, 1000.0],
            dates=["2024-01-01", "2024-01-02", "2024-01-03"],
            known_actions=[CorporateAction("2024-01-02", "DIVIDEND", 40.0)],
        )

        event = report.discontinuities_found[0]
        self.assertTrue(event.action_match)
        self.assertIn("DIVIDEND_CASH_EVENT", event.likely_cause)
        self.assertFalse(report.has_look_ahead_bias_risk)

    def test_total_return_mode_flags_dividend_discontinuity_risk(self):
        auditor = PriceAdjustmentAuditor(
            series_mode=SeriesAdjustmentMode.TOTAL_RETURN_ADJUSTED
        )
        report = auditor.detect_discontinuities(
            "SPY",
            closes=[100.0, 60.0],
            volumes=[1000.0, 1000.0],
            dates=["2024-01-01", "2024-01-02"],
            known_actions=[CorporateAction("2024-01-02", "DIVIDEND", 40.0)],
        )

        self.assertTrue(report.has_look_ahead_bias_risk)
        self.assertEqual(report.unexplained_discontinuities, 1)
        self.assertEqual(
            report.detected_adjustment_type,
            SeriesAdjustmentMode.TOTAL_RETURN_ADJUSTED.value,
        )

    def test_no_discontinuity_does_not_prove_adjustment_provenance(self):
        report = PriceAdjustmentAuditor().detect_discontinuities(
            "AAPL",
            closes=[100.0, 101.0, 100.5],
            volumes=[1000.0, 1100.0, 1050.0],
            dates=["2024-01-01", "2024-01-02", "2024-01-03"],
        )

        self.assertTrue(report.is_consistent)
        self.assertEqual(report.detected_adjustment_type, "UNKNOWN")
        self.assertIn("does not prove", report.message)

    def test_split_backward_adjustment_preserves_precision_and_volume(self):
        auditor = PriceAdjustmentAuditor()
        adjusted_closes, adjusted_volumes = auditor.apply_split_adjustment(
            [100.0, 99.0, 33.0],
            [2.0, 3.0, 4.0],
            split_index=2,
            split_ratio=3.0,
        )

        self.assertAlmostEqual(adjusted_closes[0], 100.0 / 3.0)
        self.assertAlmostEqual(adjusted_volumes[0], 6.0)
        self.assertEqual(adjusted_closes[2], 33.0)
        self.assertEqual(adjusted_volumes[2], 4.0)

    def test_reverse_split_uses_ratio_less_than_one(self):
        adjusted_closes, adjusted_volumes = PriceAdjustmentAuditor().apply_split_adjustment(
            [10.0, 20.0], [100.0, 50.0], split_index=1, split_ratio=0.5
        )

        self.assertEqual(adjusted_closes[0], 20.0)
        self.assertEqual(adjusted_volumes[0], 50.0)
        self.assertEqual(adjusted_closes[1], 20.0)

    def test_multiple_actions_on_same_date_are_preserved(self):
        actions = [
            CorporateAction("2024-01-02", "SPLIT", 2.0),
            CorporateAction("2024-01-02", "DIVIDEND", 1.0),
        ]
        report = PriceAdjustmentAuditor().detect_discontinuities(
            "AAPL",
            [100.0, 49.0],
            [1000.0, 2000.0],
            ["2024-01-01", "2024-01-02"],
            actions,
        )

        self.assertEqual(len(report.discontinuities_found), 1)
        self.assertTrue(report.discontinuities_found[0].action_match)

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            PriceAdjustmentAuditor(discontinuity_threshold_pct=0.0)
        with self.assertRaises(ValueError):
            CorporateAction("2024-01-02", "SPLIT", 0.0)
        with self.assertRaises(ValueError):
            PriceAdjustmentAuditor().detect_discontinuities(
                "AAPL", [100.0, math.nan], [1.0, 1.0], ["2024-01-01", "2024-01-02"]
            )
        with self.assertRaises(ValueError):
            PriceAdjustmentAuditor().apply_split_adjustment(
                [100.0], [1.0, 2.0], split_index=1, split_ratio=2.0
            )

    def test_universe_modes_must_match(self):
        reports = [
            PriceAdjustmentAuditor(
                series_mode=SeriesAdjustmentMode.UNADJUSTED
            ).detect_discontinuities(
                "AAPL", [100.0], [1.0], ["2024-01-01"]
            ),
            PriceAdjustmentAuditor(
                series_mode=SeriesAdjustmentMode.SPLIT_ADJUSTED
            ).detect_discontinuities(
                "MSFT", [100.0], [1.0], ["2024-01-01"]
            ),
        ]

        consistent, message = PriceAdjustmentAuditor().validate_universe_consistency(
            reports
        )

        self.assertFalse(consistent)
        self.assertIn("MIXED DECLARED SERIES MODES", message)


if __name__ == "__main__":
    unittest.main()
