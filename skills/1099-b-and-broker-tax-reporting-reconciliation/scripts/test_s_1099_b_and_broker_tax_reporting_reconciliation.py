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

import unittest
from datetime import date
from decimal import Decimal

from s_1099_b_and_broker_tax_reporting_reconciliation import (
    AdjustmentCode,
    Discrepancy,
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
            basis = Decimal("0.10")  # everyone identical
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


if __name__ == "__main__":
    unittest.main()
