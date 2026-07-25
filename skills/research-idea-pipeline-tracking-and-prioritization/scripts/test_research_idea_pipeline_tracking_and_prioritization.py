import unittest
from research_idea_pipeline_tracking_and_prioritization import ResearchIdeaPipelineTrackingAndPrioritization, ResearchIdeaPipelineTrackingAndPrioritizationConfig

class TestResearchIdeaPipelineTrackingAndPrioritization(unittest.TestCase):
    def test_init(self):
        config = ResearchIdeaPipelineTrackingAndPrioritizationConfig()
        obj = ResearchIdeaPipelineTrackingAndPrioritization(config)
        self.assertIsNotNone(obj)

    def test_process(self):
        config = ResearchIdeaPipelineTrackingAndPrioritizationConfig()
        obj = ResearchIdeaPipelineTrackingAndPrioritization(config)
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
