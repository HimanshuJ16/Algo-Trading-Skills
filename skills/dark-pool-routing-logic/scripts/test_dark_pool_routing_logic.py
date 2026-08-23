"""Unit tests for the dark pool / ATS routing engine.

Venue identifiers here are deliberately neutral placeholders (`ATS_A`, ...).
Fill rates and toxicity scores are synthetic test fixtures and must never be
read as measurements of any real trading venue.
"""

import logging
import unittest

from dark_pool_routing_logic import (
    DEFAULT_MIN_QTY,
    DarkPoolRoutingEngine,
    DarkPoolRoutingError,
    DarkPoolRoutingReport,
    DarkPoolVenueProfile,
)

logging.disable(logging.CRITICAL)


def make_venue(
    venue_id: str = "ATS_A",
    venue_name: str = None,
    historical_fill_rate: float = 0.40,
    toxicity_score_bps: float = 1.0,
    min_qty_threshold: int = 100,
    is_active: bool = True,
) -> DarkPoolVenueProfile:
    return DarkPoolVenueProfile(
        venue_id=venue_id,
        venue_name=venue_name if venue_name is not None else f"Dark Pool {venue_id}",
        historical_fill_rate=historical_fill_rate,
        toxicity_score_bps=toxicity_score_bps,
        min_qty_threshold=min_qty_threshold,
        is_active=is_active,
    )


class TestVenueProfileValidation(unittest.TestCase):
    def test_fill_rate_above_one_is_rejected(self):
        # A rate > 1.0 is a measurement error that would over-weight the venue.
        with self.assertRaises(DarkPoolRoutingError):
            make_venue(historical_fill_rate=1.4)

    def test_negative_fill_rate_is_rejected(self):
        with self.assertRaises(DarkPoolRoutingError):
            make_venue(historical_fill_rate=-0.1)

    def test_non_finite_inputs_are_rejected(self):
        with self.assertRaises(DarkPoolRoutingError):
            make_venue(toxicity_score_bps=float("nan"))
        with self.assertRaises(DarkPoolRoutingError):
            make_venue(historical_fill_rate=float("inf"))

    def test_negative_min_qty_threshold_is_rejected(self):
        with self.assertRaises(DarkPoolRoutingError):
            make_venue(min_qty_threshold=-1)

    def test_empty_identifiers_are_rejected(self):
        with self.assertRaises(DarkPoolRoutingError):
            make_venue(venue_id="  ")
        with self.assertRaises(DarkPoolRoutingError):
            make_venue(venue_name="")


class TestEngineConfigurationValidation(unittest.TestCase):
    def test_zero_toxicity_decay_is_rejected(self):
        # Would otherwise divide by zero inside the score.
        with self.assertRaises(DarkPoolRoutingError):
            DarkPoolRoutingEngine(toxicity_decay_bps=0.0)

    def test_out_of_range_parameters_are_rejected(self):
        with self.assertRaises(DarkPoolRoutingError):
            DarkPoolRoutingEngine(min_fill_rate=1.5)
        with self.assertRaises(DarkPoolRoutingError):
            DarkPoolRoutingEngine(min_qty_child_fraction=1.2)
        with self.assertRaises(DarkPoolRoutingError):
            DarkPoolRoutingEngine(max_venue_allocation_pct=0.0)
        with self.assertRaises(DarkPoolRoutingError):
            DarkPoolRoutingEngine(default_min_qty=-1)

    def test_register_venue_rejects_wrong_type(self):
        engine = DarkPoolRoutingEngine()
        with self.assertRaises(DarkPoolRoutingError):
            engine.register_venue({"venue_id": "ATS_A"})


class TestParentOrderValidation(unittest.TestCase):
    def setUp(self):
        self.engine = DarkPoolRoutingEngine()
        self.engine.register_venue(make_venue())

    def test_invalid_side_is_rejected(self):
        with self.assertRaises(DarkPoolRoutingError):
            self.engine.route_parent_order("AAPL", "LONG", 10_000)

    def test_lowercase_side_is_normalised(self):
        report = self.engine.route_parent_order("AAPL", "buy", 10_000)
        self.assertEqual(report.side, "BUY")

    def test_non_positive_quantity_is_rejected(self):
        for bad_qty in (0, -100):
            with self.assertRaises(DarkPoolRoutingError):
                self.engine.route_parent_order("AAPL", "BUY", bad_qty)

    def test_non_integer_quantity_is_rejected(self):
        with self.assertRaises(DarkPoolRoutingError):
            self.engine.route_parent_order("AAPL", "BUY", 10_000.5)

    def test_empty_symbol_is_rejected(self):
        with self.assertRaises(DarkPoolRoutingError):
            self.engine.route_parent_order("", "BUY", 10_000)


