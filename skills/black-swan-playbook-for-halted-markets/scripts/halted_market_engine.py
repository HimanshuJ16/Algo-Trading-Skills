"""
black-swan-playbook-for-halted-markets: Exchange halt detector, working order pause/cancel engine,
correlated proxy hedge deployer, and auction resume manager.
"""
from dataclasses import dataclass
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class MarketStatus(Enum):
    NORMAL = "NORMAL"
    HALTED_LULD = "HALTED_LULD"
    HALTED_CIRCUIT_BREAKER = "HALTED_CIRCUIT_BREAKER"
    RESUME_AUCTION = "RESUME_AUCTION"


@dataclass
class HaltPlaybookAction:
    action_type: str        # CANCEL_ORDERS, PROXY_HEDGE, AUCTION_RESUME_ORDER
    target_symbol: str
    quantity: float
    side: str
    rationale: str


@dataclass
class HaltedMarketReport:
    symbol: str
    status: MarketStatus
    halt_reason: str
    open_position_shares: float
    actions_executed: List[HaltPlaybookAction]
    is_proxy_hedged: bool
    status_message: str


class BlackSwanHaltedMarketEngine:
    """
    Executes automated fallback playbooks during exchange trading halts,
    canceling working orders and deploying correlated proxy hedges.
    """

    def __init__(self, proxy_hedge_map: Optional[Dict[str, str]] = None):
        # Default proxy map e.g. NVDA -> QQQ, AAPL -> SPY
        self.proxy_map = proxy_hedge_map or {"NVDA": "QQQ", "AAPL": "SPY", "TSLA": "QQQ"}

    def handle_halt_event(
        self,
        symbol: str,
        status: MarketStatus,
        halt_reason: str,
        open_position_shares: float,
        open_orders: List[Dict[str, Any]],
        beta_to_proxy: float = 1.0,
        proxy_price: float = 450.0,
    ) -> HaltedMarketReport:
        actions: List[HaltPlaybookAction] = []
        is_hedged = False

        if status in (MarketStatus.HALTED_LULD, MarketStatus.HALTED_CIRCUIT_BREAKER):
            # Step 1: Cancel all pending working orders for halted symbol
            if open_orders:
                actions.append(HaltPlaybookAction(
                    action_type="CANCEL_ORDERS",
                    target_symbol=symbol,
                    quantity=sum(o.get("quantity", 0) for o in open_orders),
                    side="N/A",
                    rationale=f"Canceled {len(open_orders)} open orders due to exchange halt ({halt_reason}).",
                ))

            # Step 2: Deploy correlated proxy hedge if position is held
            if open_position_shares != 0:
                proxy_symbol = self.proxy_map.get(symbol, "SPY")
                hedge_side = "SELL" if open_position_shares > 0 else "BUY"
                hedge_qty = abs(round(open_position_shares * beta_to_proxy, 0))

                if hedge_qty > 0:
                    actions.append(HaltPlaybookAction(
                        action_type="PROXY_HEDGE",
                        target_symbol=proxy_symbol,
                        quantity=hedge_qty,
                        side=hedge_side,
                        rationale=f"Deployed {hedge_side} {hedge_qty} {proxy_symbol} proxy hedge for halted {symbol} position.",
                    ))
                    is_hedged = True

            msg = f"BLACK SWAN HALT EXECUTED for '{symbol}': Status={status.value}, Reason={halt_reason}. Actions={len(actions)}."
            logger.warning(msg)

        elif status == MarketStatus.RESUME_AUCTION:
            msg = f"AUCTION RESUME DETECTED for '{symbol}': Preparing post-halt re-entry."
            logger.info(msg)

        else:
            msg = f"Market Normal for '{symbol}'."

        return HaltedMarketReport(
            symbol=symbol,
            status=status,
            halt_reason=halt_reason,
            open_position_shares=open_position_shares,
            actions_executed=actions,
            is_proxy_hedged=is_hedged,
            status_message=msg,
        )
