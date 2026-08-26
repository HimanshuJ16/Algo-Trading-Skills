import logging
import unittest

from microwave_vs_fiber_network_links_for_cross_market_latency import (
    ALL_STATUSES,
    DEFAULT_DEGRADING_WEATHER_STATES,
    GROUP_INDEX_SMF28_1550NM,
    LINK_TYPE_FIBER,
    LINK_TYPE_MICROWAVE,
    NetworkLinkArbitratorEngine,
    NetworkLinkConfig,
    NetworkLinkError,
    NetworkLinkReport,
    REFRACTIVE_INDEX_AIR_RADIO,
    SPEED_OF_LIGHT_AIR_KM_S,
    SPEED_OF_LIGHT_FIBER_KM_S,
    SPEED_OF_LIGHT_VACUUM_KM_S,
    STATUS_FAILOVER_RAIN_FADE,
    STATUS_FAILOVER_TELEMETRY_UNUSABLE,
    STATUS_HOLD_FIBER_HYSTERESIS,
    STATUS_MICROWAVE_PRIMARY,
    STATUS_NO_HEALTHY_LINK,
    WeatherLinkTelemetry,
    serialization_delay_us,
)

# Corridor reference geometry, from the sources cited in references/standards.md:
#   CME Aurora -> Nasdaq Carteret geodesic  = 1,176 km (Bhattacherjee et al., IMC '20)
#   Spread Networks Chicago-NY fiber        = 1,328 km of glass incl. slack coils
CORRIDOR = "CHICAGO_CME_TO_NJ_NASDAQ"
GEODESIC_KM = 1176.0
FIBER_ROUTE_KM = 1328.0


def setUpModule():
    """Silence the engine's routing logs so the suite output stays readable."""
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def clear_telemetry(link_id: str = "MW_CHI_NJ_01", **overrides) -> WeatherLinkTelemetry:
    kwargs = dict(
        current_weather="CLEAR",
        packet_loss_pct=0.05,
        signal_to_noise_ratio_db=35.0,
    )
    kwargs.update(overrides)
    return WeatherLinkTelemetry(link_id, **kwargs)


class TestPropagationConstants(unittest.TestCase):
    """Constants are derived from cited indices, not from round numbers."""

    def test_no_medium_is_superluminal(self):
        self.assertLess(SPEED_OF_LIGHT_AIR_KM_S, SPEED_OF_LIGHT_VACUUM_KM_S)
        self.assertLess(SPEED_OF_LIGHT_FIBER_KM_S, SPEED_OF_LIGHT_AIR_KM_S)

    def test_air_constant_matches_itu_r_p453_refractivity(self):
        # N_0 = 315 N-units -> n = 1.000315 -> c/n = 299,698.05 km/s.
        self.assertAlmostEqual(REFRACTIVE_INDEX_AIR_RADIO, 1.000315, places=9)
        self.assertAlmostEqual(SPEED_OF_LIGHT_AIR_KM_S, 299_698.053, places=2)
        # 3.336692 us/km, independently derived as 1e6 / (c/n).
        self.assertAlmostEqual(1e6 / SPEED_OF_LIGHT_AIR_KM_S, 3.336692, places=6)

    def test_fiber_constant_matches_smf28_group_index(self):
        # Corning SMF-28 effective group index at 1550 nm = 1.4682.
        self.assertAlmostEqual(GROUP_INDEX_SMF28_1550NM, 1.4682, places=6)
        self.assertAlmostEqual(SPEED_OF_LIGHT_FIBER_KM_S, 204_190.477, places=2)
        self.assertAlmostEqual(1e6 / SPEED_OF_LIGHT_FIBER_KM_S, 4.897388, places=6)

    def test_fiber_default_is_the_conservative_choice_for_advantage_claims(self):
        # G.655 NZ-DSF (n = 1.470) is slower than the G.652 default, so defaulting
        # to G.652 understates -- never overstates -- the microwave advantage.
        nz_dsf_km_s = SPEED_OF_LIGHT_VACUUM_KM_S / 1.470
        self.assertLess(nz_dsf_km_s, SPEED_OF_LIGHT_FIBER_KM_S)


