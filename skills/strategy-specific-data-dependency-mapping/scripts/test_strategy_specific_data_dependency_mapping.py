"""Behavioural tests for the strategy data dependency mapping engine.

Every scenario uses a fixed evaluation epoch so results are deterministic, and every expected
readiness score is derived by hand from the documented weights (CRITICAL 4, HIGH 3, MEDIUM 2,
LOW 1), the fallback credit (0.8), and the degraded credit (0.5) rather than by re-running the
engine's own arithmetic.
"""

import unittest
from dataclasses import FrozenInstanceError

from strategy_specific_data_dependency_mapping import (
    DataDependencyNode,
    DataDependencyPortfolio,
    DependencyCriticality,
    DependencyValidationError,
    FailureResponse,
    FaultCode,
    FeedObservation,
    FeedState,
    ObservationValidationError,
    ReadinessPolicy,
    StrategyDataDependencyEngine,
)

NOW = 1_700_000_000.0


def node(feed_id, criticality, vendors, sla=60.0, **kwargs):
    """Build a dependency node with a stable name so tests stay readable."""
    return DataDependencyNode(
        feed_id=feed_id,
        feed_name=f"{feed_id} feed",
        criticality=criticality,
        vendors=vendors,
        max_acceptable_lag_seconds=sla,
        **kwargs,
    )


def fresh(feed_id, vendor, age=5.0, **kwargs):
    """Observation published ``age`` seconds before NOW."""
    return FeedObservation(
        feed_id=feed_id, vendor_id=vendor, last_updated_epoch=NOW - age, is_healthy=True, **kwargs
    )


class TestHealthyPath(unittest.TestCase):
    def setUp(self):
        self.engine = StrategyDataDependencyEngine(
            "QUANT_ALPHA_01",
            [
                node("L2_BOOK", DependencyCriticality.CRITICAL, ("Refinitiv", "Bloomberg")),
                node("SENTIMENT", DependencyCriticality.HIGH, ("Dataminr", "RavenPack"), sla=300.0),
                node("FUNDAMENTALS", DependencyCriticality.MEDIUM, ("FactSet",), sla=900.0),
                node("NEWS_TAGS", DependencyCriticality.LOW, ("Benzinga",), sla=900.0),
            ],
        )

    def test_all_primaries_healthy_scores_one_hundred(self):
        report = self.engine.evaluate_strategy_readiness(
            NOW,
            [
                fresh("L2_BOOK", "Refinitiv"),
                fresh("SENTIMENT", "Dataminr", age=50.0),
                fresh("FUNDAMENTALS", "FactSet", age=100.0),
                fresh("NEWS_TAGS", "Benzinga", age=100.0),
            ],
        )
        self.assertEqual(report.readiness_score_pct, 100.0)
        self.assertTrue(report.is_strategy_ready_to_trade)
        self.assertEqual(report.blocked_dependencies, ())
        self.assertEqual(report.fallback_dependencies, ())
        self.assertEqual(report.degraded_dependencies, ())
        self.assertEqual(report.warnings, ())
        self.assertEqual(dict(report.active_feed_sources)["L2_BOOK"], "Refinitiv")
        self.assertTrue(
            all(a.state is FeedState.PRIMARY_ACTIVE for a in report.assessments)
        )

    def test_evaluation_is_deterministic(self):
        observations = [
            fresh("L2_BOOK", "Refinitiv"),
            fresh("SENTIMENT", "Dataminr", age=50.0),
            fresh("FUNDAMENTALS", "FactSet", age=100.0),
            fresh("NEWS_TAGS", "Benzinga", age=100.0),
        ]
        first = self.engine.evaluate_strategy_readiness(NOW, observations)
        second = self.engine.evaluate_strategy_readiness(NOW, list(reversed(observations)))
        self.assertEqual(first.readiness_score_pct, second.readiness_score_pct)
        self.assertEqual(first.assessments, second.assessments)
        self.assertEqual(first.blocked_dependencies, second.blocked_dependencies)

    def test_fallback_credit_applies_only_to_the_pivoted_feed(self):
        # L2_BOOK (weight 4) serves from Bloomberg at 0.8 credit; the rest score in full.
        # (4 * 0.8 + 3 + 2 + 1) / 10 = 9.2 / 10 = 92.0
        report = self.engine.evaluate_strategy_readiness(
            NOW,
            [
                FeedObservation("L2_BOOK", "Refinitiv", NOW - 600.0, True),
                fresh("L2_BOOK", "Bloomberg"),
                fresh("SENTIMENT", "Dataminr", age=50.0),
                fresh("FUNDAMENTALS", "FactSet", age=100.0),
                fresh("NEWS_TAGS", "Benzinga", age=100.0),
            ],
        )
        self.assertEqual(report.readiness_score_pct, 92.0)
        self.assertTrue(report.is_strategy_ready_to_trade)
        self.assertEqual(report.fallback_dependencies, ("L2_BOOK",))
        self.assertEqual(dict(report.active_feed_sources)["L2_BOOK"], "Bloomberg")

    def test_readiness_gate_rejects_a_score_below_the_policy_minimum(self):
        # L2_BOOK on fallback (4 * 0.8 = 3.2); SENTIMENT/FUNDAMENTALS/NEWS_TAGS unobserved and
        # therefore degraded (3 * 0.5 + 2 * 0.5 + 1 * 0.5 = 3.0).  6.2 / 10 = 62.0 < 70.
        report = self.engine.evaluate_strategy_readiness(
            NOW,
            [
                FeedObservation("L2_BOOK", "Refinitiv", NOW - 600.0, True),
                fresh("L2_BOOK", "Bloomberg"),
            ],
        )
        self.assertEqual(report.readiness_score_pct, 62.0)
        self.assertFalse(report.is_strategy_ready_to_trade)
        self.assertEqual(report.blocked_dependencies, ())
        self.assertEqual(
            report.unobserved_dependencies, ("SENTIMENT", "FUNDAMENTALS", "NEWS_TAGS")
        )


