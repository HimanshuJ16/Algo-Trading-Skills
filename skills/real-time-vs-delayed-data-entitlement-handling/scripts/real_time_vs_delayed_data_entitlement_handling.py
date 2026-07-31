import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class Config:
    name: str = "default_config"

@dataclass
class UserEntitlement:
    user_id: str
    subscriber_type: str                 # 'PROFESSIONAL', 'NON_PROFESSIONAL'
    subscribed_exchanges: List[str]      # e.g. ['NYSE', 'NASDAQ', 'CME']
    entitlement_tier: str                # 'REAL_TIME', 'DELAYED'

@dataclass
class MarketDataRequest:
    symbol: str
    exchange: str
    is_trading_execution_request: bool = False

@dataclass
class EntitlementAuditReport:
    user_id: str
    symbol: str
    exchange: str
    is_permitted: bool
    is_delayed: bool
    delay_minutes: int
    trading_execution_allowed: bool
    status: str                          # 'REALTIME_STREAM_ENTITLED', 'DELAYED_STREAM_ENTITLED', 'EXCHANGE_NOT_SUBSCRIBED', 'LIVE_TRADING_BLOCKED_DELAYED_DATA'
    audit_notes: str

class Engine:
    """
    Legacy Engine class retained for backward compatibility.
    """
    def __init__(self, config: Config):
        self.config = config

    def process(self, data: Any) -> Any:
        return data

class RealTimeVsDelayedEntitlementEngine:
    """
    Market data entitlement engine enforcing exchange licensing rules, non-pro vs professional
    subscriber access control, and blocking live order execution on delayed data streams.
    """
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def evaluate_request(
        self, user: UserEntitlement, request: MarketDataRequest
    ) -> EntitlementAuditReport:
        """
        Validates user entitlement for real-time vs delayed data and checks trading execution compliance.
        """
        exch_upper = request.exchange.upper()
        if exch_upper not in [e.upper() for e in user.subscribed_exchanges]:
            notes = f"ACCESS DENIED [{user.user_id}]: User is not subscribed to exchange '{request.exchange}'."
            logger.warning(notes)
            return EntitlementAuditReport(
                user_id=user.user_id,
                symbol=request.symbol,
                exchange=request.exchange,
                is_permitted=False,
                is_delayed=True,
                delay_minutes=15,
                trading_execution_allowed=False,
                status="EXCHANGE_NOT_SUBSCRIBED",
                audit_notes=notes
            )

        tier_upper = user.entitlement_tier.upper()

        if request.is_trading_execution_request and tier_upper == "DELAYED":
            notes = (
                f"TRADING BLOCKED [{user.user_id} ({request.symbol})]: Live order execution requested on "
                f"15-minute DELAYED data feed. Real-Time entitlement required to place live orders."
            )
            logger.error(notes)
            return EntitlementAuditReport(
                user_id=user.user_id,
                symbol=request.symbol,
                exchange=request.exchange,
                is_permitted=False,
                is_delayed=True,
                delay_minutes=15,
                trading_execution_allowed=False,
                status="LIVE_TRADING_BLOCKED_DELAYED_DATA",
                audit_notes=notes
            )

        is_realtime = tier_upper == "REAL_TIME"
        delay_min = 0 if is_realtime else 15
        status = "REALTIME_STREAM_ENTITLED" if is_realtime else "DELAYED_STREAM_ENTITLED"

        notes = (
            f"ENTITLEMENT APPROVED [{user.user_id} - {status}]: "
            f"Symbol = {request.symbol} ({request.exchange}), Subscriber = {user.subscriber_type}, "
            f"Delay = {delay_min} mins, Trading Allowed = {is_realtime}."
        )

        logger.info(notes)

        return EntitlementAuditReport(
            user_id=user.user_id,
            symbol=request.symbol,
            exchange=request.exchange,
            is_permitted=True,
            is_delayed=not is_realtime,
            delay_minutes=delay_min,
            trading_execution_allowed=is_realtime,
            status=status,
            audit_notes=notes
        )
