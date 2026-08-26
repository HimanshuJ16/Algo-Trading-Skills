"""
Unit tests for multi-broker-consolidated-position-view skill.

Expected values are derived by hand in each test's comments rather than by re-running
the implementation's own expression, so a sign flip, a dropped multiplier, or an
inverted FX rate fails the assertion instead of being reproduced by it.
"""
import math
import unittest
from datetime import datetime, timedelta, timezone

from consolidated_ledger import (
    DEFAULT_QUANTITY_TOLERANCE,
    DiscrepancyKind,
    MissingFxRateError,
    MultiBrokerConsolidatedLedger,
    RawBrokerPosition,
    StaleSnapshotError,
    UnmappedSymbolError,
)

# Fixed rates so tests assert arithmetic, not a live quote. Units: base (USD) per
# one unit of the quoted currency.
FX = {"USD": 1.0, "EUR": 1.25, "JPY": 0.008}

T0 = datetime(2026, 8, 26, 14, 30, tzinfo=timezone.utc)


def make_ledger(**kwargs) -> MultiBrokerConsolidatedLedger:
    kwargs.setdefault("fx_rates", FX)
    ledger = MultiBrokerConsolidatedLedger(**kwargs)
    ledger.register_symbol_mapping("AAPL STK SMART", "AAPL")
    ledger.register_symbol_mapping("AAPL.US", "AAPL")
    return ledger


