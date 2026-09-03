"""Unit tests for backtest-determinism-and-reproducibility.

Coverage:
1. Exact float divergence detection — the regression that mattered: rounding
   before hashing made the detector blind to the divergences it exists to catch.
2. Cash-flow accounting, which previously summed unsigned notional.
3. Non-finite and malformed input rejection.
4. Deterministic event-stream ordering, including refusal to default or tie.
5. Simulated clock, seeding, and PYTHONHASHSEED reporting.
"""
import logging
import os
import random
import unittest
from unittest import mock

from reproducibility_engine import (
    HAS_NUMPY,
    BacktestDeterminismEngine,
    DeterminismError,
    DeterministicAuditReport,
    SimulatedClock,
    TradeExecutionRecord,
    TradeSide,
)

logging.getLogger("reproducibility_engine").setLevel(logging.CRITICAL)


def engine(**kwargs):
    kwargs.setdefault("apply_seeds_on_init", False)
    return BacktestDeterminismEngine(master_seed=42, **kwargs)


def trade(ts=1000.0, sym="AAPL", act="BUY", qty=100.0, px=150.0):
    return TradeExecutionRecord(ts, sym, act, qty, px)


class TestExactDivergenceDetection(unittest.TestCase):
    """The detector must detect. Rounding before hashing defeated it."""

    def setUp(self):
        self.engine = engine()

    def test_summation_order_divergence_is_caught(self):
        # The canonical non-determinism signature: same value, different
        # accumulation order.
        #
        # Accumulate explicitly rather than with sum(). CPython 3.12 gave the
        # built-in sum() Neumaier compensated summation for floats, so
        # sum([0.1] * 10) is 0.9999999999999999 up to 3.11 and exactly 1.0 from
        # 3.12 on. A plain += loop stays naive on every version, which is the
        # behaviour a backtest accumulating a P&L series actually has.
        a = 0.0
        for _ in range(10):
            a += 0.1
        b = 0.1 * 10
        self.assertNotEqual(a, b)
        report = self.engine.audit_reproducibility(
            [trade(px=a)], [trade(px=b)]
        )
        self.assertFalse(report.is_bit_identical)

    def test_sub_ulp_price_divergence_is_caught(self):
        report = self.engine.audit_reproducibility(
            [trade(px=150.00000001)], [trade(px=150.00000009)]
        )
        self.assertFalse(report.is_bit_identical)

    def test_negative_zero_is_distinguished_from_zero(self):
        # -0.0 == 0.0 compares True but has a different bit pattern; a run that
        # produced one and then the other has diverged.
        self.assertEqual(-0.0, 0.0)
        report = self.engine.audit_reproducibility([trade(qty=0.0)], [trade(qty=-0.0)])
        self.assertFalse(report.is_bit_identical)

    def test_identical_runs_verify(self):
        trades = [trade(), trade(ts=1005.0, act="SELL", px=155.0)]
        report = self.engine.audit_reproducibility(trades, list(trades))
        self.assertTrue(report.is_bit_identical)
        self.assertIsNone(report.first_divergence_index)
        self.assertEqual(len(report.trade_log_checksum), 64)

    def test_first_divergence_index_is_reported(self):
        run1 = [trade(), trade(ts=1001.0), trade(ts=1002.0)]
        run2 = [trade(), trade(ts=1001.0), trade(ts=1002.0, px=999.0)]
        report = self.engine.audit_reproducibility(run1, run2)
        self.assertFalse(report.is_bit_identical)
        self.assertEqual(report.first_divergence_index, 2)

    def test_differing_lengths_are_reported(self):
        report = self.engine.audit_reproducibility([trade(), trade(ts=1001.0)], [trade()])
        self.assertFalse(report.is_bit_identical)
        self.assertEqual(report.first_divergence_index, 1)

    def test_float_precision_restores_tolerant_comparison(self):
        # Explicit opt-in to the old behaviour, documented as lossy.
        tolerant = engine(float_precision=6)
        report = tolerant.audit_reproducibility(
            [trade(px=150.00000001)], [trade(px=150.00000009)]
        )
        self.assertTrue(report.is_bit_identical)

    def test_generator_input_does_not_zero_the_cash_flow(self):
        # The run is consumed twice internally; a generator must not silently
        # produce a correct checksum alongside a zero cash flow.
        trades = [trade(act="BUY", qty=2.0, px=10.0)]
        report = engine().audit_reproducibility(iter(trades), iter(list(trades)))
        self.assertTrue(report.is_bit_identical)
        self.assertEqual(report.net_cash_flow, -20.0)
        self.assertEqual(report.total_trades, 1)

    def test_checksum_is_stable_across_calls(self):
        trades = [trade(), trade(ts=1005.0, act="SELL")]
        self.assertEqual(
            self.engine.generate_trade_checksum(trades),
            self.engine.generate_trade_checksum(trades),
        )

    def test_symbol_case_does_not_affect_checksum(self):
        self.assertEqual(
            self.engine.generate_trade_checksum([trade(sym="aapl")]),
            self.engine.generate_trade_checksum([trade(sym="AAPL")]),
        )


