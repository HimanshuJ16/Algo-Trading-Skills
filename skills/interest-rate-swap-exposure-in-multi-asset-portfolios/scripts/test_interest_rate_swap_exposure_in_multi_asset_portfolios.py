import unittest
from interest_rate_swap_exposure_in_multi_asset_portfolios import Configuration, InterestRateSwapExposureInMultiAssetPortfoliosEngine

class TestInterestRateSwapExposureInMultiAssetPortfolios(unittest.TestCase):
    def test_default_execution(self):
        engine = InterestRateSwapExposureInMultiAssetPortfoliosEngine()
        res = engine.execute({"value": 10})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 5.0)

    def test_disabled_execution(self):
        engine = InterestRateSwapExposureInMultiAssetPortfoliosEngine(Configuration(enabled=False))
        res = engine.execute({"value": 10})
        self.assertEqual(res["status"], "disabled")
        
    def test_empty_data(self):
        engine = InterestRateSwapExposureInMultiAssetPortfoliosEngine()
        res = engine.execute({})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 0.0)

if __name__ == '__main__':
    unittest.main()
