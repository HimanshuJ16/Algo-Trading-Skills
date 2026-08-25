"""Unit tests for esma-double-volume-cap-mechanism (MiFIR Article 5 volume cap)."""
import unittest
from datetime import date, datetime, timedelta

from esma_double_volume_cap_mechanism import (
    DEFAULT_MAX_REGISTER_AGE_DAYS,
    EsmaSuspensionRegister,
    EsmaVolumeCapEngine,
    LEGACY_DVC_SINGLE_VENUE_CAP_PCT,
    LEGACY_DVC_UNION_CAP_PCT,
    REGIME_LEGACY_DVC,
    REGIME_SVC,
    REGISTER_NOT_APPLICABLE,
    REGISTER_NOT_SUPPLIED,
    REGISTER_NOT_SUSPENDED,
    REGISTER_STALE,
    REGISTER_SUSPENDED,
    ReferencePriceWaiverVolumeMetrics,
    SVC_CAP_PCT,
    SVC_MONITORING_START_DATE,
    SorOrderRouteRequest,
    VolumeCapSuspension,
    rts1_lis_threshold_eur,
)

ISIN = "DE0007100000"          # Mercedes-Benz Group AG
LIVE_DAY = date(2026, 6, 15)   # inside the single volume cap regime
TOTAL_EU = 1_000_000_000.0     # EUR rolling 12-month total EU volume


def metrics(union_rpw_eur: float, venue_rpw_eur=None) -> ReferencePriceWaiverVolumeMetrics:
    return ReferencePriceWaiverVolumeMetrics(
        isin=ISIN,
        symbol="MBG",
        rolling_12m_total_eu_volume_eur=TOTAL_EU,
        rolling_12m_union_rpw_volume_eur=union_rpw_eur,
        rolling_12m_venue_rpw_volume_eur=venue_rpw_eur,
        venue_id="CBOE_DARK_EU",
    )


def order(order_id="ORD_1", value=50_000.0, waiver="RPW", lis_threshold=None):
    return SorOrderRouteRequest(
        order_id=order_id, isin=ISIN, symbol="MBG",
        order_val_eur=value, intended_waiver_type=waiver,
        lis_threshold_eur=lis_threshold,
    )


def clean_register(published_on=date(2026, 4, 9)) -> EsmaSuspensionRegister:
    """A published file in which nothing is suspended -- not the same as no file."""
    return EsmaSuspensionRegister(published_on=published_on, suspensions=())


def suspended_register(published_on=date(2026, 4, 9)) -> EsmaSuspensionRegister:
    return EsmaSuspensionRegister(
        published_on=published_on,
        suspensions=(VolumeCapSuspension(
            isin=ISIN,
            suspension_start_date=date(2026, 4, 14),
            suspension_end_date=date(2026, 7, 13),
        ),),
    )


class TestRegimeSelection(unittest.TestCase):
    """MiFIR Art. 5(8): the monitored period starts 29 September 2025."""

    def test_monitoring_start_date_is_svc(self):
        self.assertEqual(EsmaVolumeCapEngine.regime_for(SVC_MONITORING_START_DATE), REGIME_SVC)

    def test_day_before_monitoring_start_is_legacy_dvc(self):
        self.assertEqual(
            EsmaVolumeCapEngine.regime_for(date(2025, 9, 28)), REGIME_LEGACY_DVC)

    def test_regime_rejects_non_date(self):
        with self.assertRaises(TypeError):
            EsmaVolumeCapEngine.regime_for("2026-06-15")