class TestCashFlowAccounting(unittest.TestCase):
    """Previously summed unsigned notional and called it 'final_equity'."""

    def test_round_trip_yields_realised_pnl(self):
        trades = [
            trade(act="BUY", qty=100.0, px=150.0),
            trade(ts=1005.0, act="SELL", qty=100.0, px=155.0),
        ]
        report = engine().audit_reproducibility(trades, list(trades))
        # Independently derived: -100*150 + 100*155 = +500.
        # The pre-fix implementation reported 30500 (the unsigned notional sum).
        self.assertEqual(report.net_cash_flow, 500.0)

    def test_buy_only_cash_flow_is_negative(self):
        report = engine().audit_reproducibility([trade()], [trade()])
        self.assertEqual(report.net_cash_flow, -15000.0)

    def test_signed_cash_flow_per_trade(self):
        self.assertEqual(trade(act="BUY", qty=2.0, px=10.0).signed_cash_flow, -20.0)
        self.assertEqual(trade(act="SELL", qty=2.0, px=10.0).signed_cash_flow, 20.0)

    def test_side_parsing_is_case_insensitive(self):
        self.assertIs(trade(act=" sell ").side, TradeSide.SELL)

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(DeterminismError):
            trade(act="HOLD").side


class TestRecordValidation(unittest.TestCase):
    def test_non_finite_values_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            for fieldname in ("timestamp", "quantity", "price"):
                with self.subTest(bad=bad, field=fieldname):
                    with self.assertRaises(DeterminismError):
                        trade(**{{"timestamp": "ts", "quantity": "qty", "price": "px"}[fieldname]: bad})

    def test_empty_symbol_or_action_rejected(self):
        with self.assertRaises(DeterminismError):
            trade(sym="  ")
        with self.assertRaises(DeterminismError):
            trade(act="")

    def test_non_numeric_price_rejected(self):
        with self.assertRaises(DeterminismError):
            trade(px="150.0")

    def test_record_is_immutable(self):
        record = trade()
        with self.assertRaises(Exception):
            record.price = 999.0

    def test_checksum_rejects_non_record(self):
        with self.assertRaises(DeterminismError):
            engine().generate_trade_checksum([{"px": 1.0}])


