import json
import unittest

from risk_dependency_mapper import (
    Criticality,
    DependencyEdge,
    FailureResponse,
    GraphValidationError,
    ImpactMode,
    IssueSeverity,
    NodeKind,
    RiskDependencyMapper,
    RiskNode,
)


def node(
    node_id,
    kind,
    criticality=Criticality.HIGH,
    owner="risk-platform",
    scopes=frozenset({"production", "global"}),
    stale_after_seconds=None,
):
    return RiskNode(
        node_id=node_id,
        kind=kind,
        criticality=criticality,
        owner=owner,
        description=f"{node_id} description",
        scopes=scopes,
        stale_after_seconds=stale_after_seconds,
        recovery_objective_seconds=30,
    )


def edge(
    dependency_id,
    consumer_id,
    response=FailureResponse.FAIL_CLOSED,
    redundancy_group=None,
    monitored=True,
):
    return DependencyEdge(
        dependency_id=dependency_id,
        consumer_id=consumer_id,
        response=response,
        rationale=f"{consumer_id} requires {dependency_id}",
        redundancy_group=redundancy_group,
        monitored=monitored,
    )


class RiskDependencyMapperTests(unittest.TestCase):
    def setUp(self):
        self.nodes = [
            node("market-data-a", NodeKind.DATA_FEED, stale_after_seconds=2),
            node("market-data-b", NodeKind.DATA_FEED, stale_after_seconds=2),
            node("positions", NodeKind.STATE_STORE, owner="position-platform"),
            node("fx-rates", NodeKind.DATA_FEED, stale_after_seconds=5),
            node("risk-engine", NodeKind.SERVICE),
            node("order-limit", NodeKind.CONTROL, Criticality.CRITICAL),
            node("drawdown-limit", NodeKind.CONTROL, Criticality.HIGH),
            node("order-router", NodeKind.ACTUATOR, Criticality.CRITICAL, owner="execution"),
        ]
        self.edges = [
            edge("market-data-a", "risk-engine", redundancy_group="market-data"),
            edge("market-data-b", "risk-engine", redundancy_group="market-data"),
            edge("positions", "risk-engine"),
            edge("risk-engine", "order-limit"),
            edge("risk-engine", "drawdown-limit"),
            edge("fx-rates", "order-limit", FailureResponse.FAIL_OPEN),
            edge("order-limit", "order-router"),
        ]
        self.mapper = RiskDependencyMapper(self.nodes, self.edges)

    def impact(self, report, node_id):
        return next(impact for impact in report.impacts if impact.node_id == node_id)

    def test_single_redundant_feed_loss_only_degrades_consumers(self):
        report = self.mapper.analyze(["market-data-a"])
        self.assertEqual(self.impact(report, "risk-engine").mode, ImpactMode.DEGRADED)
        self.assertEqual(self.impact(report, "order-limit").mode, ImpactMode.DEGRADED)
        self.assertEqual(report.unsafe_controls, ())

    def test_complete_redundancy_group_loss_fails_closed(self):
        report = self.mapper.analyze(["market-data-a", "market-data-b"])
        self.assertEqual(self.impact(report, "risk-engine").mode, ImpactMode.FAIL_CLOSED)
        self.assertEqual(self.impact(report, "drawdown-limit").mode, ImpactMode.FAIL_CLOSED)
        self.assertIn("drawdown-limit", report.fail_closed_controls)

    def test_multi_hop_state_store_failure_reaches_actuator(self):
        report = self.mapper.analyze(["positions"])
        self.assertEqual(self.impact(report, "risk-engine").mode, ImpactMode.FAIL_CLOSED)
        self.assertEqual(self.impact(report, "order-limit").mode, ImpactMode.FAIL_CLOSED)
        self.assertEqual(self.impact(report, "order-router").mode, ImpactMode.FAIL_CLOSED)
        self.assertEqual(self.impact(report, "risk-engine").triggered_by, ("positions",))
        self.assertEqual(self.impact(report, "order-router").triggered_by, ("positions",))

    def test_fail_open_dependency_is_explicitly_unsafe(self):
        report = self.mapper.analyze(["fx-rates"])
        self.assertEqual(self.impact(report, "order-limit").mode, ImpactMode.FAIL_OPEN)
        self.assertEqual(report.unsafe_controls, ("order-limit",))

    def test_simultaneous_failures_are_deduplicated_and_sorted(self):
        report = self.mapper.analyze(["positions", "fx-rates", "positions"])
        self.assertEqual(report.failed_nodes, ("fx-rates", "positions"))
        self.assertEqual(report.maximum_criticality, Criticality.CRITICAL)
        self.assertEqual(report.responsible_owners, tuple(sorted(report.responsible_owners)))
        self.assertIn("production", report.affected_scopes)

    def test_report_is_json_compatible_and_deterministic(self):
        first = self.mapper.analyze(["positions"])
        second = self.mapper.analyze(["positions"])
        self.assertEqual(first.to_json(), second.to_json())
        decoded = json.loads(first.to_json())
        self.assertEqual(decoded["failed_nodes"], ["positions"])
        self.assertEqual(decoded["maximum_criticality"], "CRITICAL")

    def test_single_points_exclude_survived_redundancy(self):
        points = {point.node_id: point for point in self.mapper.single_points_of_failure()}
        self.assertNotIn("market-data-a", points)
        self.assertNotIn("market-data-b", points)
        self.assertIn("positions", points)
        self.assertIn("fx-rates", points)
        self.assertEqual(points["fx-rates"].unsafe_controls, ("order-limit",))

    def test_degraded_alternative_still_counts_as_available(self):
        """A group member that only degraded has not failed over, so the group survives."""

        nodes = [
            node("feed-a1", NodeKind.DATA_FEED, stale_after_seconds=1),
            node("feed-a2", NodeKind.DATA_FEED, stale_after_seconds=1),
            node("feed-b1", NodeKind.DATA_FEED, stale_after_seconds=1),
            node("feed-b2", NodeKind.DATA_FEED, stale_after_seconds=1),
            node("normalizer-a", NodeKind.SERVICE),
            node("normalizer-b", NodeKind.SERVICE),
            node("exposure-limit", NodeKind.CONTROL, Criticality.CRITICAL),
        ]
        edges = [
            edge("feed-a1", "normalizer-a", redundancy_group="vendor-a"),
            edge("feed-a2", "normalizer-a", redundancy_group="vendor-a"),
            edge("feed-b1", "normalizer-b", redundancy_group="vendor-b"),
            edge("feed-b2", "normalizer-b", redundancy_group="vendor-b"),
            edge("normalizer-a", "exposure-limit", redundancy_group="normalizers"),
            edge("normalizer-b", "exposure-limit", redundancy_group="normalizers"),
        ]
        graph = RiskDependencyMapper(nodes, edges)

        both_normalizers_degraded = graph.analyze(["feed-a1", "feed-b1"])
        self.assertEqual(
            self.impact(both_normalizers_degraded, "normalizer-a").mode,
            ImpactMode.DEGRADED,
        )
        self.assertEqual(
            self.impact(both_normalizers_degraded, "normalizer-b").mode,
            ImpactMode.DEGRADED,
        )
        self.assertEqual(
            self.impact(both_normalizers_degraded, "exposure-limit").mode,
            ImpactMode.DEGRADED,
        )
        self.assertEqual(both_normalizers_degraded.fail_closed_controls, ())

        one_lost_one_degraded = graph.analyze(["feed-a1", "feed-a2", "feed-b1"])
        self.assertEqual(
            self.impact(one_lost_one_degraded, "normalizer-a").mode,
            ImpactMode.FAIL_CLOSED,
        )
        self.assertEqual(
            self.impact(one_lost_one_degraded, "exposure-limit").mode,
            ImpactMode.DEGRADED,
        )

    def test_group_response_applies_when_every_alternative_is_lost(self):
        report = self.mapper.analyze(["market-data-a", "market-data-b", "positions"])
        self.assertEqual(self.impact(report, "risk-engine").mode, ImpactMode.FAIL_CLOSED)
        self.assertEqual(
            self.impact(report, "risk-engine").triggered_by,
            ("market-data-a", "market-data-b", "positions"),
        )

    def test_control_feeding_another_control_is_reported_as_single_point(self):
        graph = RiskDependencyMapper(
            [
                node("positions", NodeKind.STATE_STORE),
                node("pretrade-aggregator", NodeKind.CONTROL, Criticality.HIGH),
                node("order-limit", NodeKind.CONTROL, Criticality.CRITICAL),
            ],
            [
                edge("positions", "pretrade-aggregator"),
                edge("pretrade-aggregator", "order-limit"),
            ],
        )
        points = {point.node_id: point for point in graph.single_points_of_failure()}
        self.assertIn("pretrade-aggregator", points)
        self.assertEqual(
            points["pretrade-aggregator"].affected_high_criticality_controls,
            ("order-limit",),
        )
        self.assertNotIn("order-limit", points)

    def test_single_point_analysis_ignores_the_failed_node_itself(self):
        points = {point.node_id for point in self.mapper.single_points_of_failure()}
        self.assertNotIn("order-limit", points)
        self.assertNotIn("drawdown-limit", points)
        self.assertIn("risk-engine", points)

    def test_analyze_rejects_a_bare_string(self):
        with self.assertRaises(GraphValidationError):
            self.mapper.analyze("positions")

    def test_validation_reports_fail_open_without_rejecting_analysis(self):
        issues = self.mapper.validate()
        fail_open = [issue for issue in issues if issue.code == "FAIL_OPEN_DEPENDENCY"]
        self.assertEqual(len(fail_open), 1)
        self.assertEqual(fail_open[0].severity, IssueSeverity.ERROR)
        self.assertEqual(fail_open[0].node_ids, ("fx-rates", "order-limit"))

    def test_validation_detects_cycle_orphan_missing_staleness_and_monitoring(self):
        graph = RiskDependencyMapper(
            [
                node("feed", NodeKind.DATA_FEED, stale_after_seconds=None),
                node("service", NodeKind.SERVICE),
                node("control", NodeKind.CONTROL),
                node("orphan", NodeKind.EXTERNAL),
            ],
            [
                edge("feed", "service", monitored=False),
                edge("service", "control"),
                edge("control", "service"),
            ],
        )
        codes = {issue.code for issue in graph.validate()}
        self.assertTrue(
            {
                "DEPENDENCY_CYCLE",
                "ORPHAN_NODE",
                "FEED_WITHOUT_STALENESS_BOUND",
                "UNMONITORED_DEPENDENCY",
            }.issubset(codes)
        )

    def test_validation_detects_bad_redundancy_group_and_control_without_input(self):
        graph = RiskDependencyMapper(
            [
                node("feed", NodeKind.DATA_FEED, stale_after_seconds=1),
                node("service", NodeKind.SERVICE),
                node("control", NodeKind.CONTROL),
                node("empty-control", NodeKind.CONTROL),
            ],
            [edge("feed", "service", redundancy_group="only-one"), edge("service", "control")],
        )
        codes = {issue.code for issue in graph.validate()}
        self.assertIn("INVALID_REDUNDANCY_GROUP", codes)
        self.assertIn("CONTROL_WITHOUT_DEPENDENCIES", codes)

    def test_structural_errors_fail_fast(self):
        feed = node("feed", NodeKind.DATA_FEED, stale_after_seconds=1)
        with self.assertRaises(GraphValidationError):
            RiskDependencyMapper([feed, feed], [])
        with self.assertRaises(GraphValidationError):
            RiskDependencyMapper([feed], [edge("missing", "feed")])
        with self.assertRaises(GraphValidationError):
            RiskDependencyMapper([feed], [edge("feed", "feed")])
        with self.assertRaises(GraphValidationError):
            RiskDependencyMapper([feed], [edge("feed", "missing")])

    def test_duplicate_edge_fails_fast(self):
        nodes = [
            node("feed", NodeKind.DATA_FEED, stale_after_seconds=1),
            node("control", NodeKind.CONTROL),
        ]
        dependency = edge("feed", "control")
        with self.assertRaises(GraphValidationError):
            RiskDependencyMapper(nodes, [dependency, dependency])

    def test_invalid_analysis_input_fails_fast(self):
        with self.assertRaises(GraphValidationError):
            self.mapper.analyze([])
        with self.assertRaises(GraphValidationError):
            self.mapper.analyze(["unknown"])

    def test_node_numeric_bounds_are_validated(self):
        with self.assertRaises(GraphValidationError):
            RiskNode(
                "feed",
                NodeKind.DATA_FEED,
                Criticality.HIGH,
                "owner",
                "description",
                stale_after_seconds=0,
            )
        with self.assertRaises(GraphValidationError):
            RiskNode(
                "feed",
                "DATA_FEED",
                Criticality.HIGH,
                "owner",
                "description",
            )
        with self.assertRaises(GraphValidationError):
            RiskNode(
                "feed",
                NodeKind.DATA_FEED,
                Criticality.HIGH,
                "owner",
                "description",
                stale_after_seconds=float("nan"),
            )
        with self.assertRaises(GraphValidationError):
            RiskNode(
                "feed",
                NodeKind.DATA_FEED,
                Criticality.HIGH,
                "owner",
                "description",
                recovery_objective_seconds=True,
            )

    def test_dot_export_escapes_identifiers_and_marks_impacts(self):
        unusual = node('feed"primary', NodeKind.DATA_FEED, stale_after_seconds=1)
        control = node("control", NodeKind.CONTROL)
        graph = RiskDependencyMapper([unusual, control], [edge(unusual.node_id, "control")])
        rendered = graph.to_dot(graph.analyze([unusual.node_id]))
        self.assertIn('"feed\\"primary"', rendered)
        self.assertIn('fillcolor="black"', rendered)
        self.assertIn('fillcolor="orange"', rendered)

    def test_cycle_validation_handles_deep_graph_without_recursion(self):
        nodes = [node(f"service-{index}", NodeKind.SERVICE) for index in range(1_100)]
        edges = [
            edge(f"service-{index}", f"service-{index + 1}")
            for index in range(1_099)
        ]
        graph = RiskDependencyMapper(nodes, edges)
        self.assertNotIn("DEPENDENCY_CYCLE", {issue.code for issue in graph.validate()})


if __name__ == "__main__":
    unittest.main()
