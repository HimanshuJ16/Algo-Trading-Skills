import unittest
from datetime import datetime, timezone
from best_execution_record_keeping_global import BestExecutionRecordKeepingGlobalEngine, TradeRecord

class TestBestExecutionRecordKeepingGlobal(unittest.TestCase):
    def setUp(self):
        self.engine = BestExecutionRecordKeepingGlobalEngine(slippage_tolerance=0.01)
        self.base_timestamps = {
            'creation': datetime.now(timezone.utc).isoformat(),
            'submission': datetime.now(timezone.utc).isoformat(),
            'execution': datetime.now(timezone.utc).isoformat()
        }
        
    def test_compliant_trade(self):
        record = TradeRecord(
            order_id="ORD-001",
            symbol="AAPL",
            side="BUY",
            quantity=100,
            price=150.0,
            order_type="LIMIT",
            venue="NASDAQ",
            algo_id="VWAP_01",
            trader_id="TRD123",
            client_id="CLI456",
            timestamps=self.base_timestamps,
            fills=[{'price': 150.0, 'qty': 100, 'timestamp': self.base_timestamps['execution']}],
            benchmark_price=149.5,
            regulatory_tags={"MiFID_II_Capacity": "AOTC"}
        )
        
        analysis = self.engine.run_best_ex_checks(record)
        self.assertTrue(analysis.is_compliant)
        self.assertIn("Best execution standards met", analysis.notes)
        self.assertIsNotNone(analysis.record_hash)

    def test_high_slippage(self):
        record = TradeRecord(
            order_id="ORD-002",
            symbol="MSFT",
            side="BUY",
            quantity=100,
            price=300.0,
            order_type="MARKET",
            venue="NYSE",
            algo_id="TWAP_02",
            trader_id="TRD123",
            client_id="CLI456",
            timestamps=self.base_timestamps,
            fills=[{'price': 305.0, 'qty': 100, 'timestamp': self.base_timestamps['execution']}],
            benchmark_price=300.0,
            regulatory_tags={"SEC_606": "Y"}
        )
        
        analysis = self.engine.run_best_ex_checks(record)
        self.assertFalse(analysis.is_compliant)
        self.assertIn("Slippage", analysis.notes)
        
    def test_missing_regulatory_tags(self):
        record = TradeRecord(
            order_id="ORD-003",
            symbol="GOOGL",
            side="SELL",
            quantity=50,
            price=2800.0,
            order_type="LIMIT",
            venue="NASDAQ",
            algo_id="POV_01",
            trader_id="TRD123",
            client_id="CLI456",
            timestamps=self.base_timestamps,
            fills=[{'price': 2800.0, 'qty': 50, 'timestamp': self.base_timestamps['execution']}],
            benchmark_price=2790.0,
            regulatory_tags={}
        )
        
        analysis = self.engine.run_best_ex_checks(record)
        self.assertFalse(analysis.is_compliant)
        self.assertIn("Missing regulatory tags", analysis.notes)

    def test_missing_execution_timestamp_with_fills(self):
        timestamps = self.base_timestamps.copy()
        del timestamps['execution']
        record = TradeRecord(
            order_id="ORD-004",
            symbol="TSLA",
            side="BUY",
            quantity=10,
            price=900.0,
            order_type="MARKET",
            venue="NASDAQ",
            algo_id="VWAP_01",
            trader_id="TRD123",
            client_id="CLI456",
            timestamps=timestamps,
            fills=[{'price': 905.0, 'qty': 10, 'timestamp': self.base_timestamps['creation']}],
            benchmark_price=900.0,
            regulatory_tags={"MiFID_II_Capacity": "AOTC"}
        )
        analysis = self.engine.run_best_ex_checks(record)
        self.assertFalse(analysis.is_compliant)
        self.assertIn("Missing execution timestamp", analysis.notes)

if __name__ == '__main__':
    unittest.main()