class TestConsolidationAndNetting(unittest.TestCase):

    def test_cross_broker_netting_and_gross(self):
        ledger = make_ledger()
        positions = [
            RawBrokerPosition("ibkr", "AAPL STK SMART", 100, 150.0, 160.0, "USD"),
            RawBrokerPosition("alpaca", "AAPL.US", -40, 152.0, 160.0, "USD"),
        ]

        aapl = ledger.consolidate_positions(positions)["AAPL"]

        # Net 100 + (-40) = 60; gross |100| + |-40| = 140.
        self.assertEqual(aapl.net_quantity, 60)
        self.assertEqual(aapl.gross_quantity, 140)
        self.assertEqual(aapl.broker_breakdown["ibkr"], 100)
        self.assertEqual(aapl.broker_breakdown["alpaca"], -40)
        self.assertEqual(aapl.leg_count, 2)
        self.assertTrue(aapl.is_internally_offset)

        # Signed market value: 100*160 + (-40*160) = 16000 - 6400 = 9600.
        self.assertAlmostEqual(aapl.total_market_value_base, 9600.0, places=6)
        # Gross market value: |16000| + |-6400| = 22400 -- the exposure figure a GMV
        # cap must consume. Netting to 9600 understates it by 12800.
        self.assertAlmostEqual(aapl.gross_market_value_base, 22400.0, places=6)

        # Cost: 100*150 + (-40*152) = 15000 - 6080 = 8920.
        self.assertAlmostEqual(aapl.total_cost_basis_base, 8920.0, places=6)
        # Unrealized: long leg +1000, short leg 40*(152-160) = -320 -> +680.
        self.assertAlmostEqual(aapl.unrealized_pnl_base, 680.0, places=6)

    def test_gross_market_value_reported_when_book_nets_flat(self):
        """A fully offset book has ~zero net value but real, margined exposure."""
        ledger = make_ledger()
        positions = [
            RawBrokerPosition("ibkr", "AAPL STK SMART", 100, 150.0, 160.0, "USD"),
            RawBrokerPosition("alpaca", "AAPL.US", -100, 155.0, 160.0, "USD"),
        ]

        aapl = ledger.consolidate_positions(positions)["AAPL"]

        self.assertEqual(aapl.net_quantity, 0)
        self.assertAlmostEqual(aapl.total_market_value_base, 0.0, places=6)
        # 100*160 on each side = 32000 gross.
        self.assertAlmostEqual(aapl.gross_market_value_base, 32000.0, places=6)
        # No meaningful per-contract basis on a flat net position -- None, never 0.0,
        # which a caller would read as "acquired for free".
        self.assertIsNone(aapl.weighted_avg_cost_base)
        self.assertTrue(aapl.is_internally_offset)

    def test_weighted_avg_cost_not_divergent_on_near_flat_net(self):
        """Long 100 / short 99.999999 must not report a cost basis in the millions."""
        ledger = make_ledger(quantity_tolerance=1e-5)
        positions = [
            RawBrokerPosition("ibkr", "AAPL STK SMART", 100.0, 150.0, 160.0, "USD"),
            RawBrokerPosition("alpaca", "AAPL.US", -99.999999, 150.0, 160.0, "USD"),
        ]

        aapl = ledger.consolidate_positions(positions)["AAPL"]

        # Net is 1e-6, inside the 1e-5 tolerance. Dividing cost by it would yield a
        # per-contract basis around 1.5e5 times the real one.
        self.assertLess(abs(aapl.net_quantity), 1e-5)
        self.assertIsNone(aapl.weighted_avg_cost_base)

    def test_weighted_avg_cost_never_overflows_to_infinity(self):
        """A zero tolerance lets a denormal net clear the threshold; inf must not escape."""
        ledger = make_ledger(quantity_tolerance=0.0)
        positions = [
            RawBrokerPosition("ibkr", "XYZ", 1e-300, 1e200, 1e200, "USD"),
            RawBrokerPosition("ibkr", "XYZ", 0.0, 0.0, 1e200, "USD"),
        ]

        xyz = ledger.consolidate_positions(positions)["XYZ"]

        self.assertTrue(
            xyz.weighted_avg_cost_base is None or math.isfinite(xyz.weighted_avg_cost_base)
        )

    def test_weighted_avg_cost_on_a_genuine_net_long(self):
        ledger = make_ledger()
        positions = [
            RawBrokerPosition("ibkr", "AAPL STK SMART", 100, 150.0, 160.0, "USD"),
            RawBrokerPosition("alpaca", "AAPL.US", 100, 170.0, 160.0, "USD"),
        ]

        aapl = ledger.consolidate_positions(positions)["AAPL"]

        # (100*150 + 100*170) / 200 = 32000 / 200 = 160.
        self.assertAlmostEqual(aapl.weighted_avg_cost_base, 160.0, places=6)

    def test_single_broker_hedge_mode_legs_net_within_the_breakdown(self):
        """Binance hedge mode reports LONG and SHORT rows for one symbol, one account."""
        ledger = make_ledger()
        positions = [
            RawBrokerPosition("binance", "BTCUSDT", 3.0, 60000.0, 65000.0, "USD"),
            RawBrokerPosition("binance", "BTCUSDT", -1.0, 62000.0, 65000.0, "USD"),
        ]

        btc = ledger.consolidate_positions(positions)["BTCUSDT"]

        self.assertEqual(btc.net_quantity, 2.0)
        self.assertEqual(btc.gross_quantity, 4.0)
        self.assertEqual(btc.broker_breakdown, {"binance": 2.0})
        self.assertTrue(btc.is_internally_offset)
        # 3*65000 = 195000 long, 1*65000 = 65000 short -> 260000 gross, 130000 net.
        self.assertAlmostEqual(btc.gross_market_value_base, 260000.0, places=6)
        self.assertAlmostEqual(btc.total_market_value_base, 130000.0, places=6)

    def test_output_ordering_is_deterministic(self):
        ledger = make_ledger()
        positions = [
            RawBrokerPosition("b", "ZZZ", 1, 1.0, 1.0, "USD"),
            RawBrokerPosition("b", "AAA", 1, 1.0, 1.0, "USD"),
            RawBrokerPosition("b", "MMM", 1, 1.0, 1.0, "USD"),
        ]

        self.assertEqual(
            list(ledger.consolidate_positions(positions)), ["AAA", "MMM", "ZZZ"]
        )

    def test_empty_input_returns_empty_view(self):
        self.assertEqual(make_ledger().consolidate_positions([]), {})


