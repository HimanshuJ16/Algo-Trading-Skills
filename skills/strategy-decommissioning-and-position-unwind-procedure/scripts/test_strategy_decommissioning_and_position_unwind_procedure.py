"""Unit tests for the strategy decommissioning / position unwind engine.

Expected values are derived by hand from the position inputs, never by re-running the
engine's own arithmetic. Tests marked "regression" fail against the pre-2.0.0 behaviour
described in their docstring.
"""
import unittest

from strategy_decommissioning_and_position_unwind_procedure import (
    DEFAULT_MAX_ADV_SLICE_PCT,
    DecommissionState,
    DecommissionStateError,
    EntryBlockedError,
    StrategyDecommissioningEngine,
    StrategyPosition,
)


def _book():
    """AAPL long 1,000 @ 150 (ADV 5,000) and MSFT short 500 @ 300 (ADV 2,000).

    At a 10% participation cap the first wave is 500 AAPL and 200 MSFT.
    Initial notional = 1,000 * 150 + 500 * 300 = 300,000.
    """
    return [
        StrategyPosition("AAPL", quantity=1000.0, market_price=150.0, avg_daily_volume=5000.0),
        StrategyPosition("MSFT", quantity=-500.0, market_price=300.0, avg_daily_volume=2000.0),
    ]


class TestStrategyPositionValidation(unittest.TestCase):
    def test_default_participation_cap_is_ten_percent(self):
        self.assertEqual(DEFAULT_MAX_ADV_SLICE_PCT, 10.0)
        self.assertEqual(
            StrategyPosition("AAPL", 100.0, 10.0, 1000.0).max_adv_slice_pct, 10.0)

    def test_zero_adv_rejected(self):
        """Regression: a zero ADV used to yield a zero-quantity slice forever."""
        with self.assertRaises(ValueError) as ctx:
            StrategyPosition("AAPL", 100.0, 10.0, 0.0)
        self.assertIn("avg_daily_volume", str(ctx.exception))

    def test_negative_adv_rejected(self):
        with self.assertRaises(ValueError):
            StrategyPosition("AAPL", 100.0, 10.0, -5000.0)

    def test_non_positive_price_rejected(self):
        for price in (0.0, -10.0):
            with self.subTest(price=price):
                with self.assertRaises(ValueError):
                    StrategyPosition("AAPL", 100.0, price, 1000.0)

    def test_participation_cap_bounds(self):
        for pct in (0.0, -5.0, 100.001, 250.0):
            with self.subTest(pct=pct):
                with self.assertRaises(ValueError):
                    StrategyPosition("AAPL", 100.0, 10.0, 1000.0, max_adv_slice_pct=pct)
        self.assertEqual(
            StrategyPosition("AAPL", 100.0, 10.0, 1000.0, max_adv_slice_pct=100.0
                             ).max_adv_slice_pct, 100.0)

    def test_non_finite_quantity_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    StrategyPosition("AAPL", bad, 10.0, 1000.0)

    def test_empty_symbol_and_bad_lot_size_rejected(self):
        with self.assertRaises(ValueError):
            StrategyPosition("   ", 100.0, 10.0, 1000.0)
        with self.assertRaises(ValueError):
            StrategyPosition("AAPL", 100.0, 10.0, 1000.0, lot_size=0.0)

    def test_boolean_quantity_rejected(self):
        with self.assertRaises(TypeError):
            StrategyPosition("AAPL", True, 10.0, 1000.0)


