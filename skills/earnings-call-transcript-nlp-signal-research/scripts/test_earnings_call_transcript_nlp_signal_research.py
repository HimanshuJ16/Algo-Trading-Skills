import unittest
from earnings_call_transcript_nlp_signal_research import (
    EarningsTranscriptNlpEngine, EarningsTranscriptAuditReport
)

class TestEarningsTranscriptNlpEngine(unittest.TestCase):

    def setUp(self):
        self.engine = EarningsTranscriptNlpEngine()

    def test_bearish_qa_tone_divergence_signal(self):
        # Prepared remarks: Highly optimistic (positive words: strong, growth, profit, robust) -> Sentiment ~ +1.0
        prep_text = "We delivered strong growth, record profit, and robust expansion across all business units."
        # Q&A session: Hesitant/Defensive (negative/uncertainty words: decline, loss, headwind, risk, uncertain) -> Sentiment ~ -1.0
        qa_text = "We face significant headwind, decline in margins, potential loss, and uncertain market risk."

        report = self.engine.generate_transcript_signal("NVDA", "Q1 2026", prep_text, qa_text)

        self.assertEqual(report.signal, "BEARISH_QA_DIVERGENCE")
        self.assertLess(report.qa_tone_divergence, -0.50)
        self.assertGreater(report.prepared_remarks_sentiment, 0.5)
        self.assertLess(report.qa_session_sentiment, -0.5)

    def test_bullish_earnings_tone_signal(self):
        prep_text = "We achieved strong growth and robust profit momentum."
        qa_text = "Management sees continued growth, strong demand, and robust margin expansion."

        report = self.engine.generate_transcript_signal("AAPL", "Q1 2026", prep_text, qa_text)

        self.assertEqual(report.signal, "BULLISH_EARNINGS_TONE")
        self.assertGreater(report.overall_net_sentiment, 0.5)

if __name__ == '__main__':
    unittest.main()