class TestFallbackRequiresEvidence(unittest.TestCase):
    """Regression cover for the two failure modes in the pre-2.0 fallback logic."""

    def setUp(self):
        self.engine = StrategyDataDependencyEngine(
            "QUANT_ALPHA_02",
            [node("L2_BOOK", DependencyCriticality.CRITICAL, ("Refinitiv", "Bloomberg"))],
        )

    def test_stale_primary_without_a_secondary_observation_blocks_trading(self):
        # The earlier engine credited the secondary vendor on the strength of the primary's
        # failure alone and reported the strategy ready.  Nothing here observed Bloomberg.
        report = self.engine.evaluate_strategy_readiness(
            NOW, [FeedObservation("L2_BOOK", "Refinitiv", NOW - 600.0, True)]
        )
        self.assertFalse(report.is_strategy_ready_to_trade)
        self.assertEqual(report.blocked_dependencies, ("L2_BOOK",))
        self.assertEqual(report.readiness_score_pct, 0.0)
        self.assertEqual(report.active_feed_sources, {})
        self.assertEqual(report.assessments[0].fault, FaultCode.STALE)

    def test_unhealthy_primary_with_a_dead_secondary_blocks_trading(self):
        report = self.engine.evaluate_strategy_readiness(
            NOW,
            [
                FeedObservation("L2_BOOK", "Refinitiv", NOW - 5.0, False),
                FeedObservation("L2_BOOK", "Bloomberg", NOW - 5.0, False),
            ],
        )
        self.assertFalse(report.is_strategy_ready_to_trade)
        self.assertEqual(report.blocked_dependencies, ("L2_BOOK",))
        self.assertEqual(report.assessments[0].state, FeedState.UNAVAILABLE)
        self.assertEqual(report.assessments[0].fault, FaultCode.UNHEALTHY)

    def test_healthy_feed_already_serving_from_the_secondary_is_not_blocked(self):
        # The earlier engine classified a fresh, healthy non-primary vendor as
        # "primary and secondary failed" and hard-blocked the strategy.
        report = self.engine.evaluate_strategy_readiness(NOW, [fresh("L2_BOOK", "Bloomberg")])
        self.assertEqual(report.blocked_dependencies, ())
        self.assertEqual(report.fallback_dependencies, ("L2_BOOK",))
        self.assertEqual(report.assessments[0].state, FeedState.FALLBACK_ACTIVE)
        self.assertEqual(report.assessments[0].active_vendor, "Bloomberg")
        self.assertEqual(report.readiness_score_pct, 80.0)

    def test_observation_from_a_vendor_outside_the_hierarchy_cannot_rescue_a_feed(self):
        report = self.engine.evaluate_strategy_readiness(
            NOW,
            [
                FeedObservation("L2_BOOK", "Refinitiv", NOW - 600.0, True),
                fresh("L2_BOOK", "SomeOtherVendor"),
            ],
        )
        self.assertEqual(report.blocked_dependencies, ("L2_BOOK",))
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("outside hierarchy", report.warnings[0])

    def test_observation_for_an_unmapped_feed_is_recorded_and_ignored(self):
        report = self.engine.evaluate_strategy_readiness(
            NOW, [fresh("L2_BOOK", "Refinitiv"), fresh("NOT_MAPPED", "Refinitiv")]
        )
        self.assertTrue(report.is_strategy_ready_to_trade)
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("unmapped feed", report.warnings[0])