class TestSerializationDelay(unittest.TestCase):

    def test_known_frame_sizes(self):
        # 1,500 bytes = 12,000 bits. At 100 Mbps that is 120 us; at 1 Gbps, 12 us.
        self.assertAlmostEqual(serialization_delay_us(1500, 100.0), 120.0, places=9)
        self.assertAlmostEqual(serialization_delay_us(1500, 1000.0), 12.0, places=9)

    def test_narrow_radio_channel_dominates_the_competitive_margin(self):
        # Competing Chicago-NJ radio networks are separated by 0.4-8.1 us. A single
        # 1,500-byte frame on a 100 Mbps radio channel costs an order of magnitude more.
        self.assertGreater(serialization_delay_us(1500, 100.0), 10 * 8.1)

    def test_zero_bandwidth_rejected(self):
        with self.assertRaises(NetworkLinkError):
            serialization_delay_us(1500, 0.0)

    def test_negative_payload_rejected(self):
        with self.assertRaises(NetworkLinkError):
            serialization_delay_us(-1, 100.0)


class TestNetworkLinkConfigValidation(unittest.TestCase):

    def _cfg(self, **overrides) -> NetworkLinkConfig:
        kwargs = dict(
            link_id="MW_CHI_NJ_01",
            link_type=LINK_TYPE_MICROWAVE,
            corridor_name=CORRIDOR,
            distance_km=GEODESIC_KM,
        )
        kwargs.update(overrides)
        return NetworkLinkConfig(**kwargs)

    def test_nan_distance_rejected(self):
        # A NaN distance yields a NaN RTT, and every NaN comparison is False, so
        # an unchecked NaN renders as a clean primary route.
        with self.assertRaises(NetworkLinkError):
            self._cfg(distance_km=float("nan"))

    def test_infinite_distance_rejected(self):
        with self.assertRaises(NetworkLinkError):
            self._cfg(distance_km=float("inf"))

    def test_non_positive_distance_rejected(self):
        for bad in (0.0, -1.0):
            with self.assertRaises(NetworkLinkError):
                self._cfg(distance_km=bad)

    def test_implausible_distance_rejected_as_unit_error(self):
        # 1,176,000 -- metres supplied where kilometres were meant.
        with self.assertRaises(NetworkLinkError):
            self._cfg(distance_km=1_176_000.0)

    def test_unknown_link_type_rejected(self):
        with self.assertRaises(NetworkLinkError):
            self._cfg(link_type="SATELLITE")

    def test_link_type_is_normalised(self):
        self.assertEqual(self._cfg(link_type="microwave").link_type, LINK_TYPE_MICROWAVE)

    def test_superluminal_propagation_speed_rejected(self):
        # The widespread "300,000 km/s" shorthand is faster than light.
        with self.assertRaises(NetworkLinkError):
            self._cfg(propagation_speed_km_s=300_000.0)

    def test_negative_repeater_count_rejected(self):
        with self.assertRaises(NetworkLinkError):
            self._cfg(repeater_count=-1)

    def test_boolean_repeater_count_rejected(self):
        with self.assertRaises(NetworkLinkError):
            self._cfg(repeater_count=True)

    def test_empty_link_id_rejected(self):
        with self.assertRaises(NetworkLinkError):
            self._cfg(link_id="   ")

    def test_defaults_are_medium_specific(self):
        self.assertAlmostEqual(
            self._cfg().propagation_speed_km_s, SPEED_OF_LIGHT_AIR_KM_S, places=6
        )
        fiber = self._cfg(link_id="FB", link_type=LINK_TYPE_FIBER)
        self.assertAlmostEqual(fiber.propagation_speed_km_s, SPEED_OF_LIGHT_FIBER_KM_S, places=6)