class TestSingleVolumeCapThreshold(unittest.TestCase):
    """Art. 5(1) suspends where the share *exceeds* 7 % -- strict inequality."""

    def setUp(self):
        self.engine = EsmaVolumeCapEngine()

    def test_cap_default_is_seven_percent(self):
        self.assertEqual(SVC_CAP_PCT, 7.0)
        self.assertEqual(self.engine.cap_pct, 7.0)

    def test_exactly_at_cap_is_not_a_breach(self):
        # 70,000,000 / 1,000,000,000 = exactly 7.0000%
        report = self.engine.audit_volume_cap_and_route_order(
            order(), metrics(70_000_000.0), as_of=LIVE_DAY, register=clean_register())
        self.assertEqual(report.union_rpw_share_pct, 7.0)
        self.assertEqual(report.internal_estimate_status, "ESTIMATED_WITHIN_CAP")
        self.assertTrue(report.is_dark_routing_allowed)
        self.assertEqual(report.final_routed_venue_type, "DARK_RPW")

    def test_just_below_cap_is_not_rounded_up_into_a_breach(self):
        # 69,960,000 / 1,000,000,000 = 6.996%, which rounds to 7.00 at 2 dp.
        # Rounding before the comparison would fabricate a suspension.
        report = self.engine.audit_volume_cap_and_route_order(
            order(), metrics(69_960_000.0), as_of=LIVE_DAY, register=clean_register())
        self.assertAlmostEqual(report.union_rpw_share_pct, 6.996, places=9)
        self.assertEqual(report.internal_estimate_status, "ESTIMATED_WITHIN_CAP")
        self.assertTrue(report.is_dark_routing_allowed)

    def test_above_cap_blocks_and_reroutes_to_lit(self):
        report = self.engine.audit_volume_cap_and_route_order(
            order(), metrics(85_000_000.0), as_of=LIVE_DAY, register=clean_register())
        self.assertAlmostEqual(report.union_rpw_share_pct, 8.5, places=9)
        self.assertEqual(report.internal_estimate_status, "ESTIMATED_BREACH_UNION_CAP")
        self.assertFalse(report.is_dark_routing_allowed)
        self.assertEqual(report.final_routed_venue_type, "LIT_VENUE")

    def test_headroom_is_eur_remaining_before_the_cap(self):
        report = self.engine.audit_volume_cap_and_route_order(
            order(), metrics(50_000_000.0), as_of=LIVE_DAY, register=clean_register())
        # 7% of 1bn = 70m; 70m - 50m already used = 20m of headroom.
        self.assertAlmostEqual(report.rpw_headroom_eur, 20_000_000.0, places=2)

    def test_headroom_is_negative_once_the_cap_is_exceeded(self):
        report = self.engine.audit_volume_cap_and_route_order(
            order(), metrics(85_000_000.0), as_of=LIVE_DAY, register=clean_register())
        self.assertAlmostEqual(report.rpw_headroom_eur, -15_000_000.0, places=2)


