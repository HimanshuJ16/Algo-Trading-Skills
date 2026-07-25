import unittest
from research_environment_vs_production_environment_parity import ResearchEnvironmentVsProductionEnvironmentParity, ResearchEnvironmentVsProductionEnvironmentParityConfig

class TestResearchEnvironmentVsProductionEnvironmentParity(unittest.TestCase):
    def test_init(self):
        config = ResearchEnvironmentVsProductionEnvironmentParityConfig()
        obj = ResearchEnvironmentVsProductionEnvironmentParity(config)
        self.assertIsNotNone(obj)

    def test_process(self):
        config = ResearchEnvironmentVsProductionEnvironmentParityConfig()
        obj = ResearchEnvironmentVsProductionEnvironmentParity(config)
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
