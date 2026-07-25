import unittest
from mark_to_market_election_for_active_traders_us import MarkToMarketElectionForActiveTradersUsEngine, Record

class TestMarkToMarketElectionForActiveTradersUsEngine(unittest.TestCase):
    def test_initialization(self):
        engine = MarkToMarketElectionForActiveTradersUsEngine()
        self.assertEqual(len(engine.records), 0)

    def test_add_record(self):
        engine = MarkToMarketElectionForActiveTradersUsEngine()
        engine.add_record(Record("1", 10.0))
        self.assertEqual(len(engine.records), 1)

    def test_process(self):
        engine = MarkToMarketElectionForActiveTradersUsEngine()
        engine.add_record(Record("1", 10.0))
        engine.add_record(Record("2", 20.0))
        self.assertEqual(engine.process(), 30.0)

if __name__ == '__main__':
    unittest.main()
