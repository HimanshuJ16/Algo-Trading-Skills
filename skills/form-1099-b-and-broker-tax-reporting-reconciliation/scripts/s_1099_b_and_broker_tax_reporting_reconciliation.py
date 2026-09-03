"""Institutional reconciliation engine for 1099-B vs internal tax lot ledgers.

Compares an internal realized-lot ledger against a broker 1099-B filing and
classifies every record as:

  * matched_clean              – exact match within tolerance
  * matched_with_discrepancies – matched but flags disagree (e.g. wash sale)
  * missing_in_1099b           – present internally, absent from broker
  * missing_in_internal        – present on broker, absent internally

The engine is pure-Python (no third-party deps), deterministic, side-effect-free
(except structured logging), and uses :class:`decimal.Decimal` for every monetary
field so it can be run on ledgers with tens of thousands of tax lots without
IEEE-754 drift.

Design notes
------------
* Lot matching uses a hash index bucketed by ``(symbol, quantity, acquired_date,
  sold_date)``. Pairing within a bucket is O(k**2) in the bucket size k, which
  is 1 for the overwhelming majority of real lots, so the reconcile cost is
  O(N + M) in practice and O(N + M + sum(k**2)) in the worst case.
* Buckets are visited in internal-ledger insertion order so that two runs over
  the same input emit the discrepancy list in the same order. Iterating a
  ``set`` of match keys would not: string hashing is randomized per process, so
  the artifact would reorder between runs and could not be diffed against the
  previously accepted one (see ``assets/checklist.md`` step 7).
* Tolerances are configurable and dual (absolute cents + relative percent) so
  the engine works equally well on penny-lots and $10M bond lots.
* All output monetary fields are :class:`decimal.Decimal` quantized to two
  decimal places (cents).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Tax-lot math needs well under 20 significant digits, so the interpreter's
# default context precision (28) is always sufficient. This module deliberately
# does NOT set ``getcontext().prec``: that mutates state shared with every other
# Decimal user in the host process, and silently lowering a caller's
# deliberately-raised precision would be very hard to trace back to here.
_CENT = Decimal("0.01")

#: Fields validated for finiteness at ingestion (see ``_validate_lot``).
_NUMERIC_FIELDS = ("quantity", "proceeds", "cost_basis", "wash_sale_disallowed_amount")


# ---------------------------------------------------------------------------
# Domain enums
# ---------------------------------------------------------------------------


class AdjustmentCode(str, Enum):
    """IRS Form 8949 / 1099-B column (f) adjustment codes (subset in scope)."""

    B = "B"  # Incorrect basis shown on 1099-B
    D = "D"  # Accrued market discount
    E = "E"  # Unreported selling expenses/option premiums
    L = "L"  # Nondeductible loss (non-wash-sale)
    M = "M"  # Multiple transactions on one row
    N = "N"  # Nominee reporting
    T = "T"  # Incorrect gain/loss type on 1099-B
    W = "W"  # Wash sale nondeductible loss
    O = "O"  # Other adjustment


# ---------------------------------------------------------------------------
# Lot record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaxLot:
    """A single realized tax lot.

    Monetary fields are :class:`decimal.Decimal` to eliminate floating-point
    drift. The ``covered`` flag corresponds to ``box 12`` on Form 1099-B (basis
    reported to the IRS) and selects the Form 8949 box (A/D vs B/E).
    """

    lot_id: str
    symbol: str
    quantity: Decimal
    acquired_date: date
    sold_date: date
    proceeds: Decimal
    cost_basis: Decimal
    is_wash_sale: bool = False
    wash_sale_disallowed_amount: Optional[Decimal] = None
    covered: bool = True
    adjustment_code: Optional[AdjustmentCode] = None

    def __post_init__(self) -> None:  # pragma: no cover - dataclass safety net
        # Coerce numeric inputs to Decimal if a caller passed a float/int.
        # Dataclasses with frozen=True require object.__setattr__, hence the dance.
        if not isinstance(self.quantity, Decimal):
            object.__setattr__(self, "quantity", Decimal(str(self.quantity)))
        if not isinstance(self.proceeds, Decimal):
            object.__setattr__(self, "proceeds", Decimal(str(self.proceeds)))
        if not isinstance(self.cost_basis, Decimal):
            object.__setattr__(self, "cost_basis", Decimal(str(self.cost_basis)))
        if (
            self.wash_sale_disallowed_amount is not None
            and not isinstance(self.wash_sale_disallowed_amount, Decimal)
        ):
            object.__setattr__(
                self,
                "wash_sale_disallowed_amount",
                Decimal(str(self.wash_sale_disallowed_amount)),
            )
        if (
            self.adjustment_code is not None
            and not isinstance(self.adjustment_code, AdjustmentCode)
        ):
            raise ValueError(
                f"adjustment_code must be an AdjustmentCode member, got {self.adjustment_code!r}"
            )

    @property
    def gain(self) -> Decimal:
        """Realized gain (loss may be negative)."""
        return (self.proceeds - self.cost_basis).quantize(_CENT, rounding=ROUND_HALF_UP)

    def match_key(self) -> tuple:
        """Hash key used for bucketed matching.

        Quantity is normalized to a Decimal with 8 fractional digits so that
        ``0.1`` and ``Decimal("0.1")`` hash identically across input shapes.
        """
        normalized_qty = self.quantity.quantize(Decimal("0.00000001"))
        return (self.symbol, normalized_qty, self.acquired_date, self.sold_date)


# ---------------------------------------------------------------------------
# Reconciliation result types
# ---------------------------------------------------------------------------


class DiscrepancyReason(str, Enum):
    """The fixed taxonomy of reconciliation outcomes."""

    MISSING_IN_BROKER = "Missing in 1099-B"
    MISSING_IN_INTERNAL = "Missing Internally"
    WASH_SALE_FLAG_MISMATCH = "Wash Sale Mismatch"
    WASH_SALE_AMOUNT_MISMATCH = "Wash Sale Disallowed Amount Mismatch"
    BASIS_OUTSIDE_TOLERANCE = "Basis Outside Tolerance"
    PROCEEDS_OUTSIDE_TOLERANCE = "Proceeds Outside Tolerance"
    #: Reserved. Duplicate ids are rejected at ingestion time by
    #: ``load_internal_lot`` / ``load_broker_lot``, which raise ``ValueError``
    #: rather than emitting a discrepancy, so the engine never returns this
    #: reason. It is retained because it is part of the published enum.
    DUPLICATE_LOT_ID = "Duplicate Lot ID"


#: Stable snake_case metric key per reason, so ``metrics()`` output keys do not
#: shift if an enum value string is ever reworded.
_METRIC_KEYS: Dict["DiscrepancyReason", str] = {
    DiscrepancyReason.MISSING_IN_BROKER: "missing_in_broker",
    DiscrepancyReason.MISSING_IN_INTERNAL: "missing_in_internal",
    DiscrepancyReason.WASH_SALE_FLAG_MISMATCH: "wash_sale_flag_mismatch",
    DiscrepancyReason.WASH_SALE_AMOUNT_MISMATCH: "wash_sale_amount_mismatch",
    DiscrepancyReason.BASIS_OUTSIDE_TOLERANCE: "basis_outside_tolerance",
    DiscrepancyReason.PROCEEDS_OUTSIDE_TOLERANCE: "proceeds_outside_tolerance",
    DiscrepancyReason.DUPLICATE_LOT_ID: "duplicate_lot_id",
}


@dataclass(frozen=True)
class Discrepancy:
    internal_lot_id: Optional[str]
    broker_lot_id: Optional[str]
    reason: DiscrepancyReason
    details: str
    difference_amount: Optional[Decimal] = None  # signed $ delta if applicable


@dataclass(frozen=True)
class MatchPair:
    """One internal lot paired with one broker 1099-B line.

    ``proceeds_delta`` and ``basis_delta`` are both signed *internal minus
    broker*. Note that Form 8949 column (g) runs the other way round — use
    :attr:`form_8949_column_g_basis_adjustment` rather than ``basis_delta``
    when building a filing.
    """

    internal_lot_id: str
    broker_lot_id: str
    is_wash_sale: bool
    proceeds_delta: Decimal
    basis_delta: Decimal
    #: True only where BOTH sides call the lot covered. Conservative summary
    #: flag; it is deliberately *not* what drives Form 8949 box routing.
    covered: bool = True
    #: The broker's own covered status (Form 1099-B box 12 / box 5). This is
    #: what the IRS matches the return against, so this is the flag that
    #: selects box A/D versus B/E.
    broker_covered: bool = True

    @property
    def form_8949_column_g_basis_adjustment(self) -> Decimal:
        """Form 8949 column (g) basis adjustment implied by this pair.

        Per the "Worksheet for Basis Adjustments in Column (g)" in the
        Instructions for Form 8949, line 1 is the basis shown on Form 1099-B
        (box 1e) and line 2 is the correct basis. The worksheet directs a
        *positive* column (g) entry when line 1 exceeds line 2 and a *negative*
        one when line 2 exceeds line 1. Treating the broker figure as line 1
        and the internal figure as line 2, the entry is therefore
        ``broker_basis - internal_basis`` — the **negation** of
        :attr:`basis_delta`. Getting this sign backwards doubles the basis
        error instead of cancelling it, so the rule is encoded here rather
        than left to the caller.

        Returns ``Decimal("0.00")`` for a noncovered pair (Form 8949 box B or
        E), where the instructions direct the filer to put the correct basis
        straight into column (e) and enter -0- in column (g).

        Routing keys off :attr:`broker_covered`, not :attr:`covered`. Where the
        two sides disagree about covered status, the broker's box 12 is what
        the IRS matched the return against, so a lot the broker reported as
        covered still needs a real column (g) entry even if the internal ledger
        called it noncovered. Keying off the conservative ``covered`` AND would
        silently return -0- there and under-state the correction. The engine
        does not currently emit a discrepancy for a covered-flag disagreement;
        compare ``covered`` against ``broker_covered`` if you need to catch it.
        """
        if not self.broker_covered:
            return Decimal("0.00")
        adjustment = -self.basis_delta
        # ``-Decimal("0.00")`` is Decimal("-0.00"); normalize the sign so the
        # archived artifact never renders a negative zero.
        return adjustment if adjustment else Decimal("0.00")


@dataclass(frozen=True)
class ReconciliationResult:
    matched_clean_seq: Sequence[MatchPair]
    matched_with_discrepancies_seq: Sequence[Tuple[MatchPair, List[Discrepancy]]]
    discrepancies: List[Discrepancy]
    unmatched_internal: List[str]
    unmatched_broker: List[str]

    @property
    def matched_clean(self) -> int:
        return len(self.matched_clean_seq)

    @property
    def matched_with_discrepancies(self) -> int:
        return len(self.matched_with_discrepancies_seq)

    @property
    def matched_total(self) -> int:
        return self.matched_clean + self.matched_with_discrepancies

    @property
    def discrepancy_count(self) -> int:
        return len(self.discrepancies)

    def metrics(self) -> Dict[str, int | Decimal]:
        """Operational metrics by reason and aggregate dollar deltas.

        Every member of :class:`DiscrepancyReason` is represented, zero-valued
        when it did not occur. The two aggregate deltas are signed *internal
        minus broker* sums over all matched pairs.
        """
        by_reason: Dict[DiscrepancyReason, int] = defaultdict(int)
        total_proceeds_delta = Decimal("0.00")
        total_basis_delta = Decimal("0.00")
        for pair in self.matched_clean_seq:
            total_proceeds_delta += pair.proceeds_delta
            total_basis_delta += pair.basis_delta
        for pair, _ in self.matched_with_discrepancies_seq:
            total_proceeds_delta += pair.proceeds_delta
            total_basis_delta += pair.basis_delta
        for d in self.discrepancies:
            by_reason[d.reason] += 1
        metrics: Dict[str, int | Decimal] = {
            "matched_clean": self.matched_clean,
            "matched_with_discrepancies": self.matched_with_discrepancies,
            "discrepancy_count": self.discrepancy_count,
            "total_basis_delta": total_basis_delta.quantize(_CENT, rounding=ROUND_HALF_UP),
            "total_proceeds_delta": total_proceeds_delta.quantize(
                _CENT, rounding=ROUND_HALF_UP
            ),
        }
        # Every emittable reason gets a key, present and zero when unseen, so a
        # downstream dashboard or alert rule never has to guard on absence.
        for reason in DiscrepancyReason:
            metrics[_METRIC_KEYS[reason]] = by_reason[reason]
        return metrics


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToleranceConfig:
    """Dual tolerance — absolute cents *and* a relative percent of basis.

    A difference passes if it satisfies EITHER bound. This matches how leading
    reconciliation systems price rounding tolerance on cost basis, since the
    error profile of broker-reported basis is often step-shaped (e.g. one-penny
    drift on a $1k lot is 0.1%, on a $1M lot is 0.0001%).
    """

    absolute_usd: Decimal = Decimal("0.05")
    relative_basis_pct: Decimal = Decimal("0.0001")  # 1 basis point

    def meets(self, internal: Decimal, broker: Decimal) -> bool:
        diff = abs(internal - broker)
        if diff <= self.absolute_usd:
            return True
        # Avoid div-by-zero / near-zero basis
        if broker.is_zero() or abs(broker) < Decimal("0.01"):
            return False
        relative_max = abs(broker) * self.relative_basis_pct
        return diff <= relative_max


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class S1099BAndBrokerTaxReportingReconciliationEngine:
    """Institutional reconciliation engine for 1099-B tax reports.

    The engine consumes one or more internal tax lots and one or more broker
    1099-B lines through ``load_internal_lot`` / ``load_broker_lot``. After all
    input is loaded, ``process_reconciliation`` runs the hash-bucketed match
    and returns a :class:`ReconciliationResult`.

    Example::

        engine = S1099BAndBrokerTaxReportingReconciliationEngine()
        engine.load_internal_lot(internal_lot)
        engine.load_broker_lot(broker_lot)
        result = engine.process_reconciliation()
        print(result.metrics())
    """

    def __init__(
        self,
        tolerance: ToleranceConfig | None = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._internal_lots: Dict[str, TaxLot] = {}
        self._broker_lots: Dict[str, TaxLot] = {}
        self._tolerance = tolerance or ToleranceConfig()
        self._logger = logger or _default_logger()

    # ------------------------------------------------------------------ ingest

    def load_internal_lot(self, lot: TaxLot) -> None:
        self._validate_lot(lot, source="internal")
        if lot.lot_id in self._internal_lots:
            raise ValueError(f"Duplicate internal lot_id: {lot.lot_id}")
        self._internal_lots[lot.lot_id] = lot

    def load_broker_lot(self, lot: TaxLot) -> None:
        self._validate_lot(lot, source="broker")
        if lot.lot_id in self._broker_lots:
            raise ValueError(f"Duplicate broker lot_id: {lot.lot_id}")
        self._broker_lots[lot.lot_id] = lot

    def load_internal_lots(self, lots: Iterable[TaxLot]) -> None:
        for lot in lots:
            self.load_internal_lot(lot)

    def load_broker_lots(self, lots: Iterable[TaxLot]) -> None:
        for lot in lots:
            self.load_broker_lot(lot)

    def clear(self) -> None:
        """Reset the engine for a fresh reconciliation run (idempotency)."""
        self._internal_lots.clear()
        self._broker_lots.clear()

    # -------------------------------------------------------------- accessors

    @property
    def internal_count(self) -> int:
        return len(self._internal_lots)

    @property
    def broker_count(self) -> int:
        return len(self._broker_lots)

    # ---------------------------------------------------------- reconciliation

    def process_reconciliation(self) -> ReconciliationResult:
        """Run the reconciliation and classify all lots.

        Returns a :class:`ReconciliationResult`. Never raises on data anomalies
        (those become discrepancies); only programmer error or invalid input
        raises.
        """
        matched_clean: List[MatchPair] = []
        matched_with_discrepancies: List[Tuple[MatchPair, List[Discrepancy]]] = []
        discrepancies: List[Discrepancy] = []

        # Build hash-bucketed indices for O(N+M) matching.
        internal_index: Dict[tuple, List[TaxLot]] = _bucket(self._internal_lots.values())
        broker_index: Dict[tuple, List[TaxLot]] = _bucket(self._broker_lots.values())

        consumed_broker: set[str] = set()
        consumed_internal: set[str] = set()

        # Iterate in internal-ledger insertion order, NOT over a set of match
        # keys: str hashing is randomized per process, so set iteration would
        # reorder the emitted discrepancy list between runs on identical input
        # and make the archived artifact undiffable. Keys present only on the
        # broker side are skipped here anyway (no internal lot to pair with)
        # and are swept up by the missing-in-internal pass below.
        for key, internal_bucket in internal_index.items():
            broker_bucket = broker_index.get(key, [])

            if not broker_bucket:
                continue

            # Two-pass within bucket:
            #   Pass 1: pair lots that produce NO discrepancies (clean match).
            #   Pass 2: any remaining lots are paired best-effort; whatever
            #           doesn't match goes to the global missing-in-* lists.
            # This handles the legitimate case where multiple lots share
            # (symbol, qty, dates) but have distinct basis values — a real
            # FIFO-collision scenario.
            unconsumed_int = [l for l in internal_bucket if l.lot_id not in consumed_internal]
            unconsumed_brk = [l for l in broker_bucket if l.lot_id not in consumed_broker]

            # Pass 1: clean matches.
            for int_lot in list(unconsumed_int):
                clean_partner = None
                for b in unconsumed_brk:
                    if b.lot_id in consumed_broker:
                        continue
                    _, d = self._evaluate_pair(int_lot, b)
                    if not d:
                        clean_partner = b
                        break
                if clean_partner is None:
                    continue
                consumed_internal.add(int_lot.lot_id)
                consumed_broker.add(clean_partner.lot_id)
                pair, _ = self._evaluate_pair(int_lot, clean_partner)
                matched_clean.append(pair)

            # Pass 2: best-effort pairing of remaining lots.
            leftover_int = [l for l in unconsumed_int if l.lot_id not in consumed_internal]
            leftover_brk = [l for l in unconsumed_brk if l.lot_id not in consumed_broker]
            while leftover_int and leftover_brk:
                int_lot = leftover_int.pop(0)
                broker_match = leftover_brk.pop(0)
                consumed_internal.add(int_lot.lot_id)
                consumed_broker.add(broker_match.lot_id)
                pair, pair_discrepancies = self._evaluate_pair(int_lot, broker_match)
                if pair_discrepancies:
                    matched_with_discrepancies.append((pair, pair_discrepancies))
                    discrepancies.extend(pair_discrepancies)
                else:
                    matched_clean.append(pair)

        # Bucket-only-in-internal implies quantity/date mismatch *globally* —
        # report as Missing in 1099-B rather than shape-specific. This is the
        # conservative auditor posture.
        for int_id, int_lot in self._internal_lots.items():
            if int_id in consumed_internal:
                continue
            discrepancies.append(
                Discrepancy(int_id, None, DiscrepancyReason.MISSING_IN_BROKER, "Internal lot not found in broker 1099-B report.")
            )

        for brk_id in self._broker_lots:
            if brk_id in consumed_broker:
                continue
            discrepancies.append(
                Discrepancy(None, brk_id, DiscrepancyReason.MISSING_IN_INTERNAL, "Broker lot not found in internal ledger.")
            )

        unmatched_internal = [
            lot_id for lot_id in self._internal_lots if lot_id not in consumed_internal
        ]
        unmatched_broker = [
            lot_id for lot_id in self._broker_lots if lot_id not in consumed_broker
        ]

        self._logger.info(
            "reconciliation.complete",
            extra={
                "matched_clean": len(matched_clean),
                "matched_with_discrepancies": len(matched_with_discrepancies),
                "discrepancies": len(discrepancies),
                "internal_count": self.internal_count,
                "broker_count": self.broker_count,
            },
        )

        return ReconciliationResult(
            matched_clean_seq=tuple(matched_clean),
            matched_with_discrepancies_seq=tuple(matched_with_discrepancies),
            discrepancies=discrepancies,
            unmatched_internal=unmatched_internal,
            unmatched_broker=unmatched_broker,
        )

    # --------------------------------------------------------------- internals

    def _evaluate_pair(
        self, internal: TaxLot, broker: TaxLot
    ) -> Tuple[MatchPair, List[Discrepancy]]:
        discrepancies: List[Discrepancy] = []

        proceeds_delta = (internal.proceeds - broker.proceeds).quantize(
            _CENT, rounding=ROUND_HALF_UP
        )
        basis_delta = (internal.cost_basis - broker.cost_basis).quantize(
            _CENT, rounding=ROUND_HALF_UP
        )

        if not self._tolerance.meets(internal.proceeds, broker.proceeds):
            discrepancies.append(
                Discrepancy(
                    internal.lot_id,
                    broker.lot_id,
                    DiscrepancyReason.PROCEEDS_OUTSIDE_TOLERANCE,
                    f"Proceeds diff ${proceeds_delta} exceeds tolerance",
                    difference_amount=proceeds_delta,
                )
            )

        if not self._tolerance.meets(internal.cost_basis, broker.cost_basis):
            discrepancies.append(
                Discrepancy(
                    internal.lot_id,
                    broker.lot_id,
                    DiscrepancyReason.BASIS_OUTSIDE_TOLERANCE,
                    f"Cost basis diff ${basis_delta} exceeds tolerance",
                    difference_amount=basis_delta,
                )
            )

        wash_sale_disallowed_a: Optional[Decimal] = internal.wash_sale_disallowed_amount
        wash_sale_disallowed_b: Optional[Decimal] = broker.wash_sale_disallowed_amount

        if internal.is_wash_sale != broker.is_wash_sale:
            discrepancies.append(
                Discrepancy(
                    internal.lot_id,
                    broker.lot_id,
                    DiscrepancyReason.WASH_SALE_FLAG_MISMATCH,
                    f"Internal is_wash_sale={internal.is_wash_sale}, broker is_wash_sale={broker.is_wash_sale}",
                )
            )

        if (
            (wash_sale_disallowed_a is None) != (wash_sale_disallowed_b is None)
        ) or (
            wash_sale_disallowed_a is not None
            and wash_sale_disallowed_b is not None
            and not self._tolerance.meets(wash_sale_disallowed_a, wash_sale_disallowed_b)
        ):
            delta = (
                (wash_sale_disallowed_a or Decimal("0"))
                - (wash_sale_disallowed_b or Decimal("0"))
            )
            discrepancies.append(
                Discrepancy(
                    internal.lot_id,
                    broker.lot_id,
                    DiscrepancyReason.WASH_SALE_AMOUNT_MISMATCH,
                    f"Wash sale disallowed diff ${delta.quantize(_CENT, rounding=ROUND_HALF_UP)}",
                    difference_amount=delta.quantize(_CENT, rounding=ROUND_HALF_UP),
                )
            )

        return (
            MatchPair(
                internal_lot_id=internal.lot_id,
                broker_lot_id=broker.lot_id,
                is_wash_sale=internal.is_wash_sale or broker.is_wash_sale,
                proceeds_delta=proceeds_delta,
                basis_delta=basis_delta,
                covered=internal.covered and broker.covered,
                broker_covered=broker.covered,
            ),
            discrepancies,
        )

    @staticmethod
    def _validate_lot(lot: TaxLot, source: str) -> None:
        if not lot.lot_id or not isinstance(lot.lot_id, str):
            raise ValueError(f"{source} lot_id must be a non-empty string")
        if not lot.symbol or not isinstance(lot.symbol, str):
            raise ValueError(f"{source} symbol must be a non-empty string")
        # Non-finite values must be rejected BEFORE any comparison or
        # arithmetic. A blank cost-basis cell in a broker CSV (common on
        # noncovered rows) becomes float("nan") -> Decimal("NaN") in
        # TaxLot.__post_init__; Decimal NaN raises InvalidOperation on ``<=``
        # and Decimal Infinity raises it on ``quantize``. Without this guard a
        # single blank cell aborts the whole run with a bare InvalidOperation
        # carrying no lot id.
        for field_name in _NUMERIC_FIELDS:
            value = getattr(lot, field_name)
            if value is not None and not value.is_finite():
                raise ValueError(
                    f"{source} lot {lot.lot_id} has non-finite {field_name} "
                    f"({value}); a blank or unparsed monetary cell must be "
                    "resolved before ingestion, not passed through as NaN"
                )
        if lot.quantity is None or lot.quantity <= 0:
            raise ValueError(
                f"{source} lot {lot.lot_id} quantity must be > 0 (got {lot.quantity})"
            )
        if lot.sold_date < lot.acquired_date:
            # Coverage of short sales requires a `quantity < 0` convention this
            # skill does not yet expose. Treat backwards date as data error.
            raise ValueError(
                f"{source} lot {lot.lot_id} sold_date ({lot.sold_date}) precedes "
                f"acquired_date ({lot.acquired_date}) — short sale support is out "
                "of scope for this engine; pre-process before ingestion."
            )


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _default_logger() -> logging.Logger:
    """Return a module-level logger without configuring global logging."""
    return logging.getLogger(__name__)


def _bucket(lots: Iterable[TaxLot]) -> Dict[tuple, List[TaxLot]]:
    out: Dict[tuple, List[TaxLot]] = defaultdict(list)
    for lot in lots:
        out[lot.match_key()].append(lot)
    return out
