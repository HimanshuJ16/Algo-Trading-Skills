"""Unit tests for the offline market-data simulator.

Several tests are explicit regressions against defects in the 1.0.0 engine and are
labelled REGRESSION. Each of them fails against that implementation.
"""
import math
import random
import unittest

from market_data_simulator import (
    DEFAULT_START_TIMESTAMP_EPOCH,
    SECONDS_PER_YEAR_CONTINUOUS,
    TRADING_SECONDS_PER_YEAR_US_EQUITY_RTH,
    MarketDataSimulatorEngine,
    SimulatedTick,
    SimulationConfig,
    SimulationReport,
    decimals_for_tick,
)


class TestMarketDataSimulatorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MarketDataSimulatorEngine()

    # -- baseline behaviour ------------------------------------------------

    def test_gbm_tick_generation_and_reproducibility(self):
        cfg = SimulationConfig(
            symbol="AAPL", initial_price=100.0, drift_mu=0.05, volatility_sigma=0.20,
            time_step_sec=1.0, num_steps=500, spread_bps=10.0, random_seed=42
        )

        report1 = self.engine.generate_synthetic_tick_stream(cfg)
        report2 = self.engine.generate_synthetic_tick_stream(cfg)

        self.assertIsInstance(report1, SimulationReport)
        self.assertEqual(report1.status, "SIMULATION_COMPLETED")
        self.assertEqual(report1.total_ticks_generated, 500)
        self.assertEqual(len(report1.ticks), 500)
        self.assertGreater(report1.final_price, 0.0)
        self.assertTrue(report1.deterministic)
        self.assertEqual(report1.ticks, report2.ticks)

    def test_bid_ask_spread_ordering(self):
        cfg = SimulationConfig(
            symbol="BTC-USD", initial_price=50000.0, num_steps=100, spread_bps=20.0,
            random_seed=123, seconds_per_year=SECONDS_PER_YEAR_CONTINUOUS,
        )
        report = self.engine.generate_synthetic_tick_stream(cfg)

        for tick in report.ticks:
            self.assertLess(tick.bid_price, tick.ask_price)
            self.assertGreater(tick.bid_price, 0.0)
            self.assertLessEqual(tick.bid_price, tick.mid_price)
            self.assertLessEqual(tick.mid_price, tick.ask_price)
            self.assertEqual(tick.symbol, "BTC-USD")

    def test_sequence_ids_are_contiguous_from_one(self):
        report = self.engine.generate_synthetic_tick_stream(
            SimulationConfig(symbol="X", initial_price=100.0, num_steps=25,
                             random_seed=1))
        self.assertEqual([t.sequence_id for t in report.ticks], list(range(1, 26)))

    # -- REGRESSION: determinism -------------------------------------------

    def test_engine_does_not_seed_the_global_rng(self):
        """REGRESSION: 1.0.0 called random.seed(), hijacking the caller's stream."""
        random.seed(999)
        expected = [random.random() for _ in range(3)]

        random.seed(999)
        self.engine.generate_synthetic_tick_stream(
            SimulationConfig(symbol="X", initial_price=100.0, num_steps=50,
                             random_seed=42))
        actual = [random.random() for _ in range(3)]

        self.assertEqual(expected, actual)

    def test_default_start_timestamp_is_fixed_not_wall_clock(self):
        """REGRESSION: 1.0.0 defaulted start_timestamp_epoch to time.time(), so two
        'reproducible' runs carried different timestamps."""
        first = SimulationConfig(symbol="X", initial_price=100.0, num_steps=5,
                                 random_seed=42)
        second = SimulationConfig(symbol="X", initial_price=100.0, num_steps=5,
                                  random_seed=42)

        self.assertEqual(first.start_timestamp_epoch, DEFAULT_START_TIMESTAMP_EPOCH)
        self.assertEqual(
            [t.timestamp_nanos
             for t in self.engine.generate_synthetic_tick_stream(first).ticks],
            [t.timestamp_nanos
             for t in self.engine.generate_synthetic_tick_stream(second).ticks],
        )

    def test_depth_model_does_not_perturb_the_price_path(self):
        """Price and depth draw from independent streams, so re-tuning depth leaves an
        existing price fixture byte-identical."""
        def mids(**overrides):
            cfg = SimulationConfig(symbol="X", initial_price=100.0, num_steps=40,
                                   random_seed=9, **overrides)
            return [t.mid_price for t in self.engine.iter_synthetic_ticks(cfg)]

        self.assertEqual(mids(), mids(min_depth=1.0, max_depth=2.0))
        self.assertEqual(mids(), mids(depth_decimals=0))

    def test_unseeded_run_is_flagged_as_non_reproducible(self):
        with self.assertLogs("market_data_simulator", level="WARNING") as captured:
            report = self.engine.generate_synthetic_tick_stream(
                SimulationConfig(symbol="X", initial_price=100.0, num_steps=5,
                                 random_seed=None))
        self.assertFalse(report.deterministic)
        self.assertIn("NOT REPRODUCIBLE", report.audit_notes)
        self.assertTrue(any("cannot be reproduced" in line for line in captured.output))

    # -- REGRESSION: price quantisation ------------------------------------

    def test_sub_tick_instrument_does_not_collapse_to_zero(self):
        """REGRESSION: 1.0.0 rounded the *state* to 4dp, so any instrument priced
        below 0.00005 snapped to 0.0 on the first tick and stayed there forever,
        emitting an all-zero feed with bid == ask == 0."""
        cfg = SimulationConfig(
            symbol="TOKEN", initial_price=0.00005, num_steps=200, spread_bps=25.0,
            random_seed=7, price_tick_size=1e-8,
            seconds_per_year=SECONDS_PER_YEAR_CONTINUOUS,
        )
        report = self.engine.generate_synthetic_tick_stream(cfg)

        self.assertEqual(report.total_ticks_generated, 200)
        for tick in report.ticks:
            self.assertGreater(tick.bid_price, 0.0)
            self.assertLess(tick.bid_price, tick.ask_price)
        # The path must actually move, not sit pinned on one grid point.
        self.assertGreater(len({t.mid_price for t in report.ticks}), 1)

    def test_quantisation_does_not_feed_back_into_the_walk(self):
        """A coarse tick size must not change the underlying path: only its display."""
        def log_returns(tick_size):
            cfg = SimulationConfig(symbol="X", initial_price=100.0, num_steps=100,
                                   random_seed=11, price_tick_size=tick_size)
            return [t.log_return for t in self.engine.iter_synthetic_ticks(cfg)]

        self.assertEqual(log_returns(0.01), log_returns(1e-8))

    def test_prices_stay_on_the_configured_tick_grid(self):
        """A $0.25 futures grid needs two decimals, not one; rounding to one would
        move every odd quarter off the grid."""
        cfg = SimulationConfig(symbol="ES", initial_price=5000.0, num_steps=200,
                               price_tick_size=0.25, spread_bps=2.0, random_seed=3)
        for tick in self.engine.generate_synthetic_tick_stream(cfg).ticks:
            for price in (tick.bid_price, tick.mid_price, tick.ask_price):
                self.assertAlmostEqual(price / 0.25, round(price / 0.25), places=9)

    def test_decimals_for_tick(self):
        self.assertEqual(decimals_for_tick(0.01), 2)
        self.assertEqual(decimals_for_tick(0.0001), 4)
        self.assertEqual(decimals_for_tick(1e-8), 8)
        self.assertEqual(decimals_for_tick(0.005), 3)
        self.assertEqual(decimals_for_tick(0.25), 2)   # not 1
        self.assertEqual(decimals_for_tick(0.5), 1)
        self.assertEqual(decimals_for_tick(1.0), 0)
        self.assertEqual(decimals_for_tick(5.0), 0)
        with self.assertRaises(ValueError):
            decimals_for_tick(0.0)

    def test_tick_size_that_cannot_be_represented_exactly_is_rejected(self):
        """Silently quantising to a coarser grid than the caller asked for would be a
        lie about the venue being simulated, so it raises instead."""
        for tick in (1e-20, 3 * 1e-8, 1.0 / 3.0):
            with self.subTest(tick=tick):
                with self.assertRaises(ValueError):
                    decimals_for_tick(tick)
                with self.assertRaises(ValueError):
                    SimulationConfig(symbol="X", initial_price=1.0, num_steps=5,
                                     price_tick_size=tick)

    def test_tick_grid_coarser_than_the_price_is_rejected(self):
        """A grid coarser than the price quantises every mid to zero or a whole tick
        away from it -- and previously surfaced only as a confusing bid error."""
        with self.assertRaises(ValueError):
            SimulationConfig(symbol="X", initial_price=0.5, num_steps=5,
                             price_tick_size=1.0)

    def test_price_worth_few_ticks_warns(self):
        with self.assertLogs("market_data_simulator", level="WARNING") as captured:
            SimulationConfig(symbol="X", initial_price=0.20, num_steps=5,
                             price_tick_size=0.01)
        self.assertTrue(any("quantisation error" in line for line in captured.output))

    # -- REGRESSION: quote construction ------------------------------------

    def test_bid_stays_strictly_below_ask_for_sub_tick_spreads(self):
        """REGRESSION: 1.0.0 rounded the half-spread to 4dp, so any spread narrower
        than 1e-4 of the price produced bid == ask, violating the documented
        `bid < ask` invariant."""
        # At a $1.00 mid on a 0.0001 grid, one tick is exactly 1 bp. Anything below
        # that is unambiguously sub-tick; 1.0 bp is the boundary and lands either side
        # of it depending on where the mid has drifted, so it is checked separately.
        for spread_bps in (0.0, 1e-9, 0.5):
            cfg = SimulationConfig(symbol="X", initial_price=1.0, num_steps=100,
                                   spread_bps=spread_bps, random_seed=7,
                                   price_tick_size=0.0001)
            report = self.engine.generate_synthetic_tick_stream(cfg)
            with self.subTest(spread_bps=spread_bps):
                self.assertTrue(all(t.bid_price < t.ask_price for t in report.ticks))
                self.assertTrue(all(t.tick_constrained for t in report.ticks))
                self.assertEqual(report.tick_constrained_quote_count, 100)

        boundary = self.engine.generate_synthetic_tick_stream(
            SimulationConfig(symbol="X", initial_price=1.0, num_steps=100,
                             spread_bps=1.0, random_seed=7, price_tick_size=0.0001))
        self.assertTrue(all(t.bid_price < t.ask_price for t in boundary.ticks))
        self.assertEqual(
            boundary.tick_constrained_quote_count,
            sum(1 for t in boundary.ticks if t.tick_constrained))
        # It is genuinely a boundary: the mid straddles $1.00, so both outcomes occur.
        self.assertGreater(boundary.tick_constrained_quote_count, 0)
        self.assertLess(boundary.tick_constrained_quote_count, 100)

    def test_quoted_spread_is_never_narrower_than_requested(self):
        """Quotes are widened to the enclosing tick boundaries, never narrowed: a
        simulator that narrowed them would understate transaction costs."""
        cfg = SimulationConfig(symbol="X", initial_price=100.0, num_steps=500,
                               spread_bps=10.0, random_seed=17)
        report = self.engine.generate_synthetic_tick_stream(cfg)

        for tick in report.ticks:
            self.assertGreaterEqual(tick.quoted_spread_bps, 10.0 - 1e-9)
            self.assertFalse(tick.tick_constrained)
        self.assertGreaterEqual(report.mean_quoted_spread_bps, 10.0)
        # ...and the widening is bounded by one tick on each side.
        self.assertLess(report.mean_quoted_spread_bps, 10.0 + 2.0)

    def test_spread_wide_enough_to_zero_the_bid_is_rejected(self):
        """REGRESSION: 1.0.0 accepted spread_bps >= 20000 and emitted negative bids."""
        with self.assertRaises(ValueError):
            SimulationConfig(symbol="X", initial_price=100.0, num_steps=2,
                             spread_bps=20000.0)
        with self.assertRaises(ValueError):
            SimulationConfig(symbol="X", initial_price=100.0, num_steps=2,
                             spread_bps=30000.0)

        # Just inside the bound the bid must still be strictly positive.
        cfg = SimulationConfig(symbol="X", initial_price=100.0, num_steps=50,
                               spread_bps=19000.0, random_seed=5)
        for tick in self.engine.generate_synthetic_tick_stream(cfg).ticks:
            self.assertGreater(tick.bid_price, 0.0)
            self.assertLess(tick.bid_price, tick.ask_price)

    # -- quantitative correctness ------------------------------------------

    def test_zero_volatility_path_matches_the_closed_form(self):
        """With sigma = 0 the walk is deterministic: S_n = S_0 * exp(mu * n * dt).
        Expected values are derived from that identity, not from the implementation."""
        cfg = SimulationConfig(
            symbol="X", initial_price=100.0, drift_mu=0.05, volatility_sigma=0.0,
            time_step_sec=60.0, num_steps=250, price_tick_size=1e-8, random_seed=1,
        )
        dt = cfg.time_step_sec / cfg.seconds_per_year
        report = self.engine.generate_synthetic_tick_stream(cfg)

        for tick in report.ticks:
            expected = 100.0 * math.exp(0.05 * dt * tick.sequence_id)
            self.assertAlmostEqual(tick.mid_price, expected, places=8)
        self.assertEqual(report.realized_annualized_volatility, 0.0)

    def test_realized_volatility_matches_the_configured_sigma(self):
        """Self-consistency of the discretisation on the run's own clock."""
        cfg = SimulationConfig(symbol="X", initial_price=100.0, num_steps=20000,
                               volatility_sigma=0.20, random_seed=42)
        report = self.engine.generate_synthetic_tick_stream(cfg, retain_ticks=False)

        self.assertIsNotNone(report.realized_annualized_volatility)
        self.assertAlmostEqual(report.realized_annualized_volatility, 0.20, delta=0.006)
        self.assertEqual(report.configured_annualized_volatility, 0.20)

    def test_wall_clock_volatility_exposes_a_mismatched_clock(self):
        """A 24/7 instrument simulated on the 6.5h equity clock realises
        sqrt(31,536,000 / 5,896,800) ~= 2.3126x the requested sigma per elapsed
        wall-clock second. That factor is what the wall-clock figure surfaces."""
        expected_ratio = math.sqrt(
            SECONDS_PER_YEAR_CONTINUOUS / TRADING_SECONDS_PER_YEAR_US_EQUITY_RTH)
        self.assertAlmostEqual(expected_ratio, 2.3126, places=4)

        wrong = self.engine.generate_synthetic_tick_stream(
            SimulationConfig(symbol="BTC-USD", initial_price=50000.0, num_steps=20000,
                             volatility_sigma=0.20, random_seed=42),
            retain_ticks=False)
        right = self.engine.generate_synthetic_tick_stream(
            SimulationConfig(symbol="BTC-USD", initial_price=50000.0, num_steps=20000,
                             volatility_sigma=0.20, random_seed=42,
                             seconds_per_year=SECONDS_PER_YEAR_CONTINUOUS),
            retain_ticks=False)

        # On its own clock every run looks correctly calibrated -- which is exactly
        # why that figure cannot detect the mistake.
        self.assertAlmostEqual(wrong.realized_annualized_volatility, 0.20, delta=0.006)
        self.assertAlmostEqual(right.realized_annualized_volatility, 0.20, delta=0.006)

        # On the wall clock the equity-clock run is 2.31x too volatile.
        self.assertAlmostEqual(
            wrong.realized_wall_clock_annualized_volatility,
            0.20 * expected_ratio, delta=0.02)
        self.assertAlmostEqual(
            right.realized_wall_clock_annualized_volatility, 0.20, delta=0.006)

    def test_volatility_is_undefined_below_two_observations(self):
        report = self.engine.generate_synthetic_tick_stream(
            SimulationConfig(symbol="X", initial_price=100.0, num_steps=1,
                             random_seed=1))
        self.assertIsNone(report.realized_annualized_volatility)
        self.assertIsNone(report.realized_wall_clock_annualized_volatility)

    # -- timestamps --------------------------------------------------------

    def test_timestamps_are_exact_integer_nanoseconds(self):
        """REGRESSION: 1.0.0 accumulated float seconds. Float epoch seconds resolve to
        ~238ns at a 2020 epoch, so any sub-microsecond step collapsed to duplicate
        timestamps, and coarser steps accumulated drift."""
        cfg = SimulationConfig(symbol="X", initial_price=100.0, num_steps=1000,
                               time_step_sec=1e-7, random_seed=1)
        ticks = self.engine.generate_synthetic_tick_stream(cfg).ticks

        stamps = [t.timestamp_nanos for t in ticks]
        self.assertEqual(len(set(stamps)), 1000)
        self.assertEqual(stamps, sorted(stamps))
        start_nanos = round(DEFAULT_START_TIMESTAMP_EPOCH * 1e9)
        self.assertEqual(stamps[0], start_nanos + 100)
        self.assertEqual(stamps[-1], start_nanos + 1000 * 100)

    def test_timestamps_do_not_accumulate_float_drift(self):
        cfg = SimulationConfig(symbol="X", initial_price=100.0, num_steps=5000,
                               time_step_sec=0.1, random_seed=1)
        start_nanos = round(DEFAULT_START_TIMESTAMP_EPOCH * 1e9)
        for tick in self.engine.generate_synthetic_tick_stream(cfg).ticks:
            self.assertEqual(tick.timestamp_nanos,
                             start_nanos + tick.sequence_id * 100_000_000)

    def test_step_below_one_nanosecond_is_rejected(self):
        with self.assertRaises(ValueError):
            SimulationConfig(symbol="X", initial_price=100.0, num_steps=2,
                             time_step_sec=1e-10)

    # -- report semantics --------------------------------------------------

    def test_price_statistics_cover_emitted_ticks_only(self):
        """REGRESSION: 1.0.0 seeded min/max with initial_price, which is never emitted
        as a tick, so on any run whose extremum was the starting price the report
        quoted a price present in no tick. `num_steps=20, seed=1` is such a run: under
        1.0.0 it reported min_price = 100.0 while the lowest emitted tick was above
        it."""
        for num_steps in (20, 200):
            cfg = SimulationConfig(symbol="X", initial_price=100.0,
                                   num_steps=num_steps, random_seed=1,
                                   price_tick_size=1e-6)
            report = self.engine.generate_synthetic_tick_stream(cfg)

            mids = [t.mid_price for t in report.ticks]
            with self.subTest(num_steps=num_steps):
                self.assertEqual(report.min_price, min(mids))
                self.assertEqual(report.max_price, max(mids))
                self.assertEqual(report.final_price, mids[-1])
                self.assertEqual(report.total_simulated_duration_sec, float(num_steps))

    def test_streaming_matches_materialised_run(self):
        cfg = SimulationConfig(symbol="X", initial_price=100.0, num_steps=250,
                               random_seed=5)
        streamed = list(self.engine.iter_synthetic_ticks(cfg))
        report = self.engine.generate_synthetic_tick_stream(cfg)
        self.assertEqual(streamed, report.ticks)
        self.assertTrue(report.ticks_retained)

    def test_retain_ticks_false_keeps_statistics_but_drops_ticks(self):
        cfg = SimulationConfig(symbol="X", initial_price=100.0, num_steps=250,
                               random_seed=5)
        retained = self.engine.generate_synthetic_tick_stream(cfg)
        streamed = self.engine.generate_synthetic_tick_stream(cfg, retain_ticks=False)

        self.assertEqual(streamed.ticks, [])
        self.assertFalse(streamed.ticks_retained)
        self.assertEqual(streamed.total_ticks_generated, 250)
        for attr in ("final_price", "min_price", "max_price",
                     "realized_annualized_volatility", "mean_quoted_spread_bps",
                     "tick_constrained_quote_count"):
            self.assertEqual(getattr(streamed, attr), getattr(retained, attr), attr)

    def test_tick_is_a_plain_record(self):
        tick = next(iter(self.engine.iter_synthetic_ticks(
            SimulationConfig(symbol="X", initial_price=100.0, num_steps=1,
                             random_seed=1))))
        self.assertIsInstance(tick, SimulatedTick)
        self.assertEqual(tick.last_price, tick.mid_price)
        self.assertEqual(tick.timestamp_epoch, tick.timestamp_nanos / 1e9)

    # -- validation --------------------------------------------------------

    def test_invalid_configuration_is_rejected(self):
        cases = {
            "zero_price": (ValueError, dict(initial_price=0.0)),
            "negative_price": (ValueError, dict(initial_price=-1.0)),
            "nan_price": (ValueError, dict(initial_price=float("nan"))),
            "inf_price": (ValueError, dict(initial_price=float("inf"))),
            "nan_drift": (ValueError, dict(drift_mu=float("nan"))),
            "negative_sigma": (ValueError, dict(volatility_sigma=-0.1)),
            "zero_steps": (ValueError, dict(num_steps=0)),
            "negative_steps": (ValueError, dict(num_steps=-5)),
            "float_steps": (TypeError, dict(num_steps=10.0)),
            "bool_steps": (TypeError, dict(num_steps=True)),
            "zero_step_sec": (ValueError, dict(time_step_sec=0.0)),
            "negative_step_sec": (ValueError, dict(time_step_sec=-1.0)),
            "negative_spread": (ValueError, dict(spread_bps=-1.0)),
            "zero_tick": (ValueError, dict(price_tick_size=0.0)),
            "negative_tick": (ValueError, dict(price_tick_size=-0.01)),
            "zero_year": (ValueError, dict(seconds_per_year=0.0)),
            "blank_symbol": (ValueError, dict(symbol="   ")),
            "non_string_symbol": (ValueError, dict(symbol=123)),
            "negative_depth": (ValueError, dict(min_depth=-1.0)),
            "inverted_depth": (ValueError, dict(min_depth=10.0, max_depth=1.0)),
            "bad_depth_decimals": (ValueError, dict(depth_decimals=-1)),
            "string_seed": (TypeError, dict(random_seed="42")),
            "nan_start": (ValueError, dict(start_timestamp_epoch=float("nan"))),
        }
        for name, (exc, overrides) in cases.items():
            params = dict(symbol="X", initial_price=100.0, num_steps=10)
            params.update(overrides)
            with self.subTest(case=name):
                with self.assertRaises(exc):
                    SimulationConfig(**params)

    def test_configuration_is_revalidated_at_generation_time(self):
        """A config mutated after construction must not reach the price loop."""
        cfg = SimulationConfig(symbol="X", initial_price=100.0, num_steps=10,
                               random_seed=1)
        cfg.initial_price = -5.0
        with self.assertRaises(ValueError):
            self.engine.generate_synthetic_tick_stream(cfg)
        cfg.initial_price = 100.0
        cfg.num_steps = 0
        with self.assertRaises(ValueError):
            list(self.engine.iter_synthetic_ticks(cfg))

    def test_generator_validates_eagerly_not_on_first_next(self):
        """An invalid config must fail where it was passed, not wherever the iterator
        happens to be consumed."""
        cfg = SimulationConfig(symbol="X", initial_price=100.0, num_steps=5,
                               random_seed=1)
        cfg.initial_price = -1.0
        with self.assertRaises(ValueError):
            self.engine.iter_synthetic_ticks(cfg)   # not wrapped in list()

    def test_integer_parameters_are_accepted(self):
        report = self.engine.generate_synthetic_tick_stream(
            SimulationConfig(symbol="X", initial_price=100, num_steps=3, spread_bps=10,
                             random_seed=1))
        self.assertEqual(report.total_ticks_generated, 3)
        self.assertIsInstance(report.initial_price, float)

    def test_overflowing_path_raises_rather_than_emitting_inf(self):
        """An absurd sigma/step combination must fail loudly, not emit inf prices."""
        cfg = SimulationConfig(
            symbol="X", initial_price=100.0, volatility_sigma=1e5,
            time_step_sec=1e6, num_steps=5000, seconds_per_year=1e-6,
            random_seed=1, price_tick_size=1e-8,
        )
        with self.assertRaises(ArithmeticError):
            self.engine.generate_synthetic_tick_stream(cfg)


if __name__ == '__main__':
    unittest.main()
