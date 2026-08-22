import math
import unittest

from co_location_provider_selection_and_network_topology import (
    ColocationTopologyEvaluator,
    FacilitySpec,
    LAYER1_SWITCH_LATENCY_US,
    MEDIUM_PROPAGATION_US_KM,
    NetworkLinkSpec,
    PROPAGATION_PHYSICS_FIBER_US_KM,
    PROPAGATION_SPEED_FIBER_US_KM,
    PROPAGATION_SPEED_MICROWAVE_US_KM,
    VACUUM_DELAY_US_KM,
)

KM_PER_MILE = 1.609344


class TestPropagationConstants(unittest.TestCase):
    """Constants are checked against independently sourced physics, not against
    the module's own arithmetic."""

    def test_vacuum_delay_matches_si_speed_of_light(self):
        # c is exactly 299,792,458 m/s by SI definition -> 3.3356 us/km.
        self.assertAlmostEqual(VACUUM_DELAY_US_KM, 1e6 / 299_792_458 * 1000, places=9)
        self.assertAlmostEqual(VACUUM_DELAY_US_KM, 3.335641, places=6)

    def test_microwave_constant_is_not_superluminal(self):
        # The legacy 3.333 us/km shorthand implies 300,000 km/s, which is faster
        # than light in vacuum. The corrected constant must be slower than c.
        self.assertGreater(PROPAGATION_SPEED_MICROWAVE_US_KM, VACUUM_DELAY_US_KM)
        self.assertLess(3.333, VACUUM_DELAY_US_KM)

    def test_microwave_constant_matches_published_per_mile_figure(self):
        # Industry rule of thumb for free-space RF: ~5.4 us per mile.
        us_per_mile = PROPAGATION_SPEED_MICROWAVE_US_KM * KM_PER_MILE
        self.assertAlmostEqual(us_per_mile, 5.37, places=2)

    def test_fiber_planning_constant_is_conservative_versus_physics(self):
        # SMF-28 group index 1.4682 at 1550 nm -> ~4.897 us/km. The 5.0 us/km
        # planning default must never be faster than the physics value.
        self.assertAlmostEqual(PROPAGATION_PHYSICS_FIBER_US_KM, 4.897388, places=6)
        self.assertGreater(PROPAGATION_SPEED_FIBER_US_KM, PROPAGATION_PHYSICS_FIBER_US_KM)

    def test_every_supported_medium_is_physically_achievable(self):
        for medium, delay in MEDIUM_PROPAGATION_US_KM.items():
            with self.subTest(medium=medium):
                self.assertGreaterEqual(delay, VACUUM_DELAY_US_KM)


