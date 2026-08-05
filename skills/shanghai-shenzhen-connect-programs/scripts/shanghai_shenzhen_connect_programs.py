import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Legacy Config container for backward compatibility."""
    api_key: str = "default_connect_key"

class IntegrationEngine:
    """Legacy IntegrationEngine class for backward compatibility."""
    def __init__(self, config: Config):
        self.config = config

    def connect(self) -> bool:
        return True

    def process(self) -> str:
        return "success"

class StockConnectChannel(str, Enum):
    SHANGHAI_CONNECT = "SHANGHAI_CONNECT"   # SSE Northbound
    SHENZHEN_CONNECT = "SHENZHEN_CONNECT"   # SZSE Northbound

@dataclass
class ConnectOrder:
    order_id: str
    symbol: str                              # e.g., '600519.SH' (Moutai), '000858.SZ' (Wuliangye)
    channel: StockConnectChannel
    side: str                                # 'BUY' or 'SELL'
    quantity: float                          # Round lots of 100 shares
    price_cnh: float
    order_date_iso: str                      # 'YYYY-MM-DD'
    purchased_date_iso: Optional[str] = None # Date shares were bought (for T+1 day trading check)

@dataclass
class ConnectExecutionResult:
    order_id: str
    symbol: str
    channel: StockConnectChannel
    is_executed: bool
    rejection_reason: Optional[str]
    remaining_quota_cnh: float
    audit_notes: str

class ShanghaiShenzhenConnectEngine:
    """
    Production-grade Shanghai-Shenzhen Stock Connect (Northbound Trading) Engine enforcing
    RMB 52 billion daily quota tracking, T+1 day-trading restrictions, 100-share round lots, and CNH currency settlement.
    """
    # Stock Connect Daily Quota (RMB 52 Billion per channel)
    DAILY_QUOTA_CNH = 52_000_000_000.0

    def __init__(self):
        self.channel_quotas: Dict[StockConnectChannel, float] = {
            StockConnectChannel.SHANGHAI_CONNECT: self.DAILY_QUOTA_CNH,
            StockConnectChannel.SHENZHEN_CONNECT: self.DAILY_QUOTA_CNH,
        }

    def reset_daily_quotas(self):
        """Resets daily quotas at the start of each trading session."""
        self.channel_quotas[StockConnectChannel.SHANGHAI_CONNECT] = self.DAILY_QUOTA_CNH
        self.channel_quotas[StockConnectChannel.SHENZHEN_CONNECT] = self.DAILY_QUOTA_CNH

    def validate_and_route_order(self, order: ConnectOrder) -> ConnectExecutionResult:
        """
        Validates Northbound Connect order against:
        1. 100-share round lot size (Buys must be multiples of 100).
        2. T+1 Day-Trading restriction (Cannot sell shares bought on the same day T).
        3. Daily Quota Balance (Net buy deduction; sells always permitted).
        """
        # 1. Round Lot Check (Buys must be in 100-share board lots)
        if order.side.upper() == "BUY" and (order.quantity % 100 != 0 or order.quantity <= 0):
            reason = f"INVALID_BOARD_LOT: Northbound BUY order qty ({order.quantity}) must be in round lots of 100 shares."
            logger.warning(reason)
            return ConnectExecutionResult(
                order_id=order.order_id, symbol=order.symbol, channel=order.channel,
                is_executed=False, rejection_reason=reason,
                remaining_quota_cnh=self.channel_quotas[order.channel], audit_notes=reason
            )

        # 2. T+1 Day Trading Prohibition Check
        if order.side.upper() == "SELL" and order.purchased_date_iso == order.order_date_iso:
            reason = f"TPLUS1_VIOLATION: Day trading prohibited under Stock Connect. Shares bought on {order.purchased_date_iso} cannot be sold on {order.order_date_iso}."
            logger.warning(reason)
            return ConnectExecutionResult(
                order_id=order.order_id, symbol=order.symbol, channel=order.channel,
                is_executed=False, rejection_reason=reason,
                remaining_quota_cnh=self.channel_quotas[order.channel], audit_notes=reason
            )

        # 3. Daily Quota Check (Applies to BUY orders only)
        notional_cnh = order.quantity * order.price_cnh
        current_quota = self.channel_quotas[order.channel]

        if order.side.upper() == "BUY":
            if current_quota < notional_cnh:
                reason = f"QUOTA_EXCEEDED: Insufficient Northbound quota on {order.channel.value}. Required = {notional_cnh:,.2f} CNH, Available = {current_quota:,.2f} CNH."
                logger.warning(reason)
                return ConnectExecutionResult(
                    order_id=order.order_id, symbol=order.symbol, channel=order.channel,
                    is_executed=False, rejection_reason=reason,
                    remaining_quota_cnh=current_quota, audit_notes=reason
                )

            # Deduct buy notional from quota
            self.channel_quotas[order.channel] -= notional_cnh
        else:
            # Sell orders restore quota
            self.channel_quotas[order.channel] = min(self.DAILY_QUOTA_CNH, self.channel_quotas[order.channel] + notional_cnh)

        updated_quota = self.channel_quotas[order.channel]
        notes = (
            f"STOCK CONNECT EXECUTED [{order.order_id}] ({order.symbol}): {order.side} {order.quantity} @ {order.price_cnh} CNH. "
            f"Channel = {order.channel.value}, Remaining Quota = {updated_quota:,.2f} CNH."
        )

        logger.info(notes)

        return ConnectExecutionResult(
            order_id=order.order_id,
            symbol=order.symbol,
            channel=order.channel,
            is_executed=True,
            rejection_reason=None,
            remaining_quota_cnh=updated_quota,
            audit_notes=notes
        )
