import unittest
from datetime import datetime
from universe_lookahead_auditor import UniverseLookaheadAuditor, ConstituentRecord

class TestUniverseLookaheadAuditor(unittest.TestCase):
    def setUp(self):
        self.auditor = UniverseLookaheadAuditor()

    def test_clean_universe(self):
        snapshot = datetime(2020, 1, 1)
        constituents = [
            ConstituentRecord("AAPL", datetime(1980, 12, 12), None, datetime(2019, 12, 31)),
            ConstituentRecord("GE", datetime(1900, 1, 1), datetime(2025, 1, 1), datetime(2019, 12, 31)),
        ]
        result = self.auditor.audit_universe_snapshot(snapshot, constituents)
        self.assertTrue(result.is_clean)

    def test_lookahead_publication_date(self):
        snapshot = datetime(2020, 1, 1)
        constituents = [
            # Data wasn't publicly known until Jan 5th!
            ConstituentRecord("TSLA", datetime(2019, 1, 1), None, datetime(2020, 1, 5)),
        ]
        result = self.auditor.audit_universe_snapshot(snapshot, constituents)
        self.assertFalse(result.is_clean)
        self.assertTrue(any("Lookahead Leak" in v for v in result.lookahead_violations))

    def test_survivorship_bias_warning(self):
        snapshot = datetime(2020, 1, 1)
        # 51 constituents, NONE of them have a removed_date. Highly suspicious for a broad index.
        constituents = [
            ConstituentRecord(f"SYM_{i}", datetime(2010, 1, 1), None, datetime(2019, 12, 31))
            for i in range(51)
        ]
        result = self.auditor.audit_universe_snapshot(snapshot, constituents)
        self.assertFalse(result.is_clean)
        self.assertTrue(any("Survivorship Bias" in w for w in result.survivorship_warnings))

if __name__ == '__main__':
    unittest.main()
