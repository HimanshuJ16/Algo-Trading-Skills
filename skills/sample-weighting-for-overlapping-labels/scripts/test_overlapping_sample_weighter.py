import unittest
from overlapping_sample_weighter import (
    OverlappingSampleWeighter, OverlappingSampleWeighterConfig,
    SampleWeightingForOverlappingLabelsEngine, LabelSpan,
    WeightingMethod, SampleWeightingReport
)

class TestOverlappingSampleWeighterLegacy(unittest.TestCase):
    def test_initialization(self):
        config = OverlappingSampleWeighterConfig()
        obj = OverlappingSampleWeighter(config)
        self.assertEqual(obj.config.parameter_1, 1.0)

    def test_process(self):
        config = OverlappingSampleWeighterConfig(parameter_1=2.0)
        obj = OverlappingSampleWeighter(config)
        data = [{"value": 10}, {"value": 20}]
        result = obj.process(data)
        self.assertEqual(result[0]["result"], 20.0)
        self.assertEqual(result[1]["result"], 40.0)

class TestSampleWeightingForOverlappingLabelsEngine(unittest.TestCase):

    def setUp(self):
        self.engine = SampleWeightingForOverlappingLabelsEngine()

    def test_non_overlapping_spans_have_full_uniqueness(self):
        # Spans do not overlap: [1, 5] and [6, 10]
        spans = [
            LabelSpan("S1", 1, 5, 0.02),
            LabelSpan("S2", 6, 10, -0.01)
        ]
        report = self.engine.compute_sample_weights(spans, WeightingMethod.UNIQUENESS_ONLY)
        self.assertEqual(report.average_dataset_uniqueness, 1.0)
        self.assertEqual(report.sample_results[0].average_uniqueness, 1.0)
        self.assertEqual(report.sample_results[1].average_uniqueness, 1.0)
        self.assertAlmostEqual(report.sample_results[0].normalized_weight, 1.0, places=2)

    def test_overlapping_spans_reduce_uniqueness(self):
        # Spans overlap completely: [1, 10] and [1, 10] (concurrency = 2 -> uniqueness = 0.5)
        spans = [
            LabelSpan("S1", 1, 10, 0.05),
            LabelSpan("S2", 1, 10, 0.03)
        ]
        report = self.engine.compute_sample_weights(spans, WeightingMethod.UNIQUENESS_ONLY)
        self.assertEqual(report.average_dataset_uniqueness, 0.5)
        self.assertEqual(report.sample_results[0].average_uniqueness, 0.5)
        self.assertEqual(report.sample_results[1].average_uniqueness, 0.5)

    def test_return_attributed_weighting(self):
        # Sample with higher return gets higher return-attributed weight
        spans = [
            LabelSpan("S1", 1, 5, 0.10),   # 10% return
            LabelSpan("S2", 1, 5, 0.02)    # 2% return
        ]
        report = self.engine.compute_sample_weights(spans, WeightingMethod.RETURN_ATTRIBUTED)
        w1 = report.sample_results[0].normalized_weight
        w2 = report.sample_results[1].normalized_weight
        self.assertGreater(w1, w2)
        self.assertAlmostEqual(w1 + w2, 2.0, places=2)

    def test_empty_spans_raises_error(self):
        with self.assertRaises(ValueError):
            self.engine.compute_sample_weights([])

if __name__ == '__main__':
    unittest.main()