class TestFreshnessClassification(unittest.TestCase):
    def setUp(self):
        self.engine = StrategyDataDependencyEngine(
            "QUANT_ALPHA_03",
            [node("PX", DependencyCriticality.CRITICAL, ("V1",), sla=60.0)],
        )

    def _fault(self, observation):
        report = self.engine.evaluate_strategy_readiness(NOW, [observation])
        return report.assessments[0].fault

    def test_lag_exactly_at_the_sla_bound_is_fresh(self):
        self.assertEqual(self._fault(FeedObservation("PX", "V1", NOW - 60.0, True)), FaultCode.NONE)

    def test_lag_just_beyond_the_sla_bound_is_stale(self):
        self.assertEqual(
            self._fault(FeedObservation("PX", "V1", NOW - 60.001, True)), FaultCode.STALE
        )

    def test_future_timestamp_beyond_tolerance_is_a_clock_fault_not_fresh_data(self):
        # A vendor clock running fast yields a negative lag, which an unbounded comparison
        # treats as permanently inside the freshness bound.
        fault = self._fault(FeedObservation("PX", "V1", NOW + 3600.0, True))
        self.assertEqual(fault, FaultCode.CLOCK_SKEW)

    def test_future_timestamp_within_tolerance_is_accepted(self):
        self.assertEqual(self._fault(FeedObservation("PX", "V1", NOW + 0.5, True)), FaultCode.NONE)

    def test_clock_skew_blocks_a_critical_feed(self):
        report = self.engine.evaluate_strategy_readiness(
            NOW, [FeedObservation("PX", "V1", NOW + 3600.0, True)]
        )
        self.assertFalse(report.is_strategy_ready_to_trade)
        self.assertEqual(report.blocked_dependencies, ("PX",))

    def test_schema_error_rejects_the_vendor(self):
        self.assertEqual(
            self._fault(FeedObservation("PX", "V1", NOW - 5.0, True, schema_error="missing bid")),
            FaultCode.SCHEMA_ERROR,
        )

    def test_schema_contract_mismatch_rejects_the_vendor(self):
        engine = StrategyDataDependencyEngine(
            "QUANT_ALPHA_04",
            [
                node(
                    "PX",
                    DependencyCriticality.CRITICAL,
                    ("V1",),
                    schema_contract_version="2025-01-book-v3",
                )
            ],
        )
        report = engine.evaluate_strategy_readiness(
            NOW, [FeedObservation("PX", "V1", NOW - 5.0, True, schema_version="2024-06-book-v2")]
        )
        self.assertEqual(report.assessments[0].fault, FaultCode.SCHEMA_MISMATCH)
        self.assertEqual(report.blocked_dependencies, ("PX",))


