"""
survivorship-bias-free-universe-construction: Production-grade point-in-time universe engine,
delisted constituent tracker, terminal delisting settlement liquidator, and bias auditor.
"""
from dataclasses import dataclass
import datetime
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class UniverseError(ValueError):
    """Raised when instrument metadata is missing or invalid."""
    pass


class DelistingReason(str, Enum):
    ACTIVE = "ACTIVE"
    BANKRUPTCY = "BANKRUPTCY"
    MERGER_ACQUISITION = "MERGER_ACQUISITION"
    VOLUNTARY = "VOLUNTARY"


@dataclass
class InstrumentMetadata:
    symbol: str
    name: str
    listing_date: datetime.date
    delisting_date: Optional[datetime.date] = None
    delisting_reason: DelistingReason = DelistingReason.ACTIVE
    delisting_settlement_price: float = 0.0


class SurvivorshipFreeUniverseEngine:
    """
    Constructs point-in-time survivorship-bias-free asset universes,
    handles terminal delisting settlements, and audits delisted coverage.
    """

    def __init__(self):
        self.instruments: Dict[str, InstrumentMetadata] = {}

    def add_instrument(self, meta: InstrumentMetadata):
        """Registers instrument metadata with listing and delisting dates."""
        self.instruments[meta.symbol.upper()] = meta

    def get_active_universe(self, as_of_date: datetime.date) -> List[str]:
        """
        Returns list of symbols active on as_of_date (listing_date <= as_of_date <= delisting_date).
        """
        active_symbols: List[str] = []
        for symbol, meta in self.instruments.items():
            if meta.listing_date <= as_of_date:
                if meta.delisting_date is None or as_of_date <= meta.delisting_date:
                    active_symbols.append(symbol)

        logger.debug(f"Point-in-time universe on {as_of_date}: {len(active_symbols)} active instruments.")
        return sorted(active_symbols)

    def process_delisting_settlement(self, symbol: str, position_qty: float) -> Tuple[float, str]:
        """
        Calculates terminal P&L settlement cash value when position hits delisting date.
        Returns (settlement_cash_value, message).
        """
        meta = self.instruments.get(symbol.upper())
        if not meta:
            raise UniverseError(f"Instrument '{symbol}' not found in universe registry.")

        if meta.delisting_reason == DelistingReason.ACTIVE:
            return position_qty * meta.delisting_settlement_price, "Active instrument."

        if meta.delisting_reason == DelistingReason.BANKRUPTCY:
            cash = position_qty * meta.delisting_settlement_price  # Usually 0.0
            msg = f"DELISTING SETTLEMENT [BANKRUPTCY]: Symbol '{symbol}' liquidated at ${meta.delisting_settlement_price:.2f}/share. Cash realized: ${cash:,.2f}"
            logger.critical(msg)
            return cash, msg

        elif meta.delisting_reason == DelistingReason.MERGER_ACQUISITION:
            cash = position_qty * meta.delisting_settlement_price
            msg = f"DELISTING SETTLEMENT [MERGER]: Symbol '{symbol}' acquired at ${meta.delisting_settlement_price:.2f}/share. Cash realized: ${cash:,.2f}"
            logger.info(msg)
            return cash, msg

        else:  # VOLUNTARY
            cash = position_qty * meta.delisting_settlement_price
            msg = f"DELISTING SETTLEMENT [VOLUNTARY]: Symbol '{symbol}' settled at ${meta.delisting_settlement_price:.2f}/share."
            logger.info(msg)
            return cash, msg

    def audit_survivorship_bias(
        self, start_date: datetime.date, end_date: datetime.date
    ) -> Dict[str, Any]:
        """Audits total registered constituents and delisted ratio across historical window."""
        total_count = len(self.instruments)
        delisted_count = 0
        active_at_end_count = 0

        for meta in self.instruments.values():
            if meta.delisting_date and start_date <= meta.delisting_date <= end_date:
                delisted_count += 1
            if meta.delisting_date is None or meta.delisting_date > end_date:
                active_at_end_count += 1

        delisting_ratio = delisted_count / max(total_count, 1)
        has_bias_protection = delisted_count > 0

        logger.info(
            f"Survivorship Audit: Total {total_count}, Active at end {active_at_end_count}, Delisted {delisted_count} ({delisting_ratio:.1%})"
        )
        return {
            "total_instruments": total_count,
            "active_at_end": active_at_end_count,
            "delisted_in_period": delisted_count,
            "delisting_ratio": delisting_ratio,
            "has_bias_protection": has_bias_protection,
        }
