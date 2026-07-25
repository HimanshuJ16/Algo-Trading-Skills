import unittest
from cross_margining_across_asset_classes import Configuration, CrossMarginingAcrossAssetClassesEngine

class TestCrossMarginingAcrossAssetClasses(unittest.TestCase):
    def test_default_execution(self):
        engine = CrossMarginingAcrossAssetClassesEngine()
        res = engine.execute({"value": 10})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 5.0)

    def test_disabled_execution(self):
        engine = CrossMarginingAcrossAssetClassesEngine(Configuration(enabled=False))
        res = engine.execute({"value": 10})
        self.assertEqual(res["status"], "disabled")
        
    def test_empty_data(self):
        engine = CrossMarginingAcrossAssetClassesEngine()
        res = engine.execute({})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 0.0)

if __name__ == '__main__':
    unittest.main()
