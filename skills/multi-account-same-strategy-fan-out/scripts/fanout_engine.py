"""
multi-account-same-strategy-fan-out: Pro-rata signal fan-out engine for multi-account
fund management with collision-free client order ID generation.
"""
from dataclasses import dataclass, field
import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ClientAccount:
    account_id: str
    nav_usd: float
    is_active: bool = True


@dataclass
class AccountOrder:
    client_order_id: str
    account_id: str
    symbol: str
    action: str  # "BUY" or "SELL"
    allocated_quantity: int
    order_type: str = "MARKET"
    limit_price: Optional[float] = None


@dataclass
class FanOutReport:
    master_symbol: str
    master_action: str
    master_target_qty: int
    total_allocated_qty: int
    account_orders: List[AccountOrder]
    failed_accounts: List[str] = field(default_factory=list)


class MultiAccountStrategyFanOut:
    """
    Broadcasts strategy signals across multiple client accounts using pro-rata
    NAV sizing and collision-free client order ID assignment.
    """

    def __init__(self, min_order_qty: int = 1):
        self.accounts: Dict[str, ClientAccount] = {}
        self.min_order_qty = min_order_qty
        self._seq_counter = 0

    def register_account(self, account_id: str, nav_usd: float) -> None:
        """Registers a client account with its Net Asset Value (NAV)."""
        if nav_usd <= 0:
            raise ValueError(f"Invalid NAV ${nav_usd} for account {account_id}")
        self.accounts[account_id] = ClientAccount(account_id=account_id, nav_usd=nav_usd)

    def _generate_client_order_id(self, account_id: str) -> str:
        self._seq_counter += 1
        ts = int(time.time() * 1000)
        return f"CLORD_{account_id}_{ts}_{self._seq_counter}"

    def calculate_fanout_orders(
        self,
        symbol: str,
        action: str,
        total_target_quantity: int,
        order_type: str = "MARKET",
        limit_price: Optional[float] = None,
    ) -> FanOutReport:
        """
        Calculates pro-rata quantities for each active sub-account and assigns
        collision-free client order IDs.
        """
        active_accs = [acc for acc in self.accounts.values() if acc.is_active]
        if not active_accs:
            return FanOutReport(
                master_symbol=symbol,
                master_action=action,
                master_target_qty=total_target_quantity,
                total_allocated_qty=0,
                account_orders=[],
            )

        total_nav = sum(acc.nav_usd for acc in active_accs)
        account_orders: List[AccountOrder] = []
        allocated_sum = 0

        for acc in active_accs:
            weight = acc.nav_usd / total_nav
            raw_qty = total_target_quantity * weight
            allocated_qty = max(self.min_order_qty, int(round(raw_qty)))

            client_ord_id = self._generate_client_order_id(acc.account_id)
            account_orders.append(AccountOrder(
                client_order_id=client_ord_id,
                account_id=acc.account_id,
                symbol=symbol,
                action=action,
                allocated_quantity=allocated_qty,
                order_type=order_type,
                limit_price=limit_price,
            ))
            allocated_sum += allocated_qty

        logger.info(
            f"Fan-Out Master Signal: {action} {total_target_quantity} {symbol} -> "
            f"Fanned out to {len(account_orders)} accounts (Total Alloc: {allocated_sum})"
        )

        return FanOutReport(
            master_symbol=symbol,
            master_action=action,
            master_target_qty=total_target_quantity,
            total_allocated_qty=allocated_sum,
            account_orders=account_orders,
        )
