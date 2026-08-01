import unittest
from research_idea_pipeline_tracking_and_prioritization import (
    ResearchIdeaPipelineTrackingAndPrioritization,
    ResearchIdeaPipelineTrackingAndPrioritizationConfig,
    ResearchIdeaPipelineTrackingAndPrioritizationEngine,
    ResearchIdea, ResearchPipelineReport
)

class TestResearchIdeaPipelineTrackingAndPrioritization(unittest.TestCase):

    def test_legacy_init(self):
        config = ResearchIdeaPipelineTrackingAndPrioritizationConfig()
        obj = ResearchIdeaPipelineTrackingAndPrioritization(config)
        self.assertIsNotNone(obj)

    def test_legacy_process(self):
        config = ResearchIdeaPipelineTrackingAndPrioritizationConfig()
        obj = ResearchIdeaPipelineTrackingAndPrioritization(config)
        self.assertTrue(obj.process())

class TestResearchPipelineEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ResearchIdeaPipelineTrackingAndPrioritizationEngine()

    def test_idea_prioritization_ranking(self):
        # Idea 1: High Sharpe (2.0), $50M cap, low complexity (1), cheap data (1)
        idea1 = ResearchIdea("ID_01", "StatArb Pair Trading", "Quant A", 2.0, 50000000.0, 1, 1, stage="BACKTESTING")
        # Idea 2: Low Sharpe (1.2), $10M cap, high complexity (4), expensive data (4)
        idea2 = ResearchIdea("ID_02", "Satellite Image Foot Traffic", "Quant B", 1.2, 10000000.0, 4, 4, stage="PROPOSED")

        self.engine.add_idea(idea1)
        self.engine.add_idea(idea2)

        report = self.engine.generate_pipeline_report()
        self.assertEqual(report.status, "PIPELINE_ACTIVE")
        self.assertEqual(report.total_ideas, 2)
        self.assertEqual(report.active_ideas, 2)

        top_idea = report.top_priority_ideas[0]
        self.assertEqual(top_idea.idea_id, "ID_01")
        self.assertEqual(top_idea.rank, 1)

    def test_stage_update_and_rejection(self):
        idea = ResearchIdea("ID_03", "Cryptocurrency Arbitrage", "Quant C", 1.5, 5000000.0, 2, 2)
        self.engine.add_idea(idea)

        # Update stage to REJECTED
        self.engine.update_stage("ID_03", "REJECTED")

        report = self.engine.generate_pipeline_report()
        self.assertEqual(report.total_ideas, 1)
        self.assertEqual(report.active_ideas, 0)
        self.assertEqual(report.stage_breakdown["REJECTED"], 1)

if __name__ == '__main__':
    unittest.main()