class TestContractMultiplier(unittest.TestCase):

    def test_option_multiplier_applied_to_value_and_cost(self):
        """A standard OCC equity option covers 100 shares; omitting that is a 100x error."""
        ledger = make_ledger()
        positions = [
            RawBrokerPosition(
                "ibkr", "AAPL 260918C00200000", 10, 5.00, 7.50, "USD",
                contract_multiplier=100.0,
            ),
        ]

        opt = ledger.consolidate_positions(positions)["AAPL 260918C00200000"]

        # 10 contracts * 7.50 premium * 100 shares = 7500 (not 75).
        self.assertAlmostEqual(opt.total_market_value_base, 7500.0, places=6)
        self.assertAlmostEqual(opt.gross_market_value_base, 7500.0, places=6)
        # Cost 10 * 5.00 * 100 = 5000; unrealized 7500 - 5000 = 2500.
        self.assertAlmostEqual(opt.total_cost_basis_base, 5000.0, places=6)
        self.assertAlmostEqual(opt.unrealized_pnl_base, 2500.0, places=6)

    def test_broker_supplied_cost_already_carrying_the_multiplier(self):
        """IBKR avgCost reflects the multiplier for derivatives; avgPrice does not."""
        ledger = make_ledger()
        positions = [
            RawBrokerPosition(
                "ibkr", "ESZ6", 2, 275000.0, 5600.0, "USD",
                contract_multiplier=50.0,
                average_cost_includes_multiplier=True,
            ),
        ]

        fut = ledger.consolidate_positions(positions)["ESZ6"]

        # Value 2 * 5600 * 50 = 560000. Cost 2 * 275000 = 550000 -- the 50x is
        # already inside avgCost (5500 index * 50). Re-applying it would report
        # 27,500,000 of cost basis and a -26.9m unrealized loss on a winning trade.
        self.assertAlmostEqual(fut.total_market_value_base, 560000.0, places=6)
        self.assertAlmostEqual(fut.total_cost_basis_base, 550000.0, places=6)
        self.assertAlmostEqual(fut.unrealized_pnl_base, 10000.0, places=6)

    def test_non_positive_multiplier_rejected(self):
        for bad in (0.0, -100.0):
            with self.assertRaises(ValueError):
                RawBrokerPosition("ibkr", "X", 1, 1.0, 1.0, "USD", contract_multiplier=bad)