class TestOfficialRegisterIsAuthoritative(unittest.TestCase):
    """
    Art. 5(1): venues suspend on the basis of data *published by ESMA*. The
    firm's own ratio never substitutes for the register.
    """

    def setUp(self):
        self.engine = EsmaVolumeCapEngine()

    def test_register_suspension_blocks_even_when_internal_estimate_is_clean(self):
        report = self.engine.audit_volume_cap_and_route_order(
            order(), metrics(10_000_000.0),  # 1.0% -- nowhere near the cap
            as_of=LIVE_DAY, register=suspended_register())
        self.assertEqual(report.internal_estimate_status, "ESTIMATED_WITHIN_CAP")
        self.assertEqual(report.official_register_status, REGISTER_SUSPENDED)
        self.assertFalse(report.is_dark_routing_allowed)
        self.assertEqual(report.final_routed_venue_type, "LIT_VENUE")
        self.assertEqual(report.suspension_end_date, date(2026, 7, 13))

    def test_missing_register_fails_closed(self):
        report = self.engine.audit_volume_cap_and_route_order(
            order(), metrics(10_000_000.0), as_of=LIVE_DAY, register=None)
        self.assertEqual(report.official_register_status, REGISTER_NOT_SUPPLIED)
        self.assertFalse(report.is_dark_routing_allowed)
        self.assertEqual(report.final_routed_venue_type, "LIT_VENUE")

    def test_stale_register_fails_closed(self):
        stale_day = date(2026, 4, 9) + timedelta(days=DEFAULT_MAX_REGISTER_AGE_DAYS + 1)
        report = self.engine.audit_volume_cap_and_route_order(
            order(), metrics(10_000_000.0), as_of=stale_day, register=clean_register())
        self.assertEqual(report.official_register_status, REGISTER_STALE)
        self.assertFalse(report.is_dark_routing_allowed)

    def test_empty_register_is_not_treated_as_missing(self):
        report = self.engine.audit_volume_cap_and_route_order(
            order(), metrics(10_000_000.0), as_of=LIVE_DAY, register=clean_register())
        self.assertEqual(report.official_register_status, REGISTER_NOT_SUSPENDED)
        self.assertTrue(report.is_dark_routing_allowed)

    def test_suspension_endpoints_are_inclusive(self):
        register = suspended_register()
        entry = register.suspensions[0]
        self.assertTrue(entry.is_active_on(date(2026, 4, 14)))    # start day
        self.assertTrue(entry.is_active_on(date(2026, 7, 13)))    # end day
        self.assertFalse(entry.is_active_on(date(2026, 4, 13)))   # day before
        self.assertFalse(entry.is_active_on(date(2026, 7, 14)))   # day after

    def test_expired_suspension_no_longer_blocks(self):
        register = EsmaSuspensionRegister(
            published_on=date(2026, 7, 9),
            suspensions=(VolumeCapSuspension(
                isin=ISIN,
                suspension_start_date=date(2026, 4, 14),
                suspension_end_date=date(2026, 7, 13),
            ),),
        )
        report = self.engine.audit_volume_cap_and_route_order(
            order(), metrics(10_000_000.0), as_of=date(2026, 7, 20), register=register)
        self.assertEqual(report.official_register_status, REGISTER_NOT_SUSPENDED)
        self.assertTrue(report.is_dark_routing_allowed)

    def test_isin_matching_tolerates_whitespace_and_case(self):
        register = EsmaSuspensionRegister(
            published_on=date(2026, 4, 9),
            suspensions=(VolumeCapSuspension(
                isin="  de0007100000  ",
                suspension_start_date=date(2026, 4, 14),
                suspension_end_date=date(2026, 7, 13),
            ),),
        )
        self.assertIsNotNone(register.active_suspension(ISIN, LIVE_DAY))

    def test_other_instrument_suspension_does_not_block_this_order(self):
        register = EsmaSuspensionRegister(
            published_on=date(2026, 4, 9),
            suspensions=(VolumeCapSuspension(
                isin="FR0000120271",
                suspension_start_date=date(2026, 4, 14),
                suspension_end_date=date(2026, 7, 13),
            ),),
        )
        report = self.engine.audit_volume_cap_and_route_order(
            order(), metrics(10_000_000.0), as_of=LIVE_DAY, register=register)
        self.assertEqual(report.official_register_status, REGISTER_NOT_SUSPENDED)
        self.assertTrue(report.is_dark_routing_allowed)

    def test_future_dated_register_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_volume_cap_and_route_order(
                order(), metrics(10_000_000.0),
                as_of=date(2026, 1, 1), register=clean_register(date(2026, 4, 9)))

    def test_estimate_only_block_can_be_disabled(self):
        permissive = EsmaVolumeCapEngine(block_rpw_on_estimated_breach=False)
        report = permissive.audit_volume_cap_and_route_order(
            order(), metrics(85_000_000.0),  # 8.5%, over the cap
            as_of=LIVE_DAY, register=clean_register())
        self.assertEqual(report.internal_estimate_status, "ESTIMATED_BREACH_UNION_CAP")
        self.assertEqual(report.official_register_status, REGISTER_NOT_SUSPENDED)
        self.assertTrue(report.is_dark_routing_allowed)