class TestEventStreamSorting(unittest.TestCase):
    def test_sorts_by_timestamp_symbol_sequence(self):
        events = [
            {"timestamp": 100.5, "symbol": "MSFT", "sequence_id": 2},
            {"timestamp": 100.0, "symbol": "AAPL", "sequence_id": 1},
            {"timestamp": 100.0, "symbol": "AAPL", "sequence_id": 0},
        ]
        result = BacktestDeterminismEngine.sort_event_stream(events)
        self.assertEqual([e["sequence_id"] for e in result], [0, 1, 2])
        self.assertEqual(result[0]["symbol"], "AAPL")

    def test_symbol_breaks_timestamp_ties(self):
        events = [
            {"timestamp": 1.0, "symbol": "ZZZ", "sequence_id": 0},
            {"timestamp": 1.0, "symbol": "AAA", "sequence_id": 0},
        ]
        self.assertEqual(
            [e["symbol"] for e in BacktestDeterminismEngine.sort_event_stream(events)],
            ["AAA", "ZZZ"],
        )

    def test_missing_sort_key_is_rejected(self):
        # Previously defaulted to 0.0 and silently moved the event to the front.
        with self.assertRaises(DeterminismError) as ctx:
            BacktestDeterminismEngine.sort_event_stream([{"symbol": "B", "sequence_id": 0}])
        self.assertIn("timestamp", str(ctx.exception))

    def test_duplicate_sort_keys_are_rejected(self):
        # Stable sort would resolve ties by input order, which is not deterministic.
        events = [{"timestamp": 1.0, "symbol": "A", "sequence_id": 1}] * 2
        with self.assertRaises(DeterminismError) as ctx:
            BacktestDeterminismEngine.sort_event_stream(events)
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_input_order_does_not_affect_result(self):
        events = [
            {"timestamp": 2.0, "symbol": "B", "sequence_id": 1},
            {"timestamp": 1.0, "symbol": "A", "sequence_id": 0},
            {"timestamp": 1.5, "symbol": "C", "sequence_id": 2},
        ]
        forward = BacktestDeterminismEngine.sort_event_stream(events)
        reverse = BacktestDeterminismEngine.sort_event_stream(list(reversed(events)))
        self.assertEqual(forward, reverse)

    def test_malformed_events_rejected(self):
        for bad in (
            [{"timestamp": "1.0", "symbol": "A", "sequence_id": 0}],
            [{"timestamp": float("nan"), "symbol": "A", "sequence_id": 0}],
            [{"timestamp": 1.0, "symbol": "A", "sequence_id": 1.5}],
            [{"timestamp": 1.0, "symbol": "A", "sequence_id": True}],
            ["not-a-dict"],
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(DeterminismError):
                    BacktestDeterminismEngine.sort_event_stream(bad)

    def test_empty_stream_is_allowed(self):
        self.assertEqual(BacktestDeterminismEngine.sort_event_stream([]), [])


class TestSimulatedClock(unittest.TestCase):
    def test_advances_forward(self):
        clock = SimulatedClock(100.0)
        self.assertEqual(clock.now(), 100.0)
        clock.advance_to(150.0)
        self.assertEqual(clock.now(), 150.0)

    def test_same_timestamp_is_allowed(self):
        clock = SimulatedClock(100.0)
        clock.advance_to(100.0)
        self.assertEqual(clock.now(), 100.0)

    def test_backwards_jump_is_rejected(self):
        clock = SimulatedClock(100.0)
        with self.assertRaises(DeterminismError) as ctx:
            clock.advance_to(99.0)
        self.assertIn("backwards", str(ctx.exception))

    def test_non_finite_rejected(self):
        with self.assertRaises(DeterminismError):
            SimulatedClock(float("nan"))
        with self.assertRaises(DeterminismError):
            SimulatedClock(0.0).advance_to(float("inf"))


class TestSeeding(unittest.TestCase):
    def test_seeding_makes_random_reproducible(self):
        BacktestDeterminismEngine.apply_master_seeds(7)
        first = [random.random() for _ in range(5)]
        BacktestDeterminismEngine.apply_master_seeds(7)
        self.assertEqual([random.random() for _ in range(5)], first)

    def test_different_seeds_diverge(self):
        BacktestDeterminismEngine.apply_master_seeds(1)
        a = [random.random() for _ in range(5)]
        BacktestDeterminismEngine.apply_master_seeds(2)
        self.assertNotEqual([random.random() for _ in range(5)], a)

    def test_apply_seeds_on_init_false_leaves_global_state_alone(self):
        random.seed(123)
        expected = random.random()
        random.seed(123)
        BacktestDeterminismEngine(master_seed=999, apply_seeds_on_init=False)
        self.assertEqual(random.random(), expected)

    def test_hash_seed_check_reports_unset(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(BacktestDeterminismEngine.check_hash_seed(42))
        with mock.patch.dict(os.environ, {"PYTHONHASHSEED": "random"}):
            self.assertFalse(BacktestDeterminismEngine.check_hash_seed(42))

    def test_hash_seed_check_reports_fixed(self):
        for value in ("0", "42"):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"PYTHONHASHSEED": value}):
                    self.assertTrue(BacktestDeterminismEngine.check_hash_seed(42))

    @unittest.skipUnless(HAS_NUMPY, "NumPy not installed")
    def test_numpy_generator_is_seeded_and_reproducible(self):
        # np.random.seed only touches the legacy global RandomState; a Generator
        # must be seeded explicitly or it stays non-deterministic.
        a = engine().make_numpy_generator().random(5).tolist()
        b = engine().make_numpy_generator().random(5).tolist()
        self.assertEqual(a, b)

    @unittest.skipUnless(HAS_NUMPY, "NumPy not installed")
    def test_numpy_generator_differs_by_seed(self):
        a = engine().make_numpy_generator().random(5).tolist()
        other = BacktestDeterminismEngine(master_seed=43, apply_seeds_on_init=False)
        self.assertNotEqual(other.make_numpy_generator().random(5).tolist(), a)


class TestEngineValidation(unittest.TestCase):
    def test_invalid_master_seed_rejected(self):
        for bad in ("42", 4.2, True):
            with self.subTest(bad=bad):
                with self.assertRaises(DeterminismError):
                    BacktestDeterminismEngine(master_seed=bad, apply_seeds_on_init=False)

    def test_invalid_float_precision_rejected(self):
        for bad in (-1, "6", 2.5):
            with self.subTest(bad=bad):
                with self.assertRaises(DeterminismError):
                    engine(float_precision=bad)

    def test_report_is_immutable(self):
        report = engine().audit_reproducibility([trade()], [trade()])
        self.assertIsInstance(report, DeterministicAuditReport)
        with self.assertRaises(Exception):
            report.is_bit_identical = False


if __name__ == "__main__":
    unittest.main()