class TestLatencyDecomposition(unittest.TestCase):
    """Latency figures checked against independently derived arithmetic."""

    def test_propagation_only_microwave_one_way(self):
        cfg = NetworkLinkConfig("MW", LINK_TYPE_MICROWAVE, CORRIDOR, distance_km=GEODESIC_KM)
        # 1176 km / 299,698.053 km/s = 3.9239494 ms.
        self.assertAlmostEqual(cfg.one_way_latency_ms(), 3.9239494, places=6)
        self.assertAlmostEqual(cfg.round_trip_latency_ms(), 7.8478988, places=6)

    def test_propagation_only_fiber_one_way(self):
        cfg = NetworkLinkConfig("FB", LINK_TYPE_FIBER, CORRIDOR, distance_km=FIBER_ROUTE_KM)
        # 1328 km / 204,190.477 km/s = 6.5037313 ms.
        self.assertAlmostEqual(cfg.one_way_latency_ms(), 6.5037313, places=6)
        self.assertAlmostEqual(cfg.round_trip_latency_ms(), 13.0074626, places=6)

    def test_propagation_only_figure_is_below_the_published_measured_figure(self):
        # McKay Brothers published 3.982 ms one-way Aurora->Carteret (2016). The
        # propagation-only model must land *below* it: it is a floor, not an estimate.
        cfg = NetworkLinkConfig("MW", LINK_TYPE_MICROWAVE, CORRIDOR, distance_km=GEODESIC_KM)
        self.assertLess(cfg.one_way_latency_ms(), 3.982)

    def test_equipment_terms_are_added_to_propagation(self):
        cfg = NetworkLinkConfig(
            "MW",
            LINK_TYPE_MICROWAVE,
            CORRIDOR,
            distance_km=GEODESIC_KM,
            repeater_count=22,
            per_repeater_latency_us=2.5,
            fiber_tail_km=20.0,
        )
        # 22 x 2.5 us = 55 us of radios; 20 km of fiber tail = 97.948 us.
        self.assertAlmostEqual(cfg.equipment_one_way_ms(), 0.055, places=9)
        self.assertAlmostEqual(cfg.propagation_one_way_ms(), 3.9239494 + 0.0979478, places=6)
        self.assertAlmostEqual(cfg.one_way_latency_ms(), 4.0768972, places=6)

    def test_serialization_term_included_only_when_payload_given(self):
        base = NetworkLinkConfig(
            "MW", LINK_TYPE_MICROWAVE, CORRIDOR, distance_km=GEODESIC_KM, bandwidth_mbps=100.0
        )
        self.assertEqual(base.equipment_one_way_ms(), 0.0)
        with_payload = NetworkLinkConfig(
            "MW",
            LINK_TYPE_MICROWAVE,
            CORRIDOR,
            distance_km=GEODESIC_KM,
            bandwidth_mbps=100.0,
            payload_bytes=1500,
        )
        self.assertAlmostEqual(with_payload.equipment_one_way_ms(), 0.120, places=9)

    def test_lower_bound_flag_tracks_the_equipment_model(self):
        bare = NetworkLinkConfig("MW", LINK_TYPE_MICROWAVE, CORRIDOR, distance_km=GEODESIC_KM)
        self.assertFalse(bare.has_equipment_model)
        modelled = NetworkLinkConfig(
            "MW",
            LINK_TYPE_MICROWAVE,
            CORRIDOR,
            distance_km=GEODESIC_KM,
            repeater_count=22,
            per_repeater_latency_us=2.5,
        )
        self.assertTrue(modelled.has_equipment_model)

    def test_repeater_count_without_a_per_repeater_figure_is_not_a_model(self):
        cfg = NetworkLinkConfig(
            "MW", LINK_TYPE_MICROWAVE, CORRIDOR, distance_km=GEODESIC_KM, repeater_count=22
        )
        self.assertFalse(cfg.has_equipment_model)

    def test_fiber_type_choice_moves_the_corridor_by_microseconds(self):
        # G.652 (n=1.4682) vs G.655 NZ-DSF (n=1.470) over 1,328 km: ~8 us one-way,
        # larger than the 0.4-8.1 us that separates competing radio networks.
        g652 = NetworkLinkConfig("FB", LINK_TYPE_FIBER, CORRIDOR, distance_km=FIBER_ROUTE_KM)
        g655 = NetworkLinkConfig(
            "FB",
            LINK_TYPE_FIBER,
            CORRIDOR,
            distance_km=FIBER_ROUTE_KM,
            propagation_speed_km_s=SPEED_OF_LIGHT_VACUUM_KM_S / 1.470,
        )
        delta_us = (g655.one_way_latency_ms() - g652.one_way_latency_ms()) * 1000.0
        self.assertAlmostEqual(delta_us, 7.97, places=1)