class TestEntryBlocking(unittest.TestCase):
    def setUp(self):
        self.engine = StrategyDecommissioningEngine("STRAT_MOMENTUM_ALPHA")
        self.engine.load_positions(_book())

    def test_entries_allowed_only_while_active(self):
        """Regression: new_entries_allowed used to be hard-coded False in every report."""
        self.assertTrue(self.engine.new_entries_allowed)
        self.assertTrue(self.engine.status_report().new_entries_allowed)
        self.engine.assert_entry_allowed("AAPL")

        report = self.engine.initiate_decommissioning("IR 0.31 below the 0.50 floor")
        self.assertEqual(report.state, DecommissionState.ORDER_ENTRY_BLOCKED)
        self.assertFalse(report.new_entries_allowed)
        self.assertFalse(self.engine.new_entries_allowed)
        with self.assertRaises(EntryBlockedError):
            self.engine.assert_entry_allowed("AAPL")

    def test_reason_is_mandatory(self):
        for bad in ("", "   "):
            with self.subTest(reason=bad):
                with self.assertRaises(ValueError):
                    StrategyDecommissioningEngine("S").initiate_decommissioning(bad)

    def test_cannot_reinitiate(self):
        self.engine.initiate_decommissioning("alpha decay")
        with self.assertRaises(DecommissionStateError):
            self.engine.initiate_decommissioning("someone pressed the button twice")
        self.assertEqual(self.engine.decommission_reason, "alpha decay")

    def test_engine_id_must_be_non_empty(self):
        with self.assertRaises(ValueError):
            StrategyDecommissioningEngine("  ")


class TestLoadPositions(unittest.TestCase):
    def test_duplicate_symbol_rejected(self):
        engine = StrategyDecommissioningEngine("S")
        engine.load_positions([StrategyPosition("AAPL", 100.0, 10.0, 1000.0)])
        with self.assertRaises(ValueError):
            engine.load_positions([StrategyPosition("AAPL", 50.0, 10.0, 1000.0)])
        self.assertEqual(engine.positions["AAPL"].quantity, 100.0)

    def test_generator_input_is_materialised_before_use(self):
        engine = StrategyDecommissioningEngine("S")
        engine.load_positions(p for p in _book())
        self.assertEqual(sorted(engine.positions), ["AAPL", "MSFT"])
        self.assertAlmostEqual(engine.initial_total_notional_usd, 300_000.0)

    def test_initial_notional_is_marked_once_at_load(self):
        engine = StrategyDecommissioningEngine("S")
        engine.load_positions(_book())
        self.assertAlmostEqual(engine.initial_total_notional_usd, 300_000.0)

    def test_load_after_flat_rejected(self):
        engine = StrategyDecommissioningEngine("S")
        engine.load_positions([StrategyPosition("AAPL", 100.0, 10.0, 1000.0)])
        engine.initiate_decommissioning("retired")
        engine.record_slice_execution("manual", "AAPL", 100.0, 10.0, 0.0, execution_id="E1")
        self.assertEqual(engine.state, DecommissionState.FULLY_UNWOUND)
        with self.assertRaises(DecommissionStateError):
            engine.load_positions([StrategyPosition("MSFT", 10.0, 10.0, 1000.0)])