class TestWaiverScope(unittest.TestCase):
    """Art. 5(1) covers Art. 4(1)(a) only -- NTW, LIS and OMF are outside it."""

    def setUp(self):
        self.engine = EsmaVolumeCapEngine()

    def test_negotiated_trade_waiver_is_not_capped_under_svc(self):
        report = self.engine.audit_volume_cap_and_route_order(
            order(waiver="NTW"), metrics(85_000_000.0),  # 8.5%, well over the cap
            as_of=LIVE_DAY, register=suspended_register())
        self.assertFalse(report.is_cap_applicable)
        self.assertTrue(report.is_dark_routing_allowed)
        self.assertEqual(report.final_routed_venue_type, "DARK_NTW")
        self.assertEqual(report.official_register_status, REGISTER_NOT_APPLICABLE)

    def test_order_management_facility_waiver_is_not_capped(self):
        report = self.engine.audit_volume_cap_and_route_order(
            order(waiver="OMF"), metrics(85_000_000.0),
            as_of=LIVE_DAY, register=suspended_register())
        self.assertTrue(report.is_dark_routing_allowed)
        self.assertEqual(report.final_routed_venue_type, "DARK_OMF")

    def test_eligible_lis_order_is_exempt_from_an_active_suspension(self):
        report = self.engine.audit_volume_cap_and_route_order(
            order(value=200_000.0, waiver="LIS", lis_threshold=100_000.0),
            metrics(85_000_000.0), as_of=LIVE_DAY, register=suspended_register())
        self.assertTrue(report.is_dark_routing_allowed)
        self.assertEqual(report.effective_waiver_type, "LIS")
        self.assertEqual(report.final_routed_venue_type, "DARK_LIS_EXEMPT")

    def test_lis_order_exactly_at_threshold_is_eligible(self):
        report = self.engine.audit_volume_cap_and_route_order(
            order(value=650_000.0, waiver="LIS", lis_threshold=650_000.0),
            metrics(85_000_000.0), as_of=LIVE_DAY, register=suspended_register())
        self.assertEqual(report.final_routed_venue_type, "DARK_LIS_EXEMPT")

    def test_undersized_lis_claim_is_downgraded_to_rpw_and_blocked(self):
        """
        Regression: the previous engine treated any order above a flat EUR 100k
        as LIS-exempt. A EUR 200k order in a name whose RTS 1 threshold is
        EUR 650k is NOT large in scale and must not execute dark on a suspended
        instrument.
        """
        report = self.engine.audit_volume_cap_and_route_order(
            order(value=200_000.0, waiver="LIS", lis_threshold=650_000.0),
            metrics(85_000_000.0), as_of=LIVE_DAY, register=suspended_register())
        self.assertEqual(report.effective_waiver_type, "RPW")
        self.assertTrue(report.is_cap_applicable)
        self.assertFalse(report.is_dark_routing_allowed)
        self.assertEqual(report.final_routed_venue_type, "LIT_VENUE")
        self.assertIn("LIS CLAIM REJECTED", report.audit_notes)

    def test_large_rpw_order_is_not_silently_promoted_to_lis(self):
        """A big order submitted under the RPW is still an RPW order."""
        report = self.engine.audit_volume_cap_and_route_order(
            order(value=5_000_000.0, waiver="RPW"),
            metrics(10_000_000.0), as_of=LIVE_DAY, register=suspended_register())
        self.assertEqual(report.effective_waiver_type, "RPW")
        self.assertFalse(report.is_dark_routing_allowed)
        self.assertEqual(report.final_routed_venue_type, "LIT_VENUE")