class TestArbitration(unittest.TestCase):

    def setUp(self):
        self.engine = NetworkLinkArbitratorEngine(max_packet_loss_threshold_pct=1.0)
        self.micro_cfg = NetworkLinkConfig(
            "MW_CHI_NJ_01",
            LINK_TYPE_MICROWAVE,
            CORRIDOR,
            distance_km=GEODESIC_KM,
            frequency_ghz=11.0,
        )
        self.fiber_cfg = NetworkLinkConfig(
            "FB_CHI_NJ_01", LINK_TYPE_FIBER, CORRIDOR, distance_km=FIBER_ROUTE_KM
        )

    def arbitrate(self, telemetry=None, **kwargs) -> NetworkLinkReport:
        return self.engine.arbitrate_cross_market_links(
            self.micro_cfg, self.fiber_cfg, telemetry or clear_telemetry(), **kwargs
        )

    def test_microwave_primary_route_clear_weather(self):
        report = self.arbitrate()
        self.assertEqual(report.status, STATUS_MICROWAVE_PRIMARY)
        self.assertFalse(report.is_failover_active)
        self.assertEqual(report.selected_link_type, LINK_TYPE_MICROWAVE)
        self.assertEqual(report.selected_routing_link_id, "MW_CHI_NJ_01")
        self.assertEqual(report.degradation_reasons, [])

    def test_advantage_matches_independently_derived_arithmetic(self):
        report = self.arbitrate()
        # RTT: microwave 7.8478988 ms, fiber 13.0074626 ms -> saving 5.1595638 ms.
        self.assertAlmostEqual(report.microwave_rtt_ms, 7.847899, places=5)
        self.assertAlmostEqual(report.fiber_rtt_ms, 13.007463, places=5)
        self.assertAlmostEqual(report.latency_savings_ms, 5.159564, places=5)
        # 5.1595638 / 13.0074626 = 39.666%.
        self.assertAlmostEqual(report.latency_advantage_pct, 39.67, places=2)

    def test_equipment_terms_erode_the_headline_advantage(self):
        # Regression against the propagation-only model: adding 22 towers at 2.5 us
        # and a 20 km fiber tail costs 306 us of the claimed round-trip saving.
        bare = self.arbitrate()
        self.micro_cfg = NetworkLinkConfig(
            "MW_CHI_NJ_01",
            LINK_TYPE_MICROWAVE,
            CORRIDOR,
            distance_km=GEODESIC_KM,
            repeater_count=22,
            per_repeater_latency_us=2.5,
            fiber_tail_km=20.0,
            frequency_ghz=11.0,
        )
        modelled = self.arbitrate()
        self.assertTrue(bare.is_microwave_lower_bound_only)
        self.assertFalse(modelled.is_microwave_lower_bound_only)
        self.assertAlmostEqual(
            (bare.latency_savings_ms - modelled.latency_savings_ms) * 1000.0, 305.9, places=1
        )
        self.assertAlmostEqual(modelled.latency_advantage_pct, 37.31, places=2)
        self.assertIn("LOWER BOUND", bare.audit_notes)
        self.assertNotIn("LOWER BOUND", modelled.audit_notes)

    def test_report_exposes_the_decomposition(self):
        report = self.arbitrate()
        self.assertAlmostEqual(
            report.microwave_one_way_ms,
            report.microwave_propagation_one_way_ms + report.microwave_equipment_one_way_ms,
            places=9,
        )
        self.assertAlmostEqual(report.microwave_rtt_ms, 2 * report.microwave_one_way_ms, places=5)

    def test_fiber_failover_during_heavy_rain(self):
        report = self.arbitrate(clear_telemetry(current_weather="HEAVY_RAIN", packet_loss_pct=2.5))
        self.assertEqual(report.status, STATUS_FAILOVER_RAIN_FADE)
        self.assertTrue(report.is_failover_active)
        self.assertEqual(report.selected_link_type, LINK_TYPE_FIBER)
        self.assertEqual(report.selected_routing_link_id, "FB_CHI_NJ_01")
        self.assertEqual(len(report.degradation_reasons), 2)

    def test_storm_alone_triggers_failover(self):
        report = self.arbitrate(clear_telemetry(current_weather="STORM", packet_loss_pct=0.0))
        self.assertEqual(report.status, STATUS_FAILOVER_RAIN_FADE)

    def test_packet_loss_alone_triggers_failover(self):
        report = self.arbitrate(clear_telemetry(packet_loss_pct=1.5))
        self.assertEqual(report.status, STATUS_FAILOVER_RAIN_FADE)

    def test_packet_loss_threshold_is_exclusive_at_the_boundary(self):
        # 1.0% is documented as "> 1.0% fails over", so exactly 1.0% must not.
        at_threshold = self.arbitrate(clear_telemetry(packet_loss_pct=1.0))
        self.assertEqual(at_threshold.status, STATUS_MICROWAVE_PRIMARY)
        just_over = self.arbitrate(clear_telemetry(packet_loss_pct=1.0000001))
        self.assertEqual(just_over.status, STATUS_FAILOVER_RAIN_FADE)

    def test_marginal_breach_reason_does_not_render_as_a_contradiction(self):
        # Regression: fixed 3-dp formatting rendered a 1.0000001% breach as
        # "PACKET_LOSS:1.000% > 1.000%", which reads as a contradiction on the
        # pager. Significant-digit formatting must keep the two values distinct.
        report = self.arbitrate(clear_telemetry(packet_loss_pct=1.0000001))
        reason = next(r for r in report.degradation_reasons if "PACKET_LOSS" in r)
        observed, _, threshold = reason.partition(" > ")
        self.assertNotEqual(observed.split(":")[1], threshold)

    def test_light_rain_does_not_trigger_failover(self):
        report = self.arbitrate(clear_telemetry(current_weather="LIGHT_RAIN"))
        self.assertEqual(report.status, STATUS_MICROWAVE_PRIMARY)