class TestSlicing(unittest.TestCase):
    def setUp(self):
        self.engine = StrategyDecommissioningEngine("STRAT_MOMENTUM_ALPHA")
        self.engine.load_positions(_book())

    def test_slicing_requires_decommissioning_first(self):
        with self.assertRaises(DecommissionStateError):
            self.engine.generate_unwind_liquidation_slices()

    def test_first_wave_quantities_sides_and_participation(self):
        self.engine.initiate_decommissioning("alpha decay")
        report = self.engine.generate_unwind_liquidation_slices()

        self.assertEqual(report.state, DecommissionState.UNWIND_IN_PROGRESS)
        self.assertEqual(len(report.slices_generated), 2)
        by_symbol = {s.symbol: s for s in report.slices_generated}

        aapl = by_symbol["AAPL"]                     # long 1000, cap 10% of 5000 = 500
        self.assertEqual(aapl.side, "SELL")
        self.assertEqual(aapl.slice_quantity, 500.0)
        self.assertEqual(aapl.remaining_after_slice_quantity, 500.0)
        self.assertAlmostEqual(aapl.participation_pct, 10.0)
        self.assertFalse(aapl.is_final_slice)
        self.assertEqual(aapl.wave_index, 1)

        msft = by_symbol["MSFT"]                     # short -500, cap 10% of 2000 = 200
        self.assertEqual(msft.side, "BUY")
        self.assertEqual(msft.slice_quantity, 200.0)
        self.assertEqual(msft.remaining_after_slice_quantity, -300.0)
        self.assertAlmostEqual(msft.participation_pct, 10.0)
        self.assertFalse(msft.is_final_slice)

    def test_position_below_cap_becomes_a_single_final_slice(self):
        engine = StrategyDecommissioningEngine("S")
        engine.load_positions([StrategyPosition("AAPL", 100.0, 150.0, 5000.0)])
        engine.initiate_decommissioning("retired")
        sliced = engine.generate_unwind_liquidation_slices().slices_generated[0]
        self.assertEqual(sliced.slice_quantity, 100.0)      # 100 <= 10% of 5000
        self.assertTrue(sliced.is_final_slice)
        self.assertEqual(sliced.remaining_after_slice_quantity, 0.0)
        self.assertAlmostEqual(sliced.participation_pct, 2.0)

    def test_non_final_wave_floors_to_a_whole_lot(self):
        engine = StrategyDecommissioningEngine("S")
        # cap = 10% of 1234 = 123.4 -> floor(123.4 / 100) * 100 = 100
        engine.load_positions([
            StrategyPosition("XYZ", 500.0, 10.0, 1234.0, lot_size=100.0)])
        engine.initiate_decommissioning("retired")
        sliced = engine.generate_unwind_liquidation_slices().slices_generated[0]
        self.assertEqual(sliced.slice_quantity, 100.0)
        self.assertEqual(sliced.remaining_after_slice_quantity, 400.0)

    def test_final_wave_sends_the_odd_lot_residual(self):
        engine = StrategyDecommissioningEngine("S")
        # 30 <= cap (10% of 500 = 50), so the residual ships whole despite lot_size 100.
        engine.load_positions([
            StrategyPosition("XYZ", 30.0, 10.0, 500.0, lot_size=100.0)])
        engine.initiate_decommissioning("retired")
        sliced = engine.generate_unwind_liquidation_slices().slices_generated[0]
        self.assertEqual(sliced.slice_quantity, 30.0)
        self.assertTrue(sliced.is_final_slice)

    def test_cap_below_one_lot_is_reported_not_silently_rounded(self):
        engine = StrategyDecommissioningEngine("S")
        # cap = 10% of 500 = 50 < lot_size 100, and 500 > cap, so no compliant wave exists.
        engine.load_positions([
            StrategyPosition("ILLIQ", 500.0, 10.0, 500.0, lot_size=100.0)])
        engine.initiate_decommissioning("retired")
        report = engine.generate_unwind_liquidation_slices()
        self.assertEqual(report.slices_generated, [])
        self.assertEqual(report.unsliceable_symbols, ["ILLIQ"])
        self.assertEqual(engine.positions["ILLIQ"].quantity, 500.0)
        self.assertNotEqual(engine.state, DecommissionState.FULLY_UNWOUND)
        # A read-only snapshot must surface the same blockage, not an empty list.
        self.assertEqual(engine.status_report().unsliceable_symbols, ["ILLIQ"])

    def test_regenerating_does_not_reauthorise_an_open_slice(self):
        """Regression: the old engine re-emitted a full-size wave per call, so two
        rounds of generate-then-send released 1,000 AAPL shares for a 1,000 share
        position and flipped it short on the second fill."""
        self.engine.initiate_decommissioning("alpha decay")
        first = self.engine.generate_unwind_liquidation_slices()
        second = self.engine.generate_unwind_liquidation_slices()
        self.assertEqual(len(first.slices_generated), 2)
        self.assertEqual(second.slices_generated, [])
        self.assertEqual(len(second.open_slice_ids), 2)

    def test_slice_ids_are_unique_across_waves(self):
        self.engine.initiate_decommissioning("alpha decay")
        wave1 = self.engine.generate_unwind_liquidation_slices().slices_generated
        for s in wave1:
            self.engine.record_slice_execution(
                s.slice_id, s.symbol, s.slice_quantity, s.target_price, 0.0,
                execution_id=f"E-{s.slice_id}")
        wave2 = self.engine.generate_unwind_liquidation_slices().slices_generated
        ids = [s.slice_id for s in wave1] + [s.slice_id for s in wave2]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual({s.wave_index for s in wave2}, {2})

    def test_partial_fill_keeps_the_slice_open(self):
        self.engine.initiate_decommissioning("alpha decay")
        aapl = [s for s in self.engine.generate_unwind_liquidation_slices().slices_generated
                if s.symbol == "AAPL"][0]
        self.engine.record_slice_execution(aapl.slice_id, "AAPL", 200.0, 150.0, 0.0,
                                           execution_id="E1")
        self.assertIn(aapl.slice_id, self.engine.status_report().open_slice_ids)
        self.engine.record_slice_execution(aapl.slice_id, "AAPL", 300.0, 150.0, 0.0,
                                           execution_id="E2")
        self.assertNotIn(aapl.slice_id, self.engine.status_report().open_slice_ids)
        self.assertEqual(self.engine.positions["AAPL"].quantity, 500.0)

    def test_cancel_slice_releases_the_symbol_for_reslicing(self):
        self.engine.initiate_decommissioning("alpha decay")
        aapl = [s for s in self.engine.generate_unwind_liquidation_slices().slices_generated
                if s.symbol == "AAPL"][0]
        self.engine.cancel_slice(aapl.slice_id, "venue halted")
        with self.assertRaises(ValueError):
            self.engine.cancel_slice(aapl.slice_id)
        resliced = [s for s in self.engine.generate_unwind_liquidation_slices().slices_generated
                    if s.symbol == "AAPL"]
        self.assertEqual(len(resliced), 1)
        self.assertEqual(resliced[0].wave_index, 2)

    def test_cannot_slice_once_flat(self):
        engine = StrategyDecommissioningEngine("S")
        engine.load_positions([StrategyPosition("AAPL", 100.0, 10.0, 5000.0)])
        engine.initiate_decommissioning("retired")
        sliced = engine.generate_unwind_liquidation_slices().slices_generated[0]
        engine.record_slice_execution(sliced.slice_id, "AAPL", 100.0, 10.0, 0.0,
                                      execution_id="E1")
        self.assertEqual(engine.state, DecommissionState.FULLY_UNWOUND)
        with self.assertRaises(DecommissionStateError):
            engine.generate_unwind_liquidation_slices()


