"""
multi-asset-backtest-currency-normalization: Production-grade multi-currency portfolio accounting engine,
point-in-time FX rate converter, per-currency cash ledger isolator, and unified NAV calculator.
"""
from dataclasses import dataclass, field
import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CurrencyMismatchError(ValueError):
    """Raised when un-converted currency aggregation or missing FX rates occur."""
    pass


@dataclass
class PositionValuation:
    symbol: str
    local_currency: str
    quantity: float
    price_in_local: float

    @property
    def local_value(self) -> float:
        return self.quantity * self.price_in_local


@dataclass
class MultiCurrencyNAV:
    reporting_currency: str
    date: datetime.date
    total_cash_reporting: float
    total_positions_reporting: float
    total_nav_reporting: float
    cash_by_currency: Dict[str, float]
    positions_by_currency: Dict[str, float]


class MultiCurrencyPortfolioNormalizer:
    """
    Manages multi-currency cash balances and position valuations, applies point-in-time
    FX rate conversion, and computes total Net Asset Value (NAV) in base reporting currency.
    """

    def __init__(self, reporting_currency: str = "USD"):
        self.reporting_currency = reporting_currency.upper()
        self.cash_balances: Dict[str, float] = {}  # currency -> cash amount
        self.positions: List[PositionValuation] = []
        # FX rates: (from_currency, to_currency, date) -> rate
        self.fx_rate_table: Dict[Tuple[str, str, datetime.date], float] = {}

    def set_cash_balance(self, currency: str, amount: float):
        """Sets cash balance for a specific currency."""
        self.cash_balances[currency.upper()] = amount

    def add_position(self, pos: PositionValuation):
        """Adds open position valuation."""
        self.positions.append(pos)

    def register_fx_rate(self, from_currency: str, to_currency: str, date: datetime.date, rate: float):
        """Registers point-in-time FX conversion rate."""
        c1 = from_currency.upper()
        c2 = to_currency.upper()
        if rate <= 0:
            raise CurrencyMismatchError(f"FX rate must be positive: got {rate}")

        self.fx_rate_table[(c1, c2, date)] = rate
        self.fx_rate_table[(c2, c1, date)] = 1.0 / rate

    def get_fx_rate(self, from_currency: str, to_currency: str, date: datetime.date) -> float:
        """Retrieves FX conversion rate for given date."""
        c1 = from_currency.upper()
        c2 = to_currency.upper()

        if c1 == c2:
            return 1.0

        key = (c1, c2, date)
        if key in self.fx_rate_table:
            return self.fx_rate_table[key]

        raise CurrencyMismatchError(
            f"Missing FX rate for conversion '{c1}' to '{c2}' on {date}."
        )

    def convert_amount(self, amount: float, from_currency: str, to_currency: str, date: datetime.date) -> float:
        """Converts cash or P&L amount to target currency on date."""
        rate = self.get_fx_rate(from_currency, to_currency, date)
        return amount * rate

    def compute_total_nav(self, date: datetime.date) -> MultiCurrencyNAV:
        """
        Computes total Net Asset Value (NAV) in reporting currency by converting all
        cash balances and position valuations on date.
        """
        total_cash_rep = 0.0
        total_pos_rep = 0.0

        cash_by_curr: Dict[str, float] = {}
        pos_by_curr: Dict[str, float] = {}

        # 1. Convert Cash Balances
        for curr, amount in self.cash_balances.items():
            converted_cash = self.convert_amount(amount, curr, self.reporting_currency, date)
            total_cash_rep += converted_cash
            cash_by_curr[curr] = amount

        # 2. Convert Position Valuations
        for pos in self.positions:
            local_val = pos.local_value
            converted_pos = self.convert_amount(local_val, pos.local_currency, self.reporting_currency, date)
            total_pos_rep += converted_pos
            pos_by_curr[pos.symbol] = converted_pos

        total_nav = total_cash_rep + total_pos_rep

        logger.info(
            f"Multi-Currency NAV ({date}): Cash: ${total_cash_rep:,.2f}, Positions: ${total_pos_rep:,.2f}, Total NAV: ${total_nav:,.2f} {self.reporting_currency}"
        )

        return MultiCurrencyNAV(
            reporting_currency=self.reporting_currency,
            date=date,
            total_cash_reporting=total_cash_rep,
            total_positions_reporting=total_pos_rep,
            total_nav_reporting=total_nav,
            cash_by_currency=cash_by_curr,
            positions_by_currency=pos_by_curr,
        )