class TestVenueScreening(unittest.TestCase):
    def setUp(self):
        self.engine = DarkPoolRoutingEngine(max_acceptable_toxicity_bps=5.0, default_min_qty=200)
        # ATS_A: 40% fill, 1.0 bps  -> eligible
        # ATS_B: 50% fill, 8.0 bps  -> excluded, above the 5.0 bps ceiling
        # ATS_C: 30% fill, 0.5 bps  -> eligible
        self.engine.register_venue(make_venue("ATS_A", historical_fill_rate=0.40, toxicity_score_bps=1.0))
        self.engine.register_venue(make_venue("ATS_B", historical_fill_rate=0.50, toxicity_score_bps=8.0))
        self.engine.register_venue(make_venue("ATS_C", historical_fill_rate=0.30, toxicity_score_bps=0.5))

    def test_toxic_venue_excluded_and_min_qty_attached(self):
        report = self.engine.route_parent_order("AAPL", "BUY", total_quantity=10_000)

        self.assertIsInstance(report, DarkPoolRoutingReport)
        self.assertEqual(report.total_parent_quantity, 10_000)
        venue_ids = [d.venue_id for d in report.child_directives]
        self.assertEqual(sorted(venue_ids), ["ATS_A", "ATS_C"])
        self.assertTrue(any("Toxic" in reason for reason in report.excluded_venues))
        for child in report.child_directives:
            self.assertGreaterEqual(child.min_qty_instruction, DEFAULT_MIN_QTY)

    def test_allocation_weights_match_independently_derived_scores(self):
        # Scores derived by hand: 0.40 * (1 - 1.0/50) = 0.392;
        #                         0.30 * (1 - 0.5/50) = 0.297. Total 0.689.
        report = self.engine.route_parent_order("AAPL", "BUY", total_quantity=10_000)
        by_id = {d.venue_id: d for d in report.child_directives}

        self.assertAlmostEqual(by_id["ATS_A"].venue_score, 0.392, places=4)
        self.assertAlmostEqual(by_id["ATS_C"].venue_score, 0.297, places=4)
        # 10_000 * 0.392/0.689 = 5689.4 -> 5689 (+1 residual share, see below)
        # 10_000 * 0.297/0.689 = 4310.6 -> 4310 + 1 = 4311
        self.assertEqual(by_id["ATS_A"].allocated_quantity, 5689)
        self.assertEqual(by_id["ATS_C"].allocated_quantity, 4311)
        self.assertGreater(by_id["ATS_A"].allocated_quantity, by_id["ATS_C"].allocated_quantity)

    def test_toxicity_ceiling_is_inclusive_at_the_boundary(self):
        # Exclusion is strictly-greater-than, so a venue exactly at the ceiling stays in.
        engine = DarkPoolRoutingEngine(max_acceptable_toxicity_bps=5.0)
        engine.register_venue(make_venue("ATS_EDGE", toxicity_score_bps=5.0))
        report = engine.route_parent_order("AAPL", "BUY", 10_000)
        self.assertEqual([d.venue_id for d in report.child_directives], ["ATS_EDGE"])

    def test_custom_toxicity_override_applies_per_order(self):
        report = self.engine.route_parent_order(
            "AAPL", "BUY", 10_000, custom_max_toxicity_bps=10.0
        )
        self.assertIn("ATS_B", [d.venue_id for d in report.child_directives])

    def test_inactive_and_low_fill_rate_venues_are_excluded(self):
        engine = DarkPoolRoutingEngine(min_fill_rate=0.05)
        engine.register_venue(make_venue("ATS_OFF", is_active=False))
        engine.register_venue(make_venue("ATS_COLD", historical_fill_rate=0.01))
        engine.register_venue(make_venue("ATS_OK", historical_fill_rate=0.40))

        report = engine.route_parent_order("AAPL", "SELL", 10_000)
        self.assertEqual([d.venue_id for d in report.child_directives], ["ATS_OK"])
        self.assertTrue(any("Inactive" in reason for reason in report.excluded_venues))
        self.assertTrue(any("Low Fill Rate" in reason for reason in report.excluded_venues))

    def test_favourable_markout_cannot_dominate_the_allocation(self):
        # Regression: an unclamped (1 - toxicity/50) discount turns a -500 bps
        # markout into a 11x multiplier, handing the venue the whole parent.
        engine = DarkPoolRoutingEngine()
        engine.register_venue(
            make_venue("ATS_NEG", historical_fill_rate=0.30, toxicity_score_bps=-500.0)
        )
        engine.register_venue(
            make_venue("ATS_POS", historical_fill_rate=0.40, toxicity_score_bps=0.0)
        )
        report = engine.route_parent_order("AAPL", "BUY", 10_000)
        by_id = {d.venue_id: d for d in report.child_directives}

        self.assertLessEqual(by_id["ATS_NEG"].venue_score, 1.0)
        # Scores collapse to the raw fill rates 0.30 / 0.40.
        self.assertAlmostEqual(by_id["ATS_NEG"].venue_score, 0.30, places=4)
        self.assertLess(by_id["ATS_NEG"].allocated_quantity, by_id["ATS_POS"].allocated_quantity)


