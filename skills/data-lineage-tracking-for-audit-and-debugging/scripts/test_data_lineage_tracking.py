import hashlib
import unittest

from data_lineage_tracking import (
    DataLineageTrackerEngine,
    LineageNode,
    DataLineageAuditReport,
    VALID_NODE_TYPES,
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

    # ------------------------------------------------------------------
    # Core traversals
    # ------------------------------------------------------------------
    def test_upstream_root_cause_analysis(self):
        report = self.engine.trace_upstream_root_cause("MODEL_ALPHA")

        self.assertEqual(report.target_node_id, "MODEL_ALPHA")
        self.assertEqual(report.traversal_direction, "UPSTREAM_ROOT_CAUSE")
        self.assertEqual(report.total_nodes_traversed, 4)
        self.assertIn("SRC_RAW_BLOOMBERG", report.root_cause_sources)
        self.assertEqual(report.orphan_root_nodes, [])
        self.assertTrue(report.is_dag_valid)

    def test_downstream_impact_analysis(self):
        report = self.engine.trace_downstream_impact("SRC_RAW_BLOOMBERG")

        self.assertEqual(report.target_node_id, "SRC_RAW_BLOOMBERG")
        self.assertEqual(report.traversal_direction, "DOWNSTREAM_IMPACT")
        self.assertEqual(report.total_nodes_traversed, 4)
        self.assertIn("MODEL_ALPHA", report.impacted_downstream_models)
        self.assertTrue(report.is_dag_valid)

    def test_traversal_order_is_deterministic_bfs(self):
        """Audit artifacts must be reproducible: visit order, not set order."""
        expected = ["MODEL_ALPHA", "FEAT_MOMENTUM", "TR_VWAP", "SRC_RAW_BLOOMBERG"]
        self.assertEqual(self.engine.trace_upstream_root_cause("MODEL_ALPHA").traversed_node_ids, expected)
        self.assertEqual(
            self.engine.trace_downstream_impact("SRC_RAW_BLOOMBERG").traversed_node_ids,
            list(reversed(expected)),
        )

    def test_traversal_stops_at_branch_that_does_not_reach_target(self):
        """A sibling branch off the same source must not appear in an unrelated upstream trace."""
        self.engine.register_node("FEAT_UNRELATED", "FEATURE_STORE", "v1.0", "other", "2026-03-01T10:02:00Z")
        self.engine.add_dependency("TR_VWAP", "FEAT_UNRELATED", "OTHER_MATERIALIZATION")

        upstream = self.engine.trace_upstream_root_cause("MODEL_ALPHA")
        self.assertNotIn("FEAT_UNRELATED", upstream.traversed_node_ids)

        downstream = self.engine.trace_downstream_impact("SRC_RAW_BLOOMBERG")
        self.assertIn("FEAT_UNRELATED", downstream.traversed_node_ids)
        self.assertEqual(downstream.impacted_downstream_models, ["MODEL_ALPHA"])

    def test_diamond_dependency_counts_each_node_once(self):
        """Two paths into the same model must not double-count or loop."""
        self.engine.register_node("SRC_REUTERS", "DATA_SOURCE", "v1.0", "reuters_ticks", "2026-03-01T10:00:00Z")
        self.engine.register_node("FEAT_SPREAD", "FEATURE_STORE", "v1.0", "spread_feat", "2026-03-01T10:02:00Z")
        self.engine.add_dependency("SRC_REUTERS", "FEAT_SPREAD", "SPREAD_CALC")
        self.engine.add_dependency("FEAT_SPREAD", "MODEL_ALPHA", "MODEL_SCORE_INFERENCE")

        report = self.engine.trace_upstream_root_cause("MODEL_ALPHA")
        self.assertEqual(report.total_nodes_traversed, len(set(report.traversed_node_ids)))
        self.assertEqual(report.total_nodes_traversed, 6)
        self.assertCountEqual(report.root_cause_sources, ["SRC_RAW_BLOOMBERG", "SRC_REUTERS"])

    def test_order_decision_counted_as_impacted(self):
        self.engine.register_node("ORD_001", "ORDER_DECISION", "v1.0", "buy_100_aapl", "2026-03-01T10:04:00Z")
        self.engine.add_dependency("MODEL_ALPHA", "ORD_001", "ORDER_ROUTING")

        report = self.engine.trace_downstream_impact("SRC_RAW_BLOOMBERG")
        self.assertEqual(report.impacted_downstream_models, ["MODEL_ALPHA", "ORD_001"])

    # ------------------------------------------------------------------
    # DAG integrity (regression: is_dag_valid was hard-coded True)
    # ------------------------------------------------------------------
    def test_cycle_creating_dependency_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.add_dependency("MODEL_ALPHA", "SRC_RAW_BLOOMBERG", "FEEDBACK_LOOP")
        self.assertIn("cycle", str(ctx.exception).lower())
        # The rejected edge must not have been half-applied.
        self.assertEqual(self.engine.child_to_parents["SRC_RAW_BLOOMBERG"], [])

    def test_self_dependency_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.add_dependency("TR_VWAP", "TR_VWAP", "SELF")

    def test_is_dag_valid_is_measured_not_asserted(self):
        """If a cycle is forced into the edge maps directly, the report must say so."""
        from data_lineage_tracking import LineageEdge
        forced = LineageEdge("MODEL_ALPHA", "TR_VWAP", "FORCED_FEEDBACK")
        self.engine.parent_to_children["MODEL_ALPHA"].append(forced)
        self.engine.child_to_parents["TR_VWAP"].append(forced)

        self.assertFalse(self.engine.trace_upstream_root_cause("MODEL_ALPHA").is_dag_valid)
        self.assertFalse(self.engine.trace_downstream_impact("SRC_RAW_BLOOMBERG").is_dag_valid)

    def test_edge_to_unregistered_node_fails_diagnosably(self):
        """A truncated rehydration must not surface as a bare KeyError mid-incident."""
        from data_lineage_tracking import LineageEdge
        self.engine.parent_to_children["MODEL_ALPHA"].append(LineageEdge("MODEL_ALPHA", "GHOST", "T"))
        with self.assertRaises(ValueError) as ctx:
            self.engine.trace_downstream_impact("SRC_RAW_BLOOMBERG")
        self.assertIn("GHOST", str(ctx.exception))

    def test_duplicate_edge_is_idempotent_and_conflicting_edge_raises(self):
        before = len(self.engine.parent_to_children["SRC_RAW_BLOOMBERG"])
        self.engine.add_dependency("SRC_RAW_BLOOMBERG", "TR_VWAP", "VWAP_AGGREGATION")
        self.assertEqual(len(self.engine.parent_to_children["SRC_RAW_BLOOMBERG"]), before)
        self.assertEqual(len(self.engine.child_to_parents["TR_VWAP"]), 1)

        with self.assertRaises(ValueError):
            self.engine.add_dependency("SRC_RAW_BLOOMBERG", "TR_VWAP", "DIFFERENT_TRANSFORM")

    def test_dependency_on_unregistered_node_raises(self):
        with self.assertRaises(ValueError):
            self.engine.add_dependency("SRC_RAW_BLOOMBERG", "GHOST_NODE", "T")

    def test_traversal_from_unknown_node_raises(self):
        with self.assertRaises(ValueError):
            self.engine.trace_upstream_root_cause("NO_SUCH_NODE")
        with self.assertRaises(ValueError):
            self.engine.trace_downstream_impact("NO_SUCH_NODE")

    # ------------------------------------------------------------------
    # Append-only node registration
    # ------------------------------------------------------------------
    def test_conflicting_reregistration_is_rejected(self):
        """Silent overwrite would destroy the history the graph exists to prove."""
        original_hash = self.engine.nodes["FEAT_MOMENTUM"].data_hash_sha256
        with self.assertRaises(ValueError) as ctx:
            self.engine.register_node(
                "FEAT_MOMENTUM", "FEATURE_STORE", "v2.1", "BACKFILLED_PAYLOAD",
                "2026-03-01T11:00:00Z", "5-min Momentum Feature",
            )
        self.assertIn("append-only", str(ctx.exception))
        self.assertEqual(self.engine.nodes["FEAT_MOMENTUM"].data_hash_sha256, original_hash)

    def test_identical_reregistration_is_idempotent(self):
        node = self.engine.register_node(
            "FEAT_MOMENTUM", "FEATURE_STORE", "v2.0", "feat_payload_x89",
            "2026-03-01T10:02:00Z", "5-min Momentum Feature",
        )
        self.assertEqual(node, self.engine.nodes["FEAT_MOMENTUM"])
        self.assertEqual(len(self.engine.nodes), 4)

    def test_revised_artifact_is_recorded_as_a_new_versioned_node(self):
        """The documented workflow for a backfill: new node id linked to its predecessor."""
        self.engine.register_node("FEAT_MOMENTUM@v2", "FEATURE_STORE", "v2.1", "backfilled_payload", "2026-03-01T11:00:00Z")
        self.engine.add_dependency("FEAT_MOMENTUM", "FEAT_MOMENTUM@v2", "BACKFILL_REVISION")

        self.assertNotEqual(
            self.engine.nodes["FEAT_MOMENTUM"].data_hash_sha256,
            self.engine.nodes["FEAT_MOMENTUM@v2"].data_hash_sha256,
        )
        report = self.engine.trace_upstream_root_cause("FEAT_MOMENTUM@v2")
        self.assertIn("SRC_RAW_BLOOMBERG", report.root_cause_sources)

    # ------------------------------------------------------------------
    # Node validation
    # ------------------------------------------------------------------
    def test_unknown_node_type_is_rejected(self):
        """A typo'd type would silently return an empty impacted-models list."""
        with self.assertRaises(ValueError) as ctx:
            self.engine.register_node("MODEL_TYPO", "MODEL", "v1.0", "p", "2026-03-01T10:00:00Z")
        self.assertIn("MODEL_INFERENCE", str(ctx.exception))
        self.assertNotIn("MODEL_TYPO", self.engine.nodes)

    def test_node_type_is_case_and_whitespace_normalised(self):
        node = self.engine.register_node("SRC_LOWER", " data_source ", "v1.0", "p", "2026-03-01T10:00:00Z")
        self.assertEqual(node.node_type, "DATA_SOURCE")
        self.assertIn(node.node_type, VALID_NODE_TYPES)

    def test_blank_identifiers_are_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.register_node("", "DATA_SOURCE", "v1.0", "p", "2026-03-01T10:00:00Z")
        with self.assertRaises(ValueError):
            self.engine.register_node("SRC_X", "DATA_SOURCE", "   ", "p", "2026-03-01T10:00:00Z")
        with self.assertRaises(ValueError):
            self.engine.add_dependency("SRC_RAW_BLOOMBERG", "TR_VWAP", "")

    # ------------------------------------------------------------------
    # Fingerprinting
    # ------------------------------------------------------------------
    def test_sha256_fingerprint_matches_independent_computation(self):
        expected = hashlib.sha256(b"raw_tick_payload_001").hexdigest()
        self.assertEqual(self.engine.nodes["SRC_RAW_BLOOMBERG"].data_hash_sha256, expected)
        # Known-answer check against the published SHA-256 of the empty string.
        empty = self.engine.register_node("SRC_EMPTY", "DATA_SOURCE", "v1.0", "", "2026-03-01T10:00:00Z")
        self.assertEqual(
            empty.data_hash_sha256,
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )

    def test_binary_payload_is_fingerprinted_without_decode(self):
        payload = b"\x00\x01\xff\xfeparquet-block"
        node = self.engine.register_node("SRC_PARQUET", "DATA_SOURCE", "v1.0", payload, "2026-03-01T10:00:00Z")
        self.assertEqual(node.data_hash_sha256, hashlib.sha256(payload).hexdigest())

    def test_non_str_bytes_payload_is_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.register_node("SRC_BAD", "DATA_SOURCE", "v1.0", {"price": 1.0}, "2026-03-01T10:00:00Z")

    def test_report_carries_fingerprint_trace_for_traversed_nodes(self):
        report = self.engine.trace_upstream_root_cause("MODEL_ALPHA")
        self.assertEqual(set(report.node_fingerprints), set(report.traversed_node_ids))
        self.assertEqual(
            report.node_fingerprints["SRC_RAW_BLOOMBERG"],
            hashlib.sha256(b"raw_tick_payload_001").hexdigest(),
        )

    # ------------------------------------------------------------------
    # Timestamps
    # ------------------------------------------------------------------
    def test_timestamp_is_canonicalised_to_utc(self):
        node = self.engine.register_node("SRC_IST", "DATA_SOURCE", "v1.0", "p", "2026-03-01T15:30:00+05:30")
        self.assertEqual(node.timestamp_utc, "2026-03-01T10:00:00+00:00")
        self.assertEqual(self.engine.nodes["SRC_RAW_BLOOMBERG"].timestamp_utc, "2026-03-01T10:00:00+00:00")

    def test_naive_or_unparseable_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.register_node("SRC_NAIVE", "DATA_SOURCE", "v1.0", "p", "2026-03-01T10:00:00")
        with self.assertRaises(ValueError):
            self.engine.register_node("SRC_JUNK", "DATA_SOURCE", "v1.0", "p", "not-a-timestamp")

    # ------------------------------------------------------------------
    # Broken lineage detection
    # ------------------------------------------------------------------
    def test_orphan_root_is_reported_when_lineage_is_dangling(self):
        """An inference never linked to a DATA_SOURCE must not audit as clean."""
        engine = DataLineageTrackerEngine()
        engine.register_node("FEAT_ORPHAN", "FEATURE_STORE", "v1.0", "p", "2026-03-01T10:00:00Z")
        engine.register_node("MODEL_B", "MODEL_INFERENCE", "v1.0", "p", "2026-03-01T10:01:00Z")
        engine.add_dependency("FEAT_ORPHAN", "MODEL_B", "INFERENCE")

        with self.assertLogs("data_lineage_tracking", level="WARNING") as logs:
            report = engine.trace_upstream_root_cause("MODEL_B")
        self.assertEqual(report.root_cause_sources, [])
        self.assertEqual(report.orphan_root_nodes, ["FEAT_ORPHAN"])
        self.assertIn("FEAT_ORPHAN", "".join(logs.output))

    def test_isolated_node_traversal_returns_only_itself(self):
        engine = DataLineageTrackerEngine()
        engine.register_node("SRC_LONE", "DATA_SOURCE", "v1.0", "p", "2026-03-01T10:00:00Z")

        upstream = engine.trace_upstream_root_cause("SRC_LONE")
        self.assertEqual(upstream.total_nodes_traversed, 1)
        self.assertEqual(upstream.root_cause_sources, ["SRC_LONE"])
        self.assertEqual(upstream.orphan_root_nodes, [])

        downstream = engine.trace_downstream_impact("SRC_LONE")
        self.assertEqual(downstream.impacted_downstream_models, [])

    def test_deep_chain_traversal_does_not_recurse(self):
        """Standards require unlimited traversal depth: 5000 levels must not blow the stack."""
        engine = DataLineageTrackerEngine()
        engine.register_node("SRC_0", "DATA_SOURCE", "v1.0", "p0", "2026-03-01T10:00:00Z")
        for i in range(1, 5000):
            engine.register_node(f"TR_{i}", "TRANSFORMATION", "v1.0", f"p{i}", "2026-03-01T10:00:00Z")
            parent = "SRC_0" if i == 1 else f"TR_{i - 1}"
            engine.add_dependency(parent, f"TR_{i}", "STEP")
        engine.register_node("MODEL_DEEP", "MODEL_INFERENCE", "v1.0", "pm", "2026-03-01T10:00:00Z")
        engine.add_dependency("TR_4999", "MODEL_DEEP", "INFERENCE")

        report = engine.trace_upstream_root_cause("MODEL_DEEP")
        self.assertEqual(report.total_nodes_traversed, 5001)
        self.assertEqual(report.root_cause_sources, ["SRC_0"])


if __name__ == '__main__':
    unittest.main()