class TestLatencyBudget(unittest.TestCase):

    def setUp(self):
        self.evaluator = ColocationTopologyEvaluator()

    def test_fiber_latency_calculation(self):
        # 100 km fiber link with 2 switches (200 ns each = 0.4 us)
        # Propagation: 100 km * 5 us/km = 500 us
        # Total one way = 500.4 us, RTT = 1000.8 us
        link = NetworkLinkSpec(
            source="NY4", destination="CARTERET",
            distance_km=100.0, medium_type="FIBER",
            num_switches=2, switch_latency_us=0.200,
        )
        res = self.evaluator.calculate_latency_budget(link)

        self.assertEqual(res.propagation_delay_us, 500.0)
        self.assertEqual(res.switch_delay_us, 0.4)
        self.assertEqual(res.cross_connect_delay_us, 0.0)
        self.assertEqual(res.total_one_way_us, 500.4)
        self.assertEqual(res.total_rtt_us, 1000.8)

    def test_microwave_vs_fiber_speed(self):
        # 1200 km great-circle corridor (Secaucus NY4 <-> CME Aurora IL).
        # Fiber:     1200 * 5.0      = 6000.00 us
        # Microwave: 1200 * 3.336642 = 4003.97 us
        fiber_link = NetworkLinkSpec("NY4", "AURORA", distance_km=1200.0, medium_type="FIBER")
        mw_link = NetworkLinkSpec("NY4", "AURORA", distance_km=1200.0, medium_type="MICROWAVE")

        res_fiber = self.evaluator.calculate_latency_budget(fiber_link)
        res_mw = self.evaluator.calculate_latency_budget(mw_link)

        self.assertEqual(res_fiber.propagation_delay_us, 6000.0)
        self.assertAlmostEqual(res_mw.propagation_delay_us, 4003.970, places=3)
        self.assertLess(res_mw.total_one_way_us, res_fiber.total_one_way_us)

    def test_cross_connect_delay_is_included_in_total(self):
        # Regression: cross-connect delay is documented in the workflow and must
        # appear in the one-way total, not be silently dropped.
        link = NetworkLinkSpec(
            source="NY4", destination="NASDAQ",
            distance_km=1.0, medium_type="FIBER",
            num_switches=1, switch_latency_us=0.350,
            cross_connect_us=0.25,
        )
        res = self.evaluator.calculate_latency_budget(link)
        self.assertEqual(res.cross_connect_delay_us, 0.25)
        self.assertEqual(res.total_one_way_us, 5.0 + 0.35 + 0.25)

    def test_route_circuity_factor_inflates_straight_line_distance(self):
        # 1000 km straight line, fiber right-of-way 25% longer -> 1250 km route.
        link = NetworkLinkSpec(
            source="AURORA", destination="NY4",
            distance_km=1000.0, medium_type="FIBER",
            num_switches=0, route_circuity_factor=1.25,
        )
        res = self.evaluator.calculate_latency_budget(link)
        self.assertEqual(res.route_distance_km, 1250.0)
        self.assertEqual(res.propagation_delay_us, 6250.0)

    def test_carrier_measured_propagation_override(self):
        link = NetworkLinkSpec(
            source="LD4", destination="LSE",
            distance_km=10.0, medium_type="FIBER",
            num_switches=0,
            propagation_us_per_km=PROPAGATION_PHYSICS_FIBER_US_KM,
        )
        res = self.evaluator.calculate_latency_budget(link)
        self.assertAlmostEqual(res.propagation_delay_us, 48.974, places=3)
        self.assertLess(res.propagation_delay_us, 10.0 * PROPAGATION_SPEED_FIBER_US_KM)

    def test_layer1_switch_default_is_available_and_cheaper(self):
        self.assertLess(LAYER1_SWITCH_LATENCY_US, 0.350)
        link = NetworkLinkSpec(
            source="NY4", destination="NASDAQ", distance_km=0.0,
            medium_type="FIBER", num_switches=3,
            switch_latency_us=LAYER1_SWITCH_LATENCY_US,
        )
        res = self.evaluator.calculate_latency_budget(link)
        self.assertAlmostEqual(res.switch_delay_us, 0.012, places=6)

    def test_asymmetric_round_trip_budget(self):
        # Microwave outbound, fiber return: RTT is NOT 2x either leg.
        out = NetworkLinkSpec("AURORA", "NY4", distance_km=1200.0,
                              medium_type="MICROWAVE", num_switches=0)
        back = NetworkLinkSpec("NY4", "AURORA", distance_km=1200.0,
                               medium_type="FIBER", num_switches=0)
        rtt = ColocationTopologyEvaluator.calculate_round_trip_budget(out, back)
        self.assertAlmostEqual(rtt, 4003.970 + 6000.0, places=3)

        symmetric_mw = self.evaluator.calculate_latency_budget(out).total_rtt_us
        self.assertNotAlmostEqual(rtt, symmetric_mw, places=1)


