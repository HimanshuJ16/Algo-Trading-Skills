
import unittest
from historical_order_book_reconstruction_from_message_logs import HistoricalOrderBookReconstructionFromMessageLogsEngine, HistoricalOrderBookReconstructionFromMessageLogsConfig

class TestHistoricalOrderBookReconstructionFromMessageLogs(unittest.TestCase):
    def setUp(self):
        self.engine = HistoricalOrderBookReconstructionFromMessageLogsEngine(HistoricalOrderBookReconstructionFromMessageLogsConfig())
        
    def test_process(self):
        self.assertEqual(self.engine.process("test"), "TEST")
        
    def test_disabled(self):
        engine = HistoricalOrderBookReconstructionFromMessageLogsEngine(HistoricalOrderBookReconstructionFromMessageLogsConfig(enabled=False))
        self.assertEqual(engine.process("test"), "")

if __name__ == '__main__':
    unittest.main()