class TestUpstreamPropagation(unittest.TestCase):
    def _engine(self, raw_vendors=("V1",)):
        return StrategyDataDependencyEngine(
            "QUANT_ALPHA_05",
            [
                node("RAW_TICKS", DependencyCriticality.CRITICAL, raw_vendors),
                node(
                    "VOL_FEATURE",
                    DependencyCriticality.HIGH,
                    ("V2",),
                    upstream_feed_ids={"RAW_TICKS"},
                ),
            ],
        )

    def test_derived_feed_cannot_be_healthier_than_a_dead_upstream(self):
        # VOL_FEATURE publishes a fresh timestamp of its own but is computed from a dead input.
        # RAW_TICKS blocks (CRITICAL); VOL_FEATURE degrades to 3 * 0.5 = 1.5 of 7 => 21.428571.
        report = self._engine().evaluate_strategy_readiness(NOW, [fresh("VOL_FEATURE", "V2")])
        self.assertEqual(report.blocked_dependencies, ("RAW_TICKS",))
        self.assertFalse(report.is_strategy_ready_to_trade)
        feature = next(a for a in report.assessments if a.feed_id == "VOL_FEATURE")
        self.assertEqual(feature.state, FeedState.DEGRADED)
        self.assertEqual(feature.fault, FaultCode.UPSTREAM_IMPAIRED)
        self.assertAlmostEqual(report.readiness_score_pct, 21.428571, places=6)

    def test_derived_feed_inherits_an_upstream_fallback(self):
        # RAW_TICKS serves from its secondary; VOL_FEATURE is capped at the same level.
        # (4 * 0.8 + 3 * 0.8) / 7 = 5.6 / 7 = 80.0
        report = self._engine(("V1", "V1_BACKUP")).evaluate_strategy_readiness(
            NOW,
            [
                FeedObservation("RAW_TICKS", "V1", NOW - 600.0, True),
                fresh("RAW_TICKS", "V1_BACKUP"),
                fresh("VOL_FEATURE", "V2"),
            ],
        )
        self.assertEqual(report.readiness_score_pct, 80.0)
        feature = next(a for a in report.assessments if a.feed_id == "VOL_FEATURE")
        self.assertEqual(feature.state, FeedState.FALLBACK_ACTIVE)
        self.assertEqual(feature.fault, FaultCode.UPSTREAM_IMPAIRED)
        self.assertEqual(feature.active_vendor, "V2")

    def test_healthy_upstream_does_not_downgrade_a_healthy_derived_feed(self):
        report = self._engine().evaluate_strategy_readiness(
            NOW, [fresh("RAW_TICKS", "V1"), fresh("VOL_FEATURE", "V2")]
        )
        self.assertEqual(report.readiness_score_pct, 100.0)
        self.assertTrue(report.is_strategy_ready_to_trade)


class TestDegradeAndBlockResponses(unittest.TestCase):
    def test_unobserved_high_feed_degrades_rather_than_blocking(self):
        engine = StrategyDataDependencyEngine(
            "QUANT_ALPHA_06", [node("SENTIMENT", DependencyCriticality.HIGH, ("Dataminr",))]
        )
        report = engine.evaluate_strategy_readiness(NOW, [])
        self.assertEqual(report.degraded_dependencies, ("SENTIMENT",))
        self.assertEqual(report.unobserved_dependencies, ("SENTIMENT",))
        self.assertEqual(report.blocked_dependencies, ())
        self.assertEqual(report.readiness_score_pct, 50.0)
        self.assertEqual(report.assessments[0].fault, FaultCode.NO_OBSERVATION)

    def test_explicit_block_response_overrides_the_criticality_default(self):
        engine = StrategyDataDependencyEngine(
            "QUANT_ALPHA_07",
            [
                node(
                    "BORROW_RATES",
                    DependencyCriticality.MEDIUM,
                    ("V1",),
                    failure_response=FailureResponse.BLOCK,
                )
            ],
        )
        report = engine.evaluate_strategy_readiness(NOW, [])
        self.assertEqual(report.blocked_dependencies, ("BORROW_RATES",))
        self.assertFalse(report.is_strategy_ready_to_trade)

    def test_explicit_degrade_response_overrides_the_critical_default(self):
        engine = StrategyDataDependencyEngine(
            "QUANT_ALPHA_08",
            [
                node(
                    "L2_BOOK",
                    DependencyCriticality.CRITICAL,
                    ("V1",),
                    failure_response=FailureResponse.DEGRADE,
                )
            ],
        )
        report = engine.evaluate_strategy_readiness(NOW, [])
        self.assertEqual(report.blocked_dependencies, ())
        self.assertEqual(report.degraded_dependencies, ("L2_BOOK",))

    def test_default_failure_response_follows_criticality(self):
        critical = node("A", DependencyCriticality.CRITICAL, ("V1",))
        high = node("B", DependencyCriticality.HIGH, ("V1",))
        self.assertEqual(critical.effective_failure_response, FailureResponse.BLOCK)
        self.assertEqual(high.effective_failure_response, FailureResponse.DEGRADE)