class TestExecutionRecording(unittest.TestCase):
    def setUp(self):
        self.engine = StrategyDecommissioningEngine("STRAT_MOMENTUM_ALPHA")
        self.engine.load_positions(_book())
        self.engine.initiate_decommissioning("alpha decay")

    def test_execution_before_initiation_rejected(self):
        fresh = StrategyDecommissioningEngine("S")
        fresh.load_positions([StrategyPosition("AAPL", 100.0, 10.0, 1000.0)])
        with self.assertRaises(DecommissionStateError):
            fresh.record_slice_execution("S1", "AAPL", 10.0, 10.0, 0.0, execution_id="E1")

    def test_unknown_symbol_rejected(self):
        """Regression: a fill for a symbol outside the inventory used to be a silent no-op."""
        with self.assertRaises(ValueError):
            self.engine.record_slice_execution("S1", "TSLA", 10.0, 10.0, 0.0,
                                               execution_id="E1")

    def test_invalid_fill_values_rejected(self):
        for qty, px in ((0.0, 10.0), (-5.0, 10.0), (float("nan"), 10.0),
                        (10.0, 0.0), (10.0, float("inf"))):
            with self.subTest(qty=qty, px=px):
                with self.assertRaises(ValueError):
                    self.engine.record_slice_execution("S1", "AAPL", qty, px, 0.0,
                                                       execution_id=f"E{qty}{px}")
        with self.assertRaises(ValueError):
            self.engine.record_slice_execution("S1", "AAPL", 10.0, 10.0, float("nan"),
                                               execution_id="EP")

    def test_duplicate_execution_id_is_suppressed(self):
        """Regression: a replayed fill callback used to double-decrement the position
        and double-count realized P&L."""
        self.engine.record_slice_execution("S1", "AAPL", 400.0, 151.0, 1000.0,
                                           execution_id="FILL-1")
        self.engine.record_slice_execution("S1", "AAPL", 400.0, 151.0, 1000.0,
                                           execution_id="FILL-1")
        self.assertEqual(self.engine.positions["AAPL"].quantity, 600.0)
        self.assertEqual(self.engine.liquidated_pnl_usd, 1000.0)
        self.assertAlmostEqual(self.engine.liquidated_notional_usd, 400.0 * 151.0)

    def test_missing_execution_id_warns_and_cannot_deduplicate(self):
        with self.assertLogs(
                "strategy_decommissioning_and_position_unwind_procedure", level="WARNING") as log:
            self.engine.record_slice_execution("S1", "AAPL", 100.0, 150.0, 0.0)
        self.assertTrue(any("execution_id" in line for line in log.output))
        self.engine.record_slice_execution("S1", "AAPL", 100.0, 150.0, 0.0)
        self.assertEqual(self.engine.positions["AAPL"].quantity, 800.0)

    def test_overfill_flips_the_position_and_raises_a_break(self):
        """Regression: an overfill used to be clamped to flat, reporting a closed book
        while an unintended short was live."""
        self.engine.record_slice_execution("S1", "AAPL", 1500.0, 150.0, 0.0,
                                           execution_id="FILL-OVER")
        self.assertEqual(self.engine.positions["AAPL"].quantity, -500.0)
        self.assertEqual(len(self.engine.reconciliation_breaks), 1)
        self.assertEqual(self.engine.reconciliation_breaks[0].symbol, "AAPL")
        self.assertIn("short", self.engine.reconciliation_breaks[0].description)
        self.assertNotEqual(self.engine.state, DecommissionState.FULLY_UNWOUND)

    def test_short_side_fills_reduce_toward_zero(self):
        self.engine.record_slice_execution("S1", "MSFT", 200.0, 299.0, -400.0,
                                           execution_id="FILL-M1")
        self.assertEqual(self.engine.positions["MSFT"].quantity, -300.0)
        self.engine.record_slice_execution("S2", "MSFT", 300.0, 299.0, -600.0,
                                           execution_id="FILL-M2")
        self.assertEqual(self.engine.positions["MSFT"].quantity, 0.0)
        self.assertEqual(self.engine.liquidated_pnl_usd, -1000.0)

    def test_float_residue_is_treated_as_flat(self):
        engine = StrategyDecommissioningEngine("S")
        engine.load_positions([StrategyPosition("XYZ", 0.3, 10.0, 1000.0)])
        engine.initiate_decommissioning("retired")
        engine.record_slice_execution("S1", "XYZ", 0.1, 10.0, 0.0, execution_id="E1")
        engine.record_slice_execution("S1", "XYZ", 0.2, 10.0, 0.0, execution_id="E2")
        self.assertEqual(engine.positions["XYZ"].quantity, 0.0)
        self.assertEqual(engine.state, DecommissionState.FULLY_UNWOUND)


