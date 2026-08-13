"""
black-swan-playbook-for-halted-markets: Exchange halt detector, working order pause/cancel engine,
correlated proxy hedge deployer, and auction resume manager.

Scope: modelled on US NMS equity halt mechanics (LULD single-name pauses and
market-wide circuit breakers). The state machine is generic, but the market-wide
halt handling encodes a US-specific fact -- see MARKET_WIDE_HALT_STATUSES.

All risk gates in this module fail *closed*: whenever an input required to size a
hedge is missing, non-finite, or out of range, no hedge order is produced and the
reason is recorded on the report for post-incident audit.
"""
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MarketStatus(Enum):
    NORMAL = "NORMAL"
    HALTED_LULD = "HALTED_LULD"
    HALTED_CIRCUIT_BREAKER = "HALTED_CIRCUIT_BREAKER"
    RESUME_AUCTION = "RESUME_AUCTION"
    PRE_OPEN = "PRE_OPEN"


# A US market-wide circuit breaker (NYSE/NYSE American/NYSE Arca Rule 7.12,
# Nasdaq Rule 4121) halts *all* NMS securities -- including the ETFs normally used
# as proxies -- and CME coordinates a simultaneous halt of all US-based equity
# index futures and options. No equity or index-futures proxy hedge is executable
# while such a halt is in force.
MARKET_WIDE_HALT_STATUSES = frozenset({MarketStatus.HALTED_CIRCUIT_BREAKER})

# Single-name volatility pauses leave the broad market -- and therefore the proxy
# instrument -- trading normally, so proxy hedging is viable here.
SINGLE_NAME_HALT_STATUSES = frozenset({MarketStatus.HALTED_LULD})

HALT_STATUSES = MARKET_WIDE_HALT_STATUSES | SINGLE_NAME_HALT_STATUSES

# States in which the halted symbol is about to trade again and any temporary
# proxy hedge must be retired.
RESUMPTION_STATUSES = frozenset({MarketStatus.RESUME_AUCTION, MarketStatus.PRE_OPEN})


def _is_finite(value: Any) -> bool:
    """Return True only for a real, finite number.

    Guards the risk gates against ``None``, ``NaN`` and infinities. This matters
    because ``float('nan') > limit`` evaluates to ``False``: a naive comparison
    against a NaN basis-risk reading would fail *open* and deploy a hedge on
    stale or corrupt data.
    """
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


@dataclass
class ProxyConfig:
    """Hedging configuration for one halted symbol.

    Attributes:
        symbol: Proxy instrument actually traded to hedge the halted name.
        beta: Return beta of the halted asset regressed on the proxy's returns
            (``r_asset = beta * r_proxy``). This is a *return* beta, not a
            share-for-share ratio, so hedge sizing must be notional-scaled --
            see :meth:`BlackSwanHaltedMarketEngine.calculate_proxy_hedge`.
            May be negative for an inverse proxy.
        liquidity_score: Proxy tradability score in [0, 1]. Only gates hedging
            when the engine is constructed with a ``min_liquidity_score``.
        basis_risk_limit: Maximum tolerated proxy/underlying spread divergence,
            in the same units the caller measures ``current_basis_risk`` in.
            Non-negative.
    """

    symbol: str
    beta: float
    liquidity_score: float
    basis_risk_limit: float

    def __post_init__(self) -> None:
        if not self.symbol or not isinstance(self.symbol, str):
            raise ValueError("ProxyConfig.symbol must be a non-empty string.")
        if not _is_finite(self.beta):
            raise ValueError(f"ProxyConfig.beta must be finite, got {self.beta!r}.")
        if not _is_finite(self.liquidity_score) or not 0.0 <= float(self.liquidity_score) <= 1.0:
            raise ValueError(
                f"ProxyConfig.liquidity_score must be within [0, 1], got {self.liquidity_score!r}."
            )
        if not _is_finite(self.basis_risk_limit) or float(self.basis_risk_limit) < 0.0:
            raise ValueError(
                f"ProxyConfig.basis_risk_limit must be finite and non-negative, "
                f"got {self.basis_risk_limit!r}."
            )


@dataclass(frozen=True)
class ProxyHedgeState:
    """The proxy hedge actually working for a halted symbol.

    Recorded at deployment so the unwind reproduces the exact instrument, size
    and direction rather than re-deriving them from a possibly mutated proxy map.
    """

    proxy_symbol: str
    quantity: float
    side: str
    beta: float


@dataclass(frozen=True)
class ProxyHedgeDecision:
    """Outcome of a hedge evaluation: either a hedge, or an auditable reason not to."""

    hedge: Optional[ProxyHedgeState] = None
    suppression_reason: Optional[str] = None