class TestDuplicateObservations(unittest.TestCase):
    def setUp(self):
        self.engine = StrategyDataDependencyEngine(
            "QUANT_ALPHA_09", [node("PX", DependencyCriticality.CRITICAL, ("V1",))]
        )

    def test_duplicate_pair_resolves_to_the_unhealthy_observation(self):
        report = self.engine.evaluate_strategy_readiness(
            NOW,
            [
                FeedObservation("PX", "V1", NOW - 5.0, True),
                FeedObservation("PX", "V1", NOW - 5.0, False),
            ],
        )
        self.assertEqual(report.blocked_dependencies, ("PX",))
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("duplicate observation", report.warnings[0])

    def test_duplicate_pair_resolves_to_the_older_timestamp(self):
        report = self.engine.evaluate_strategy_readiness(
            NOW,
            [
                FeedObservation("PX", "V1", NOW - 5.0, True),
                FeedObservation("PX", "V1", NOW - 600.0, True),
            ],
        )
        self.assertEqual(report.assessments[0].fault, FaultCode.STALE)
        self.assertEqual(report.blocked_dependencies, ("PX",))


class TestVendorBlastRadius(unittest.TestCase):
    def setUp(self):
        self.engine = StrategyDataDependencyEngine(
            "QUANT_ALPHA_10",
            [
                node("L2_BOOK", DependencyCriticality.CRITICAL, ("Refinitiv", "Bloomberg")),
                node("REF_DATA", DependencyCriticality.CRITICAL, ("Refinitiv",), sla=900.0),
                node("NEWS", DependencyCriticality.LOW, ("Benzinga",), sla=900.0),
            ],
        )

    def test_sole_sourced_critical_feed_makes_the_vendor_a_blocking_single_point(self):
        exposure = self.engine.assess_vendor_outage("Refinitiv")
        self.assertEqual(exposure.dependent_feed_ids, ("L2_BOOK", "REF_DATA"))
        self.assertEqual(exposure.sole_source_feed_ids, ("REF_DATA",))
        self.assertEqual(exposure.blocking_feed_ids, ("REF_DATA",))
        self.assertTrue(exposure.would_block_strategy)
        self.assertEqual(exposure.max_criticality, DependencyCriticality.CRITICAL)

    def test_unused_vendor_has_no_exposure(self):
        exposure = self.engine.assess_vendor_outage("Polygon")
        self.assertEqual(exposure.dependent_feed_ids, ())
        self.assertFalse(exposure.would_block_strategy)
        self.assertEqual(exposure.projected_readiness_pct, 100.0)
        self.assertIsNone(exposure.max_criticality)

    def test_losing_a_low_criticality_sole_source_degrades_without_blocking(self):
        # NEWS is sole-sourced on Benzinga but LOW, so it degrades instead of blocking:
        # (4 + 4 + 1 * 0.5) / 9 = 8.5 / 9 = 94.444444
        exposure = self.engine.assess_vendor_outage("Benzinga")
        self.assertEqual(exposure.sole_source_feed_ids, ("NEWS",))
        self.assertAlmostEqual(exposure.projected_readiness_pct, 94.444444, places=6)
        self.assertEqual(exposure.blocking_feed_ids, ())
        self.assertFalse(exposure.would_block_strategy)

    def test_single_source_feeds_lists_feeds_without_a_fallback(self):
        self.assertEqual(self.engine.single_source_feeds(), ("REF_DATA", "NEWS"))

    def test_vendor_outage_propagates_through_derived_feeds(self):
        engine = StrategyDataDependencyEngine(
            "QUANT_ALPHA_11",
            [
                node("RAW", DependencyCriticality.CRITICAL, ("Refinitiv",)),
                node(
                    "SIGNAL",
                    DependencyCriticality.CRITICAL,
                    ("Internal",),
                    upstream_feed_ids={"RAW"},
                ),
            ],
        )
        exposure = engine.assess_vendor_outage("Refinitiv")
        self.assertEqual(exposure.blocking_feed_ids, ("RAW", "SIGNAL"))
        self.assertEqual(exposure.projected_readiness_pct, 0.0)