class TestLegacyDvcBacktestMode(unittest.TestCase):
    """The repealed 4 %/8 % DVC, reachable only for pre-29-Sep-2025 dates."""

    def setUp(self):
        self.engine = EsmaVolumeCapEngine()
        self.hist_day = date(2024, 6, 3)

    def test_legacy_thresholds_are_four_and_eight(self):
        self.assertEqual(LEGACY_DVC_SINGLE_VENUE_CAP_PCT, 4.0)
        self.assertEqual(LEGACY_DVC_UNION_CAP_PCT, 8.0)

    def test_venue_breach_is_reported_even_when_union_limb_is_clean(self):
        report = self.engine.audit_volume_cap_and_route_order(
            order(), metrics(60_000_000.0, venue_rpw_eur=50_000_000.0),  # 6.0% union, 5.0% venue
            as_of=self.hist_day, register=clean_register(date(2024, 5, 8)))
        self.assertEqual(report.regime, REGIME_LEGACY_DVC)
        self.assertEqual(report.internal_estimate_status, "ESTIMATED_BREACH_VENUE_CAP")
        self.assertFalse(report.is_dark_routing_allowed)

    def test_both_limbs_breached_is_reported_as_both(self):
        report = self.engine.audit_volume_cap_and_route_order(
            order(), metrics(85_000_000.0, venue_rpw_eur=50_000_000.0),  # 8.5% union, 5.0% venue
            as_of=self.hist_day, register=clean_register(date(2024, 5, 8)))
        self.assertEqual(report.internal_estimate_status, "ESTIMATED_BREACH_UNION_AND_VENUE_CAP")

    def test_legacy_mode_caps_the_negotiated_trade_waiver(self):
        """Pre-2024 Art. 5(1) also covered Art. 4(1)(b)(i)."""
        report = self.engine.audit_volume_cap_and_route_order(
            order(waiver="NTW"), metrics(85_000_000.0, venue_rpw_eur=10_000_000.0),
            as_of=self.hist_day, register=clean_register(date(2024, 5, 8)))
        self.assertTrue(report.is_cap_applicable)
        self.assertFalse(report.is_dark_routing_allowed)

    def test_legacy_mode_without_venue_volume_raises(self):
        with self.assertRaises(ValueError):
            self.engine.audit_volume_cap_and_route_order(
                order(), metrics(60_000_000.0), as_of=self.hist_day,
                register=clean_register(date(2024, 5, 8)))

    def test_legacy_mode_is_labelled_in_the_audit_notes(self):
        report = self.engine.audit_volume_cap_and_route_order(
            order(), metrics(10_000_000.0, venue_rpw_eur=5_000_000.0),
            as_of=self.hist_day, register=clean_register(date(2024, 5, 8)))
        self.assertIn("LEGACY DVC MODE", report.audit_notes)


class TestRts1LisThresholds(unittest.TestCase):
    """
    RTS 1 (Delegated Regulation (EU) 2017/587) Annex II Table 1 and Table 2,
    transcribed from the published tables rather than from the implementation.
    """

    def test_share_bands_match_annex_ii_table_1(self):
        expected = [
            (0.0, 15_000.0),
            (49_999.0, 15_000.0),
            (50_000.0, 30_000.0),
            (99_999.0, 30_000.0),
            (100_000.0, 60_000.0),
            (499_999.0, 60_000.0),
            (500_000.0, 100_000.0),
            (999_999.0, 100_000.0),
            (1_000_000.0, 200_000.0),
            (4_999_999.0, 200_000.0),
            (5_000_000.0, 300_000.0),
            (24_999_999.0, 300_000.0),
            (25_000_000.0, 400_000.0),
            (49_999_999.0, 400_000.0),
            (50_000_000.0, 500_000.0),
            (99_999_999.0, 500_000.0),
            (100_000_000.0, 650_000.0),
            (5_000_000_000.0, 650_000.0),
        ]
        for adt, threshold in expected:
            with self.subTest(adt=adt):
                self.assertEqual(rts1_lis_threshold_eur(adt), threshold)

    def test_depositary_receipts_use_the_share_table(self):
        self.assertEqual(rts1_lis_threshold_eur(2_000_000.0, "DPRS"), 200_000.0)

    def test_certificate_bands_match_annex_ii_table_2(self):
        self.assertEqual(rts1_lis_threshold_eur(49_999.0, "CRFT"), 15_000.0)
        self.assertEqual(rts1_lis_threshold_eur(50_000.0, "CRFT"), 30_000.0)

    def test_etf_class_raises_rather_than_guessing(self):
        with self.assertRaises(ValueError):
            rts1_lis_threshold_eur(10_000_000.0, "ETFS")

    def test_negative_adt_raises(self):
        with self.assertRaises(ValueError):
            rts1_lis_threshold_eur(-1.0)