class TestFailClosedBehaviour(unittest.TestCase):
    """Unknown or untrusted state must never resolve to the fragile link."""

    def setUp(self):
        self.engine = NetworkLinkArbitratorEngine()
        self.micro_cfg = NetworkLinkConfig(
            "MW", LINK_TYPE_MICROWAVE, CORRIDOR, distance_km=GEODESIC_KM
        )
        self.fiber_cfg = NetworkLinkConfig(
            "FB", LINK_TYPE_FIBER, CORRIDOR, distance_km=FIBER_ROUTE_KM
        )

    def arbitrate(self, telemetry, **kwargs):
        return self.engine.arbitrate_cross_market_links(
            self.micro_cfg, self.fiber_cfg, telemetry, **kwargs
        )

    def test_unknown_weather_string_fails_closed_to_fiber(self):
        # Regression: a typo or a vendor enum change previously fell through to
        # ROUTE_MICROWAVE_PRIMARY, routing live traffic over an unassessed link.
        report = self.arbitrate(clear_telemetry("MW", current_weather="HEAVYRAIN"))
        self.assertEqual(report.status, STATUS_FAILOVER_TELEMETRY_UNUSABLE)
        self.assertEqual(report.selected_link_type, LINK_TYPE_FIBER)
        self.assertTrue(report.is_failover_active)

    def test_empty_weather_string_fails_closed(self):
        report = self.arbitrate(clear_telemetry("MW", current_weather=""))
        self.assertEqual(report.status, STATUS_FAILOVER_TELEMETRY_UNUSABLE)

    def test_stale_telemetry_fails_closed_when_a_max_age_is_configured(self):
        engine = NetworkLinkArbitratorEngine(max_telemetry_age_s=30.0)
        fresh = engine.arbitrate_cross_market_links(
            self.micro_cfg,
            self.fiber_cfg,
            clear_telemetry("MW", telemetry_age_s=5.0),
        )
        self.assertEqual(fresh.status, STATUS_MICROWAVE_PRIMARY)
        stale = engine.arbitrate_cross_market_links(
            self.micro_cfg,
            self.fiber_cfg,
            clear_telemetry("MW", telemetry_age_s=600.0),
        )
        self.assertEqual(stale.status, STATUS_FAILOVER_TELEMETRY_UNUSABLE)

    def test_staleness_unchecked_when_no_max_age_configured(self):
        report = self.arbitrate(clear_telemetry("MW", telemetry_age_s=86_400.0))
        self.assertEqual(report.status, STATUS_MICROWAVE_PRIMARY)

    def test_both_links_degraded_yields_no_healthy_link(self):
        report = self.arbitrate(
            clear_telemetry("MW", current_weather="STORM"),
            fiber_telemetry=WeatherLinkTelemetry("FB", "CLEAR", 8.0, 30.0),
        )
        self.assertEqual(report.status, STATUS_NO_HEALTHY_LINK)
        self.assertEqual(report.selected_link_type, "NONE")
        self.assertEqual(report.selected_routing_link_id, "")
        self.assertTrue(report.is_failover_active)

    def test_unhealthy_fiber_alone_does_not_block_a_healthy_microwave(self):
        report = self.arbitrate(
            clear_telemetry("MW"),
            fiber_telemetry=WeatherLinkTelemetry("FB", "CLEAR", 9.0, 30.0),
        )
        self.assertEqual(report.status, STATUS_MICROWAVE_PRIMARY)

    def test_snr_below_margin_triggers_failover_when_configured(self):
        engine = NetworkLinkArbitratorEngine(min_snr_db=20.0)
        report = engine.arbitrate_cross_market_links(
            self.micro_cfg,
            self.fiber_cfg,
            clear_telemetry("MW", signal_to_noise_ratio_db=12.0),
        )
        self.assertEqual(report.status, STATUS_FAILOVER_RAIN_FADE)
        self.assertTrue(any("SNR_BELOW_MARGIN" in r for r in report.degradation_reasons))

    def test_snr_ignored_when_no_margin_configured(self):
        report = self.arbitrate(clear_telemetry("MW", signal_to_noise_ratio_db=-5.0))
        self.assertEqual(report.status, STATUS_MICROWAVE_PRIMARY)

    def test_mismatched_telemetry_link_id_rejected(self):
        with self.assertRaises(NetworkLinkError):
            self.arbitrate(clear_telemetry("SOME_OTHER_LINK"))

    def test_unrecognised_previous_status_rejected(self):
        # An unrecognised value would read as "not on fiber" and silently skip
        # recovery hysteresis, snapping the route straight back to microwave.
        with self.assertRaises(NetworkLinkError):
            self.arbitrate(clear_telemetry("MW"), previous_status="ON_FIBER")

    def test_every_returned_status_is_declared(self):
        self.assertIn(STATUS_MICROWAVE_PRIMARY, ALL_STATUSES)
        self.assertEqual(len(ALL_STATUSES), 5)

    def test_shared_link_id_between_primary_and_backup_rejected(self):
        clash = NetworkLinkConfig("MW", LINK_TYPE_FIBER, CORRIDOR, distance_km=FIBER_ROUTE_KM)
        with self.assertRaises(NetworkLinkError):
            self.engine.arbitrate_cross_market_links(
                self.micro_cfg, clash, clear_telemetry("MW")
            )

    def test_swapped_link_configs_rejected(self):
        # Passing the fiber config as the microwave leg would price 1,328 km of
        # glass at the speed of light in air.
        with self.assertRaises(NetworkLinkError):
            self.engine.arbitrate_cross_market_links(
                self.fiber_cfg, self.fiber_cfg, clear_telemetry("FB")
            )


