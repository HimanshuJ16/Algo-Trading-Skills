import unittest
import datetime
from capital_gains_vs_business_income_classification import (
    TaxClassificationEngine,
    ClosedTrade,
    AssetClass,
    TaxCategory
)

class TestTaxClassification(unittest.TestCase):
    
    def setUp(self):
        # We assume the user is a professional algo trader, meaning swing trades are business income.
        self.engine = TaxClassificationEngine(is_professional_trader=True)
        self.now = datetime.datetime(2025, 1, 1, 10, 0, 0)

    def test_derivative_trade(self):
        trade = ClosedTrade(
            trade_id="T1",
            symbol="NIFTY_FUT",
            asset_class=AssetClass.DERIVATIVE,
            open_time=self.now,
            close_time=self.now + datetime.timedelta(days=2),
            net_pnl=1000.0
        )
        cat = self.engine.classify_trade(trade)
        self.assertEqual(cat, TaxCategory.NON_SPECULATIVE_BUSINESS)

    def test_intraday_equity(self):
        trade = ClosedTrade(
            trade_id="T2",
            symbol="RELIANCE",
            asset_class=AssetClass.EQUITY,
            open_time=self.now,
            close_time=self.now + datetime.timedelta(hours=4), # Intraday
            net_pnl=-500.0
        )
        cat = self.engine.classify_trade(trade)
        self.assertEqual(cat, TaxCategory.SPECULATIVE_BUSINESS)

    def test_long_term_equity(self):
        trade = ClosedTrade(
            trade_id="T3",
            symbol="TCS",
            asset_class=AssetClass.EQUITY,
            open_time=self.now - datetime.timedelta(days=400),
            close_time=self.now,
            net_pnl=5000.0
        )
        cat = self.engine.classify_trade(trade)
        self.assertEqual(cat, TaxCategory.LONG_TERM_CAPITAL_GAINS)

    def test_swing_equity_professional(self):
        trade = ClosedTrade(
            trade_id="T4",
            symbol="HDFC",
            asset_class=AssetClass.EQUITY,
            open_time=self.now - datetime.timedelta(days=5),
            close_time=self.now,
            net_pnl=1500.0
        )
        # Because is_professional_trader = True
        cat = self.engine.classify_trade(trade)
        self.assertEqual(cat, TaxCategory.NON_SPECULATIVE_BUSINESS)
        
    def test_swing_equity_casual(self):
        casual_engine = TaxClassificationEngine(is_professional_trader=False)
        trade = ClosedTrade(
            trade_id="T5",
            symbol="HDFC",
            asset_class=AssetClass.EQUITY,
            open_time=self.now - datetime.timedelta(days=5),
            close_time=self.now,
            net_pnl=1500.0
        )
        # Because is_professional_trader = False
        cat = casual_engine.classify_trade(trade)
        self.assertEqual(cat, TaxCategory.SHORT_TERM_CAPITAL_GAINS)

if __name__ == '__main__':
    unittest.main()
