import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# MT5 Constants (MQL5 Trade Definitions)
TRADE_ACTION_DEAL = 1
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_FILLING_IOC = 1

TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_INVALID_REQUEST = 10013
TRADE_RETCODE_INVALID_VOLUME = 10014
TRADE_RETCODE_INVALID_STOPS = 10016
TRADE_RETCODE_NO_MONEY = 10019

@dataclass
class MT5Config:
    login: int
    password: str
    server: str
    path: str = "C:/Program Files/MetaTrader 5/terminal64.exe"
    max_slippage_points: int = 10
    magic_number: int = 234000

@dataclass
class MT5OrderRequest:
    symbol: str                          # e.g. 'EURUSD', 'GBPUSD'
    order_type: str                      # 'BUY' or 'SELL'
    volume_lots: float                   # e.g. 0.1 lots
    price: float                         # Limit / Market entry price
    sl_price: Optional[float] = None     # Stop Loss price
    tp_price: Optional[float] = None     # Take Profit price
    comment: str = "Python_Algo_Bot"

@dataclass
class MT5OrderReport:
    order_id: int
    symbol: str
    order_type: str
    volume_lots: float
    execution_price: float
    retcode: int
    mql_trade_request: Dict[str, Any]
    is_executed: bool
    status: str                          # 'MT5_ORDER_EXECUTED_SUCCESS', 'MT5_INVALID_VOLUME', 'MT5_INVALID_STOPS', 'MT5_EXECUTION_FAILED'
    audit_notes: str

class MT5PythonBridgeEngine:
    """
    MetaTrader 5 (MT5) Python IPC bridge engine managing automated Forex order execution,
    lot sizing, MqlTradeRequest formatting, and TRADE_RETCODE validation.
    """
    def __init__(self, config: MT5Config, mock_ipc_adapter: Optional[Any] = None):
        self.config = config
        self.ipc = mock_ipc_adapter

    def _simulate_or_send_order(self, request_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Invokes IPC adapter or returns simulated MqlTradeResult."""
        if self.ipc:
            return self.ipc.order_send(request_dict)

        # Simulation default response
        return {
            "retcode": TRADE_RETCODE_DONE,
            "order": 98765432,
            "volume": request_dict["volume"],
            "price": request_dict["price"],
            "comment": "Request executed successfully"
        }

    def execute_forex_order(self, order: MT5OrderRequest) -> MT5OrderReport:
        """
        Validates lot sizes and SL/TP distances, formats MqlTradeRequest dictionary, and audits execution retcodes.
        """
        # 1. Volume Lot Size Audit
        if order.volume_lots < 0.01 or round(order.volume_lots * 100) % 1 != 0:
            notes = f"MT5 INVALID VOLUME [{order.symbol}]: Volume {order.volume_lots:.3f} must be a positive float multiple of 0.01 lots."
            logger.error(notes)
            return MT5OrderReport(
                order_id=0,
                symbol=order.symbol,
                order_type=order.order_type,
                volume_lots=order.volume_lots,
                execution_price=0.0,
                retcode=TRADE_RETCODE_INVALID_VOLUME,
                mql_trade_request={},
                is_executed=False,
                status="MT5_INVALID_VOLUME",
                audit_notes=notes
            )

        # 2. Stop Loss / Take Profit Distance Audit
        side_upper = order.order_type.upper()
        if side_upper == "BUY":
            if order.sl_price and order.sl_price >= order.price:
                notes = f"MT5 INVALID STOPS [{order.symbol}]: Buy SL ({order.sl_price:.5f}) must be strictly below entry price ({order.price:.5f})."
                logger.error(notes)
                return MT5OrderReport(
                    order_id=0, symbol=order.symbol, order_type=order.order_type,
                    volume_lots=order.volume_lots, execution_price=0.0,
                    retcode=TRADE_RETCODE_INVALID_STOPS, mql_trade_request={},
                    is_executed=False, status="MT5_INVALID_STOPS", audit_notes=notes
                )
        elif side_upper == "SELL":
            if order.sl_price and order.sl_price <= order.price:
                notes = f"MT5 INVALID STOPS [{order.symbol}]: Sell SL ({order.sl_price:.5f}) must be strictly above entry price ({order.price:.5f})."
                logger.error(notes)
                return MT5OrderReport(
                    order_id=0, symbol=order.symbol, order_type=order.order_type,
                    volume_lots=order.volume_lots, execution_price=0.0,
                    retcode=TRADE_RETCODE_INVALID_STOPS, mql_trade_request={},
                    is_executed=False, status="MT5_INVALID_STOPS", audit_notes=notes
                )

        # 3. Format MqlTradeRequest Dictionary
        mql_request = {
            "action": TRADE_ACTION_DEAL,
            "symbol": order.symbol,
            "volume": float(order.volume_lots),
            "type": ORDER_TYPE_BUY if side_upper == "BUY" else ORDER_TYPE_SELL,
            "price": float(order.price),
            "sl": float(order.sl_price) if order.sl_price else 0.0,
            "tp": float(order.tp_price) if order.tp_price else 0.0,
            "deviation": self.config.max_slippage_points,
            "type_filling": ORDER_FILLING_IOC,
            "magic": self.config.magic_number,
            "comment": order.comment
        }

        # 4. IPC Execution
        res = self._simulate_or_send_order(mql_request)
        retcode = res.get("retcode", TRADE_RETCODE_INVALID_REQUEST)

        if retcode == TRADE_RETCODE_DONE:
            status = "MT5_ORDER_EXECUTED_SUCCESS"
            notes = (
                f"MT5 SUCCESS [{order.symbol}]: Executed {side_upper} {order.volume_lots:.2f} lots "
                f"at {order.price:.5f} (Order #{res.get('order')}, retcode=10009)."
            )
            logger.info(notes)
            return MT5OrderReport(
                order_id=res.get("order", 0),
                symbol=order.symbol,
                order_type=order.order_type,
                volume_lots=order.volume_lots,
                execution_price=res.get("price", order.price),
                retcode=retcode,
                mql_trade_request=mql_request,
                is_executed=True,
                status=status,
                audit_notes=notes
            )
        else:
            status = "MT5_EXECUTION_FAILED"
            notes = f"MT5 FAILED [{order.symbol}]: Execution failed with retcode {retcode}. Comment: {res.get('comment')}."
            logger.error(notes)
            return MT5OrderReport(
                order_id=0,
                symbol=order.symbol,
                order_type=order.order_type,
                volume_lots=order.volume_lots,
                execution_price=0.0,
                retcode=retcode,
                mql_trade_request=mql_request,
                is_executed=False,
                status=status,
                audit_notes=notes
            )