class TestRecoveryHysteresis(unittest.TestCase):

    def setUp(self):
        self.engine = NetworkLinkArbitratorEngine(
            max_packet_loss_threshold_pct=1.0,
            recovery_packet_loss_pct=0.1,
            recovery_dwell_evaluations=3,
        )
        self.micro_cfg = NetworkLinkConfig(
            "MW", LINK_TYPE_MICROWAVE, CORRIDOR, distance_km=GEODESIC_KM
        )
        self.fiber_cfg = NetworkLinkConfig(
            "FB", LINK_TYPE_FIBER, CORRIDOR, distance_km=FIBER_ROUTE_KM
        )

    def arbitrate(self, telemetry, **kwargs):
        return self.engine.arbitrate_cross_market_links(
            self.micro_cfg, self.fiber_cfg, telemetry, **kwargs
        )

    def test_recovery_requires_the_full_dwell(self):
        status = STATUS_FAILOVER_RAIN_FADE
        clean = 0
        seen = []
        for _ in range(4):
            report = self.arbitrate(
                clear_telemetry("MW", packet_loss_pct=0.02),
                previous_status=status,
                consecutive_clean_evaluations=clean,
            )
            seen.append(report.status)
            status, clean = report.status, report.consecutive_clean_evaluations
        self.assertEqual(
            seen,
            [
                STATUS_HOLD_FIBER_HYSTERESIS,
                STATUS_HOLD_FIBER_HYSTERESIS,
                STATUS_MICROWAVE_PRIMARY,
                STATUS_MICROWAVE_PRIMARY,
            ],
        )

    def test_loss_in_the_hysteresis_band_does_not_flap_the_route(self):
        # Regression: with a single 1.0% threshold, loss oscillating either side of
        # it swapped the route on every evaluation. Loss between the recovery (0.1%)
        # and failover (1.0%) thresholds must hold fiber instead.
        report = self.arbitrate(
            clear_telemetry("MW", packet_loss_pct=0.5),
            previous_status=STATUS_FAILOVER_RAIN_FADE,
            consecutive_clean_evaluations=99,
        )
        self.assertEqual(report.status, STATUS_HOLD_FIBER_HYSTERESIS)
        self.assertEqual(report.consecutive_clean_evaluations, 0)

    def test_dwell_resets_on_a_relapse(self):
        report = self.arbitrate(
            clear_telemetry("MW", current_weather="STORM"),
            previous_status=STATUS_HOLD_FIBER_HYSTERESIS,
            consecutive_clean_evaluations=2,
        )
        self.assertEqual(report.status, STATUS_FAILOVER_RAIN_FADE)
        self.assertEqual(report.consecutive_clean_evaluations, 0)

    def test_hysteresis_does_not_apply_when_already_on_microwave(self):
        # Loss inside the band while already on radio is not a reason to leave it.
        report = self.arbitrate(
            clear_telemetry("MW", packet_loss_pct=0.5),
            previous_status=STATUS_MICROWAVE_PRIMARY,
            consecutive_clean_evaluations=0,
        )
        self.assertEqual(report.status, STATUS_MICROWAVE_PRIMARY)

    def test_cold_start_with_no_previous_status_routes_microwave(self):
        report = self.arbitrate(clear_telemetry("MW", packet_loss_pct=0.5))
        self.assertEqual(report.status, STATUS_MICROWAVE_PRIMARY)


