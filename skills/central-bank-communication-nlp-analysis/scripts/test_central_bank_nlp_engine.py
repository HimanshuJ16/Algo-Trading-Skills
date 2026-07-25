import unittest
from central_bank_nlp_engine import CentralBankNLPEngine

class TestCentralBankNLPEngine(unittest.TestCase):

    def setUp(self):
        self.engine = CentralBankNLPEngine()

    def test_pure_hawkish(self):
        text = "The committee decided to hike rates due to strong inflationary pressures."
        res = self.engine.analyze_sentiment(text)
        # hike, strong, inflationary -> 3 hawkish
        self.assertEqual(res.hawkish_count, 3)
        self.assertEqual(res.dovish_count, 0)
        self.assertAlmostEqual(res.net_score, 1.0)

    def test_pure_dovish(self):
        text = "Policy will remain accommodative given the weak economic slowdown."
        res = self.engine.analyze_sentiment(text)
        # accommodative, weak, slowdown -> 3 dovish
        self.assertEqual(res.hawkish_count, 0)
        self.assertEqual(res.dovish_count, 3)
        self.assertAlmostEqual(res.net_score, -1.0)

    def test_negation_inversion(self):
        # "not" is within 3 words of "hike"
        # "less" is within 3 words of "accommodative"
        text = "We will not immediately hike rates. The policy is less accommodative."
        res = self.engine.analyze_sentiment(text)
        
        # 'hike' is hawkish, but negated -> counts as dovish
        # 'accommodative' is dovish, but negated -> counts as hawkish
        self.assertEqual(res.hawkish_count, 1) # from negated 'accommodative'
        self.assertEqual(res.dovish_count, 1) # from negated 'hike'
        self.assertAlmostEqual(res.net_score, 0.0)
        
    def test_negation_out_of_window(self):
        # "not" is 4 words away from "hike" (window is 3)
        # Words: we (0), will (1), not (2), be (3), looking (4), to (5), hike (6)
        # i=6. Window: 3 to 6: ['be', 'looking', 'to']. 'not' is missed.
        # This tests the strict boundary of the n-gram window.
        text = "We will not be looking to hike rates."
        res = self.engine.analyze_sentiment(text)
        
        self.assertEqual(res.hawkish_count, 1)
        self.assertEqual(res.dovish_count, 0)

if __name__ == '__main__':
    unittest.main()
