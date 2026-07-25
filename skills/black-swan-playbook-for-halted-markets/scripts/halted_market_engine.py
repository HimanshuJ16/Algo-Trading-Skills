"""
black-swan-playbook-for-halted-markets: Exchange halt detector, working order pause/cancel engine,
correlated proxy hedge deployer, and auction resume manager.
"""
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

class MarketStatus(Enum):
    NORMAL = "NORMAL"
    HALTED_LULD = "HALTED_LULD"
    HALTED_CIRCUIT_BREAKER = "HALTED_CIRCUIT_BREAKER"
    RESUME_AUCTION = "RESUME_AUCTION"
    PRE_OPEN = "PRE_OPEN"


@dataclass
class ProxyConfig:
    symbol: str
    beta: float
    liquidity_score: float  # 0 to 1, threshold for viability
    basis_risk_limit: float # max acceptable spread divergence


@dataclass
class HaltPlaybookAction:
    action_type: str        # CANCEL_ORDERS, PROXY_HEDGE, AUCTION_RESUME_ORDER, ADJUST_RISK_LIMITS
    target_symbol: str
    quantity: float
    side: str
    rationale: str
    price: Optional[float] = None
    order_type: str = "MARKET"


@dataclass
class HaltedMarketReport:
    symbol: str
    status: MarketStatus
    halt_reason: str
    open_position_shares: float
    actions_executed: List[HaltPlaybookAction] = field(default_factory=list)
    is_proxy_hedged: bool = False
    status_message: str = ""
    fair_value_estimate: Optional[float] = None


class BlackSwanHaltedMarketEngine:
    """
    Executes automated fallback playbooks during exchange trading halts.
    Incorporates institutional quant standards: LULD microstructure awareness,
    dynamic proxy hedging, fat-tail risk limits, and auction participation.
    """

    def __init__(self, proxy_config_map: Optional[Dict[str, ProxyConfig]] = None):
        # Default proxy map e.g. NVDA -> QQQ, AAPL -> SPY
        self.proxy_map = proxy_config_map or {
            "NVDA": ProxyConfig(symbol="QQQ", beta=1.5, liquidity_score=0.99, basis_risk_limit=0.05),
            "AAPL": ProxyConfig(symbol="SPY", beta=1.2, liquidity_score=1.0, basis_risk_limit=0.03),
            "TSLA": ProxyConfig(symbol="QQQ", beta=1.8, liquidity_score=0.95, basis_risk_limit=0.08)
        }
        self.active_hedges: Dict[str, float] = {}

    def calculate_proxy_hedge(self, symbol: str, open_position: float, current_basis_risk: float) -> Optional[Tuple[str, float, str]]:
        if open_position == 0:
            return None
            
        proxy_conf = self.proxy_map.get(symbol)
        if not proxy_conf:
            logger.warning(f"No proxy configuration found for {symbol}. Falling back to default SPY.")
            proxy_conf = ProxyConfig(symbol="SPY", beta=1.0, liquidity_score=0.9, basis_risk_limit=0.05)

        if current_basis_risk > proxy_conf.basis_risk_limit:
            logger.error(f"Basis risk {current_basis_risk} exceeds limit {proxy_conf.basis_risk_limit} for {symbol}. Aborting proxy hedge.")
            return None

        proxy_symbol = proxy_conf.symbol
        hedge_qty = abs(round(open_position * proxy_conf.beta, 0))
        hedge_side = "SELL" if open_position > 0 else "BUY"

        return proxy_symbol, hedge_qty, hedge_side

    def handle_halt_event(
        self,
        symbol: str,
        status: MarketStatus,
        halt_reason: str,
        open_position_shares: float,
        open_orders: List[Dict[str, Any]],
        current_basis_risk: float = 0.0,
        auction_fair_value: Optional[float] = None
    ) -> HaltedMarketReport:
        actions: List[HaltPlaybookAction] = []
        is_hedged = False
        msg = ""

        if status in (MarketStatus.HALTED_LULD, MarketStatus.HALTED_CIRCUIT_BREAKER):
            # 1. Microstructure Awareness: Cancel trapped orders to avoid adverse selection
            if open_orders:
                total_qty = sum(o.get("quantity", 0) for o in open_orders)
                actions.append(HaltPlaybookAction(
                    action_type="CANCEL_ORDERS",
                    target_symbol=symbol,
                    quantity=total_qty,
                    side="N/A",
                    rationale=f"Canceled {len(open_orders)} open orders to avoid trapped capital during {halt_reason}.",
                ))

            # 2. Adjust Risk Limits for Fat-Tail Environment
            actions.append(HaltPlaybookAction(
                action_type="ADJUST_RISK_LIMITS",
                target_symbol=symbol,
                quantity=0,
                side="N/A",
                rationale="Widen VaR limits and adaptive thresholds for expected post-halt volatility expansion."
            ))

            # 3. Dynamic Proxy Hedging
            hedge_decision = self.calculate_proxy_hedge(symbol, open_position_shares, current_basis_risk)
            if hedge_decision:
                proxy_sym, h_qty, h_side = hedge_decision
                if h_qty > 0:
                    actions.append(HaltPlaybookAction(
                        action_type="PROXY_HEDGE",
                        target_symbol=proxy_sym,
                        quantity=h_qty,
                        side=h_side,
                        rationale=f"Deployed {h_side} {h_qty} {proxy_sym} proxy hedge for halted {symbol} position."
                    ))
                    is_hedged = True
                    self.active_hedges[symbol] = h_qty

            msg = f"BLACK SWAN HALT EXECUTED for '{symbol}': Status={status.value}, Reason={halt_reason}. Actions={len(actions)}."
            logger.warning(msg)

        elif status == MarketStatus.RESUME_AUCTION:
            # 4. Auction Participation
            if auction_fair_value and open_position_shares != 0:
                # We attempt to liquidate or rebalance at fair value
                action_side = "SELL" if open_position_shares > 0 else "BUY"
                actions.append(HaltPlaybookAction(
                    action_type="AUCTION_RESUME_ORDER",
                    target_symbol=symbol,
                    quantity=abs(open_position_shares),
                    side=action_side,
                    price=auction_fair_value,
                    order_type="LIMIT",
                    rationale=f"Participating in re-opening auction at fair value {auction_fair_value}."
                ))
                
                # Unwind hedge if exists
                if symbol in self.active_hedges and self.active_hedges[symbol] > 0:
                    proxy_conf = self.proxy_map.get(symbol, ProxyConfig("SPY", 1.0, 1.0, 1.0))
                    unwind_side = "BUY" if action_side == "SELL" else "SELL"
                    actions.append(HaltPlaybookAction(
                        action_type="PROXY_HEDGE_UNWIND",
                        target_symbol=proxy_conf.symbol,
                        quantity=self.active_hedges[symbol],
                        side=unwind_side,
                        rationale=f"Unwinding proxy hedge ahead of auction resumption."
                    ))
                    self.active_hedges[symbol] = 0

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
            fair_value_estimate=auction_fair_value
        )