class TestPortfolioTriage(unittest.TestCase):
    def setUp(self):
        self.portfolio = DataDependencyPortfolio(
            [
                StrategyDataDependencyEngine(
                    "STAT_ARB", [node("L2", DependencyCriticality.CRITICAL, ("Refinitiv",))]
                ),
                StrategyDataDependencyEngine(
                    "TREND",
                    [node("BARS", DependencyCriticality.CRITICAL, ("Polygon", "Refinitiv"))],
                ),
            ]
        )

    def test_blocked_strategies_are_identified_per_vendor(self):
        self.assertEqual(self.portfolio.strategies_blocked_by("Refinitiv"), ("STAT_ARB",))
        self.assertEqual(self.portfolio.strategies_blocked_by("Polygon"), ())

    def test_exposure_is_reported_for_every_strategy(self):
        exposures = self.portfolio.assess_vendor_outage("Refinitiv")
        self.assertEqual([e.strategy_id for e in exposures], ["STAT_ARB", "TREND"])

    def test_vendor_ids_are_deduplicated_and_sorted(self):
        self.assertEqual(self.portfolio.vendor_ids(), ("Polygon", "Refinitiv"))

    def test_duplicate_strategy_ids_are_rejected(self):
        engine = StrategyDataDependencyEngine(
            "DUP", [node("L2", DependencyCriticality.CRITICAL, ("V1",))]
        )
        with self.assertRaises(DependencyValidationError):
            DataDependencyPortfolio([engine, engine])


class TestConfigurationValidation(unittest.TestCase):
    def test_duplicate_feed_ids_are_rejected(self):
        with self.assertRaises(DependencyValidationError):
            StrategyDataDependencyEngine(
                "S",
                [
                    node("PX", DependencyCriticality.CRITICAL, ("V1",)),
                    node("PX", DependencyCriticality.HIGH, ("V2",)),
                ],
            )

    def test_empty_dependency_list_is_rejected(self):
        with self.assertRaises(DependencyValidationError):
            StrategyDataDependencyEngine("S", [])

    def test_unknown_upstream_reference_is_rejected(self):
        with self.assertRaises(DependencyValidationError):
            StrategyDataDependencyEngine(
                "S",
                [
                    node(
                        "DERIVED",
                        DependencyCriticality.HIGH,
                        ("V1",),
                        upstream_feed_ids={"MISSING"},
                    )
                ],
            )

    def test_dependency_cycle_is_rejected(self):
        with self.assertRaises(DependencyValidationError) as ctx:
            StrategyDataDependencyEngine(
                "S",
                [
                    node("A", DependencyCriticality.HIGH, ("V1",), upstream_feed_ids={"B"}),
                    node("B", DependencyCriticality.HIGH, ("V1",), upstream_feed_ids={"A"}),
                ],
            )
        self.assertIn("cycle", str(ctx.exception))

    def test_self_referential_upstream_is_rejected(self):
        with self.assertRaises(DependencyValidationError):
            node("A", DependencyCriticality.HIGH, ("V1",), upstream_feed_ids={"A"})

    def test_empty_vendor_list_is_rejected(self):
        with self.assertRaises(DependencyValidationError):
            node("A", DependencyCriticality.HIGH, ())

    def test_repeated_vendor_is_rejected(self):
        with self.assertRaises(DependencyValidationError):
            node("A", DependencyCriticality.HIGH, ("V1", "V1"))

    def test_non_positive_sla_is_rejected(self):
        with self.assertRaises(DependencyValidationError):
            node("A", DependencyCriticality.HIGH, ("V1",), sla=0.0)

    def test_non_finite_sla_is_rejected(self):
        with self.assertRaises(DependencyValidationError):
            node("A", DependencyCriticality.HIGH, ("V1",), sla=float("inf"))

    def test_blank_identifier_is_rejected(self):
        with self.assertRaises(DependencyValidationError):
            node("  ", DependencyCriticality.HIGH, ("V1",))

    def test_criticality_must_use_the_enum(self):
        with self.assertRaises(DependencyValidationError):
            node("A", "CRITICAL", ("V1",))

    def test_vendor_preference_helpers(self):
        dual = node("A", DependencyCriticality.HIGH, ("V1", "V2"))
        single = node("B", DependencyCriticality.HIGH, ("V1",))
        self.assertEqual(dual.primary_vendor, "V1")
        self.assertEqual(dual.secondary_vendor, "V2")
        self.assertIsNone(single.secondary_vendor)