class TestNotionalAccounting(unittest.TestCase):
    """Regression: the old report added the notional of *generated* slices to the
    remaining notional, inflating `initial_total_notional_usd` and reporting shares as
    liquidated before a single fill had occurred."""

    def setUp(self):
        self.engine = StrategyDecommissioningEngine("STRAT_MOMENTUM_ALPHA")
        self.engine.load_positions(_book())
        self.engine.initiate_decommissioning("alpha decay")

    def test_generating_slices_liquidates_nothing(self):
        report = self.engine.generate_unwind_liquidation_slices()
        self.assertAlmostEqual(report.initial_total_notional_usd, 300_000.0)
        self.assertAlmostEqual(report.remaining_notional_usd, 300_000.0)
        self.assertAlmostEqual(report.liquidated_notional_usd, 0.0)

    def test_liquidated_notional_tracks_actual_fills(self):
        waves = self.engine.generate_unwind_liquidation_slices().slices_generated
        by_symbol = {s.symbol: s for s in waves}
        # 500 AAPL @ 151 = 75,500 ; 200 MSFT @ 299 = 59,800 -> 135,300
        self.engine.record_slice_execution(
            by_symbol["AAPL"].slice_id, "AAPL", 500.0, 151.0, 500.0, execution_id="F1")
        self.engine.record_slice_execution(
            by_symbol["MSFT"].slice_id, "MSFT", 200.0, 299.0, -200.0, execution_id="F2")

        report = self.engine.status_report()
        self.assertAlmostEqual(report.initial_total_notional_usd, 300_000.0)
        self.assertAlmostEqual(report.liquidated_notional_usd, 135_300.0)
        # Remaining is marked at the load price: 500 * 150 + 300 * 300 = 165,000.
        self.assertAlmostEqual(report.remaining_notional_usd, 165_000.0)
        self.assertAlmostEqual(report.total_realized_pnl_usd, 300.0)


