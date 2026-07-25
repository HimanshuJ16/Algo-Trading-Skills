import unittest
from earnings_call_transcript_nlp_signal_research import EarningsCallTranscriptNlpSignalResearch, EarningsCallTranscriptNlpSignalResearchConfig

class TestEarningsCallTranscriptNlpSignalResearch(unittest.TestCase):
    def test_init(self):
        config = EarningsCallTranscriptNlpSignalResearchConfig()
        obj = EarningsCallTranscriptNlpSignalResearch(config)
        self.assertIsNotNone(obj)

    def test_process(self):
        config = EarningsCallTranscriptNlpSignalResearchConfig()
        obj = EarningsCallTranscriptNlpSignalResearch(config)
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