class TestMinQtyInvariant(unittest.TestCase):
    """MinQty above the child quantity describes an order that can never trade."""

    def test_min_qty_never_exceeds_child_quantity(self):
        # Regression for the original defect: the eligibility gate compared the
        # child size against the *venue* minimum (100) while the MinQty
        # instruction also applied the engine floor (200), so a 150-share child
        # was sent with MinQty=200 and could never fill.
        engine = DarkPoolRoutingEngine(default_min_qty=200)
        engine.register_venue(
            make_venue("ATS_A", historical_fill_rate=0.40, toxicity_score_bps=1.0, min_qty_threshold=100)
        )
        engine.register_venue(
            make_venue("ATS_B", historical_fill_rate=0.40, toxicity_score_bps=1.0, min_qty_threshold=100)
        )

        report = engine.route_parent_order("AAPL", "BUY", total_quantity=300)

        for child in report.child_directives:
            self.assertLessEqual(child.min_qty_instruction, child.allocated_quantity)
        # 150/150 cannot satisfy MinQty=200, so one venue is dropped and the
        # whole 300 goes to the survivor rather than two dead child orders.
        self.assertEqual(len(report.child_directives), 1)
        self.assertEqual(report.child_directives[0].allocated_quantity, 300)
        self.assertEqual(report.child_directives[0].min_qty_instruction, 200)
        self.assertTrue(any("< MinQty" in reason for reason in report.excluded_venues))

    def test_min_qty_scales_with_child_size(self):
        # max(venue 100, engine 200, ceil(20% of 10_000) = 2_000) -> 2_000.
        engine = DarkPoolRoutingEngine(default_min_qty=200, min_qty_child_fraction=0.20)
        engine.register_venue(make_venue("ATS_A", min_qty_threshold=100))
        report = engine.route_parent_order("AAPL", "BUY", 10_000)
        self.assertEqual(report.child_directives[0].min_qty_instruction, 2_000)

    def test_venue_min_qty_dominates_when_larger(self):
        engine = DarkPoolRoutingEngine(default_min_qty=200, min_qty_child_fraction=0.0)
        engine.register_venue(make_venue("ATS_BLOCK", min_qty_threshold=5_000))
        report = engine.route_parent_order("AAPL", "BUY", 10_000)
        self.assertEqual(report.child_directives[0].min_qty_instruction, 5_000)

    def test_parent_below_every_venue_minimum_is_not_routed(self):
        engine = DarkPoolRoutingEngine(default_min_qty=200)
        engine.register_venue(make_venue("ATS_BLOCK", min_qty_threshold=25_000))

        report = engine.route_parent_order("AAPL", "BUY", 1_000)

        self.assertEqual(report.child_directives, [])
        self.assertEqual(report.allocated_quantity, 0)
        self.assertEqual(report.unallocated_quantity, 1_000)
        self.assertEqual(report.unallocated_reason, "PARENT_TOO_SMALL_FOR_VENUE_MIN_QTY")