class TestPolicyValidation(unittest.TestCase):
    def test_degraded_credit_above_fallback_credit_is_rejected(self):
        with self.assertRaises(DependencyValidationError):
            ReadinessPolicy(fallback_credit=0.4, degraded_credit=0.9)

    def test_credit_above_one_is_rejected(self):
        with self.assertRaises(DependencyValidationError):
            ReadinessPolicy(fallback_credit=1.5)

    def test_minimum_readiness_outside_percentage_range_is_rejected(self):
        with self.assertRaises(DependencyValidationError):
            ReadinessPolicy(minimum_readiness_pct=150.0)

    def test_negative_future_tolerance_is_rejected(self):
        with self.assertRaises(DependencyValidationError):
            ReadinessPolicy(future_timestamp_tolerance_seconds=-1.0)

    def test_missing_criticality_weight_is_rejected(self):
        with self.assertRaises(DependencyValidationError):
            ReadinessPolicy(criticality_weights={DependencyCriticality.CRITICAL: 4.0})

    def test_non_positive_weight_is_rejected(self):
        with self.assertRaises(DependencyValidationError):
            ReadinessPolicy(
                criticality_weights={
                    DependencyCriticality.CRITICAL: 4.0,
                    DependencyCriticality.HIGH: 3.0,
                    DependencyCriticality.MEDIUM: 2.0,
                    DependencyCriticality.LOW: 0.0,
                }
            )

    def test_custom_policy_changes_the_gate(self):
        engine = StrategyDataDependencyEngine(
            "S",
            [node("SENTIMENT", DependencyCriticality.HIGH, ("V1",))],
            policy=ReadinessPolicy(minimum_readiness_pct=40.0),
        )
        report = engine.evaluate_strategy_readiness(NOW, [])
        self.assertEqual(report.readiness_score_pct, 50.0)
        self.assertTrue(report.is_strategy_ready_to_trade)


class TestObservationValidation(unittest.TestCase):
    def setUp(self):
        self.engine = StrategyDataDependencyEngine(
            "S", [node("PX", DependencyCriticality.CRITICAL, ("V1",))]
        )

    def test_non_finite_observation_timestamp_is_rejected(self):
        with self.assertRaises(ObservationValidationError):
            FeedObservation("PX", "V1", float("nan"), True)

    def test_non_finite_evaluation_time_is_rejected(self):
        with self.assertRaises(ObservationValidationError):
            self.engine.evaluate_strategy_readiness(float("nan"), [])

    def test_non_observation_entries_are_rejected(self):
        with self.assertRaises(ObservationValidationError):
            self.engine.evaluate_strategy_readiness(NOW, [{"feed_id": "PX"}])

    def test_blank_vendor_id_is_rejected(self):
        with self.assertRaises(ObservationValidationError):
            FeedObservation("PX", "", NOW, True)


class TestReportImmutability(unittest.TestCase):
    def test_report_and_sources_cannot_be_mutated(self):
        engine = StrategyDataDependencyEngine(
            "S", [node("PX", DependencyCriticality.CRITICAL, ("V1",))]
        )
        report = engine.evaluate_strategy_readiness(NOW, [fresh("PX", "V1")])
        with self.assertRaises(FrozenInstanceError):
            report.is_strategy_ready_to_trade = True
        with self.assertRaises(TypeError):
            report.active_feed_sources["PX"] = "Rogue"


if __name__ == "__main__":
    unittest.main()