class TestCompletionGates(unittest.TestCase):
    def _flat_engine(self, working_order_ids=()):
        engine = StrategyDecommissioningEngine("S")
        engine.load_positions([StrategyPosition("AAPL", 100.0, 150.0, 5000.0)])
        engine.initiate_decommissioning("retired", working_order_ids=working_order_ids)
        return engine

    def test_working_orders_block_completion_until_confirmed(self):
        engine = self._flat_engine(working_order_ids=["O-1", "O-2"])
        engine.record_slice_execution("S1", "AAPL", 100.0, 150.0, 0.0, execution_id="E1")
        self.assertEqual(engine.positions["AAPL"].quantity, 0.0)
        self.assertNotEqual(engine.state, DecommissionState.FULLY_UNWOUND)
        self.assertEqual(engine.pending_order_cancellations(), ["O-1", "O-2"])
        with self.assertRaises(DecommissionStateError):
            engine.return_capital_to_treasury()

        engine.record_order_cancellation("O-1")
        self.assertNotEqual(engine.state, DecommissionState.FULLY_UNWOUND)
        engine.record_order_cancellation("O-2")
        self.assertEqual(engine.state, DecommissionState.FULLY_UNWOUND)
        report = engine.return_capital_to_treasury()
        self.assertEqual(report.state, DecommissionState.DECOMMISSION_COMPLETE)

    def test_unregistered_cancellation_rejected_and_duplicates_ignored(self):
        engine = self._flat_engine(working_order_ids=["O-1"])
        with self.assertRaises(ValueError):
            engine.record_order_cancellation("O-99")
        engine.record_order_cancellation("O-1")
        engine.record_order_cancellation("O-1")
        self.assertEqual(engine.pending_order_cancellations(), [])

    def test_zero_quantity_position_does_not_block_completion(self):
        """Regression: completion used to require the position dict to become empty, so
        a position loaded flat left the strategy stuck in UNWIND_IN_PROGRESS forever."""
        engine = StrategyDecommissioningEngine("S")
        engine.load_positions([
            StrategyPosition("AAPL", 100.0, 150.0, 5000.0),
            StrategyPosition("ZERO", 0.0, 10.0, 1000.0),
        ])
        engine.initiate_decommissioning("retired")
        engine.record_slice_execution("S1", "AAPL", 100.0, 150.0, 0.0, execution_id="E1")
        self.assertEqual(engine.state, DecommissionState.FULLY_UNWOUND)
        self.assertIn("ZERO", engine.positions)

    def test_capital_return_blocked_while_a_position_remains(self):
        engine = self._flat_engine()
        with self.assertRaises(DecommissionStateError) as ctx:
            engine.return_capital_to_treasury()
        self.assertIn("AAPL", str(ctx.exception))

    def test_capital_return_blocked_by_unacknowledged_breaks(self):
        engine = self._flat_engine()
        engine.record_slice_execution("S1", "AAPL", 150.0, 150.0, 0.0, execution_id="E1")
        # Position is now -50; unwind the flip so the book is flat but the break stands.
        engine.record_slice_execution("S2", "AAPL", 50.0, 150.0, 0.0, execution_id="E2")
        self.assertEqual(engine.state, DecommissionState.FULLY_UNWOUND)
        with self.assertRaises(DecommissionStateError):
            engine.return_capital_to_treasury()
        report = engine.return_capital_to_treasury(acknowledge_breaks=True)
        self.assertEqual(report.state, DecommissionState.DECOMMISSION_COMPLETE)
        self.assertEqual(len(report.reconciliation_breaks), 1)

    def test_capital_return_is_not_repeatable(self):
        engine = self._flat_engine()
        engine.record_slice_execution("S1", "AAPL", 100.0, 150.0, 0.0, execution_id="E1")
        engine.return_capital_to_treasury()
        with self.assertRaises(DecommissionStateError):
            engine.return_capital_to_treasury()

    def test_late_fill_after_completion_rejected(self):
        engine = self._flat_engine()
        engine.record_slice_execution("S1", "AAPL", 100.0, 150.0, 0.0, execution_id="E1")
        engine.return_capital_to_treasury()
        with self.assertRaises(DecommissionStateError):
            engine.record_slice_execution("S2", "AAPL", 10.0, 150.0, 0.0, execution_id="E2")

    def test_status_report_does_not_advance_state(self):
        engine = self._flat_engine()
        before = engine.state
        engine.status_report()
        self.assertEqual(engine.state, before)


