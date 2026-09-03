"""Tests for the supply-chain earnings read-through engine.

Every expected value below is derived by hand in the comment above the assertion,
never by re-running the engine's own expression. Several tests are explicit
regressions against version 1.0.0 defects and are annotated `v1 regression`.
"""
import logging
import math
import unittest

from supply_chain_data_for_earnings_prediction import (
    SIGNAL_BUY,
    SIGNAL_INSUFFICIENT,
    SIGNAL_NEUTRAL,
    SIGNAL_SELL,
    Config,
    CustomerObservation,
    Engine,
    SupplierCustomerLink,
    SupplierObservation,
    SupplyChainDataForEarningsPredictionEngine,
    SupplyChainEarningsSignal,
)

AS_OF = "2026-08-01T00:00:00Z"
PUBLISHED = "2026-07-20T20:05:00Z"


def supplier(**overrides) -> SupplierObservation:
    """A usable supplier observation, overridable field by field."""
    defaults = dict(
        supplier_ticker="TSM",
        revenue_growth_pct=25.0,
        supplier_share_of_target_cogs_pct=60.0,
        target_share_of_supplier_revenue_pct=20.0,
        available_from_iso=PUBLISHED,
    )
    defaults.update(overrides)
    return SupplierObservation(**defaults)


def customer(**overrides) -> CustomerObservation:
    """A usable customer observation, overridable field by field."""
    defaults = dict(
        customer_ticker="DELL",
        inventory_growth_pct=5.0,
        customer_share_of_target_revenue_pct=40.0,
        available_from_iso=PUBLISHED,
    )
    defaults.update(overrides)
    return CustomerObservation(**defaults)


def evaluate(engine, **overrides) -> SupplyChainEarningsSignal:
    kwargs = dict(
        target_asset="NVDA",
        as_of_iso=AS_OF,
        supplier_observations=[supplier()],
        customer_observations=[customer()],
        consensus_revenue_growth_pct=10.0,
        consensus_dispersion_pct=5.0,
    )
    kwargs.update(overrides)
    return engine.evaluate_supply_chain_lead_signal(**kwargs)


class TestLegacyShims(unittest.TestCase):
    """Deprecated symbols kept only so existing imports keep working."""

    def test_config_and_engine_shims_still_import_and_run(self):
        self.assertEqual(Engine(Config(name="test")).config.name, "test")
        self.assertTrue(Engine().run())

    def test_supplier_customer_link_still_constructs(self):
        link = SupplierCustomerLink("TSM", "NVDA", revenue_dependency_pct=20.0)
        self.assertEqual(link.lead_time_months, 3)