class TestEngineConfiguration(unittest.TestCase):

    def test_recovery_threshold_above_failover_threshold_rejected(self):
        with self.assertRaises(NetworkLinkError):
            NetworkLinkArbitratorEngine(
                max_packet_loss_threshold_pct=1.0, recovery_packet_loss_pct=2.0
            )

    def test_equal_thresholds_rejected_as_providing_no_hysteresis(self):
        with self.assertRaises(NetworkLinkError):
            NetworkLinkArbitratorEngine(
                max_packet_loss_threshold_pct=1.0, recovery_packet_loss_pct=1.0
            )

    def test_zero_dwell_rejected(self):
        with self.assertRaises(NetworkLinkError):
            NetworkLinkArbitratorEngine(recovery_dwell_evaluations=0)

    def test_degrading_state_outside_the_known_set_rejected(self):
        with self.assertRaises(NetworkLinkError):
            NetworkLinkArbitratorEngine(
                known_weather_states=["CLEAR", "HEAVY_RAIN"],
                degrading_weather_states=["HAIL"],
            )

    def test_custom_weather_vocabulary_is_honoured(self):
        engine = NetworkLinkArbitratorEngine(
            known_weather_states=["FAIR", "SQUALL"], degrading_weather_states=["SQUALL"]
        )
        micro = NetworkLinkConfig("MW", LINK_TYPE_MICROWAVE, CORRIDOR, distance_km=GEODESIC_KM)
        fiber = NetworkLinkConfig("FB", LINK_TYPE_FIBER, CORRIDOR, distance_km=FIBER_ROUTE_KM)
        ok = engine.arbitrate_cross_market_links(
            micro, fiber, clear_telemetry("MW", current_weather="FAIR")
        )
        self.assertEqual(ok.status, STATUS_MICROWAVE_PRIMARY)
        # "CLEAR" is no longer in the vocabulary and must fail closed.
        unknown = engine.arbitrate_cross_market_links(micro, fiber, clear_telemetry("MW"))
        self.assertEqual(unknown.status, STATUS_FAILOVER_TELEMETRY_UNUSABLE)

    def test_default_degrading_states_are_heavy_liquid_precipitation_only(self):
        self.assertEqual(DEFAULT_DEGRADING_WEATHER_STATES, frozenset({"HEAVY_RAIN", "STORM"}))