@dataclass
class HaltPlaybookAction:
    action_type: str        # CANCEL_ORDERS, PROXY_HEDGE, AUCTION_RESUME_ORDER, ADJUST_RISK_LIMITS
    target_symbol: str
    quantity: float
    side: str
    rationale: str
    price: Optional[float] = None
    order_type: str = "MARKET"
    order_ids: List[str] = field(default_factory=list)


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
    hedge_suppression_reason: Optional[str] = None


class BlackSwanHaltedMarketEngine:
    """
    Executes automated fallback playbooks during exchange trading halts.
    Incorporates institutional quant standards: LULD microstructure awareness,
    dynamic proxy hedging, fat-tail risk limits, and auction participation.

    The engine emits *intents* (`HaltPlaybookAction`); an execution layer is
    responsible for routing them and for reconciling fills.
    """

    def __init__(
        self,
        proxy_config_map: Optional[Dict[str, ProxyConfig]] = None,
        default_proxy: Optional[ProxyConfig] = None,
        min_liquidity_score: float = 0.0,
    ):
        """
        Args:
            proxy_config_map: Symbol -> proxy configuration. The defaults below are
                illustrative placeholders; betas must be recalibrated against a
                recent estimation window before production use.
            default_proxy: Optional fallback for symbols absent from the map. When
                omitted (the default) an unmapped symbol is *not* hedged, rather
                than hedged against an assumed beta.
            min_liquidity_score: Minimum ``ProxyConfig.liquidity_score`` required to
                hedge. Defaults to 0.0, i.e. no liquidity gating unless configured;
                no venue-neutral threshold is assumed on the caller's behalf.
        """
        # Default proxy map e.g. NVDA -> QQQ, AAPL -> SPY
        self.proxy_map = proxy_config_map or {
            "NVDA": ProxyConfig(symbol="QQQ", beta=1.5, liquidity_score=0.99, basis_risk_limit=0.05),
            "AAPL": ProxyConfig(symbol="SPY", beta=1.2, liquidity_score=1.0, basis_risk_limit=0.03),
            "TSLA": ProxyConfig(symbol="QQQ", beta=1.8, liquidity_score=0.95, basis_risk_limit=0.08)
        }
        self.default_proxy = default_proxy
        if not _is_finite(min_liquidity_score) or not 0.0 <= float(min_liquidity_score) <= 1.0:
            raise ValueError(
                f"min_liquidity_score must be within [0, 1], got {min_liquidity_score!r}."
            )
        self.min_liquidity_score = float(min_liquidity_score)
        self.active_hedges: Dict[str, ProxyHedgeState] = {}

    def calculate_proxy_hedge(
        self,
        symbol: str,
        open_position: float,
        current_basis_risk: float,
        asset_price: Optional[float] = None,
        proxy_price: Optional[float] = None,
    ) -> ProxyHedgeDecision:
        """Size a beta hedge in proxy units, or explain why no hedge is placed.

        A return beta relates *percentage* moves, so an equal-notional hedge is
        required::

            proxy_units = position_units * beta * (asset_price / proxy_price)

        Sizing on share count alone (``position_units * beta``) is only correct
        when the two instruments happen to trade at the same price, and otherwise
        mis-hedges by the price ratio. Both prices are therefore mandatory; the
        halted asset's price is its last trade / fair value before the pause.

        Returns:
            A :class:`ProxyHedgeDecision`. ``hedge`` is None whenever any gate
            fails, with ``suppression_reason`` set for the audit trail.
        """
        if not _is_finite(open_position):
            return ProxyHedgeDecision(suppression_reason=f"Non-finite open position {open_position!r}.")
        open_position = float(open_position)
        if open_position == 0:
            return ProxyHedgeDecision(suppression_reason="Flat position; nothing to hedge.")

        proxy_conf = self.proxy_map.get(symbol) or self.default_proxy
        if proxy_conf is None:
            reason = f"No proxy configuration for {symbol} and no default_proxy configured."
            logger.error(reason)
            return ProxyHedgeDecision(suppression_reason=reason)

        if not _is_finite(current_basis_risk) or float(current_basis_risk) < 0.0:
            reason = (
                f"Basis risk reading {current_basis_risk!r} for {symbol} is unusable "
                f"(non-finite or negative). Failing closed."
            )
            logger.error(reason)
            return ProxyHedgeDecision(suppression_reason=reason)

        if float(current_basis_risk) > proxy_conf.basis_risk_limit:
            reason = (
                f"Basis risk {current_basis_risk} exceeds limit {proxy_conf.basis_risk_limit} "
                f"for {symbol}. Aborting proxy hedge."
            )
            logger.error(reason)
            return ProxyHedgeDecision(suppression_reason=reason)

        if proxy_conf.liquidity_score < self.min_liquidity_score:
            reason = (
                f"Proxy {proxy_conf.symbol} liquidity score {proxy_conf.liquidity_score} "
                f"below configured minimum {self.min_liquidity_score}."
            )
            logger.error(reason)
            return ProxyHedgeDecision(suppression_reason=reason)

        if not (_is_finite(asset_price) and float(asset_price) > 0.0) or not (
            _is_finite(proxy_price) and float(proxy_price) > 0.0
        ):
            reason = (
                f"Notional-correct beta hedging for {symbol} requires positive finite "
                f"asset_price and proxy_price; got {asset_price!r} and {proxy_price!r}. "
                f"Failing closed rather than sizing on share count."
            )
            logger.error(reason)
            return ProxyHedgeDecision(suppression_reason=reason)

        # Signed proxy exposure that offsets the halted position's beta-adjusted delta.
        signed_units = -(open_position * proxy_conf.beta * (float(asset_price) / float(proxy_price)))
        hedge_qty = float(round(abs(signed_units)))
        if hedge_qty <= 0:
            reason = (
                f"Beta-scaled hedge for {symbol} rounds to zero proxy units "
                f"({signed_units:.4f}); no order placed."
            )
            logger.info(reason)
            return ProxyHedgeDecision(suppression_reason=reason)

        hedge_side = "SELL" if signed_units < 0 else "BUY"
        return ProxyHedgeDecision(
            hedge=ProxyHedgeState(
                proxy_symbol=proxy_conf.symbol,
                quantity=hedge_qty,
                side=hedge_side,
                beta=proxy_conf.beta,
            )
        )

    def _build_cancel_action(
        self, symbol: str, open_orders: List[Dict[str, Any]], halt_reason: str
    ) -> Optional[HaltPlaybookAction]:
        """Aggregate the working orders to pull, ignoring foreign or unusable entries."""
        cancellable: List[Dict[str, Any]] = []
        for order in open_orders:
            if not isinstance(order, dict):
                logger.warning(f"Skipping non-dict open order entry for {symbol}: {order!r}")
                continue
            order_symbol = order.get("symbol")
            if order_symbol is not None and order_symbol != symbol:
                logger.warning(
                    f"Skipping order {order.get('id')!r} for {order_symbol!r}: "
                    f"not the halted symbol {symbol}."
                )
                continue
            cancellable.append(order)

        if not cancellable:
            return None

        # Absolute sizes: buy and sell working quantities both represent exposure to pull.
        total_qty = 0.0
        for order in cancellable:
            qty = order.get("quantity")
            if _is_finite(qty):
                total_qty += abs(float(qty))
            else:
                logger.warning(
                    f"Open order {order.get('id')!r} for {symbol} has unusable quantity {qty!r}; "
                    f"excluded from the cancel aggregate but still cancelled."
                )

        order_ids = [str(o["id"]) for o in cancellable if o.get("id") is not None]
        if len(order_ids) != len(cancellable):
            logger.warning(
                f"{len(cancellable) - len(order_ids)} open order(s) for {symbol} carry no id; "
                f"the execution layer cannot target them individually."
            )

        return HaltPlaybookAction(
            action_type="CANCEL_ORDERS",
            target_symbol=symbol,
            quantity=total_qty,
            side="N/A",
            rationale=f"Canceled {len(cancellable)} open orders to avoid trapped capital during {halt_reason}.",
            order_ids=order_ids,
        )

    def handle_halt_event(
        self,
        symbol: str,
        status: MarketStatus,
        halt_reason: str,
        open_position_shares: float,
        open_orders: List[Dict[str, Any]],
        current_basis_risk: float = 0.0,
        auction_fair_value: Optional[float] = None,
        asset_price: Optional[float] = None,
        proxy_price: Optional[float] = None,
    ) -> HaltedMarketReport:
        """Run the halt playbook for one status update.

        Safe to call repeatedly with the same status: exchanges re-disseminate halt
        status, and a second `HALTED_*` message for a symbol already hedged will not
        deploy a second hedge.

        Args:
            asset_price: Last trade / pre-halt reference price of the halted symbol.
            proxy_price: Current price of the mapped proxy instrument.
                Both are required to size a hedge; without them no hedge is placed.

        Raises:
            ValueError: On a structurally invalid event (empty symbol, unknown
                status type, non-finite position). A malformed feed message must
                not be silently absorbed by a risk control.
        """
        if not symbol or not isinstance(symbol, str):
            raise ValueError("symbol must be a non-empty string.")
        if not isinstance(status, MarketStatus):
            raise ValueError(f"status must be a MarketStatus, got {status!r}.")
        if not _is_finite(open_position_shares):
            raise ValueError(f"open_position_shares must be finite, got {open_position_shares!r}.")
        open_position_shares = float(open_position_shares)
        open_orders = open_orders or []

        actions: List[HaltPlaybookAction] = []
        is_hedged = False
        suppression_reason: Optional[str] = None
        msg = ""

        if status in HALT_STATUSES:
            # 1. Microstructure Awareness: Cancel trapped orders to avoid adverse selection.
            #    Resting orders survive a US halt in the book and are eligible for the
            #    reopening auction, so leaving them working is a live exposure.
            cancel_action = self._build_cancel_action(symbol, open_orders, halt_reason)
            if cancel_action:
                actions.append(cancel_action)

            # 2. Adjust Risk Limits for Fat-Tail Environment
            actions.append(HaltPlaybookAction(
                action_type="ADJUST_RISK_LIMITS",
                target_symbol=symbol,
                quantity=0,
                side="N/A",
                rationale="Widen VaR limits and adaptive thresholds for expected post-halt volatility expansion."
            ))

            # 3. Dynamic Proxy Hedging
            if status in MARKET_WIDE_HALT_STATUSES:
                suppression_reason = (
                    f"Market-wide circuit breaker ({status.value}) halts every NMS security and, "
                    f"by coordinated CME halt, US equity index futures and options. No proxy "
                    f"instrument is tradable; hedging suppressed."
                )
                logger.error(suppression_reason)
            elif symbol in self.active_hedges:
                existing = self.active_hedges[symbol]
                suppression_reason = (
                    f"Proxy hedge already working for {symbol} "
                    f"({existing.side} {existing.quantity} {existing.proxy_symbol}); "
                    f"repeated halt status ignored to avoid double-hedging."
                )
                logger.info(suppression_reason)
                is_hedged = True
            else:
                decision = self.calculate_proxy_hedge(
                    symbol,
                    open_position_shares,
                    current_basis_risk,
                    asset_price=asset_price,
                    proxy_price=proxy_price,
                )
                suppression_reason = decision.suppression_reason
                if decision.hedge:
                    hedge = decision.hedge
                    actions.append(HaltPlaybookAction(
                        action_type="PROXY_HEDGE",
                        target_symbol=hedge.proxy_symbol,
                        quantity=hedge.quantity,
                        side=hedge.side,
                        rationale=(
                            f"Deployed {hedge.side} {hedge.quantity} {hedge.proxy_symbol} proxy hedge "
                            f"(beta {hedge.beta}) for halted {symbol} position."
                        )
                    ))
                    is_hedged = True
                    self.active_hedges[symbol] = hedge

            msg = f"BLACK SWAN HALT EXECUTED for '{symbol}': Status={status.value}, Reason={halt_reason}. Actions={len(actions)}."
            logger.warning(msg)

        elif status in RESUMPTION_STATUSES or status == MarketStatus.NORMAL:
            # 4. Auction Participation
            if status in RESUMPTION_STATUSES:
                if open_position_shares != 0 and _is_finite(auction_fair_value) and float(auction_fair_value) > 0:
                    # We attempt to liquidate or rebalance at fair value
                    action_side = "SELL" if open_position_shares > 0 else "BUY"
                    actions.append(HaltPlaybookAction(
                        action_type="AUCTION_RESUME_ORDER",
                        target_symbol=symbol,
                        quantity=abs(open_position_shares),
                        side=action_side,
                        price=float(auction_fair_value),
                        order_type="LIMIT",
                        rationale=f"Participating in re-opening auction at fair value {auction_fair_value}."
                    ))
                elif open_position_shares != 0:
                    logger.warning(
                        f"No usable fair value for {symbol} ({auction_fair_value!r}); skipping auction "
                        f"participation rather than sending an unpriced order into a reopening auction."
                    )

            # 5. Hedge unwind -- unconditional on resumption. A proxy hedge left working
            #    after the halted name resumes is naked directional exposure, so this must
            #    not be gated on the auction order being generated.
            unwind = self.active_hedges.pop(symbol, None)
            if unwind and unwind.quantity > 0:
                unwind_side = "BUY" if unwind.side == "SELL" else "SELL"
                actions.append(HaltPlaybookAction(
                    action_type="PROXY_HEDGE_UNWIND",
                    target_symbol=unwind.proxy_symbol,
                    quantity=unwind.quantity,
                    side=unwind_side,
                    rationale=f"Unwinding proxy hedge on {status.value} for {symbol}."
                ))

            if status in RESUMPTION_STATUSES:
                msg = f"AUCTION RESUME DETECTED for '{symbol}': Preparing post-halt re-entry."
                logger.info(msg)
            else:
                msg = f"Market Normal for '{symbol}'."

        else:
            msg = f"No halt playbook action defined for '{symbol}' in state {status.value}."
            logger.warning(msg)

        return HaltedMarketReport(
            symbol=symbol,
            status=status,
            halt_reason=halt_reason,
            open_position_shares=open_position_shares,
            actions_executed=actions,
            is_proxy_hedged=is_hedged,
            status_message=msg,
            fair_value_estimate=auction_fair_value,
            hedge_suppression_reason=suppression_reason,
        )