class TestConstructorValidation(unittest.TestCase):
    def test_negative_blend_weight_rejected(self):
        with self.assertRaises(ValueError):
            SupplyChainDataForEarningsPredictionEngine(supplier_blend_weight=-0.7)
        with self.assertRaises(ValueError):
            SupplyChainDataForEarningsPredictionEngine(inventory_blend_weight=-0.3)

    def test_both_blend_weights_zero_rejected(self):
        with self.assertRaises(ValueError):
            SupplyChainDataForEarningsPredictionEngine(
                supplier_blend_weight=0.0, inventory_blend_weight=0.0)

    def test_non_finite_blend_weight_rejected(self):
        with self.assertRaises(ValueError):
            SupplyChainDataForEarningsPredictionEngine(
                supplier_blend_weight=float("nan"))

    def test_non_positive_z_threshold_rejected(self):
        for bad in (0.0, -1.0):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    SupplyChainDataForEarningsPredictionEngine(
                        surprise_z_threshold=bad)

    def test_coverage_and_read_through_floors_bounded_to_percent(self):
        for kwargs in (
            {"min_supplier_coverage_pct": 100.1},
            {"min_supplier_coverage_pct": -0.1},
            {"min_read_through_share_pct": 100.1},
            {"min_read_through_share_pct": -0.1},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    SupplyChainDataForEarningsPredictionEngine(**kwargs)

    def test_non_positive_staleness_bound_rejected(self):
        with self.assertRaises(ValueError):
            SupplyChainDataForEarningsPredictionEngine(max_observation_age_days=0)
        with self.assertRaises(ValueError):
            SupplyChainDataForEarningsPredictionEngine(max_observation_age_days=-5)

    def test_lead_time_months_must_be_a_non_negative_int(self):
        with self.assertRaises(ValueError):
            SupplyChainDataForEarningsPredictionEngine(lead_time_months=-1)
        with self.assertRaises(ValueError):
            SupplyChainDataForEarningsPredictionEngine(lead_time_months=1.5)
        with self.assertRaises(ValueError):
            SupplyChainDataForEarningsPredictionEngine(lead_time_months=True)


class TestReadThroughArithmetic(unittest.TestCase):
    def setUp(self):
        self.engine = SupplyChainDataForEarningsPredictionEngine()

    def test_documented_bullish_example(self):
        # Single supplier at +25% growth, single customer at +5% inventory growth.
        # weighted supplier = 25.0, weighted customer inventory = 5.0
        # implied = 0.70 * 25 - 0.30 * 5 = 17.5 - 1.5 = 16.0
        # gap     = 16.0 - 10.0 = 6.0
        # Z       = 6.0 / 5.0 = 1.2 >= 1.0 -> BUY
        result = evaluate(self.engine)
        self.assertEqual(result.implied_revenue_growth_pct, 16.0)
        self.assertEqual(result.consensus_revenue_gap_pct, 6.0)
        self.assertEqual(result.surprise_z_score, 1.2)
        self.assertEqual(result.directional_signal, SIGNAL_BUY)
        self.assertTrue(result.is_signal_measurable)

    def test_documented_bearish_example(self):
        # Supplier -10%, customer inventory +20%, consensus +5%.
        # implied = 0.70 * (-10) - 0.30 * 20 = -7.0 - 6.0 = -13.0
        # gap     = -13.0 - 5.0 = -18.0
        # Z       = -18.0 / 5.0 = -3.6 <= -1.0 -> SELL
        result = evaluate(
            self.engine,
            supplier_observations=[supplier(revenue_growth_pct=-10.0)],
            customer_observations=[customer(inventory_growth_pct=20.0)],
            consensus_revenue_growth_pct=5.0,
        )
        self.assertEqual(result.implied_revenue_growth_pct, -13.0)
        self.assertEqual(result.consensus_revenue_gap_pct, -18.0)
        self.assertEqual(result.surprise_z_score, -3.6)
        self.assertEqual(result.directional_signal, SIGNAL_SELL)

    def test_suppliers_are_weighted_by_share_of_target_input_spend(self):
        # v1 regression: v1 accepted a single scalar and applied no concentration
        # weighting at all. A 60%-of-COGS supplier growing 20% and a 20%-of-COGS
        # supplier growing 0% must not be averaged evenly.
        #   weighted = (60 * 20 + 20 * 0) / (60 + 20) = 1200 / 80 = 15.0
        # An unweighted mean would give 10.0, and a raw weighted sum 12.0.
        result = evaluate(
            self.engine,
            supplier_observations=[
                supplier(supplier_ticker="A", revenue_growth_pct=20.0,
                         supplier_share_of_target_cogs_pct=60.0),
                supplier(supplier_ticker="B", revenue_growth_pct=0.0,
                         supplier_share_of_target_cogs_pct=20.0),
            ],
            customer_observations=[],
            consensus_revenue_growth_pct=0.0,
            consensus_dispersion_pct=1.0,
        )
        self.assertEqual(result.weighted_supplier_revenue_growth_pct, 15.0)
        self.assertEqual(result.supplier_coverage_pct, 80.0)
        self.assertEqual(result.suppliers_used, 2)
        # implied = 0.70 * 15.0 - 0.30 * 0.0 = 10.5
        self.assertEqual(result.implied_revenue_growth_pct, 10.5)

    def test_customers_are_weighted_by_share_of_target_revenue(self):
        #   weighted = (30 * 10 + 10 * 50) / (30 + 10) = (300 + 500) / 40 = 20.0
        # An unweighted mean would give 30.0.
        result = evaluate(
            self.engine,
            supplier_observations=[supplier(revenue_growth_pct=0.0)],
            customer_observations=[
                customer(customer_ticker="C1", inventory_growth_pct=10.0,
                         customer_share_of_target_revenue_pct=30.0),
                customer(customer_ticker="C2", inventory_growth_pct=50.0,
                         customer_share_of_target_revenue_pct=10.0),
            ],
            consensus_revenue_growth_pct=0.0,
            consensus_dispersion_pct=1.0,
        )
        self.assertEqual(result.weighted_customer_inventory_growth_pct, 20.0)
        self.assertEqual(result.customer_coverage_pct, 40.0)
        # implied = 0.70 * 0.0 - 0.30 * 20.0 = -6.0
        self.assertEqual(result.implied_revenue_growth_pct, -6.0)

    def test_dispersion_is_the_denominator_not_a_hard_coded_five(self):
        # v1 regression: v1 divided every gap by a hard-coded 5.0.
        # Same gap of 6.0 against a dispersion of 2.0 must give Z = 3.0, not 1.2.
        result = evaluate(self.engine, consensus_dispersion_pct=2.0)
        self.assertEqual(result.consensus_revenue_gap_pct, 6.0)
        self.assertEqual(result.surprise_z_score, 3.0)

    def test_inventory_build_reduces_implied_growth(self):
        # Bullwhip direction check: holding suppliers fixed, more customer
        # inventory must lower implied growth, never raise it.
        low = evaluate(self.engine, customer_observations=[
            customer(inventory_growth_pct=0.0)])
        high = evaluate(self.engine, customer_observations=[
            customer(inventory_growth_pct=40.0)])
        self.assertLess(high.implied_revenue_growth_pct,
                        low.implied_revenue_growth_pct)
        # 0.70*25 - 0.30*0 = 17.5 ; 0.70*25 - 0.30*40 = 17.5 - 12.0 = 5.5
        self.assertEqual(low.implied_revenue_growth_pct, 17.5)
        self.assertEqual(high.implied_revenue_growth_pct, 5.5)

    def test_supplier_only_evaluation_records_the_missing_drag_term(self):
        result = evaluate(self.engine, customer_observations=[])
        self.assertEqual(result.weighted_customer_inventory_growth_pct, 0.0)
        self.assertEqual(result.customers_used, 0)
        self.assertTrue(any("supplier-only" in note for note in result.audit_notes))


class TestBanding(unittest.TestCase):
    def setUp(self):
        self.engine = SupplyChainDataForEarningsPredictionEngine()

    def test_threshold_is_inclusive_at_the_positive_edge(self):
        # supplier +10%, no customers: implied = 0.70 * 10 = 7.0
        # consensus 5.0 -> gap 2.0 ; dispersion 2.0 -> Z = exactly 1.0 -> BUY
        result = evaluate(
            self.engine,
            supplier_observations=[supplier(revenue_growth_pct=10.0)],
            customer_observations=[],
            consensus_revenue_growth_pct=5.0,
            consensus_dispersion_pct=2.0,
        )
        self.assertEqual(result.surprise_z_score, 1.0)
        self.assertEqual(result.directional_signal, SIGNAL_BUY)

    def test_threshold_is_inclusive_at_the_negative_edge(self):
        # implied = 0.70 * 10 = 7.0 ; consensus 9.0 -> gap -2.0
        # dispersion 2.0 -> Z = exactly -1.0 -> SELL
        result = evaluate(
            self.engine,
            supplier_observations=[supplier(revenue_growth_pct=10.0)],
            customer_observations=[],
            consensus_revenue_growth_pct=9.0,
            consensus_dispersion_pct=2.0,
        )
        self.assertEqual(result.surprise_z_score, -1.0)
        self.assertEqual(result.directional_signal, SIGNAL_SELL)

    def test_band_is_decided_on_the_unrounded_z(self):
        # implied = 7.0 ; consensus 5.00008 -> gap 1.99992
        # dispersion 2.0 -> Z = 0.99996, which rounds to 1.0 at four decimals.
        # Banding must still return NEUTRAL: rounding first would promote it.
        result = evaluate(
            self.engine,
            supplier_observations=[supplier(revenue_growth_pct=10.0)],
            customer_observations=[],
            consensus_revenue_growth_pct=5.00008,
            consensus_dispersion_pct=2.0,
        )
        self.assertEqual(round(result.surprise_z_score, 4), 1.0)
        self.assertEqual(result.directional_signal, SIGNAL_NEUTRAL)

    def test_configured_threshold_is_actually_read(self):
        # v1 regression: v1 exposed surprise_z_threshold=1.5 and then ignored it,
        # hard-coding +/-1.0. Z = 1.2 must be NEUTRAL under a 2.0 threshold and
        # BUY under the default 1.0.
        strict = SupplyChainDataForEarningsPredictionEngine(surprise_z_threshold=2.0)
        self.assertEqual(evaluate(strict).surprise_z_score, 1.2)
        self.assertEqual(evaluate(strict).directional_signal, SIGNAL_NEUTRAL)
        self.assertEqual(evaluate(self.engine).directional_signal, SIGNAL_BUY)

    def test_configured_blend_weights_are_actually_read(self):
        engine = SupplyChainDataForEarningsPredictionEngine(
            supplier_blend_weight=1.0, inventory_blend_weight=0.0)
        # implied = 1.0 * 25 - 0.0 * 5 = 25.0
        self.assertEqual(evaluate(engine).implied_revenue_growth_pct, 25.0)


class TestPointInTime(unittest.TestCase):
    def setUp(self):
        self.engine = SupplyChainDataForEarningsPredictionEngine()

    def test_observation_published_after_as_of_is_excluded_and_counted(self):
        result = evaluate(
            self.engine,
            supplier_observations=[
                supplier(available_from_iso="2026-08-01T00:00:01Z")],
            customer_observations=[],
        )
        self.assertEqual(result.future_observations_excluded_count, 1)
        self.assertEqual(result.suppliers_used, 0)
        self.assertEqual(result.directional_signal, SIGNAL_INSUFFICIENT)

    def test_observation_published_exactly_at_as_of_is_retained(self):
        result = evaluate(
            self.engine,
            supplier_observations=[supplier(available_from_iso=AS_OF)],
            customer_observations=[],
        )
        self.assertEqual(result.future_observations_excluded_count, 0)
        self.assertEqual(result.suppliers_used, 1)

    def test_offset_timestamps_are_compared_in_utc(self):
        # 2026-08-01T09:00:00-04:00 is 13:00Z, which is after a 12:00Z cutoff.
        result = evaluate(
            self.engine,
            as_of_iso="2026-08-01T12:00:00Z",
            supplier_observations=[
                supplier(available_from_iso="2026-08-01T09:00:00-04:00")],
            customer_observations=[],
        )
        self.assertEqual(result.future_observations_excluded_count, 1)

    def test_naive_timestamps_are_rejected(self):
        with self.assertRaises(ValueError):
            evaluate(self.engine, as_of_iso="2026-08-01")
        with self.assertRaises(ValueError):
            evaluate(self.engine, supplier_observations=[
                supplier(available_from_iso="2026-07-20T20:05:00")])

    def test_malformed_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            evaluate(self.engine, as_of_iso="not-a-timestamp")

    def test_staleness_bound_excludes_old_observations(self):
        engine = SupplyChainDataForEarningsPredictionEngine(
            max_observation_age_days=5)
        # Published 2026-07-20, as_of 2026-08-01 -> 12 days old, beyond a 5-day bound.
        result = evaluate(engine, customer_observations=[])
        self.assertEqual(result.stale_observations_excluded_count, 1)
        self.assertEqual(result.directional_signal, SIGNAL_INSUFFICIENT)

    def test_unbounded_staleness_is_disclosed_in_the_audit_notes(self):
        result = evaluate(self.engine)
        self.assertEqual(result.stale_observations_excluded_count, 0)
        self.assertTrue(
            any("No staleness bound" in note for note in result.audit_notes))

    def test_output_timestamp_is_as_of_not_wall_clock(self):
        # v1 regression: v1 stamped results with pd.Timestamp.now(), so the same
        # inputs produced different records on different days.
        first = evaluate(self.engine)
        second = evaluate(self.engine)
        self.assertEqual(first.as_of_iso, "2026-08-01T00:00:00Z")
        self.assertEqual(first.as_of_iso, second.as_of_iso)
        self.assertEqual(first.surprise_z_score, second.surprise_z_score)


class TestConcentrationScreens(unittest.TestCase):
    def setUp(self):
        self.engine = SupplyChainDataForEarningsPredictionEngine()

    def test_supplier_below_read_through_floor_is_excluded(self):
        result = evaluate(
            self.engine,
            supplier_observations=[
                supplier(target_share_of_supplier_revenue_pct=9.99)],
            customer_observations=[],
        )
        self.assertEqual(result.low_read_through_excluded_count, 1)
        self.assertEqual(result.directional_signal, SIGNAL_INSUFFICIENT)

    def test_supplier_exactly_at_read_through_floor_is_retained(self):
        result = evaluate(
            self.engine,
            supplier_observations=[
                supplier(target_share_of_supplier_revenue_pct=10.0)],
            customer_observations=[],
        )
        self.assertEqual(result.low_read_through_excluded_count, 0)
        self.assertEqual(result.suppliers_used, 1)

    def test_read_through_screen_can_be_disabled(self):
        engine = SupplyChainDataForEarningsPredictionEngine(
            min_read_through_share_pct=0.0)
        result = evaluate(
            engine,
            supplier_observations=[
                supplier(target_share_of_supplier_revenue_pct=0.5)],
            customer_observations=[],
        )
        self.assertEqual(result.low_read_through_excluded_count, 0)
        self.assertEqual(result.suppliers_used, 1)

    def test_coverage_below_floor_suppresses_the_signal(self):
        result = evaluate(
            self.engine,
            supplier_observations=[
                supplier(supplier_share_of_target_cogs_pct=9.9)],
            customer_observations=[],
        )
        self.assertEqual(result.supplier_coverage_pct, 9.9)
        self.assertEqual(result.directional_signal, SIGNAL_INSUFFICIENT)

    def test_coverage_exactly_at_floor_is_measurable(self):
        result = evaluate(
            self.engine,
            supplier_observations=[
                supplier(supplier_share_of_target_cogs_pct=10.0)],
            customer_observations=[],
        )
        self.assertTrue(result.is_signal_measurable)
        self.assertEqual(result.supplier_coverage_pct, 10.0)

    def test_zero_weight_supplier_contributes_nothing(self):
        result = evaluate(
            self.engine,
            supplier_observations=[
                supplier(supplier_ticker="A",
                         supplier_share_of_target_cogs_pct=40.0,
                         revenue_growth_pct=25.0),
                supplier(supplier_ticker="B",
                         supplier_share_of_target_cogs_pct=0.0,
                         revenue_growth_pct=999.0),
            ],
            customer_observations=[],
        )
        self.assertEqual(result.suppliers_used, 1)
        self.assertEqual(result.weighted_supplier_revenue_growth_pct, 25.0)

    def test_empty_supplier_list_is_insufficient_data_not_neutral(self):
        result = evaluate(
            self.engine, supplier_observations=[], customer_observations=[])
        self.assertEqual(result.directional_signal, SIGNAL_INSUFFICIENT)
        self.assertNotEqual(result.directional_signal, SIGNAL_NEUTRAL)

    def test_insufficient_data_reports_none_not_zero(self):
        # v1 regression: v1 had no suppression path, so a chain it could not
        # measure still produced a numeric Z-score.
        result = evaluate(
            self.engine, supplier_observations=[], customer_observations=[])
        self.assertFalse(result.is_signal_measurable)
        self.assertIsNone(result.surprise_z_score)
        self.assertIsNone(result.implied_revenue_growth_pct)
        self.assertIsNone(result.consensus_revenue_gap_pct)
        self.assertIsNone(result.weighted_supplier_revenue_growth_pct)

    def test_suppression_is_logged(self):
        with self.assertLogs(
            "supply_chain_data_for_earnings_prediction", level=logging.WARNING
        ) as captured:
            evaluate(self.engine, supplier_observations=[],
                     customer_observations=[])
        self.assertIn("suppressed", "\n".join(captured.output))


class TestInvalidInput(unittest.TestCase):
    def setUp(self):
        self.engine = SupplyChainDataForEarningsPredictionEngine()

    def test_nan_growth_raises_rather_than_reporting_neutral(self):
        # v1 regression: NaN >= 1.0 and NaN <= -1.0 are both False, so v1 fell
        # through to a confident NEUTRAL on missing data.
        with self.assertRaises(ValueError):
            evaluate(self.engine, supplier_observations=[
                supplier(revenue_growth_pct=float("nan"))])
        with self.assertRaises(ValueError):
            evaluate(self.engine, consensus_revenue_growth_pct=float("nan"))

    def test_infinite_growth_raises(self):
        with self.assertRaises(ValueError):
            evaluate(self.engine, supplier_observations=[
                supplier(revenue_growth_pct=math.inf)])

    def test_non_numeric_growth_raises(self):
        for bad in ("25.0", None, True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    evaluate(self.engine, supplier_observations=[
                        supplier(revenue_growth_pct=bad)])

    def test_weights_outside_percentage_range_raise(self):
        with self.assertRaises(ValueError):
            evaluate(self.engine, supplier_observations=[
                supplier(supplier_share_of_target_cogs_pct=100.1)])
        with self.assertRaises(ValueError):
            evaluate(self.engine, supplier_observations=[
                supplier(supplier_share_of_target_cogs_pct=-1.0)])
        with self.assertRaises(ValueError):
            evaluate(self.engine, customer_observations=[
                customer(customer_share_of_target_revenue_pct=101.0)])

    def test_non_positive_dispersion_raises(self):
        for bad in (0.0, -1.0):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    evaluate(self.engine, consensus_dispersion_pct=bad)

    def test_nan_dispersion_raises(self):
        with self.assertRaises(ValueError):
            evaluate(self.engine, consensus_dispersion_pct=float("nan"))

    def test_duplicate_tickers_raise(self):
        with self.assertRaises(ValueError):
            evaluate(self.engine, supplier_observations=[supplier(), supplier()])
        with self.assertRaises(ValueError):
            evaluate(self.engine, customer_observations=[customer(), customer()])

    def test_weights_summing_above_one_hundred_percent_raise(self):
        with self.assertRaises(ValueError):
            evaluate(self.engine, supplier_observations=[
                supplier(supplier_ticker="A",
                         supplier_share_of_target_cogs_pct=60.0),
                supplier(supplier_ticker="B",
                         supplier_share_of_target_cogs_pct=50.0),
            ])
        with self.assertRaises(ValueError):
            evaluate(self.engine, customer_observations=[
                customer(customer_ticker="C1",
                         customer_share_of_target_revenue_pct=60.0),
                customer(customer_ticker="C2",
                         customer_share_of_target_revenue_pct=50.0),
            ])

    def test_wrong_observation_type_raises(self):
        with self.assertRaises(ValueError):
            evaluate(self.engine, supplier_observations=[customer()])
        with self.assertRaises(ValueError):
            evaluate(self.engine, customer_observations=[supplier()])

    def test_empty_target_asset_raises(self):
        with self.assertRaises(ValueError):
            evaluate(self.engine, target_asset="   ")

    def test_version_one_call_shape_fails_loudly(self):
        # v1 regression: v1's signature was
        #   (target_asset, supplier_revenue_growth_pct,
        #    customer_inventory_growth_pct, consensus_eps_growth_pct)
        # and it differenced a revenue estimate against an EPS consensus. Neither
        # the positional form nor the old keyword names may silently bind now.
        with self.assertRaises(TypeError):
            self.engine.evaluate_supply_chain_lead_signal("NVDA", 25.0, 5.0, 10.0)
        with self.assertRaises(TypeError):
            self.engine.evaluate_supply_chain_lead_signal(
                target_asset="NVDA",
                supplier_revenue_growth_pct=25.0,
                customer_inventory_growth_pct=5.0,
                consensus_eps_growth_pct=10.0,
            )


class TestAuditRecord(unittest.TestCase):
    def setUp(self):
        self.engine = SupplyChainDataForEarningsPredictionEngine(lead_time_months=2)

    def test_declared_lead_time_is_echoed_for_reproducibility(self):
        # v1 regression: v1 accepted lead_time_months and never read or reported it.
        self.assertEqual(evaluate(self.engine).declared_lead_time_months, 2)

    def test_submitted_and_used_counts_are_both_reported(self):
        result = evaluate(
            self.engine,
            supplier_observations=[
                supplier(supplier_ticker="A"),
                supplier(supplier_ticker="B",
                         target_share_of_supplier_revenue_pct=1.0,
                         supplier_share_of_target_cogs_pct=20.0),
            ],
        )
        self.assertEqual(result.suppliers_submitted, 2)
        self.assertEqual(result.suppliers_used, 1)
        self.assertEqual(result.low_read_through_excluded_count, 1)
        self.assertEqual(result.customers_submitted, 1)
        self.assertEqual(result.customers_used, 1)

    def test_consensus_inputs_are_echoed_for_replay(self):
        result = evaluate(self.engine, consensus_revenue_growth_pct=10.0,
                          consensus_dispersion_pct=5.0)
        self.assertEqual(result.consensus_revenue_growth_pct, 10.0)
        self.assertEqual(result.consensus_dispersion_pct, 5.0)
        self.assertEqual(result.asset_id, "NVDA")


if __name__ == "__main__":
    unittest.main()
