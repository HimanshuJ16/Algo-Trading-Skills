"""Tests for the forex currency gain/loss tax characterization engine.

Expected values are derived by hand from the statute, not from the code:

* Blended Sec. 1256 rate = 0.60 x 20% + 0.40 x 37% = 12.0% + 14.8% = 26.8%
  (IRC Sec. 1256(a)(3)).
* Sec. 1256 loss waterfall (IRC Sec. 1212(c) -> 1211(b) -> 1212(b)):
  carryback against prior Sec. 1256 gains, then current capital gains, then
  $3,000 ($1,500 MFS) against ordinary income, then indefinite carryforward.
"""

import logging
import unittest

from currency_gain_loss_tax_treatment_for_forex_trading import (
    CURRENCY_FUTURES,
    ELECT_SECTION_1256,
    FORWARDS,
    INSUFFICIENT_ELIGIBILITY_BASIS,
    REMAIN_SECTION_988,
    SPOT_FOREX,
    ForexTaxReport,
    ForexTaxTreatmentEngine,
    ForexTradeRecord,
)


class TestForexTaxTreatmentEngine(unittest.TestCase):

    def setUp(self):
        # Ordinary = 37%, LTCG = 20%, STCG = 37%.
        self.engine = ForexTaxTreatmentEngine(
            ordinary_income_rate=0.37, ltcg_rate=0.20, stcg_rate=0.37
        )

    @staticmethod
    def _futures(trade_id, pnl, eligible=True, date="2025-03-01", mtm=0.0, open_at_ye=False):
        return ForexTradeRecord(
            trade_id, "EUR/USD", CURRENCY_FUTURES, pnl, date,
            unrealized_mtm_pnl_usd=mtm, is_open_at_year_end=open_at_ye,
            sec1256_eligible=eligible,
        )

    # ------------------------------------------------------------------ #
    # Rates and the profitable case
    # ------------------------------------------------------------------ #
    def test_blended_rate_is_the_statutory_60_40_split(self):
        # Hand-derived: 0.60*0.20 + 0.40*0.37 = 0.268
        self.assertAlmostEqual(self.engine.sec1256_blended_rate, 0.268, places=9)

    def test_profitable_eligible_contracts_recommend_the_election(self):
        trades = [self._futures("T1", 60_000.0), self._futures("T2", 40_000.0, date="2025-06-01")]
        report = self.engine.evaluate_forex_tax(trades, tax_year=2025)

        self.assertIsInstance(report, ForexTaxReport)
        self.assertEqual(report.total_realized_pnl_usd, 100_000.0)
        self.assertEqual(report.sec988_tax_liability_usd, 37_000.0)   # 100k * 37%
        self.assertEqual(report.sec1256_tax_liability_usd, 26_800.0)  # 100k * 26.8%
        self.assertEqual(report.potential_tax_savings_usd, 10_200.0)
        self.assertEqual(report.recommended_election, ELECT_SECTION_1256)
        self.assertEqual(report.sec988_effective_tax_rate_pct, 37.0)
        self.assertEqual(report.sec1256_effective_tax_rate_pct, 26.8)

    def test_year_end_mark_to_market_enters_only_the_sec1256_scenario(self):
        # IRC Sec. 1256(a)(1): open Sec. 1256 contracts are marked to market.
        # Sec. 988 has no such regime, so realized-only there.
        trades = [self._futures("T1", 0.0, mtm=30_000.0, open_at_ye=True)]
        report = self.engine.evaluate_forex_tax(trades, tax_year=2025)

        self.assertEqual(report.total_realized_pnl_usd, 0.0)
        self.assertEqual(report.total_mtm_pnl_usd, 30_000.0)
        self.assertEqual(report.sec1256_scenario_pnl_usd, 30_000.0)
        self.assertEqual(report.sec1256_tax_liability_usd, 8_040.0)  # 30k * 26.8%
        self.assertEqual(report.sec988_tax_liability_usd, 0.0)

    # ------------------------------------------------------------------ #
    # Loss waterfall — the substantive correction
    # ------------------------------------------------------------------ #
    def test_sec1256_loss_carries_back_three_years_against_prior_gains(self):
        # Regression: the previous engine capped every Sec. 1256 loss at $3,000
        # of benefit, ignoring the Sec. 1212(c) carryback entirely.
        engine = ForexTaxTreatmentEngine(
            ordinary_income_rate=0.37, ltcg_rate=0.20, stcg_rate=0.37,
            prior_sec1256_gains_usd=30_000.0,
        )
        report = engine.evaluate_forex_tax([self._futures("T1", -50_000.0)], tax_year=2025)

        # Hand-derived: 30,000 carried back + 3,000 against ordinary + 17,000 forward.
        self.assertEqual(report.sec1256_loss_carryback_usd, 30_000.0)
        self.assertEqual(report.sec1256_loss_against_ordinary_usd, 3_000.0)
        self.assertEqual(report.sec1256_loss_carryforward_usd, 17_000.0)
        # Benefit = 30,000*26.8% + 3,000*37% = 8,040 + 1,110 = 9,150
        self.assertEqual(report.sec1256_tax_liability_usd, -9_150.0)
        self.assertEqual(report.sec988_tax_liability_usd, -18_500.0)  # 50k * 37%
        self.assertEqual(report.potential_tax_savings_usd, -9_350.0)
        self.assertEqual(report.recommended_election, REMAIN_SECTION_988)

    def test_capital_losses_offset_capital_gains_before_the_ordinary_cap(self):
        # IRC Sec. 1211(b): allowed "to the extent of the gains from such sales
        # or exchanges, plus" the lower of $3,000 or the excess.
        engine = ForexTaxTreatmentEngine(
            ordinary_income_rate=0.37, ltcg_rate=0.20, stcg_rate=0.37,
            other_capital_gains_usd=10_000.0,
        )
        report = engine.evaluate_forex_tax([self._futures("T1", -50_000.0)], tax_year=2025)

        self.assertEqual(report.sec1256_loss_offsetting_capital_gains_usd, 10_000.0)
        self.assertEqual(report.sec1256_loss_against_ordinary_usd, 3_000.0)
        self.assertEqual(report.sec1256_loss_carryforward_usd, 37_000.0)
        # Benefit = 10,000*26.8% + 3,000*37% = 2,680 + 1,110 = 3,790
        self.assertEqual(report.sec1256_tax_liability_usd, -3_790.0)

    def test_married_filing_separately_uses_the_1500_cap(self):
        engine = ForexTaxTreatmentEngine(
            ordinary_income_rate=0.37, ltcg_rate=0.20, stcg_rate=0.37,
            married_filing_separately=True,
        )
        self.assertEqual(engine.capital_loss_ordinary_offset_cap, 1_500.0)
        report = engine.evaluate_forex_tax([self._futures("T1", -50_000.0)], tax_year=2025)
        self.assertEqual(report.sec1256_loss_against_ordinary_usd, 1_500.0)
        self.assertEqual(report.sec1256_tax_liability_usd, -555.0)  # 1,500 * 37%
        self.assertEqual(report.sec1256_loss_carryforward_usd, 48_500.0)

    def test_loss_without_prior_gains_falls_back_to_the_3000_cap(self):
        report = self.engine.evaluate_forex_tax([self._futures("T1", -50_000.0)], tax_year=2025)
        self.assertEqual(report.sec1256_loss_carryback_usd, 0.0)
        self.assertEqual(report.sec1256_loss_against_ordinary_usd, 3_000.0)
        self.assertEqual(report.sec1256_tax_liability_usd, -1_110.0)
        self.assertEqual(report.sec988_tax_liability_usd, -18_500.0)
        self.assertEqual(report.recommended_election, REMAIN_SECTION_988)
        # The excess is deferred, not forfeited — the rationale must say so.
        self.assertIn("carried forward", report.rationale)
        self.assertEqual(report.sec1256_loss_carryforward_usd, 47_000.0)

    # ------------------------------------------------------------------ #
    # Sec. 1256 eligibility gating — the legal correction
    # ------------------------------------------------------------------ #
    def test_undetermined_eligibility_blocks_the_60_40_recommendation(self):
        # Regression: the previous engine recommended ELECT_SECTION_1256 for any
        # profitable spot forex, assuming 60/40 eligibility that the statute
        # does not grant.
        trades = [
            ForexTradeRecord("T1", "EUR/USD", SPOT_FOREX, 100_000.0, "2025-03-01"),
        ]
        report = self.engine.evaluate_forex_tax(trades, tax_year=2025)

        self.assertEqual(report.recommended_election, INSUFFICIENT_ELIGIBILITY_BASIS)
        self.assertEqual(report.sec1256_scenario_pnl_usd, 0.0)
        self.assertEqual(report.sec988_tax_liability_usd, 37_000.0)
        self.assertEqual(len(report.eligibility_warnings), 1)
        self.assertIn("not determined", report.eligibility_warnings[0])

    def test_spot_asserted_as_sec1256_eligible_raises_a_warning(self):
        trades = [
            ForexTradeRecord("T1", "EUR/USD", SPOT_FOREX, 100_000.0, "2025-03-01",
                             sec1256_eligible=True),
        ]
        report = self.engine.evaluate_forex_tax(trades, tax_year=2025)
        self.assertEqual(len(report.eligibility_warnings), 1)
        self.assertIn("988(c)(1)(B)(iii)", report.eligibility_warnings[0])

    def test_mixed_book_separates_ordinary_and_60_40_buckets(self):
        # Spot stays Sec. 988 ordinary; eligible futures get the 60/40 scenario.
        trades = [
            ForexTradeRecord("S1", "EUR/USD", SPOT_FOREX, 40_000.0, "2025-02-01",
                             sec1256_eligible=False),
            self._futures("F1", 60_000.0),
        ]
        report = self.engine.evaluate_forex_tax(trades, tax_year=2025)

        self.assertEqual(report.total_realized_pnl_usd, 100_000.0)
        self.assertEqual(report.sec1256_scenario_pnl_usd, 60_000.0)
        # No election: all 100k ordinary = 37,000.
        self.assertEqual(report.sec988_tax_liability_usd, 37_000.0)
        # Election made: the 40k of spot stays ordinary, only the 60k of eligible
        # futures gets 60/40. 40,000*37% + 60,000*26.8% = 14,800 + 16,080 = 30,880.
        self.assertEqual(report.sec1256_tax_liability_usd, 30_880.0)
        # Savings must equal the rate delta on the eligible slice only:
        # 60,000 * (37% - 26.8%) = 6,120.
        self.assertEqual(report.potential_tax_savings_usd, 6_120.0)
        self.assertEqual(report.recommended_election, ELECT_SECTION_1256)
        self.assertEqual(report.pnl_by_instrument_type,
                         {SPOT_FOREX: 40_000.0, CURRENCY_FUTURES: 60_000.0})

    # ------------------------------------------------------------------ #
    # Tax year handling
    # ------------------------------------------------------------------ #
    def test_tax_year_filter_excludes_other_years(self):
        trades = [
            self._futures("T1", 100_000.0, date="2025-03-01"),
            self._futures("T2", 500_000.0, date="2024-03-01"),
            self._futures("T3", 700_000.0, date="2026-03-01"),
        ]
        report = self.engine.evaluate_forex_tax(trades, tax_year=2025)
        self.assertEqual(report.total_realized_pnl_usd, 100_000.0)

    def test_missing_tax_year_includes_everything_and_says_so(self):
        trades = [
            self._futures("T1", 100_000.0, date="2025-03-01"),
            self._futures("T2", 500_000.0, date="2024-03-01"),
        ]
        report = self.engine.evaluate_forex_tax(trades)
        self.assertEqual(report.total_realized_pnl_usd, 600_000.0)
        self.assertTrue(any("tax_year was not supplied" in c for c in report.caveats))

    def test_malformed_trade_date_raises_when_filtering(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_forex_tax([self._futures("T1", 1.0, date="March 2025")], tax_year=2025)

    # ------------------------------------------------------------------ #
    # Input validation
    # ------------------------------------------------------------------ #
    def test_percentage_style_rates_are_rejected(self):
        # Regression: 37.0 instead of 0.37 previously produced a silent 100x
        # overstatement of tax, and the docs invited exactly that mistake.
        for bad in (37.0, 1.01, -0.01, float("nan")):
            with self.assertRaises(ValueError):
                ForexTaxTreatmentEngine(ordinary_income_rate=bad)
        with self.assertRaises(ValueError):
            ForexTaxTreatmentEngine(ltcg_rate=20.0)

    def test_negative_prior_gains_and_capital_gains_are_rejected(self):
        with self.assertRaises(ValueError):
            ForexTaxTreatmentEngine(prior_sec1256_gains_usd=-1.0)
        with self.assertRaises(ValueError):
            ForexTaxTreatmentEngine(other_capital_gains_usd=-1.0)

    def test_unknown_instrument_type_is_rejected(self):
        bad = ForexTradeRecord("T1", "EUR/USD", "CFD", 1_000.0, "2025-01-01")
        with self.assertRaises(ValueError):
            self.engine.evaluate_forex_tax([bad], tax_year=2025)

    def test_duplicate_trade_id_is_rejected(self):
        trades = [self._futures("T1", 1_000.0), self._futures("T1", 2_000.0)]
        with self.assertRaises(ValueError):
            self.engine.evaluate_forex_tax(trades, tax_year=2025)

    def test_mtm_on_a_closed_position_is_rejected(self):
        bad = ForexTradeRecord("T1", "EUR/USD", FORWARDS, 0.0, "2025-01-01",
                               unrealized_mtm_pnl_usd=5_000.0, is_open_at_year_end=False)
        with self.assertRaises(ValueError):
            self.engine.evaluate_forex_tax([bad], tax_year=2025)

    def test_non_finite_pnl_is_rejected(self):
        bad = ForexTradeRecord("T1", "EUR/USD", FORWARDS, float("inf"), "2025-01-01")
        with self.assertRaises(ValueError):
            self.engine.evaluate_forex_tax([bad], tax_year=2025)

    def test_wrong_record_type_is_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.evaluate_forex_tax([{"pnl": 100.0}], tax_year=2025)

    def test_empty_book_produces_zeroes_without_dividing_by_zero(self):
        report = self.engine.evaluate_forex_tax([], tax_year=2025)
        self.assertEqual(report.total_realized_pnl_usd, 0.0)
        self.assertEqual(report.sec988_effective_tax_rate_pct, 0.0)
        self.assertEqual(report.sec1256_effective_tax_rate_pct, 0.0)
        self.assertEqual(report.recommended_election, INSUFFICIENT_ELIGIBILITY_BASIS)

    def test_every_report_carries_the_not_tax_advice_caveat(self):
        report = self.engine.evaluate_forex_tax([self._futures("T1", 1_000.0)], tax_year=2025)
        self.assertTrue(any("NOT TAX ADVICE" in c for c in report.caveats))
        self.assertTrue(any("capital character, not automatically 60/40" in c for c in report.caveats))


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    unittest.main()
