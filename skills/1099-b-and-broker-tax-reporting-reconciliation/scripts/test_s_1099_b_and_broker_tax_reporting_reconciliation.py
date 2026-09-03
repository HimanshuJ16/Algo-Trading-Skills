"""Unit tests for the 1099-B reconciliation engine.

Test categories
---------------
* Normal operation: exact match, tolerance match
* Boundary conditions: tolerance just within, tolerance just outside
* Invalid inputs: negative quantity, backwards dates, malformed lot
* Edge cases: short-selling rejection, wash-sale mismatch, multi-lot FIFO
  resolution, percentage tolerance on large lots, idempotent re-run, empty
  ledger
* Mathematical correctness: Decimal precision, gain computation
* Regulatory rules: covered vs noncovered classification, wash-sale disallowed
  amount reconciliation
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from s_1099_b_and_broker_tax_reporting_reconciliation import (
    _METRIC_KEYS,
    AdjustmentCode,
    DiscrepancyReason,
    S1099BAndBrokerTaxReportingReconciliationEngine,
    TaxLot,
    ToleranceConfig,
)


def _lot(
    lot_id: str,
    symbol: str = "AAPL",
    qty: str = "100",
    acquired: date | None = None,
    sold: date | None = None,
    proceeds: str = "15000.00",
    basis: str = "14000.00",
    is_wash_sale: bool = False,
    wash_disallowed: str | None = None,
    covered: bool = True,
) -> TaxLot:
    return TaxLot(
        lot_id=lot_id,
        symbol=symbol,
        quantity=Decimal(qty),
        acquired_date=acquired or date(2023, 1, 15),
        sold_date=sold or date(2023, 2, 15),
        proceeds=Decimal(proceeds),
        cost_basis=Decimal(basis),
        is_wash_sale=is_wash_sale,
        wash_sale_disallowed_amount=Decimal(wash_disallowed) if wash_disallowed else None,
        covered=covered,
    )


class TestReconciliationBasics(unittest.TestCase):
    """Original three tests, adapted to the new Decimal API."""

    def setUp(self):
        self.engine = S1099BAndBrokerTaxReportingReconciliationEngine(
            ToleranceConfig(absolute_usd=Decimal("0.05"))
        )

    def test_exact_match(self):
        self.engine.load_internal_lot(_lot("INT_1"))
        self.engine.load_broker_lot(_lot("BRK_1"))

        result = self.engine.process_reconciliation()

        self.assertEqual(result.matched_clean, 1)
        self.assertEqual(result.matched_with_discrepancies, 0)
        self.assertEqual(result.discrepancy_count, 0)
        self.assertEqual(len(result.matched_clean_seq), 1)

    def test_match_within_tolerance(self):
        # Broker cost basis differs by $0.03 due to fee rounding.
        self.engine.load_internal_lot(_lot("INT_1", basis="9000.00"))
        self.engine.load_broker_lot(_lot("BRK_1", basis="9000.03"))

        result = self.engine.process_reconciliation()

        self.assertEqual(result.matched_clean, 1)
        self.assertEqual(result.discrepancy_count, 0)

    def test_unmatched_missing_broker_record(self):
        # Internal record with no broker counterpart.
        self.engine.load_internal_lot(_lot("INT_1"))

        result = self.engine.process_reconciliation()

        self.assertEqual(result.matched_clean, 0)
        self.assertEqual(result.matched_total, 0)
        self.assertEqual(result.discrepancy_count, 1)
        self.assertEqual(
            result.discrepancies[0].reason, DiscrepancyReason.MISSING_IN_BROKER
        )


class TestReconciliationExtended(unittest.TestCase):
    """New tests covering critical edge cases added in the institutional upgrade."""

    def setUp(self):
        self.engine = S1099BAndBrokerTaxReportingReconciliationEngine()

    # ---------- tolerance & precision

    def test_basis_outside_absolute_tolerance_reports_discrepancy(self):
        # Use a tight custom tolerance so the test is deterministic and not
        # dependent on the default relative-tolerance behaviour.
        engine = S1099BAndBrokerTaxReportingReconciliationEngine(
            ToleranceConfig(absolute_usd=Decimal("0.04"), relative_basis_pct=Decimal("0"))
        )
        engine.load_internal_lot(_lot("INT_1", basis="9000.00"))
        engine.load_broker_lot(_lot("BRK_1", basis="9000.10"))

        result = engine.process_reconciliation()

        self.assertEqual(result.matched_clean, 0)
        self.assertEqual(result.matched_with_discrepancies, 1)
        self.assertEqual(
            result.discrepancies[0].reason, DiscrepancyReason.BASIS_OUTSIDE_TOLERANCE
        )

    def test_decimal_precision_no_float_drift(self):
        # Cost basis values that look innocuous in float but aggregate badly.
        for i in range(1, 11):
            self.engine.load_internal_lot(
                _lot(f"INT_{i}", basis="8500.10", proceeds="10000.05")
            )
            self.engine.load_broker_lot(
                _lot(f"BRK_{i}", basis="8500.10", proceeds="10000.05")
            )

        result = self.engine.process_reconciliation()

        self.assertEqual(result.matched_clean, 10)
        self.assertEqual(result.discrepancy_count, 0)

    def test_relative_tolerance_holds_on_large_lots(self):
        engine = S1099BAndBrokerTaxReportingReconciliationEngine(
            ToleranceConfig(
                absolute_usd=Decimal("0.05"),
                relative_basis_pct=Decimal("0.0001"),  # 1bp
            )
        )
        # $50M lot with $30 basis drift = 0.00006% — must pass with relative tol.
        engine.load_internal_lot(_lot("INT_1", basis="50000000.00"))
        engine.load_broker_lot(_lot("BRK_1", basis="50000030.00"))

        result = engine.process_reconciliation()

        self.assertEqual(result.matched_clean, 1)
        self.assertEqual(result.discrepancy_count, 0)

    # ---------- wash sale

    def test_wash_sale_flag_mismatch_yields_discrepancy_but_still_matched(self):
        # The pair is still "matched" (the lot exists on both sides) — only the
        # wash-sale flag disagrees. We must report it, not silently suppress it.
        self.engine.load_internal_lot(_lot("INT_1", is_wash_sale=True))
        self.engine.load_broker_lot(_lot("BRK_1", is_wash_sale=False))

        result = self.engine.process_reconciliation()

        self.assertEqual(result.matched_clean, 0)
        self.assertEqual(result.matched_with_discrepancies, 1)
        self.assertEqual(result.matched_total, 1)
        self.assertTrue(
            any(
                d.reason == DiscrepancyReason.WASH_SALE_FLAG_MISMATCH
                for d in result.discrepancies
            )
        )

    def test_wash_sale_disallowed_amount_mismatch(self):
        # Both flag the wash sale identically, but reported disallowed amounts differ.
        self.engine.load_internal_lot(
            _lot("INT_1", is_wash_sale=True, wash_disallowed="250.00")
        )
        self.engine.load_broker_lot(
            _lot("BRK_1", is_wash_sale=True, wash_disallowed="300.00")
        )

        result = self.engine.process_reconciliation()

        self.assertEqual(result.matched_with_discrepancies, 1)
        self.assertTrue(
            any(
                d.reason == DiscrepancyReason.WASH_SALE_AMOUNT_MISMATCH
                for d in result.discrepancies
            )
        )

    # ---------- input validation

    def test_negative_quantity_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.load_internal_lot(_lot("INT_1", qty="-10"))

    def test_sold_before_acquired_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.load_internal_lot(
                _lot(
                    "INT_1",
                    acquired=date(2023, 3, 15),
                    sold=date(2023, 2, 15),
                )
            )

    def test_duplicate_lot_id_rejected(self):
        self.engine.load_internal_lot(_lot("INT_1"))
        with self.assertRaises(ValueError):
            self.engine.load_internal_lot(_lot("INT_1", basis="9999"))

    # ---------- bucketed matching (multi-lot FIFO)

    def test_multiple_lots_same_symbol_same_day(self):
        # Two independently-realized lots with identical match-key data; the
        # engine must pair them 1-to-1, not conflate them.
        self.engine.load_internal_lot(
            _lot("INT_1", acquired=date(2023, 1, 10), sold=date(2023, 2, 10),
                 basis="1000.00", proceeds="1500.00")
        )
        self.engine.load_internal_lot(
            _lot("INT_2", acquired=date(2023, 1, 10), sold=date(2023, 2, 10),
                 basis="2000.00", proceeds="2500.00")
        )
        self.engine.load_broker_lot(
            _lot("BRK_2", acquired=date(2023, 1, 10), sold=date(2023, 2, 10),
                 basis="2000.00", proceeds="2500.00")
        )
        self.engine.load_broker_lot(
            _lot("BRK_1", acquired=date(2023, 1, 10), sold=date(2023, 2, 10),
                 basis="1000.00", proceeds="1500.00")
        )

        result = self.engine.process_reconciliation()

        self.assertEqual(result.matched_clean, 2)
        self.assertEqual(result.discrepancy_count, 0)

    # ---------- covered vs noncovered

    def test_covered_flag_preserved_through_engine(self):
        internal = TaxLot(
            lot_id="INT_1",
            symbol="AAPL",
            quantity=Decimal("100"),
            acquired_date=date(2023, 1, 1),
            sold_date=date(2023, 2, 1),
            proceeds=Decimal("15000"),
            cost_basis=Decimal("14000"),
            covered=False,
        )
        broker = TaxLot(
            lot_id="BRK_1",
            symbol="AAPL",
            quantity=Decimal("100"),
            acquired_date=date(2023, 1, 1),
            sold_date=date(2023, 2, 1),
            proceeds=Decimal("15000"),
            cost_basis=Decimal("14000"),
            covered=False,
        )
        self.engine.load_internal_lot(internal)
        self.engine.load_broker_lot(broker)

        result = self.engine.process_reconciliation()

        self.assertEqual(result.matched_clean, 1)
        # Both are noncovered — the engine must not silently change covered status.
        self.assertFalse(result.matched_clean_seq[0].covered)

    # ---------- missing on broker side

    def test_missing_in_internal_recorded(self):
        self.engine.load_broker_lot(_lot("BRK_1"))

        result = self.engine.process_reconciliation()

        self.assertEqual(result.matched_total, 0)
        self.assertEqual(result.discrepancy_count, 1)
        self.assertEqual(
            result.discrepancies[0].reason, DiscrepancyReason.MISSING_IN_INTERNAL
        )

    # ---------- idempotency / clear

    def test_clear_resets_state(self):
        self.engine.load_internal_lot(_lot("INT_1"))
        self.engine.load_broker_lot(_lot("BRK_1"))
        first = self.engine.process_reconciliation()
        self.assertEqual(first.matched_clean, 1)

        self.engine.clear()

        self.assertEqual(self.engine.internal_count, 0)
        self.assertEqual(self.engine.broker_count, 0)

        empty = self.engine.process_reconciliation()
        self.assertEqual(empty.matched_total, 0)
        self.assertEqual(empty.discrepancy_count, 0)

    # ---------- empty ledger

    def test_empty_ledger_returns_empty_result(self):
        result = self.engine.process_reconciliation()

        self.assertEqual(result.matched_clean, 0)
        self.assertEqual(result.matched_with_discrepancies, 0)
        self.assertEqual(result.discrepancy_count, 0)
        self.assertEqual(len(result.unmatched_internal), 0)
        self.assertEqual(len(result.unmatched_broker), 0)

    # ---------- metrics

    def test_metrics_computations(self):
        # Clean match: basis diff = 0.
        # Discrepancy match: internal basis 2000 - broker basis 2010 = -10.
        self.engine.load_internal_lot(_lot("INT_1", basis="1000"))
        self.engine.load_broker_lot(_lot("BRK_1", basis="1000"))
        self.engine.load_internal_lot(_lot("INT_2", basis="2000"))
        self.engine.load_broker_lot(_lot("BRK_2", basis="2010"))
        self.engine.load_internal_lot(_lot("INT_3"))

        result = self.engine.process_reconciliation()

        m = result.metrics()
        self.assertEqual(m["matched_clean"], 1)
        self.assertEqual(m["matched_with_discrepancies"], 1)
        self.assertEqual(m["missing_in_broker"], 1)
        self.assertEqual(m["missing_in_internal"], 0)
        # Net basis delta = 0 + (-10) — broker over-reported basis by $10.
        self.assertEqual(m["total_basis_delta"], Decimal("-10.00"))


class TestAdjustmentCode(unittest.TestCase):
    """Validate the IRS Form 8949 adjustment-code enum is wired into TaxLot."""

    def test_adjustment_code_round_trips(self):
        lot = TaxLot(
            lot_id="INT_1",
            symbol="AAPL",
            quantity=Decimal("100"),
            acquired_date=date(2023, 1, 1),
            sold_date=date(2023, 2, 1),
            proceeds=Decimal("15000"),
            cost_basis=Decimal("14000"),
            adjustment_code=AdjustmentCode.W,
        )
        self.assertEqual(lot.adjustment_code, AdjustmentCode.W)

    def test_invalid_adjustment_code_rejected_by_enum(self):
        with self.assertRaises(ValueError):
            TaxLot(
                lot_id="INT_1",
                symbol="AAPL",
                quantity=Decimal("100"),
                acquired_date=date(2023, 1, 1),
                sold_date=date(2023, 2, 1),
                proceeds=Decimal("15000"),
                cost_basis=Decimal("14000"),
                adjustment_code="Z",  # noqa - intentional coverage of invalid value
            )


class TestNonFiniteInputRejected(unittest.TestCase):
    """A blank monetary cell in a broker CSV arrives as float("nan").

    Before validation covered this, such a lot was accepted at ingestion and
    then aborted the whole run mid-reconciliation with a bare
    ``decimal.InvalidOperation`` naming no lot -- Decimal NaN raises on ``<=``
    and Decimal Infinity raises on ``quantize``.
    """

    def setUp(self):
        self.engine = S1099BAndBrokerTaxReportingReconciliationEngine()

    def _lot_with(self, **overrides) -> TaxLot:
        kwargs = dict(
            lot_id="INT_1",
            symbol="AAPL",
            quantity=Decimal("100"),
            acquired_date=date(2023, 1, 1),
            sold_date=date(2023, 2, 1),
            proceeds=Decimal("15000.00"),
            cost_basis=Decimal("14000.00"),
        )
        kwargs.update(overrides)
        return TaxLot(**kwargs)

    def test_nan_proceeds_rejected_at_ingestion(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.load_internal_lot(self._lot_with(proceeds=float("nan")))
        self.assertIn("non-finite proceeds", str(ctx.exception))
        self.assertIn("INT_1", str(ctx.exception))

    def test_nan_quantity_rejected_at_ingestion(self):
        # Must be caught before the ``quantity <= 0`` comparison, which itself
        # raises InvalidOperation (not ValueError) on a Decimal NaN.
        with self.assertRaises(ValueError):
            self.engine.load_internal_lot(self._lot_with(quantity=float("nan")))

    def test_infinite_cost_basis_rejected_at_ingestion(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.load_broker_lot(self._lot_with(cost_basis=Decimal("Infinity")))
        self.assertIn("non-finite cost_basis", str(ctx.exception))

    def test_nan_wash_sale_amount_rejected_at_ingestion(self):
        with self.assertRaises(ValueError):
            self.engine.load_internal_lot(
                self._lot_with(wash_sale_disallowed_amount=float("nan"))
            )

    def test_finite_zero_basis_still_accepted(self):
        # Guard against over-rejection: a genuine $0.00 basis is legal (fully
        # disallowed lot, zero-cost grant) and must survive validation.
        self.engine.load_internal_lot(self._lot_with(cost_basis=Decimal("0.00")))
        self.assertEqual(self.engine.internal_count, 1)


class TestDeterministicOutputOrdering(unittest.TestCase):
    """The archived artifact is diffed against the prior accepted run.

    Iterating a ``set`` of match keys ordered the output by randomized string
    hashes, so an identical rerun produced a differently-ordered discrepancy
    list. Ordering must follow the internal ledger instead.
    """

    def _run(self, symbols):
        engine = S1099BAndBrokerTaxReportingReconciliationEngine(
            ToleranceConfig(absolute_usd=Decimal("0.01"), relative_basis_pct=Decimal("0"))
        )
        for i, sym in enumerate(symbols):
            engine.load_internal_lot(_lot("INT_%d" % i, symbol=sym, basis="1000.00"))
            engine.load_broker_lot(_lot("BRK_%d" % i, symbol=sym, basis="1050.00"))
        return engine.process_reconciliation()

    def test_discrepancies_follow_internal_ledger_order(self):
        symbols = ["AAPL", "MSFT", "TSLA", "NVDA", "AMZN", "GOOG", "META", "NFLX"]
        result = self._run(symbols)

        self.assertEqual(
            [d.internal_lot_id for d in result.discrepancies],
            ["INT_%d" % i for i in range(len(symbols))],
        )

    def test_repeated_runs_emit_identical_order(self):
        symbols = ["AAPL", "MSFT", "TSLA", "NVDA", "AMZN", "GOOG", "META", "NFLX"]
        first = [d.internal_lot_id for d in self._run(symbols).discrepancies]
        second = [d.internal_lot_id for d in self._run(symbols).discrepancies]
        self.assertEqual(first, second)

    def test_matched_pairs_follow_internal_ledger_order(self):
        symbols = ["ZZZZ", "AAAA", "MMMM", "QQQQ"]
        engine = S1099BAndBrokerTaxReportingReconciliationEngine()
        for i, sym in enumerate(symbols):
            engine.load_internal_lot(_lot("INT_%d" % i, symbol=sym))
            engine.load_broker_lot(_lot("BRK_%d" % i, symbol=sym))

        result = engine.process_reconciliation()

        self.assertEqual(
            [p.internal_lot_id for p in result.matched_clean_seq],
            ["INT_%d" % i for i in range(len(symbols))],
        )


class TestForm8949ColumnGSign(unittest.TestCase):
    """Column (g) runs broker-minus-internal, the opposite of ``basis_delta``.

    Per the "Worksheet for Basis Adjustments in Column (g)" in the Instructions
    for Form 8949: line 1 is the basis shown on Form 1099-B box 1e, line 2 is
    the correct basis; the entry is positive when line 1 exceeds line 2 and
    negative (in parentheses) when line 2 exceeds line 1. Expected values below
    are derived from that worksheet, not from the implementation.
    """

    def _pair(self, internal_basis: str, broker_basis: str, covered: bool = True):
        engine = S1099BAndBrokerTaxReportingReconciliationEngine(
            ToleranceConfig(absolute_usd=Decimal("0.01"), relative_basis_pct=Decimal("0"))
        )
        engine.load_internal_lot(_lot("INT_1", basis=internal_basis, covered=covered))
        engine.load_broker_lot(_lot("BRK_1", basis=broker_basis, covered=covered))
        result = engine.process_reconciliation()
        pairs = list(result.matched_clean_seq) + [
            p for p, _ in result.matched_with_discrepancies_seq
        ]
        self.assertEqual(len(pairs), 1)
        return pairs[0]

    def test_broker_basis_too_low_gives_negative_adjustment(self):
        # 1099-B says 9000.00 (line 1); correct basis is 9010.00 (line 2).
        # Line 2 > line 1, so column (g) is negative: 9000 - 9010 = -10.00.
        pair = self._pair(internal_basis="9010.00", broker_basis="9000.00")
        self.assertEqual(pair.basis_delta, Decimal("10.00"))
        self.assertEqual(pair.form_8949_column_g_basis_adjustment, Decimal("-10.00"))

    def test_broker_basis_too_high_gives_positive_adjustment(self):
        # 1099-B says 9025.50 (line 1); correct basis is 9000.00 (line 2).
        # Line 1 > line 2, so column (g) is positive: 9025.50 - 9000 = 25.50.
        pair = self._pair(internal_basis="9000.00", broker_basis="9025.50")
        self.assertEqual(pair.basis_delta, Decimal("-25.50"))
        self.assertEqual(pair.form_8949_column_g_basis_adjustment, Decimal("25.50"))

    def test_noncovered_pair_reports_zero_adjustment(self):
        # Form 8949 box B/E: enter the correct basis in column (e) and -0- in
        # column (g) -- no adjustment, regardless of the size of the difference.
        pair = self._pair(
            internal_basis="9010.00", broker_basis="9000.00", covered=False
        )
        self.assertEqual(pair.basis_delta, Decimal("10.00"))
        self.assertEqual(pair.form_8949_column_g_basis_adjustment, Decimal("0.00"))

    def test_routing_follows_broker_covered_flag_on_disagreement(self):
        # The broker reported basis to the IRS (box 12) but the internal ledger
        # thinks the lot is noncovered. The IRS matched the return against the
        # broker's form, so this is box A/D and still needs a real column (g);
        # keying off the conservative ``covered`` AND would return -0- and
        # under-state the correction.
        engine = S1099BAndBrokerTaxReportingReconciliationEngine(
            ToleranceConfig(absolute_usd=Decimal("0.01"), relative_basis_pct=Decimal("0"))
        )
        engine.load_internal_lot(_lot("INT_1", basis="9010.00", covered=False))
        engine.load_broker_lot(_lot("BRK_1", basis="9000.00", covered=True))

        result = engine.process_reconciliation()
        pair = result.matched_with_discrepancies_seq[0][0]

        self.assertFalse(pair.covered)
        self.assertTrue(pair.broker_covered)
        self.assertEqual(
            pair.form_8949_column_g_basis_adjustment, Decimal("-10.00")
        )

    def test_agreeing_basis_has_no_negative_zero(self):
        pair = self._pair(internal_basis="9000.00", broker_basis="9000.00")
        adjustment = pair.form_8949_column_g_basis_adjustment
        self.assertEqual(adjustment, Decimal("0.00"))
        # Decimal("-0.00") compares equal to zero but renders with a sign.
        self.assertEqual(str(adjustment), "0.00")


class TestToleranceBoundaries(unittest.TestCase):
    """Exact-threshold behaviour of the dual tolerance."""

    def _reconcile(self, absolute, relative, internal_basis, broker_basis):
        engine = S1099BAndBrokerTaxReportingReconciliationEngine(
            ToleranceConfig(
                absolute_usd=Decimal(absolute), relative_basis_pct=Decimal(relative)
            )
        )
        engine.load_internal_lot(_lot("INT_1", basis=internal_basis))
        engine.load_broker_lot(_lot("BRK_1", basis=broker_basis))
        return engine.process_reconciliation()

    def test_difference_exactly_at_absolute_bound_is_accepted(self):
        # ``meets`` is inclusive (``diff <= absolute_usd``).
        result = self._reconcile("0.05", "0", "9000.00", "9000.05")
        self.assertEqual(result.matched_clean, 1)
        self.assertEqual(result.discrepancy_count, 0)

    def test_one_cent_beyond_absolute_bound_is_flagged(self):
        result = self._reconcile("0.05", "0", "9000.00", "9000.06")
        self.assertEqual(result.matched_clean, 0)
        self.assertEqual(
            result.discrepancies[0].reason, DiscrepancyReason.BASIS_OUTSIDE_TOLERANCE
        )

    def test_difference_exactly_at_relative_bound_is_accepted(self):
        # 1bp of 1,000,000.00 is exactly 100.00.
        result = self._reconcile("0.05", "0.0001", "1000100.00", "1000000.00")
        self.assertEqual(result.matched_clean, 1)

    def test_one_cent_beyond_relative_bound_is_flagged(self):
        result = self._reconcile("0.05", "0.0001", "1000100.01", "1000000.00")
        self.assertEqual(result.matched_clean, 0)

    def test_near_zero_broker_basis_does_not_divide_relative_tolerance(self):
        # A $0.00 broker basis must not let an arbitrary difference through.
        result = self._reconcile("0.05", "0.0001", "5000.00", "0.00")
        self.assertEqual(result.matched_clean, 0)
        self.assertEqual(
            result.discrepancies[0].reason, DiscrepancyReason.BASIS_OUTSIDE_TOLERANCE
        )


class TestProceedsAndWashSaleEdges(unittest.TestCase):
    """Reasons and branches the original suite left unexercised."""

    def setUp(self):
        self.engine = S1099BAndBrokerTaxReportingReconciliationEngine(
            ToleranceConfig(absolute_usd=Decimal("0.05"), relative_basis_pct=Decimal("0"))
        )

    def test_proceeds_outside_tolerance_is_reported(self):
        self.engine.load_internal_lot(_lot("INT_1", proceeds="15000.00"))
        self.engine.load_broker_lot(_lot("BRK_1", proceeds="15100.00"))

        result = self.engine.process_reconciliation()

        self.assertEqual(result.matched_with_discrepancies, 1)
        reasons = [d.reason for d in result.discrepancies]
        self.assertIn(DiscrepancyReason.PROCEEDS_OUTSIDE_TOLERANCE, reasons)
        self.assertNotIn(DiscrepancyReason.BASIS_OUTSIDE_TOLERANCE, reasons)
        proceeds_discrepancy = next(
            d
            for d in result.discrepancies
            if d.reason == DiscrepancyReason.PROCEEDS_OUTSIDE_TOLERANCE
        )
        self.assertEqual(proceeds_discrepancy.difference_amount, Decimal("-100.00"))

    def test_wash_amount_present_on_one_side_only_is_reported(self):
        # Both agree a wash sale occurred, but only the broker quantified it.
        self.engine.load_internal_lot(_lot("INT_1", is_wash_sale=True))
        self.engine.load_broker_lot(
            _lot("BRK_1", is_wash_sale=True, wash_disallowed="250.00")
        )

        result = self.engine.process_reconciliation()

        self.assertEqual(result.matched_with_discrepancies, 1)
        reasons = [d.reason for d in result.discrepancies]
        self.assertIn(DiscrepancyReason.WASH_SALE_AMOUNT_MISMATCH, reasons)
        self.assertNotIn(DiscrepancyReason.WASH_SALE_FLAG_MISMATCH, reasons)

    def test_matching_wash_sale_amounts_produce_no_discrepancy(self):
        self.engine.load_internal_lot(
            _lot("INT_1", is_wash_sale=True, wash_disallowed="250.00")
        )
        self.engine.load_broker_lot(
            _lot("BRK_1", is_wash_sale=True, wash_disallowed="250.00")
        )

        result = self.engine.process_reconciliation()

        self.assertEqual(result.matched_clean, 1)
        self.assertEqual(result.discrepancy_count, 0)
        self.assertTrue(result.matched_clean_seq[0].is_wash_sale)

    def test_clean_matches_never_carry_tolerance_discrepancies(self):
        # SKILL.md success criterion 2: nothing in matched_clean may be an
        # out-of-tolerance pair.
        for i in range(5):
            self.engine.load_internal_lot(
                _lot("INT_%d" % i, symbol="SYM%d" % i, basis="1000.00")
            )
            self.engine.load_broker_lot(
                _lot(
                    "BRK_%d" % i,
                    symbol="SYM%d" % i,
                    basis="1000.00" if i % 2 else "1500.00",
                )
            )

        result = self.engine.process_reconciliation()

        for pair in result.matched_clean_seq:
            self.assertEqual(pair.basis_delta, Decimal("0.00"))
            self.assertEqual(pair.proceeds_delta, Decimal("0.00"))
        self.assertEqual(result.matched_clean, 2)
        self.assertEqual(result.matched_with_discrepancies, 3)


class TestGainAndMetrics(unittest.TestCase):
    def test_gain_is_proceeds_minus_basis_quantized_to_cents(self):
        lot = _lot("INT_1", proceeds="15000.00", basis="14000.00")
        self.assertEqual(lot.gain, Decimal("1000.00"))

    def test_gain_is_negative_on_a_loss(self):
        lot = _lot("INT_1", proceeds="9000.00", basis="14000.00")
        self.assertEqual(lot.gain, Decimal("-5000.00"))

    def test_metrics_expose_every_discrepancy_reason(self):
        # SKILL.md documents metrics() as "counts by reason"; every reason must
        # therefore be present, zero-valued when it did not occur.
        engine = S1099BAndBrokerTaxReportingReconciliationEngine()
        metrics = engine.process_reconciliation().metrics()
        for reason in DiscrepancyReason:
            key = _METRIC_KEYS[reason]
            self.assertIn(key, metrics)
            self.assertEqual(metrics[key], 0)

    def test_metrics_count_wash_and_tolerance_reasons(self):
        engine = S1099BAndBrokerTaxReportingReconciliationEngine(
            ToleranceConfig(absolute_usd=Decimal("0.05"), relative_basis_pct=Decimal("0"))
        )
        engine.load_internal_lot(_lot("INT_1", symbol="AAA", basis="1000.00"))
        engine.load_broker_lot(_lot("BRK_1", symbol="AAA", basis="1500.00"))
        engine.load_internal_lot(_lot("INT_2", symbol="BBB", is_wash_sale=True))
        engine.load_broker_lot(_lot("BRK_2", symbol="BBB", is_wash_sale=False))

        metrics = engine.process_reconciliation().metrics()

        self.assertEqual(metrics["basis_outside_tolerance"], 1)
        self.assertEqual(metrics["wash_sale_flag_mismatch"], 1)
        self.assertEqual(metrics["proceeds_outside_tolerance"], 0)
        self.assertEqual(metrics["discrepancy_count"], 2)
        self.assertEqual(metrics["total_basis_delta"], Decimal("-500.00"))


if __name__ == "__main__":
    unittest.main()
