
import unittest
from unicode_and_encoding_issues_in_global_instrument_names import UnicodeAndEncodingIssuesInGlobalInstrumentNamesEngine, UnicodeAndEncodingIssuesInGlobalInstrumentNamesConfig

class TestUnicodeAndEncodingIssuesInGlobalInstrumentNames(unittest.TestCase):
    def setUp(self):
        self.engine = UnicodeAndEncodingIssuesInGlobalInstrumentNamesEngine(UnicodeAndEncodingIssuesInGlobalInstrumentNamesConfig())
        
    def test_process(self):
        self.assertEqual(self.engine.process("test"), "TEST")
        
    def test_disabled(self):
        engine = UnicodeAndEncodingIssuesInGlobalInstrumentNamesEngine(UnicodeAndEncodingIssuesInGlobalInstrumentNamesConfig(enabled=False))
        self.assertEqual(engine.process("test"), "")

if __name__ == '__main__':
    unittest.main()
