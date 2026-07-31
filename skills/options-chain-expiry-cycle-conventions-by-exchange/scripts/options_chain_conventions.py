import calendar
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class OptionExpiryQuery:
    exchange: str                       # 'CBOE', 'CME', 'EUREX', 'DERIBIT'
    underlying_symbol: str              # 'SPX', 'SPXW', 'AAPL', 'BTC'
    reference_date_iso: str             # 'YYYY-MM-DD'
    target_year: int
    target_month: int
    cycle_type: str = "MONTHLY"          # 'MONTHLY', 'WEEKLY', 'QUARTERLY', 'LEAPS'

@dataclass
class OptionsChainConventionReport:
    exchange: str
    underlying_symbol: str
    expiration_date_iso: str            # 'YYYY-MM-DD'
    dte_days: int                       # Days to Expiration
    settlement_type: str                # 'AM_SETTLED' (SOQ), 'PM_SETTLED' (Closing Price)
    exercise_style: str                 # 'EUROPEAN', 'AMERICAN'
    delivery_type: str                 # 'CASH', 'PHYSICAL'
    cycle_type: str
    audit_notes: str

class OptionsChainExpiryConventionsEngine:
    """
    Options chain expiry cycle conventions engine resolving 3rd Friday monthly dates,
    AM vs PM settlement rules (SOQ vs closing price), European vs American exercise styles, and Days to Expiration (DTE).
    """
    @staticmethod
    def get_third_friday(year: int, month: int) -> datetime:
        """
        Calculates the exact 3rd Friday date for a given year and month.
        """
        cal = calendar.monthcalendar(year, month)
        fridays = [week[calendar.FRIDAY] for week in cal if week[calendar.FRIDAY] != 0]
        third_friday_day = fridays[2]
        return datetime(year, month, third_friday_day)

    def resolve_conventions(
        self, query: OptionExpiryQuery
    ) -> OptionsChainConventionReport:
        """
        Resolves options expiry date, settlement type, exercise style, delivery type, and DTE.
        """
        ref_dt = datetime.strptime(query.reference_date_iso, "%Y-%m-%d")

        # 1. Compute Expiration Date (Default: 3rd Friday of target month)
        exp_dt = self.get_third_friday(query.target_year, query.target_month)
        exp_date_iso = exp_dt.strftime("%Y-%m-%d")

        dte_days = max(0, (exp_dt - ref_dt).days)
        symbol_upper = query.underlying_symbol.upper()
        exchange_upper = query.exchange.upper()

        # 2. Resolve Settlement Type (AM vs PM)
        # Standard monthly SPX options (CBOE) are AM_SETTLED (SOQ)
        # SPXW (Weeklys) & Equity options (AAPL) are PM_SETTLED
        if symbol_upper in ("SPX", "NDX", "RUT") and query.cycle_type.upper() == "MONTHLY":
            settlement_type = "AM_SETTLED"
        else:
            settlement_type = "PM_SETTLED"

        # 3. Resolve Exercise Style & Delivery Type
        # Index & Crypto options (SPX, NDX, BTC) -> EUROPEAN, CASH
        # Equity & ETF options (AAPL, SPY) -> AMERICAN, PHYSICAL
        if symbol_upper in ("SPX", "SPXW", "NDX", "RUT", "BTC", "ETH"):
            exercise_style = "EUROPEAN"
            delivery_type = "CASH"
        else:
            exercise_style = "AMERICAN"
            delivery_type = "PHYSICAL"

        notes = (
            f"OPTIONS CONVENTION [{query.exchange}:{symbol_upper}]: Expiry = {exp_date_iso} ({dte_days} DTE), "
            f"Settlement = {settlement_type}, Style = {exercise_style}, Delivery = {delivery_type}."
        )
        logger.info(notes)

        return OptionsChainConventionReport(
            exchange=query.exchange,
            underlying_symbol=symbol_upper,
            expiration_date_iso=exp_date_iso,
            dte_days=dte_days,
            settlement_type=settlement_type,
            exercise_style=exercise_style,
            delivery_type=delivery_type,
            cycle_type=query.cycle_type.upper(),
            audit_notes=notes
        )