class TestAllocationConservation(unittest.TestCase):
    def test_no_shares_are_lost_to_truncation(self):
        # Regression: three equal venues over 10_000 shares truncate to
        # 3_333 x 3 = 9_999 under plain int() allocation.
        engine = DarkPoolRoutingEngine()
        for venue_id in ("ATS_A", "ATS_B", "ATS_C"):
            engine.register_venue(make_venue(venue_id, historical_fill_rate=0.40))

        report = engine.route_parent_order("AAPL", "BUY", 10_000)

        self.assertEqual(sum(d.allocated_quantity for d in report.child_directives), 10_000)
        self.assertEqual(report.allocated_quantity, 10_000)
        self.assertEqual(report.unallocated_quantity, 0)
        self.assertEqual(report.unallocated_reason, "")
        self.assertEqual(
            sorted(d.allocated_quantity for d in report.child_directives), [3_333, 3_333, 3_334]
        )

    def test_weights_sum_to_one_hundred_percent(self):
        engine = DarkPoolRoutingEngine()
        engine.register_venue(make_venue("ATS_A", historical_fill_rate=0.40))
        engine.register_venue(make_venue("ATS_C", historical_fill_rate=0.30))
        report = engine.route_parent_order("AAPL", "BUY", 10_000)
        self.assertAlmostEqual(
            sum(d.allocation_weight_pct for d in report.child_directives), 100.0, places=2
        )

    def test_allocation_is_deterministic_across_registration_order(self):
        def build(order):
            engine = DarkPoolRoutingEngine()
            for venue_id in order:
                engine.register_venue(make_venue(venue_id, historical_fill_rate=0.40))
            report = engine.route_parent_order("AAPL", "BUY", 10_000)
            return sorted((d.venue_id, d.allocated_quantity) for d in report.child_directives)

        self.assertEqual(build(["ATS_A", "ATS_B", "ATS_C"]), build(["ATS_C", "ATS_A", "ATS_B"]))

    def test_concentration_cap_limits_single_venue_share(self):
        engine = DarkPoolRoutingEngine(max_venue_allocation_pct=0.40)
        engine.register_venue(make_venue("ATS_A", historical_fill_rate=0.80, toxicity_score_bps=0.0))
        engine.register_venue(make_venue("ATS_C", historical_fill_rate=0.20, toxicity_score_bps=0.0))

        report = engine.route_parent_order("AAPL", "BUY", 10_000)
        by_id = {d.venue_id: d for d in report.child_directives}

        self.assertEqual(by_id["ATS_A"].allocated_quantity, 4_000)
        self.assertEqual(by_id["ATS_C"].allocated_quantity, 2_000)
        # The capped remainder is reported, not silently pushed elsewhere.
        self.assertEqual(report.unallocated_quantity, 4_000)
        self.assertEqual(report.unallocated_reason, "VENUE_CONCENTRATION_CAP")


class TestNoEligibleVenues(unittest.TestCase):
    def test_all_venues_toxic_returns_empty_report(self):
        engine = DarkPoolRoutingEngine(max_acceptable_toxicity_bps=5.0)
        engine.register_venue(make_venue("ATS_B", toxicity_score_bps=8.0))

        report = engine.route_parent_order("AAPL", "BUY", 10_000)

        self.assertEqual(report.child_directives, [])
        self.assertEqual(report.allocated_quantity, 0)
        self.assertEqual(report.unallocated_quantity, 10_000)
        self.assertEqual(report.unallocated_reason, "NO_ELIGIBLE_VENUE")

    def test_no_registered_venues_returns_empty_report(self):
        report = DarkPoolRoutingEngine().route_parent_order("AAPL", "BUY", 10_000)
        self.assertEqual(report.unallocated_reason, "NO_ELIGIBLE_VENUE")
        self.assertEqual(report.unallocated_quantity, 10_000)

    def test_zero_score_venue_is_excluded_rather_than_dividing_by_zero(self):
        # Raising the ceiling past the decay range makes the discount zero.
        engine = DarkPoolRoutingEngine(
            max_acceptable_toxicity_bps=100.0, toxicity_decay_bps=50.0
        )
        engine.register_venue(make_venue("ATS_DEAD", toxicity_score_bps=60.0))

        report = engine.route_parent_order("AAPL", "BUY", 10_000)

        self.assertEqual(report.child_directives, [])
        self.assertEqual(report.unallocated_reason, "NO_ELIGIBLE_VENUE")
        self.assertTrue(any("Zero Score" in reason for reason in report.excluded_venues))


class TestVenueRegistry(unittest.TestCase):
    def test_re_registering_a_venue_id_replaces_the_profile(self):
        engine = DarkPoolRoutingEngine()
        engine.register_venue(make_venue("ATS_A", toxicity_score_bps=1.0))
        engine.register_venue(make_venue("ATS_A", toxicity_score_bps=9.0))

        self.assertEqual(len(engine.venues), 1)
        report = engine.route_parent_order("AAPL", "BUY", 10_000)
        self.assertEqual(report.child_directives, [])
        self.assertEqual(report.unallocated_reason, "NO_ELIGIBLE_VENUE")


if __name__ == "__main__":
    unittest.main()