class TestCurrencyConversion(unittest.TestCase):

    def test_conversion_uses_base_units_per_foreign_unit(self):
        ledger = make_ledger()
        positions = [
            RawBrokerPosition("degiro", "ASML", 10, 500.0, 520.0, "EUR"),
        ]

        asml = ledger.consolidate_positions(positions)["ASML"]

        # 10 * 520 EUR = 5200 EUR; at 1.25 USD per EUR that is 6500 USD. An inverted
        # rate would give 4160, a 1:1 fallback 5200 -- both wrong, neither an error.
        self.assertAlmostEqual(asml.total_market_value_base, 6500.0, places=6)
        # Cost 10 * 500 * 1.25 = 6250; unrealized 250.
        self.assertAlmostEqual(asml.total_cost_basis_base, 6250.0, places=6)
        self.assertAlmostEqual(asml.unrealized_pnl_base, 250.0, places=6)
        self.assertEqual(asml.currencies, ("EUR",))

    def test_unknown_currency_fails_closed_rather_than_assuming_parity(self):
        """The pre-fix engine returned amount * 1.0 here, overstating JPY ~125x."""
        ledger = MultiBrokerConsolidatedLedger(fx_rates={"USD": 1.0})
        positions = [
            RawBrokerPosition("jpx", "7203", 1000, 2500.0, 2600.0, "JPY"),
        ]

        with self.assertRaises(MissingFxRateError):
            ledger.consolidate_positions(positions)

    def test_mixed_currency_legs_aggregate_in_base(self):
        ledger = make_ledger()
        positions = [
            RawBrokerPosition("ibkr", "GOLD", 1, 100.0, 100.0, "USD"),
            RawBrokerPosition("degiro", "GOLD", 1, 80.0, 80.0, "EUR"),
            RawBrokerPosition("jpx", "GOLD", 1, 12500.0, 12500.0, "JPY"),
        ]

        gold = ledger.consolidate_positions(positions)["GOLD"]

        # 100 USD + 80*1.25 (=100) + 12500*0.008 (=100) = 300 USD.
        self.assertAlmostEqual(gold.total_market_value_base, 300.0, places=6)
        self.assertEqual(gold.currencies, ("EUR", "JPY", "USD"))

    def test_non_usd_base_currency_is_honoured(self):
        """base_currency was previously accepted and ignored; USD figures were labelled EUR."""
        ledger = MultiBrokerConsolidatedLedger(
            base_currency="EUR", fx_rates={"EUR": 1.0, "USD": 0.80}
        )
        positions = [
            RawBrokerPosition("ibkr", "AAPL", 10, 150.0, 160.0, "USD"),
        ]

        aapl = ledger.consolidate_positions(positions)["AAPL"]

        # 10 * 160 USD = 1600 USD; at 0.80 EUR per USD that is 1280 EUR.
        self.assertAlmostEqual(aapl.total_market_value_base, 1280.0, places=6)
        self.assertEqual(aapl.base_currency, "EUR")

    def test_fx_table_is_mandatory(self):
        with self.assertRaises(ValueError):
            MultiBrokerConsolidatedLedger()

    def test_base_currency_must_be_present_at_exactly_one(self):
        with self.assertRaises(ValueError):
            MultiBrokerConsolidatedLedger(fx_rates={"EUR": 1.25})
        with self.assertRaises(ValueError):
            MultiBrokerConsolidatedLedger(fx_rates={"USD": 1.02, "EUR": 1.25})

    def test_invalid_fx_rates_rejected(self):
        for bad in (0.0, -1.25, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                MultiBrokerConsolidatedLedger(fx_rates={"USD": 1.0, "EUR": bad})

    def test_malformed_currency_code_rejected(self):
        for bad in ("US", "USDD", "US1", ""):
            with self.assertRaises(ValueError):
                RawBrokerPosition("ibkr", "X", 1, 1.0, 1.0, bad)


class TestInputValidation(unittest.TestCase):

    def test_non_finite_numbers_rejected_at_construction(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                RawBrokerPosition("ibkr", "AAPL", bad, 150.0, 160.0, "USD")
            with self.assertRaises(ValueError):
                RawBrokerPosition("ibkr", "AAPL", 100, bad, 160.0, "USD")
            with self.assertRaises(ValueError):
                RawBrokerPosition("ibkr", "AAPL", 100, 150.0, bad, "USD")

    def test_nan_price_cannot_silently_poison_a_consolidated_total(self):
        """Pre-fix, a NaN price made every downstream aggregate NaN with no signal."""
        with self.assertRaises(ValueError):
            RawBrokerPosition("ibkr", "AAPL", 100, 150.0, float("nan"), "USD")

    def test_negative_price_rejected_but_zero_allowed(self):
        with self.assertRaises(ValueError):
            RawBrokerPosition("ibkr", "AAPL", 100, 150.0, -160.0, "USD")
        # A worthless expiring option legitimately marks at zero.
        self.assertEqual(
            RawBrokerPosition("ibkr", "AAPL C", 1, 5.0, 0.0, "USD").current_price, 0.0
        )

    def test_blank_identifiers_rejected(self):
        with self.assertRaises(ValueError):
            RawBrokerPosition("", "AAPL", 1, 1.0, 1.0, "USD")
        with self.assertRaises(ValueError):
            RawBrokerPosition("ibkr", "   ", 1, 1.0, 1.0, "USD")

    def test_short_positions_are_accepted_as_signed_quantities(self):
        pos = RawBrokerPosition("binance", "BTCUSDT", -1.5, 60000.0, 65000.0, "USD")
        self.assertEqual(pos.quantity, -1.5)


class TestSymbolNormalization(unittest.TestCase):

    def test_unmapped_symbols_are_reported(self):
        ledger = make_ledger()
        positions = [
            RawBrokerPosition("ibkr", "AAPL STK SMART", 100, 150.0, 160.0, "USD"),
            RawBrokerPosition("degiro", "AAPL.AS", 10, 150.0, 160.0, "USD"),
        ]

        self.assertEqual(ledger.unmapped_broker_symbols(positions), ["AAPL.AS"])

    def test_unmapped_symbol_splits_one_asset_into_two_canonical_entries(self):
        """The documented duplicate-entry pitfall, reproduced so the guard is testable."""
        ledger = make_ledger()
        positions = [
            RawBrokerPosition("ibkr", "AAPL STK SMART", 100, 150.0, 160.0, "USD"),
            RawBrokerPosition("degiro", "AAPL.AS", 10, 150.0, 160.0, "USD"),
        ]

        consolidated = ledger.consolidate_positions(positions)

        self.assertEqual(sorted(consolidated), ["AAPL", "AAPL.AS"])
        self.assertEqual(consolidated["AAPL"].net_quantity, 100)

    def test_strict_mode_refuses_to_guess(self):
        ledger = make_ledger(strict_symbol_mapping=True)
        positions = [RawBrokerPosition("degiro", "AAPL.AS", 10, 150.0, 160.0, "USD")]

        with self.assertRaises(UnmappedSymbolError):
            ledger.consolidate_positions(positions)

    def test_mapping_registration_validates_inputs(self):
        ledger = make_ledger()
        with self.assertRaises(ValueError):
            ledger.register_symbol_mapping("", "AAPL")
        with self.assertRaises(ValueError):
            ledger.register_symbol_mapping("AAPL.L", "  ")


class TestSnapshotStaleness(unittest.TestCase):

    def test_stale_leg_rejected_when_max_age_configured(self):
        ledger = make_ledger(max_snapshot_age=timedelta(minutes=5))
        positions = [
            RawBrokerPosition(
                "ibkr", "AAPL STK SMART", 100, 150.0, 160.0, "USD", as_of=T0
            ),
            RawBrokerPosition(
                "binance", "BTCUSDT", 1, 60000.0, 65000.0, "USD",
                as_of=T0 - timedelta(minutes=40),
            ),
        ]

        with self.assertRaises(StaleSnapshotError):
            ledger.consolidate_positions(positions, valuation_time=T0)

    def test_untimestamped_leg_rejected_when_max_age_configured(self):
        ledger = make_ledger(max_snapshot_age=timedelta(minutes=5))
        positions = [RawBrokerPosition("ibkr", "AAPL STK SMART", 100, 150.0, 160.0)]

        with self.assertRaises(StaleSnapshotError):
            ledger.consolidate_positions(positions, valuation_time=T0)

    def test_future_stamped_leg_rejected_as_clock_skew(self):
        ledger = make_ledger(max_snapshot_age=timedelta(minutes=5))
        positions = [
            RawBrokerPosition(
                "ibkr", "AAPL STK SMART", 100, 150.0, 160.0, "USD",
                as_of=T0 + timedelta(seconds=30),
            ),
        ]

        with self.assertRaises(StaleSnapshotError):
            ledger.consolidate_positions(positions, valuation_time=T0)

    def test_stale_fx_table_rejected(self):
        ledger = make_ledger(
            max_snapshot_age=timedelta(minutes=5),
            fx_rates_as_of=T0 - timedelta(hours=6),
        )
        positions = [
            RawBrokerPosition(
                "ibkr", "AAPL STK SMART", 100, 150.0, 160.0, "USD", as_of=T0
            ),
        ]

        with self.assertRaises(StaleSnapshotError):
            ledger.consolidate_positions(positions, valuation_time=T0)

    def test_valuation_time_required_when_max_age_configured(self):
        ledger = make_ledger(max_snapshot_age=timedelta(minutes=5))
        positions = [
            RawBrokerPosition(
                "ibkr", "AAPL STK SMART", 100, 150.0, 160.0, "USD", as_of=T0
            ),
        ]

        with self.assertRaises(ValueError):
            ledger.consolidate_positions(positions)

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            RawBrokerPosition(
                "ibkr", "AAPL", 1, 1.0, 1.0, "USD", as_of=datetime(2026, 8, 26, 14, 30)
            )

    def test_snapshot_skew_is_surfaced(self):
        ledger = make_ledger()
        positions = [
            RawBrokerPosition(
                "ibkr", "AAPL STK SMART", 100, 150.0, 160.0, "USD", as_of=T0
            ),
            RawBrokerPosition(
                "alpaca", "AAPL.US", -40, 152.0, 160.0, "USD",
                as_of=T0 - timedelta(seconds=90),
            ),
        ]

        aapl = ledger.consolidate_positions(positions)["AAPL"]

        self.assertAlmostEqual(aapl.snapshot_skew_seconds, 90.0)
        self.assertEqual(aapl.oldest_snapshot_as_of, T0 - timedelta(seconds=90))

    def test_skew_withheld_when_any_leg_is_untimestamped(self):
        ledger = make_ledger()
        positions = [
            RawBrokerPosition(
                "ibkr", "AAPL STK SMART", 100, 150.0, 160.0, "USD", as_of=T0
            ),
            RawBrokerPosition("alpaca", "AAPL.US", -40, 152.0, 160.0, "USD"),
        ]

        aapl = ledger.consolidate_positions(positions)["AAPL"]

        self.assertIsNone(aapl.snapshot_skew_seconds)


class TestReconciliation(unittest.TestCase):

    def test_quantity_mismatch_detected_and_classified(self):
        ledger = make_ledger()
        positions = [
            RawBrokerPosition("ibkr", "AAPL STK SMART", 100, 150.0, 160.0, "USD")
        ]

        breaks = ledger.reconcile_against_target(positions, {"AAPL": 80.0})

        self.assertEqual(len(breaks), 1)
        self.assertEqual(breaks[0].canonical_symbol, "AAPL")
        self.assertEqual(breaks[0].kind, DiscrepancyKind.QUANTITY_MISMATCH)
        self.assertAlmostEqual(breaks[0].discrepancy_qty, 20.0)

    def test_missing_at_broker_distinguished_from_mismatch(self):
        ledger = make_ledger()

        breaks = ledger.reconcile_against_target([], {"AAPL": 80.0})

        self.assertEqual(len(breaks), 1)
        self.assertEqual(breaks[0].kind, DiscrepancyKind.MISSING_AT_BROKER)
        self.assertAlmostEqual(breaks[0].discrepancy_qty, -80.0)

    def test_unexpected_at_broker_distinguished_from_mismatch(self):
        ledger = make_ledger()
        positions = [
            RawBrokerPosition("ibkr", "AAPL STK SMART", 100, 150.0, 160.0, "USD")
        ]

        breaks = ledger.reconcile_against_target(positions, {})

        self.assertEqual(len(breaks), 1)
        self.assertEqual(breaks[0].kind, DiscrepancyKind.UNEXPECTED_AT_BROKER)

    def test_agreement_within_tolerance_produces_no_break(self):
        ledger = make_ledger()
        positions = [
            RawBrokerPosition("ibkr", "AAPL STK SMART", 100.000001, 150.0, 160.0, "USD")
        ]

        self.assertEqual(ledger.reconcile_against_target(positions, {"AAPL": 100.0}), [])

    def test_tolerance_boundary_is_inclusive(self):
        # 0.25 and 100.25 are exactly representable in binary floating point, so the
        # difference lands precisely on the threshold and the >/<= boundary is what is
        # actually under test.
        ledger = make_ledger(quantity_tolerance=0.25)
        at_tolerance = [RawBrokerPosition("ibkr", "XYZ", 100.25, 1.0, 1.0, "USD")]
        past_tolerance = [RawBrokerPosition("ibkr", "XYZ", 100.50, 1.0, 1.0, "USD")]

        self.assertEqual(ledger.reconcile_against_target(at_tolerance, {"XYZ": 100.0}), [])
        self.assertEqual(
            len(ledger.reconcile_against_target(past_tolerance, {"XYZ": 100.0})), 1
        )

    def test_decimal_tolerance_is_not_an_exact_threshold_in_binary_float(self):
        """
        A tolerance written as a round decimal is not a round number in float:
        100.01 - 100.0 evaluates to 0.010000000000005116, which is *greater* than a
        0.01 tolerance. Set tolerances with headroom rather than at the exact
        quantity increment you expect, or a purely representational residue is
        alerted as a position break every reconciliation cycle.
        """
        ledger = make_ledger(quantity_tolerance=0.01)
        positions = [RawBrokerPosition("ibkr", "XYZ", 100.01, 1.0, 1.0, "USD")]

        breaks = ledger.reconcile_against_target(positions, {"XYZ": 100.0})

        self.assertEqual(len(breaks), 1)
        self.assertGreater(breaks[0].discrepancy_qty, 0.01)

    def test_per_symbol_tolerance_catches_a_break_the_default_would_mask(self):
        """1e-5 is fine for shares and far too coarse for an 8-decimal crypto quantity."""
        positions = [
            RawBrokerPosition("binance", "BTCUSDT", 0.00001500, 60000.0, 65000.0, "USD")
        ]
        target = {"BTCUSDT": 0.00001000}

        default_ledger = make_ledger()
        self.assertEqual(default_ledger.reconcile_against_target(positions, target), [])

        tight_ledger = make_ledger(symbol_tolerances={"BTCUSDT": 1e-8})
        breaks = tight_ledger.reconcile_against_target(positions, target)
        self.assertEqual(len(breaks), 1)
        # A 50% position error the default tolerance silently accepted.
        self.assertAlmostEqual(breaks[0].discrepancy_qty, 5e-6)

    def test_small_crypto_break_is_not_formatted_away_to_zero(self):
        """:+.2f printed a real break as '+0.00'; the message must show the magnitude."""
        ledger = make_ledger(symbol_tolerances={"BTCUSDT": 1e-9})
        positions = [
            RawBrokerPosition("binance", "BTCUSDT", 0.00001500, 60000.0, 65000.0, "USD")
        ]

        breaks = ledger.reconcile_against_target(positions, {"BTCUSDT": 0.00001000})

        self.assertNotIn("+0.00,", breaks[0].message)
        self.assertIn("5e-06", breaks[0].message)

    def test_expected_and_actual_remain_distinguishable_at_crypto_precision(self):
        """
        At 8 significant digits both sides of a 5e-08 BTC break rendered as "1", so
        the alert read as though the quantities agreed.
        """
        ledger = make_ledger(symbol_tolerances={"BTC": 1e-9})
        ledger.register_symbol_mapping("BTCUSDT", "BTC")
        positions = [
            RawBrokerPosition("binance", "BTCUSDT", 1.0, 60000.0, 65000.0, "USD")
        ]

        breaks = ledger.reconcile_against_target(positions, {"BTC": 1.00000005})

        self.assertIn("1.00000005", breaks[0].message)
        self.assertAlmostEqual(breaks[0].discrepancy_qty, -5e-08)

    def test_breaks_are_sorted_deterministically(self):
        ledger = make_ledger()
        positions = [
            RawBrokerPosition("ibkr", "ZZZ", 5, 1.0, 1.0, "USD"),
            RawBrokerPosition("ibkr", "AAA", 5, 1.0, 1.0, "USD"),
            RawBrokerPosition("ibkr", "MMM", 5, 1.0, 1.0, "USD"),
        ]

        breaks = ledger.reconcile_against_target(positions, {})

        self.assertEqual([b.canonical_symbol for b in breaks], ["AAA", "MMM", "ZZZ"])

    def test_target_keys_are_case_normalized(self):
        """A lower-cased target key must not manufacture two phantom breaks."""
        ledger = make_ledger()
        positions = [
            RawBrokerPosition("ibkr", "AAPL STK SMART", 100, 150.0, 160.0, "USD")
        ]

        self.assertEqual(ledger.reconcile_against_target(positions, {"aapl": 100.0}), [])

    def test_ambiguous_duplicate_target_keys_rejected(self):
        ledger = make_ledger()
        with self.assertRaises(ValueError):
            ledger.reconcile_against_target([], {"AAPL": 100.0, "aapl": 80.0})

    def test_non_finite_target_quantity_rejected(self):
        ledger = make_ledger()
        with self.assertRaises(ValueError):
            ledger.reconcile_against_target([], {"AAPL": float("nan")})

    def test_offsetting_brokers_reconcile_against_the_net_target(self):
        ledger = make_ledger()
        positions = [
            RawBrokerPosition("ibkr", "AAPL STK SMART", 100, 150.0, 160.0, "USD"),
            RawBrokerPosition("alpaca", "AAPL.US", -40, 152.0, 160.0, "USD"),
        ]

        self.assertEqual(ledger.reconcile_against_target(positions, {"AAPL": 60.0}), [])


class TestModuleConstants(unittest.TestCase):

    def test_default_tolerance_preserved(self):
        self.assertEqual(DEFAULT_QUANTITY_TOLERANCE, 1e-5)


if __name__ == "__main__":
    unittest.main()
