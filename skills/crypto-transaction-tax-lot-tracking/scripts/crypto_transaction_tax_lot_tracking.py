"""Crypto tax lot tracking for US federal reporting (Form 8949).

Tracks tax lots per wallet/account, matches disposals (crypto-to-crypto swaps,
crypto-to-fiat sales, crypto spent on fees) against those lots, and produces a
per-lot breakdown suitable for Form 8949 rows.

Jurisdiction: **United States federal income tax only.** Every rule encoded here
(FIFO default, wallet-by-wallet scoping, the "more than one year" holding period,
net-proceeds treatment of transaction costs) is a US rule. See
`references/standards.md` for the sourced citations. Do not reuse this
classification logic for another jurisdiction without re-deriving its rules.

This module computes and records; it does not file anything, does not apply
wash-sale adjustments, and does not value assets — the caller supplies USD fair
market values.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Lot matching methods. FIFO is the default because, absent an adequate
# identification made no later than the date and time of the disposal, the
# regulations treat units as disposed of in first-in-first-out order.
METHOD_FIFO = "FIFO"
METHOD_HIFO = "HIFO"
METHOD_LIFO = "LIFO"
SUPPORTED_METHODS = (METHOD_FIFO, METHOD_HIFO, METHOD_LIFO)

# Methods that are elections of specific identification and therefore require a
# contemporaneous identification record to be permissible.
SPECIFIC_ID_METHODS = (METHOD_HIFO, METHOD_LIFO)

TERM_SHORT = "SHORT_TERM"
TERM_LONG = "LONG_TERM"

# Wallet/account scope used when the caller does not name one. Basis must be
# tracked per wallet or account, so a single default bucket is only correct for
# a single-wallet taxpayer.
DEFAULT_WALLET_ID = "UNSPECIFIED"

# Quantity tolerance for float dust when depleting lots. Well below the smallest
# economically meaningful unit of any major asset.
_QUANTITY_EPSILON = 1e-9

_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
)


def parse_timestamp(value: str, field_name: str = "timestamp") -> datetime:
    """Parse an accepted timestamp string into a naive ``datetime``.

    Accepts ``YYYY-MM-DD HH:MM:SS``, ``YYYY-MM-DDTHH:MM:SS`` (optionally with a
    trailing ``Z``) and ``YYYY-MM-DD``. Timestamps are parsed rather than compared
    as strings: lexicographic ordering silently mis-sorts a mix of the space- and
    ``T``-separated forms, which would reorder FIFO/LIFO matching and change the
    tax result.

    Raises:
        ValueError: if the value is not a string in one of the accepted formats.
    """
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string, got {type(value).__name__}")
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1]
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue
    raise ValueError(
        f"{field_name} {value!r} is not an accepted format "
        f"(expected one of {', '.join(_TIMESTAMP_FORMATS)}, optional trailing 'Z')"
    )


def one_year_anniversary(acquisition: date) -> date:
    """Return the date one year after ``acquisition``.

    February 29 has no anniversary in a common year. This module resolves that to
    March 1, which is the conservative choice: it makes long-term treatment
    require one more day rather than one fewer. The IRS guidance consulted for
    this module does not settle the February 29 case, so a taxpayer acquiring on
    a leap day and disposing on February 28/March 1 of the following year should
    confirm the boundary with a tax adviser.
    """
    try:
        return acquisition.replace(year=acquisition.year + 1)
    except ValueError:
        return date(acquisition.year + 1, 3, 1)


def is_long_term(acquisition: datetime, disposal: datetime) -> bool:
    """True if the holding period is **more than one year**.

    The holding period begins on the day after acquisition and includes the day
    of disposal, so a disposal on the one-year anniversary is exactly one year —
    short-term — and long-term treatment starts the following day.

    This is deliberately calendar arithmetic and not a ``days_held > 365``
    comparison: across a leap year, 366 elapsed days can still be exactly one
    year, which a day count misclassifies as long-term.
    """
    return disposal.date() > one_year_anniversary(acquisition.date())


def holding_period_days(acquisition: datetime, disposal: datetime) -> int:
    """Holding period in days, counting from the day after acquisition through disposal.

    Reported for audit trails only — term classification uses :func:`is_long_term`.
    """
    return (disposal.date() - acquisition.date()).days


@dataclass
class CryptoTaxLot:
    """One acquired lot of a digital asset, held in one wallet or account.

    ``unit_cost_basis_usd`` is the per-unit basis and should already include the
    acquisition-side transaction costs allocable to this lot.

    Note: there is deliberately no ``days_held`` field. A holding period is a
    function of the acquisition date *and the disposal date*, so a number stored
    on the lot is stale for every disposal after the one it was computed for.
    """

    lot_id: str
    asset: str                          # e.g. 'ETH', 'BTC', 'SOL'
    acquisition_timestamp: str          # 'YYYY-MM-DD[ HH:MM:SS]' or ISO-8601
    quantity: float
    unit_cost_basis_usd: float
    wallet_id: str = DEFAULT_WALLET_ID  # wallet / account this lot sits in


@dataclass
class CryptoLotMatch:
    """One matched lot within a disposal — one Form 8949 row.

    Each row carries its own acquisition date, disposal date, proceeds, basis and
    term, because a single disposal can straddle Part I (short-term) and Part II
    (long-term) of Form 8949.
    """

    lot_id: str
    wallet_id: str
    quantity: float
    acquisition_timestamp: str
    disposal_timestamp: str
    holding_period_days: int
    cost_basis_usd: float
    proceeds_usd: float
    gain_loss_usd: float
    term: str                           # TERM_SHORT or TERM_LONG


@dataclass
class CryptoDispositionResult:
    """Outcome of one disposal, aggregated plus the per-lot Form 8949 breakdown."""

    disposal_id: str
    asset_sold: str
    asset_received: str
    quantity_disposed: float
    gross_proceeds_usd: float
    gas_fee_usd: float
    net_proceeds_usd: float
    total_cost_basis_usd: float
    realized_gain_loss_usd: float
    is_short_term: bool                 # True only if EVERY matched lot is short-term
    matching_method_used: str
    wallet_id: str = DEFAULT_WALLET_ID
    identification_reference: Optional[str] = None
    lot_matches: List[CryptoLotMatch] = field(default_factory=list)
    short_term_gain_loss_usd: float = 0.0
    long_term_gain_loss_usd: float = 0.0

    @property
    def is_mixed_term(self) -> bool:
        """True if this disposal spans both short-term and long-term lots.

        Such a disposal cannot be reported as a single Form 8949 row — it splits
        across Part I and Part II. Use ``lot_matches``, not the aggregates.
        """
        terms = {m.term for m in self.lot_matches}
        return len(terms) > 1


class CryptoTaxLotTrackerEngine:
    """Tracks crypto tax lots per wallet and matches disposals against them.

    Matching is FIFO by default. HIFO and LIFO are elections of specific
    identification and require an ``identification_reference`` recording the
    contemporaneous identification; the engine refuses them otherwise rather than
    producing a basis figure the taxpayer cannot support.

    Lot inventory is scoped per (wallet, asset). A disposal only consumes lots in
    the wallet it names.
    """

    def __init__(self, default_matching_method: str = METHOD_FIFO) -> None:
        """
        Args:
            default_matching_method: Method used when a disposal does not name one.
                Defaults to FIFO, the treatment that applies absent an adequate
                identification.

        Raises:
            ValueError: if the method is not one of ``SUPPORTED_METHODS``.
        """
        self.default_matching_method = _validate_method(default_matching_method)
        # {wallet_id: {asset: [lots]}}
        self.tax_lots: Dict[str, Dict[str, List[CryptoTaxLot]]] = {}

    def register_acquisition(self, lot: CryptoTaxLot) -> None:
        """Register an acquired lot (buy, swap-in, mining or staking reward).

        Raises:
            ValueError: if the lot's quantity is not positive and finite, its unit
                basis is negative or non-finite, or its timestamp is unparseable.
        """
        _validate_positive(lot.quantity, "lot.quantity")
        _validate_non_negative(lot.unit_cost_basis_usd, "lot.unit_cost_basis_usd")
        parse_timestamp(lot.acquisition_timestamp, "lot.acquisition_timestamp")

        asset = lot.asset.upper()
        wallet = lot.wallet_id or DEFAULT_WALLET_ID
        lot.asset = asset
        lot.wallet_id = wallet
        self.tax_lots.setdefault(wallet, {}).setdefault(asset, []).append(lot)

    def get_open_quantity(self, asset: str, wallet_id: str = DEFAULT_WALLET_ID) -> float:
        """Total open (undepleted) quantity of ``asset`` in ``wallet_id``."""
        lots = self.tax_lots.get(wallet_id, {}).get(asset.upper(), [])
        return sum(lot.quantity for lot in lots if lot.quantity > 0)

    def process_crypto_disposition(
        self,
        disposal_id: str,
        asset_sold: str,
        quantity_sold: float,
        asset_received: str,
        gross_proceeds_usd: float,
        disposal_timestamp: str,
        gas_fee_usd: float = 0.0,
        matching_method: Optional[str] = None,
        wallet_id: str = DEFAULT_WALLET_ID,
        identification_reference: Optional[str] = None,
    ) -> CryptoDispositionResult:
        """Process a disposal and return its Form 8949 breakdown.

        Net proceeds are gross proceeds less the transaction cost allocable to
        this disposal, and are allocated across matched lots pro rata by quantity.
        A transaction cost paid to effect a swap belongs to the asset given up,
        not to the asset received — do not also add it to the received lot's basis.

        The disposal is applied atomically: lot quantities are only decremented
        after the full match plan is known to be satisfiable, so a disposal that
        exceeds inventory raises without leaving lots partially consumed.

        Args:
            disposal_timestamp: When the disposal occurred. Required — the holding
                period, and therefore short-term vs long-term treatment, cannot be
                determined without it.
            matching_method: FIFO (default), HIFO or LIFO.
            wallet_id: Wallet/account whose lots may be matched. Basis is tracked
                per wallet, so a disposal never reaches into another wallet.
            identification_reference: Record of the contemporaneous identification
                supporting a HIFO/LIFO election (e.g. a books-and-records entry or
                standing-order id). Required for those methods.

        Raises:
            ValueError: on an unsupported method, a HIFO/LIFO election with no
                identification reference, invalid quantities or amounts, an
                unparseable timestamp, or insufficient inventory in the wallet.
        """
        method = _validate_method(matching_method or self.default_matching_method)
        if method in SPECIFIC_ID_METHODS and not identification_reference:
            raise ValueError(
                f"{method} is an election of specific identification and requires "
                "identification_reference — a record of the identification made no later "
                "than the date and time of this disposal. Absent an adequate "
                "identification the units are treated as disposed of FIFO; pass "
                f"matching_method='{METHOD_FIFO}' if that is what actually happened."
            )

        _validate_positive(quantity_sold, "quantity_sold")
        _validate_finite(gross_proceeds_usd, "gross_proceeds_usd")
        _validate_non_negative(gas_fee_usd, "gas_fee_usd")

        asset = asset_sold.upper()
        wallet = wallet_id or DEFAULT_WALLET_ID
        disposal_dt = parse_timestamp(disposal_timestamp, "disposal_timestamp")

        candidates = [
            lot for lot in self.tax_lots.get(wallet, {}).get(asset, [])
            if lot.quantity > _QUANTITY_EPSILON
            and parse_timestamp(lot.acquisition_timestamp) <= disposal_dt
        ]
        if not candidates:
            raise ValueError(
                f"No open tax lots for {asset} in wallet {wallet!r} acquired on or before "
                f"{disposal_timestamp}. Lots acquired after a disposal cannot be matched "
                "against it."
            )

        available = sum(lot.quantity for lot in candidates)
        if available + _QUANTITY_EPSILON < quantity_sold:
            raise ValueError(
                f"Insufficient tax lot inventory for {asset} in wallet {wallet!r}: "
                f"disposing {quantity_sold} but only {available} open. No lots were "
                "consumed. A shortfall usually means a missing acquisition, a transfer "
                "recorded in the wrong wallet, or a double-counted disposal."
            )

        ordered = _order_lots(candidates, method)

        # Phase 1: plan without mutating, so a failure leaves the ledger intact.
        plan: List[Tuple[CryptoTaxLot, float]] = []
        remaining = quantity_sold
        for lot in ordered:
            if remaining <= _QUANTITY_EPSILON:
                break
            take = min(remaining, lot.quantity)
            plan.append((lot, take))
            remaining -= take

        net_proceeds = gross_proceeds_usd - gas_fee_usd
        matched_quantity = sum(take for _, take in plan)

        # Phase 2: commit.
        matches: List[CryptoLotMatch] = []
        for lot, take in plan:
            lot.quantity -= take
            acquisition_dt = parse_timestamp(lot.acquisition_timestamp)
            long_term = is_long_term(acquisition_dt, disposal_dt)
            # Pro-rata allocation of net proceeds keeps the transaction cost split
            # across lots in the same proportion as the quantity disposed.
            lot_proceeds = net_proceeds * (take / matched_quantity)
            lot_basis = take * lot.unit_cost_basis_usd
            matches.append(CryptoLotMatch(
                lot_id=lot.lot_id,
                wallet_id=lot.wallet_id,
                quantity=take,
                acquisition_timestamp=lot.acquisition_timestamp,
                disposal_timestamp=disposal_timestamp,
                holding_period_days=holding_period_days(acquisition_dt, disposal_dt),
                cost_basis_usd=round(lot_basis, 2),
                proceeds_usd=round(lot_proceeds, 2),
                gain_loss_usd=round(lot_proceeds - lot_basis, 2),
                term=TERM_LONG if long_term else TERM_SHORT,
            ))

        total_cost_basis = sum(take * lot.unit_cost_basis_usd for lot, take in plan)
        realized_pnl = net_proceeds - total_cost_basis
        short_term_pnl = sum(m.gain_loss_usd for m in matches if m.term == TERM_SHORT)
        long_term_pnl = sum(m.gain_loss_usd for m in matches if m.term == TERM_LONG)
        all_short_term = all(m.term == TERM_SHORT for m in matches)

        result = CryptoDispositionResult(
            disposal_id=disposal_id,
            asset_sold=asset,
            asset_received=asset_received,
            quantity_disposed=quantity_sold,
            gross_proceeds_usd=round(gross_proceeds_usd, 2),
            gas_fee_usd=round(gas_fee_usd, 2),
            net_proceeds_usd=round(net_proceeds, 2),
            total_cost_basis_usd=round(total_cost_basis, 2),
            realized_gain_loss_usd=round(realized_pnl, 2),
            is_short_term=all_short_term,
            matching_method_used=method,
            wallet_id=wallet,
            identification_reference=identification_reference,
            lot_matches=matches,
            short_term_gain_loss_usd=round(short_term_pnl, 2),
            long_term_gain_loss_usd=round(long_term_pnl, 2),
        )

        logger.info(
            "CRYPTO DISPOSAL [%s] wallet=%s: %s %s -> %s across %d lot(s). "
            "Net proceeds=$%s, basis=$%s, PnL=$%s (%s; ST=$%s, LT=$%s)",
            disposal_id, wallet, quantity_sold, asset, asset_received, len(matches),
            f"{net_proceeds:,.2f}", f"{total_cost_basis:,.2f}", f"{realized_pnl:,.2f}",
            method, f"{short_term_pnl:,.2f}", f"{long_term_pnl:,.2f}",
        )
        if result.is_mixed_term:
            logger.warning(
                "DISPOSAL [%s] spans short-term and long-term lots; it reports as "
                "multiple Form 8949 rows across Part I and Part II. Use lot_matches, "
                "not the aggregate figures.", disposal_id,
            )
        return result


def _order_lots(lots: Sequence[CryptoTaxLot], method: str) -> List[CryptoTaxLot]:
    """Order candidate lots for the given matching method.

    Ties break on insertion order (Python's sort is stable), so the same inputs
    always produce the same match plan.
    """
    if method == METHOD_HIFO:
        return sorted(lots, key=lambda lot: lot.unit_cost_basis_usd, reverse=True)
    if method == METHOD_LIFO:
        return sorted(
            lots, key=lambda lot: parse_timestamp(lot.acquisition_timestamp), reverse=True
        )
    return sorted(lots, key=lambda lot: parse_timestamp(lot.acquisition_timestamp))


def _validate_method(method: str) -> str:
    """Validate a matching method, rejecting anything unrecognised.

    An unrecognised method must never fall through to a default: a typo silently
    treated as FIFO changes which lots are consumed and therefore the tax owed.
    """
    if not isinstance(method, str) or method.upper() not in SUPPORTED_METHODS:
        raise ValueError(
            f"Unsupported matching_method {method!r}; expected one of "
            f"{', '.join(SUPPORTED_METHODS)}"
        )
    return method.upper()


def _validate_finite(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return float(value)


def _validate_positive(value: float, name: str) -> float:
    _validate_finite(value, name)
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value!r}")
    return float(value)


def _validate_non_negative(value: float, name: str) -> float:
    _validate_finite(value, name)
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {value!r}")
    return float(value)