class TestTelemetryValidation(unittest.TestCase):

    def test_nan_packet_loss_rejected(self):
        with self.assertRaises(NetworkLinkError):
            WeatherLinkTelemetry("MW", "CLEAR", float("nan"), 30.0)

    def test_out_of_range_packet_loss_rejected(self):
        for bad in (-0.1, 100.1):
            with self.assertRaises(NetworkLinkError):
                WeatherLinkTelemetry("MW", "CLEAR", bad, 30.0)

    def test_nan_snr_rejected(self):
        with self.assertRaises(NetworkLinkError):
            WeatherLinkTelemetry("MW", "CLEAR", 0.0, float("nan"))

    def test_negative_telemetry_age_rejected(self):
        with self.assertRaises(NetworkLinkError):
            WeatherLinkTelemetry("MW", "CLEAR", 0.0, 30.0, telemetry_age_s=-1.0)

    def test_weather_state_is_normalised(self):
        self.assertEqual(WeatherLinkTelemetry("MW", " heavy_rain ", 0.0, 30.0).current_weather,
                         "HEAVY_RAIN")

    def test_error_type_remains_a_value_error(self):
        # Callers written against the previous `raise ValueError` keep working.
        self.assertTrue(issubclass(NetworkLinkError, ValueError))


class TestWeatherSensitivityNote(unittest.TestCase):

    def setUp(self):
        self.engine = NetworkLinkArbitratorEngine()
        self.fiber_cfg = NetworkLinkConfig(
            "FB", LINK_TYPE_FIBER, CORRIDOR, distance_km=FIBER_ROUTE_KM
        )

    def _note(self, frequency_ghz):
        cfg = NetworkLinkConfig(
            "MW",
            LINK_TYPE_MICROWAVE,
            CORRIDOR,
            distance_km=GEODESIC_KM,
            frequency_ghz=frequency_ghz,
        )
        return self.engine.arbitrate_cross_market_links(
            cfg, self.fiber_cfg, clear_telemetry("MW")
        ).weather_sensitivity_note

    def test_below_five_ghz_is_reported_as_rain_insensitive(self):
        self.assertIn("may be ignored", self._note(4.0))

    def test_eleven_ghz_is_reported_as_rain_sensitive(self):
        self.assertIn("increases rapidly", self._note(11.0))

    def test_missing_frequency_is_reported_as_unknown(self):
        self.assertIn("unknown", self._note(None))


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main()
