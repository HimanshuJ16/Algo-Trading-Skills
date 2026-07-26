import uuid
import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

@dataclass
class LegacyProOrderRequest:
    product_id: str             # e.g. "BTC-USD"
    side: str                   # "buy" or "sell"
    type: str                   # "limit" or "market" or "stop"
    size: str                   # Base currency size
    price: Optional[str] = None # Limit price
    stop_price: Optional[str] = None
    post_only: bool = False
    client_oid: Optional[str] = None

@dataclass
class StandardizedOrderResponse:
    order_id: str
    client_order_id: str
    product_id: str
    side: str
    status: str
    order_type: str

class CoinbaseAdvancedTradeAdapter:
    """
    Adapter that converts legacy Coinbase Pro API order requests into 
    Coinbase Advanced Trade v3 payloads with nested order_configuration.
    """
    def translate_order_request(self, legacy_req: LegacyProOrderRequest) -> Dict[str, Any]:
        """
        Translates legacy Coinbase Pro request parameters into Advanced Trade v3 REST payload.
        """
        if not legacy_req.product_id or not legacy_req.side or not legacy_req.type:
            raise ValueError("Legacy order request missing required fields (product_id, side, type).")

        client_oid = legacy_req.client_oid or str(uuid.uuid4())
        side_upper = legacy_req.side.upper()
        
        if side_upper not in ("BUY", "SELL"):
            raise ValueError(f"Invalid order side: {legacy_req.side}. Must be 'BUY' or 'SELL'.")

        order_type_lower = legacy_req.type.lower()
        order_config: Dict[str, Any] = {}

        if order_type_lower == "limit":
            if not legacy_req.price:
                raise ValueError("Limit orders must specify a price.")
            order_config["limit_limit_gtc"] = {
                "base_size": str(legacy_req.size),
                "limit_price": str(legacy_req.price),
                "post_only": legacy_req.post_only
            }
        elif order_type_lower == "market":
            # For Market Buy, quote_size or base_size can be used; default to market_market_ioc
            order_config["market_market_ioc"] = {
                "base_size": str(legacy_req.size)
            }
        elif order_type_lower == "stop":
            if not legacy_req.price or not legacy_req.stop_price:
                raise ValueError("Stop orders must specify both limit price and stop_price.")
            order_config["stop_limit_stop_limit_gtc"] = {
                "base_size": str(legacy_req.size),
                "limit_price": str(legacy_req.price),
                "stop_price": str(legacy_req.stop_price),
                "stop_direction": "STOP_DIRECTION_STOP_UP" if side_upper == "BUY" else "STOP_DIRECTION_STOP_DOWN"
            }
        else:
            raise ValueError(f"Unsupported order type for Advanced Trade migration: {legacy_req.type}")

        v3_payload = {
            "client_order_id": client_oid,
            "product_id": legacy_req.product_id,
            "side": side_upper,
            "order_configuration": order_config
        }
        
        logger.info(f"Translated legacy order request to Advanced Trade v3 payload for {client_oid}")
        return v3_payload

    def parse_v3_response(self, v3_response: Dict[str, Any]) -> StandardizedOrderResponse:
        """
        Parses Advanced Trade v3 POST /api/v3/brokerage/orders response.
        """
        if not v3_response.get("success", False):
            error_msg = v3_response.get("error_response", {}).get("message", "Unknown Advanced Trade API Error")
            raise RuntimeError(f"Advanced Trade Order Submission Failed: {error_msg}")

        success_data = v3_response.get("success_response", {})
        return StandardizedOrderResponse(
            order_id=success_data.get("order_id", ""),
            client_order_id=success_data.get("client_order_id", ""),
            product_id=success_data.get("product_id", ""),
            side=success_data.get("side", ""),
            status="PENDING",
            order_type="ADVANCED_TRADE_V3"
        )
