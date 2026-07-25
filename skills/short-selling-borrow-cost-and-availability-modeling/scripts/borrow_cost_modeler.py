from dataclasses import dataclass, field
from typing import Dict, List, Optional

@dataclass
class BorrowStatus:
    ticker: str
    utilization_rate: float  # 0.0 to 1.0
    is_hard_to_borrow: bool = field(init=False)
    available_shares: int = 1000000 # default large amount
    
    def __post_init__(self):
        self.is_hard_to_borrow = self.utilization_rate > 0.8

@dataclass
class ShortTrade:
    ticker: str
    shares: int
    entry_price: float
    days_held: int

class BorrowCostModeler:
    def __init__(self, gc_rate: float = 0.003, htb_base_rate: float = 0.05, max_htb_rate: float = 0.50):
        """
        gc_rate: General Collateral rate (e.g. 0.3% annualized)
        htb_base_rate: Base rate for HTB
        max_htb_rate: Max rate for HTB
        """
        self.gc_rate = gc_rate
        self.htb_base_rate = htb_base_rate
        self.max_htb_rate = max_htb_rate
        self.borrow_statuses: Dict[str, BorrowStatus] = {}

    def update_status(self, status: BorrowStatus):
        self.borrow_statuses[status.ticker] = status

    def can_short(self, ticker: str, requested_shares: int) -> bool:
        if ticker not in self.borrow_statuses:
            # Assume GC if no explicit status is given
            return True
        status = self.borrow_statuses[ticker]
        if status.utilization_rate >= 1.0:
            return False # No shares available
        if requested_shares > status.available_shares:
            return False
        return True

    def calculate_annualized_rate(self, ticker: str) -> float:
        if ticker not in self.borrow_statuses:
            return self.gc_rate
        
        status = self.borrow_statuses[ticker]
        if not status.is_hard_to_borrow:
            return self.gc_rate
        
        # Scale linearly from htb_base_rate at 80% utilization to max_htb_rate at 100% utilization
        util_diff = status.utilization_rate - 0.8
        scale = util_diff / 0.2  # 0 to 1
        rate = self.htb_base_rate + (scale * (self.max_htb_rate - self.htb_base_rate))
        return min(rate, self.max_htb_rate)

    def calculate_borrow_cost(self, trade: ShortTrade) -> float:
        """
        Calculate the absolute dollar cost for a given short trade over the holding period.
        """
        rate = self.calculate_annualized_rate(trade.ticker)
        trade_value = trade.shares * trade.entry_price
        
        daily_rate = rate / 365.0
        cost = trade_value * daily_rate * trade.days_held
        return cost