class TestInputValidation(unittest.TestCase):

    def test_zero_total_volume_raises(self):
        with self.assertRaises(ValueError):
            ReferencePriceWaiverVolumeMetrics(
                isin=ISIN, symbol="MBG",
                rolling_12m_total_eu_volume_eur=0.0,
                rolling_12m_union_rpw_volume_eur=0.0)

    def test_nan_volume_raises(self):
        with self.assertRaises(ValueError):
            ReferencePriceWaiverVolumeMetrics(
                isin=ISIN, symbol="MBG",
                rolling_12m_total_eu_volume_eur=float("nan"),
                rolling_12m_union_rpw_volume_eur=1.0)

    def test_negative_volume_raises(self):
        with self.assertRaises(ValueError):
            metrics(-1.0)

    def test_rpw_volume_exceeding_total_raises(self):
        with self.assertRaises(ValueError):
            metrics(TOTAL_EU + 1.0)

    def test_venue_volume_exceeding_union_volume_raises(self):
        with self.assertRaises(ValueError):
            metrics(10_000_000.0, venue_rpw_eur=20_000_000.0)

    def test_unknown_waiver_raises(self):
        with self.assertRaises(ValueError):
            order(waiver="DARK")

    def test_lis_order_without_threshold_raises(self):
        with self.assertRaises(ValueError):
            order(value=200_000.0, waiver="LIS")

    def test_non_positive_order_value_raises(self):
        with self.assertRaises(ValueError):
            order(value=0.0)

    def test_waiver_is_normalised_to_upper_case(self):
        self.assertEqual(order(waiver=" rpw ").intended_waiver_type, "RPW")

    def test_isin_mismatch_between_order_and_metrics_raises(self):
        engine = EsmaVolumeCapEngine()
        wrong = SorOrderRouteRequest(
            order_id="ORD_X", isin="FR0000120271", symbol="OR",
            order_val_eur=50_000.0, intended_waiver_type="RPW")
        with self.assertRaises(ValueError):
            engine.audit_volume_cap_and_route_order(
                wrong, metrics(10_000_000.0), as_of=LIVE_DAY, register=clean_register())

    def test_inverted_suspension_window_raises(self):
        with self.assertRaises(ValueError):
            VolumeCapSuspension(
                isin=ISIN,
                suspension_start_date=date(2026, 7, 13),
                suspension_end_date=date(2026, 4, 14))

    def test_engine_rejects_out_of_range_cap(self):
        with self.assertRaises(ValueError):
            EsmaVolumeCapEngine(cap_pct=0.0)
        with self.assertRaises(ValueError):
            EsmaVolumeCapEngine(cap_pct=101.0)

    def test_engine_rejects_non_positive_register_age(self):
        with self.assertRaises(ValueError):
            EsmaVolumeCapEngine(max_register_age_days=0)

    def test_as_of_must_be_a_date(self):
        engine = EsmaVolumeCapEngine()
        with self.assertRaises(TypeError):
            engine.audit_volume_cap_and_route_order(
                order(), metrics(10_000_000.0), as_of="2026-06-15", register=clean_register())

    def test_datetime_is_rejected_rather_than_silently_accepted(self):
        """
        datetime subclasses date, so an isinstance check alone lets a timestamp
        through and it then raises on the first date comparison. Mapping an
        instant to a trading date is a timezone decision for the caller.
        """
        engine = EsmaVolumeCapEngine()
        stamp = datetime(2026, 6, 15, 9, 30)
        with self.assertRaises(TypeError):
            EsmaVolumeCapEngine.regime_for(stamp)
        with self.assertRaises(TypeError):
            engine.audit_volume_cap_and_route_order(
                order(), metrics(10_000_000.0), as_of=stamp, register=clean_register())
        with self.assertRaises(TypeError):
            EsmaSuspensionRegister(published_on=stamp)
        with self.assertRaises(TypeError):
            VolumeCapSuspension(
                isin=ISIN,
                suspension_start_date=stamp,
                suspension_end_date=date(2026, 7, 13))


if __name__ == "__main__":
    unittest.main()
