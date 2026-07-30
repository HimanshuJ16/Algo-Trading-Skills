import unittest
from data_lineage_tracking import (
    DataLineageTrackerEngine, LineageNode, DataLineageAuditReport
)

class TestDataLineageTrackerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = DataLineageTrackerEngine()

        # Build a 4-level DAG:
        # SRC_RAW_BLOOMBERG (DATA_SOURCE) -> TR_VWAP (TRANSFORMATION) -> FEAT_MOMENTUM (FEATURE_STORE) -> MODEL_ALPHA (MODEL_INFERENCE)
        self.engine.register_node("SRC_RAW_BLOOMBERG", "DATA_SOURCE", "v1.0", "raw_tick_payload_001", "2026-03-01T10:00:00Z", "Bloomberg raw ticks")
        self.engine.register_node("TR_VWAP", "TRANSFORMATION", "v1.2", "vwap_code_v1.2", "2026-03-01T10:01:00Z", "VWAP calculator")
        self.engine.register_node("FEAT_MOMENTUM", "FEATURE_STORE", "v2.0", "feat_payload_x89", "2026-03-01T10:02:00Z", "5-min Momentum Feature")
        self.engine.register_node("MODEL_ALPHA", "MODEL_INFERENCE", "v3.1", "inference_output_buy", "2026-03-01T10:03:00Z", "Alpha model buy directive")

        self.engine.add_dependency("SRC_RAW_BLOOMBERG", "TR_VWAP", "VWAP_AGGREGATION")
        self.engine.add_dependency("TR_VWAP", "FEAT_MOMENTUM", "FEATURE_MATERIALIZATION")
        self.engine.add_dependency("FEAT_MOMENTUM", "MODEL_ALPHA", "MODEL_SCORE_INFERENCE")

    def test_upstream_root_cause_analysis(self):
        report = self.engine.trace_upstream_root_cause("MODEL_ALPHA")
        
        self.assertEqual(report.target_node_id, "MODEL_ALPHA")
        self.assertEqual(report.total_nodes_traversed, 4)
        self.assertIn("SRC_RAW_BLOOMBERG", report.root_cause_sources)

    def test_downstream_impact_analysis(self):
        report = self.engine.trace_downstream_impact("SRC_RAW_BLOOMBERG")
        
        self.assertEqual(report.target_node_id, "SRC_RAW_BLOOMBERG")
        self.assertEqual(report.total_nodes_traversed, 4)
        self.assertIn("MODEL_ALPHA", report.impacted_downstream_models)

if __name__ == '__main__':
    unittest.main()
