import unittest
import datetime
from trs_exposure import (
    TRSSide,
    BenchmarkRate,
    DayCountConvention,
    DividendEvent,
    TRSContractConfig,
    TRSResetPeriod,
    TRSSettlement,
    TRSEngine,
    DERIVATIVES_ERROR,
)


class TestTRSEngine(unittest.TestCase):
    def setUp(self):
        self.engine = TRSEngine()
        self.config = TRSContractConfig(
            swap_id="TRS-AAPL-2026",
            symbol="AAPL",
            notional_amount_usd=1_000_000.0,
            initial_reference_price=200.0,
            quantity_shares=5_000.0,  # 5,000 shares * $200 = $1,000,000
            funding_benchmark=BenchmarkRate.SOFR,
            funding_spread_bps=50.0,  # SOFR + 0.50%
            day_count=DayCountConvention.ACT_360,
            initial_margin_pct=15.0,
        )

    def test_day_count_fraction(self):
        self.assertEqual(TRSEngine.calculate_day_count_fraction(90, DayCountConvention.ACT_360), 0.25)
        self.assertEqual(TRSEngine.calculate_day_count_fraction(73, DayCountConvention.ACT_365), 0.2)

    def test_total_return_leg_gain_and_manufactured_dividends(self):
        div = DividendEvent(
            ex_date=datetime.date(2026, 3, 1),
            payment_date=datetime.date(2026, 3, 15),
            gross_amount_per_share=1.00,
            withholding_tax_pct=15.0,  # Net = $0.85 per share
        )
        reset = TRSResetPeriod(
            period_id="Q1-2026",
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 3, 31),
            start_price=200.0,
            end_price=220.0,  # +$20 per share gain
            benchmark_rate_pct=5.00,
            dividends=[div],
        )

        cap_return, net_div = self.engine.calculate_total_return_leg(self.config, reset)
        # Cap Return: 5,000 shares * $20 = $100,000
        self.assertEqual(cap_return, 100_000.0)
        # Net Dividends: 5,000 shares * $0.85 = $4,250
        self.assertEqual(net_div, 4_250.0)

    def test_funding_leg_sofr_plus_spread(self):
        # 90 days, SOFR = 5.00%, Spread = 0.50% -> Total = 5.50%
        reset = TRSResetPeriod(
            period_id="Q1-2026",
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 4, 1),  # 90 days
            start_price=200.0,
            end_price=200.0,
            benchmark_rate_pct=5.00,
        )
        funding = self.engine.calculate_funding_leg(self.config, reset)
        # Funding = $1,000,000 * 5.50% * (90 / 360) = $1,000,000 * 0.055 * 0.25 = $13,750
        self.assertEqual(funding, 13_750.0)

    def test_process_reset_period_receiver_positive_return(self):
        reset = TRSResetPeriod(
            period_id="Q1-2026",
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 4, 1),  # 90 days
            start_price=200.0,
            end_price=210.0,  # +$10 per share gain = $50,000 cap return
            benchmark_rate_pct=5.00,  # Funding = $13,750
        )
        settlement = self.engine.process_reset_period(self.config, reset, TRSSide.RECEIVER_TOTAL_RETURN)
        self.assertEqual(settlement.total_return_amount_usd, 50_000.0)
        self.assertEqual(settlement.funding_interest_amount_usd, 13_750.0)
        # Net Cashflow to Receiver = $50,000 - $13,750 = $36,250
        self.assertEqual(settlement.net_cashflow_usd, 36_250.0)
        self.assertEqual(settlement.synthetic_delta_shares, 5_000.0)

    def test_process_reset_period_receiver_negative_return(self):
        reset = TRSResetPeriod(
            period_id="Q1-2026",
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 4, 1),
            start_price=200.0,
            end_price=180.0,  # -$20 per share loss = -$100,000 cap return
            benchmark_rate_pct=5.00,  # Funding = $13,750
        )
        settlement = self.engine.process_reset_period(self.config, reset, TRSSide.RECEIVER_TOTAL_RETURN)
        self.assertEqual(settlement.total_return_amount_usd, -100_000.0)
        # Net Cashflow = -$100,000 - $13,750 = -$113,750 (Outflow from Receiver)
        self.assertEqual(settlement.net_cashflow_usd, -113_750.0)

    def test_process_reset_period_payer_short_synthetic(self):
        reset = TRSResetPeriod(
            period_id="Q1-2026",
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 4, 1),
            start_price=200.0,
            end_price=180.0,  # Price fell: -$100,000 return paid by Payer, so Payer gains $100k + receives funding $13,750
            benchmark_rate_pct=5.00,
        )
        settlement = self.engine.process_reset_period(self.config, reset, TRSSide.PAYER_TOTAL_RETURN)
        # Payer net cashflow = Funding ($13,750) - Total Return (-$100,000) = +$113,750 Inflow
        self.assertEqual(settlement.net_cashflow_usd, 113_750.0)
        self.assertEqual(settlement.synthetic_delta_shares, -5_000.0)

    def test_invalid_dates_raises_error(self):
        bad_reset = TRSResetPeriod(
            period_id="BAD",
            start_date=datetime.date(2026, 4, 1),
            end_date=datetime.date(2026, 1, 1),  # Invalid end before start!
            start_price=200.0,
            end_price=210.0,
            benchmark_rate_pct=5.0,
        )
        with self.assertRaises(DERIVATIVES_ERROR):
            self.engine.calculate_funding_leg(self.config, bad_reset)


if __name__ == "__main__":
    unittest.main()