class TestLinkValidation(unittest.TestCase):

    def _link(self, **overrides):
        kwargs = dict(source="A", destination="B", distance_km=10.0, medium_type="FIBER")
        kwargs.update(overrides)
        return NetworkLinkSpec(**kwargs)

    def test_negative_distance_rejected(self):
        with self.assertRaises(ValueError):
            self._link(distance_km=-1.0)

    def test_non_finite_distance_rejected(self):
        for bad in (float("nan"), float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self._link(distance_km=bad)

    def test_unsupported_medium_rejected(self):
        with self.assertRaises(ValueError):
            self._link(medium_type="CARRIER_PIGEON")

    def test_medium_type_is_case_insensitive(self):
        res = ColocationTopologyEvaluator.calculate_latency_budget(
            self._link(medium_type="fiber", num_switches=0)
        )
        self.assertEqual(res.propagation_delay_us, 50.0)

    def test_negative_switch_count_rejected(self):
        with self.assertRaises(ValueError):
            self._link(num_switches=-1)

    def test_non_integer_switch_count_rejected(self):
        with self.assertRaises(ValueError):
            self._link(num_switches=2.5)

    def test_negative_switch_latency_rejected(self):
        with self.assertRaises(ValueError):
            self._link(switch_latency_us=-0.1)

    def test_negative_cross_connect_delay_rejected(self):
        with self.assertRaises(ValueError):
            self._link(cross_connect_us=-0.1)

    def test_circuity_factor_below_one_rejected(self):
        with self.assertRaises(ValueError):
            self._link(route_circuity_factor=0.9)

    def test_superluminal_propagation_override_rejected(self):
        # 3.0 us/km would be ~333,333 km/s, faster than light in vacuum.
        with self.assertRaises(ValueError):
            self._link(propagation_us_per_km=3.0)

    def test_empty_endpoint_rejected(self):
        with self.assertRaises(ValueError):
            self._link(source="   ")

    def test_numeric_strings_are_coerced_not_left_as_text(self):
        # A spec decoded from JSON may arrive with string numbers; they must not
        # pass validation only to raise TypeError inside the arithmetic.
        link = self._link(distance_km="100", num_switches=0)
        self.assertIsInstance(link.distance_km, float)
        res = ColocationTopologyEvaluator.calculate_latency_budget(link)
        self.assertEqual(res.propagation_delay_us, 500.0)

        fac = FacilitySpec("Str", "STR", "1000", 2.0, "100", 0.0)
        self.assertEqual(ColocationTopologyEvaluator.calculate_facility_tco(fac), 1200.0)

    def test_overflow_to_infinity_is_rejected(self):
        with self.assertRaises(ValueError):
            ColocationTopologyEvaluator.calculate_latency_budget(
                self._link(distance_km=1e308, route_circuity_factor=1e10)
            )


class TestFacilityTco(unittest.TestCase):

    def setUp(self):
        self.evaluator = ColocationTopologyEvaluator()

    def test_tco_calculation(self):
        fac = FacilitySpec(
            name="Equinix NY4", location_code="NY4",
            rack_cost_mrc=2500.0, power_kw=5.0,
            power_cost_per_kw=200.0, cross_connect_mrc=500.0,
        )
        # Power = 5.0 * 200 = 1000. TCO = 2500 + 1000 + 500 = 4000.0
        self.assertEqual(self.evaluator.calculate_facility_tco(fac), 4000.0)

    def test_zero_cost_facility_is_allowed(self):
        fac = FacilitySpec("Owned Cage", "OWN", 0.0, 0.0, 0.0, 0.0)
        self.assertEqual(self.evaluator.calculate_facility_tco(fac), 0.0)

    def test_negative_cost_rejected(self):
        with self.assertRaises(ValueError):
            FacilitySpec("Bad", "BAD", -1.0, 5.0, 200.0, 500.0)

    def test_non_finite_cost_rejected(self):
        with self.assertRaises(ValueError):
            FacilitySpec("Bad", "BAD", 100.0, 5.0, float("nan"), 500.0)


class TestColocationScoring(unittest.TestCase):
    """Expected scores below are derived by hand from the documented formula
    Score = w_lat * NormLatency + w_cost * NormCost with min-max normalization.
    """

    def setUp(self):
        self.evaluator = ColocationTopologyEvaluator()
        # TCO: AURORA 4000, CARTERET 3000, NY4 6000
        self.aurora = FacilitySpec("CME Aurora", "AURORA", 3000.0, 5.0, 100.0, 500.0)
        self.carteret = FacilitySpec("Carteret NJ", "CARTERET", 2000.0, 5.0, 100.0, 500.0)
        self.ny4 = FacilitySpec("Equinix NY4", "NY4", 5000.0, 5.0, 100.0, 500.0)
        # Zero switches keeps the arithmetic exact: RTT = 2 * dist * 5 us/km
        # AURORA 10 km -> 100 us ; CARTERET 100 km -> 1000 us ; NY4 55 km -> 550 us
        self.links = [
            NetworkLinkSpec("AURORA", "CME_ME", 10.0, "FIBER", num_switches=0),
            NetworkLinkSpec("CARTERET", "NASDAQ_ME", 100.0, "FIBER", num_switches=0),
            NetworkLinkSpec("NY4", "NYSE_ME", 55.0, "FIBER", num_switches=0),
        ]
        self.facilities = [self.aurora, self.carteret, self.ny4]

    def test_average_rtt_per_facility(self):
        by_code = {s.code: s for s in
                   self.evaluator.evaluate_colocation_setup(self.facilities, self.links)}
        self.assertEqual(by_code["AURORA"].avg_rtt_us, 100.0)
        self.assertEqual(by_code["CARTERET"].avg_rtt_us, 1000.0)
        self.assertEqual(by_code["NY4"].avg_rtt_us, 550.0)
        self.assertEqual(by_code["AURORA"].num_links, 1)

    def test_composite_score_and_ranking_default_weights(self):
        # NormLatency: AURORA 0.0, NY4 0.5, CARTERET 1.0
        # NormCost:    CARTERET 0.0, AURORA 1/3, NY4 1.0
        # Score(0.7/0.3): AURORA 0.10, NY4 0.65, CARTERET 0.70
        results = self.evaluator.evaluate_colocation_setup(self.facilities, self.links)
        self.assertEqual([r.code for r in results], ["AURORA", "NY4", "CARTERET"])
        self.assertEqual([r.rank for r in results], [1, 2, 3])

        by_code = {r.code: r for r in results}
        self.assertAlmostEqual(by_code["AURORA"].normalized_latency, 0.0, places=6)
        self.assertAlmostEqual(by_code["NY4"].normalized_latency, 0.5, places=6)
        self.assertAlmostEqual(by_code["CARTERET"].normalized_latency, 1.0, places=6)
        self.assertAlmostEqual(by_code["AURORA"].normalized_cost, 1.0 / 3.0, places=6)
        self.assertAlmostEqual(by_code["AURORA"].composite_score, 0.10, places=6)
        self.assertAlmostEqual(by_code["NY4"].composite_score, 0.65, places=6)
        self.assertAlmostEqual(by_code["CARTERET"].composite_score, 0.70, places=6)

    def test_weights_change_the_ranking(self):
        cost_only = self.evaluator.evaluate_colocation_setup(
            self.facilities, self.links, latency_weight=0.0, cost_weight=1.0
        )
        self.assertEqual([r.code for r in cost_only], ["CARTERET", "AURORA", "NY4"])

        latency_only = self.evaluator.evaluate_colocation_setup(
            self.facilities, self.links, latency_weight=1.0, cost_weight=0.0
        )
        self.assertEqual([r.code for r in latency_only], ["AURORA", "NY4", "CARTERET"])
        self.assertAlmostEqual(latency_only[0].composite_score, 0.0, places=6)

    def test_weights_are_rescaled_not_taken_literally(self):
        base = self.evaluator.evaluate_colocation_setup(self.facilities, self.links)
        scaled = self.evaluator.evaluate_colocation_setup(
            self.facilities, self.links, latency_weight=7.0, cost_weight=3.0
        )
        for a, b in zip(base, scaled):
            self.assertEqual(a.code, b.code)
            self.assertAlmostEqual(a.composite_score, b.composite_score, places=9)

    def test_facility_without_links_is_unranked_not_scored_as_zero_latency(self):
        # Regression: a facility with no modelled link previously reported
        # avg_rtt_us = 0.0, which is the minimum and would rank it first.
        ld4 = FacilitySpec("Equinix LD4", "LD4", 1000.0, 1.0, 100.0, 100.0)
        with self.assertLogs(
            "co_location_provider_selection_and_network_topology", level="WARNING"
        ) as captured:
            results = self.evaluator.evaluate_colocation_setup(
                self.facilities + [ld4], self.links
            )
        self.assertIn("no modelled network link", "".join(captured.output))

        orphan = results[-1]
        self.assertEqual(orphan.code, "LD4")
        self.assertIsNone(orphan.avg_rtt_us)
        self.assertIsNone(orphan.composite_score)
        self.assertIsNone(orphan.rank)
        self.assertEqual(orphan.num_links, 0)
        # Its (lowest) TCO must not distort the ranked candidates either.
        self.assertEqual([r.code for r in results[:3]], ["AURORA", "NY4", "CARTERET"])

    def test_tied_scores_share_a_rank(self):
        twin_a = FacilitySpec("Twin A", "TWIN_A", 2500.0, 5.0, 100.0, 0.0)
        twin_b = FacilitySpec("Twin B", "TWIN_B", 2500.0, 5.0, 100.0, 0.0)
        worse = FacilitySpec("Worse", "WORSE", 4500.0, 5.0, 100.0, 0.0)
        links = [
            NetworkLinkSpec("TWIN_A", "ME", 100.0, "FIBER", num_switches=0),
            NetworkLinkSpec("TWIN_B", "ME", 100.0, "FIBER", num_switches=0),
            NetworkLinkSpec("WORSE", "ME", 200.0, "FIBER", num_switches=0),
        ]
        results = self.evaluator.evaluate_colocation_setup([twin_a, twin_b, worse], links)
        self.assertEqual([r.rank for r in results], [1, 1, 3])
        self.assertEqual([r.facility for r in results], ["Twin A", "Twin B", "Worse"])

    def test_single_candidate_normalizes_without_dividing_by_zero(self):
        results = self.evaluator.evaluate_colocation_setup([self.ny4], self.links)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].rank, 1)
        self.assertEqual(results[0].composite_score, 0.0)
        self.assertTrue(math.isfinite(results[0].composite_score))

    def test_empty_facility_list_returns_empty(self):
        self.assertEqual(self.evaluator.evaluate_colocation_setup([], self.links), [])

    def test_duplicate_location_codes_rejected(self):
        clone = FacilitySpec("NY4 Clone", "NY4", 1.0, 1.0, 1.0, 1.0)
        with self.assertRaises(ValueError):
            self.evaluator.evaluate_colocation_setup(self.facilities + [clone], self.links)

    def test_zero_total_weight_rejected(self):
        with self.assertRaises(ValueError):
            self.evaluator.evaluate_colocation_setup(
                self.facilities, self.links, latency_weight=0.0, cost_weight=0.0
            )

    def test_negative_weight_rejected(self):
        with self.assertRaises(ValueError):
            self.evaluator.evaluate_colocation_setup(
                self.facilities, self.links, latency_weight=-0.5, cost_weight=1.5
            )

    def test_non_finite_weight_rejected(self):
        with self.assertRaises(ValueError):
            self.evaluator.evaluate_colocation_setup(
                self.facilities, self.links, latency_weight=float("nan"), cost_weight=1.0
            )

    def test_results_are_deterministic_across_input_order(self):
        forward = self.evaluator.evaluate_colocation_setup(self.facilities, self.links)
        reversed_inputs = self.evaluator.evaluate_colocation_setup(
            list(reversed(self.facilities)), list(reversed(self.links))
        )
        self.assertEqual([r.code for r in forward], [r.code for r in reversed_inputs])
        self.assertEqual(
            [r.composite_score for r in forward],
            [r.composite_score for r in reversed_inputs],
        )


if __name__ == '__main__':
    unittest.main()