class TestEndToEndUnwind(unittest.TestCase):
    def test_full_lifecycle_reaches_treasury_return(self):
        engine = StrategyDecommissioningEngine("STRAT_MOMENTUM_ALPHA")
        engine.load_positions(_book())
        engine.initiate_decommissioning("Sharpe 0.21 over 6 months, committee vote 2026-08-14",
                                        working_order_ids=["O-1"])
        engine.record_order_cancellation("O-1")

        waves = 0
        while engine.state is not DecommissionState.FULLY_UNWOUND:
            waves += 1
            self.assertLess(waves, 20, "unwind failed to converge")
            report = engine.generate_unwind_liquidation_slices()
            self.assertEqual(report.unsliceable_symbols, [])
            for child in report.slices_generated:
                engine.record_slice_execution(
                    child.slice_id, child.symbol, child.slice_quantity,
                    child.target_price, 0.0, execution_id=f"F-{child.slice_id}")

        # AAPL 1000 at 500/wave = 2 waves; MSFT 500 at 200/wave = 3 waves.
        self.assertEqual(waves, 3)
        final = engine.return_capital_to_treasury()
        self.assertEqual(final.state, DecommissionState.DECOMMISSION_COMPLETE)
        self.assertFalse(final.new_entries_allowed)
        self.assertEqual(final.reconciliation_breaks, [])
        self.assertAlmostEqual(final.remaining_notional_usd, 0.0)
        # Every wave filled at its mark, so liquidated notional equals initial notional.
        self.assertAlmostEqual(final.liquidated_notional_usd, 300_000.0)
        self.assertAlmostEqual(final.initial_total_notional_usd, 300_000.0)
        self.assertTrue(engine.audit_trail)


if __name__ == "__main__":
    unittest.main()
