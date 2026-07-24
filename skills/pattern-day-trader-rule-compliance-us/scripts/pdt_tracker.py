"""
pattern-day-trader-rule-compliance-us: Production-grade FINRA Rule 4210 PDT compliance manager,
rolling 5-business-day calendar window tracking, same-day trade classifier,
broker reconciliation, and 4th day-trade veto engine for sub-$25,000 equity accounts.
"""
from collections import deque
from dataclasses import dataclass
import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PDT_EQUITY_THRESHOLD = 25_000.0
PDT_TRADE_LIMIT = 4  # 4th day trade in 5 business days triggers PDT flag
PDT_WINDOW_BUSINESS_DAYS = 5


@dataclass
class TradeExecution:
    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: float
    timestamp: datetime.datetime


@dataclass
class DayTradeRecord:
    symbol: str
    open_timestamp: datetime.datetime
    close_timestamp: datetime.datetime
    trade_date: datetime.date


class PDTComplianceEngine:
    """
    FINRA Rule 4210 compliant PDT tracker maintaining rolling 5-business-day day-trade records
    and vetoing 4th day trades for accounts with equity < $25,000.
    """

    def __init__(
        self,
        equity_threshold: float = PDT_EQUITY_THRESHOLD,
        window_business_days: int = PDT_WINDOW_BUSINESS_DAYS,
    ):
        self.equity_threshold = equity_threshold
        self.window_business_days = window_business_days

        self.open_positions: Dict[str, List[TradeExecution]] = {}
        self.day_trade_history: List[DayTradeRecord] = []

    @staticmethod
    def _is_business_day(d: datetime.date) -> bool:
        return d.weekday() < 5  # Monday-Friday

    def _get_business_days_diff(self, start_date: datetime.date, end_date: datetime.date) -> int:
        """Calculates business day difference between two dates."""
        cur = start_date
        b_days = 0
        while cur < end_date:
            cur += datetime.timedelta(days=1)
            if self._is_business_day(cur):
                b_days += 1
        return b_days

    def record_execution(
        self, symbol: str, side: str, quantity: float, timestamp: datetime.datetime
    ) -> Optional[DayTradeRecord]:
        """
        Records trade execution and checks if it closes an intraday position (Day Trade).
        """
        sym = symbol.upper()
        s = side.upper()
        trade = TradeExecution(symbol=sym, side=s, quantity=quantity, timestamp=timestamp)

        if sym not in self.open_positions or not self.open_positions[sym]:
            self.open_positions[sym] = [trade]
            return None

        # Check existing open position
        existing = self.open_positions[sym][0]
        if existing.side != s:
            # Opposite side execution -> Position closed
            is_same_day = existing.timestamp.date() == timestamp.date()
            if is_same_day:
                dt_record = DayTradeRecord(
                    symbol=sym,
                    open_timestamp=existing.timestamp,
                    close_timestamp=timestamp,
                    trade_date=timestamp.date(),
                )
                self.day_trade_history.append(dt_record)
                self.open_positions[sym].pop(0)
                logger.info(f"DAY TRADE DETECTED on '{sym}' on {timestamp.date()}")
                return dt_record
            else:
                # Overnight position closed
                self.open_positions[sym].pop(0)

        return None

    def get_rolling_day_trade_count(self, as_of_date: Optional[datetime.date] = None) -> int:
        if as_of_date is None:
            if self.day_trade_history:
                target_date = self.day_trade_history[-1].trade_date
            else:
                target_date = datetime.date.today()
        else:
            target_date = as_of_date

        count = 0
        for record in self.day_trade_history:
            b_diff = self._get_business_days_diff(record.trade_date, target_date)
            if b_diff < self.window_business_days:
                count += 1

        return count

    def would_breach_pdt(
        self, current_equity: float, as_of_date: Optional[datetime.date] = None
    ) -> Tuple[bool, str]:
        """
        Vetoes proposed day trade if account equity < $25,000 and rolling day-trade count >= 3.
        """
        if current_equity >= self.equity_threshold:
            return False, f"Equity ${current_equity:,.2f} >= ${self.equity_threshold:,.2f} threshold. Day trade allowed."

        count = self.get_rolling_day_trade_count(as_of_date)
        if count >= (PDT_TRADE_LIMIT - 1):
            msg = (
                f"PDT VETO: Account equity ${current_equity:,.2f} < ${self.equity_threshold:,.2f} "
                f"and rolling day trades = {count} (4th trade would trigger 90-day PDT restriction)."
            )
            logger.warning(msg)
            return True, msg

        return False, f"Day trade allowed (Current rolling count = {count}/3)."

    def reconcile_broker_count(self, broker_count: int, as_of_date: Optional[datetime.date] = None) -> bool:
        local_count = self.get_rolling_day_trade_count(as_of_date)
        if local_count != broker_count:
            logger.warning(f"PDT COUNT DESYNC: Local count={local_count}, Broker count={broker_count}")
            return False
        return True


# Backward compatibility class
class DayTradeTracker:

    def __init__(self):
        self.engine = PDTComplianceEngine()

    def record_day_trade(self, trade_date: datetime.date):
        dt_record = DayTradeRecord(
            symbol="DUMMY",
            open_timestamp=datetime.datetime.combine(trade_date, datetime.time(9, 30)),
            close_timestamp=datetime.datetime.combine(trade_date, datetime.time(15, 0)),
            trade_date=trade_date,
        )
        self.engine.day_trade_history.append(dt_record)

    def would_breach(self, account_equity: float) -> bool:
        breached, _ = self.engine.would_breach_pdt(account_equity)
        return breached
