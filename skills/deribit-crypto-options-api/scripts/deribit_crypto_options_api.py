import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class DeribitInstrumentSpec:
    instrument_name: str               # e.g. 'BTC-28MAR26-60000-C'
    base_currency: str                 # 'BTC' or 'ETH'
    quote_currency: str                # 'USD'
    expiry_date: str                   # '28MAR26'
    strike: float                      # 60000.0
    option_type: str                   # 'call' or 'put'

@dataclass
class DeribitOptionTicker:
    instrument_name: str
    index_price_usd: float             # e.g. 60000.0 USD
    mark_price_coin: float             # e.g. 0.05 BTC
    mark_iv: float                     # e.g. 65.4%
    delta: float                       # e.g. 0.55
    gamma: float
    vega: float
    theta: float
    best_bid_price_coin: float
    best_ask_price_coin: float

@dataclass
class DeribitOrderRequest:
    order_id: str
    instrument_name: str
    action: str                        # 'buy' or 'sell'
    amount_contracts: float            # Quantity of contracts
    price_coin: float                  # Limit price in BTC/ETH
    order_type: str = "limit"

@dataclass
class DeribitOptionsOrderReport:
    order_id: str
    instrument_name: str
    action: str
    amount_contracts: float
    price_coin: float
    price_usd_equivalent: float
    total_premium_coin: float
    total_premium_usd: float
    portfolio_delta_coin_impact: float
    portfolio_delta_usd_impact: float
    json_rpc_payload: Dict[str, Any]
    is_dispatched: bool

class DeribitCryptoOptionsApiEngine:
    """
    Quantitative Deribit crypto options API engine for JSON-RPC 2.0 formatting,
    inverse option premium conversions (BTC/ETH to USD), portfolio Greeks aggregation, and margin safety audits.
    """
    def __init__(self, is_testnet: bool = True):
        self.endpoint_url = "wss://test.deribit.com/ws/api/v2" if is_testnet else "wss://www.deribit.com/ws/api/v2"

    def parse_instrument_name(self, symbol: str) -> DeribitInstrumentSpec:
        """
        Parses Deribit instrument symbol (e.g. 'BTC-28MAR26-60000-C') into component fields.
        """
        parts = symbol.split("-")
        if len(parts) != 4:
            raise ValueError(f"Invalid Deribit instrument name format: {symbol}")

        currency = parts[0].upper()
        expiry = parts[1].upper()
        strike = float(parts[2])
        opt_type = "call" if parts[3].upper() == "C" else "put"

        return DeribitInstrumentSpec(
            instrument_name=symbol,
            base_currency=currency,
            quote_currency="USD",
            expiry_date=expiry,
            strike=strike,
            option_type=opt_type
        )

    def convert_inverse_premium_to_usd(self, price_coin: float, index_price_usd: float) -> float:
        """
        Converts coin-denominated premium (BTC/ETH) to USD equivalent:
        P_usd = P_coin * S_index_usd
        """
        return round(price_coin * index_price_usd, 2)

    def format_json_rpc_order(self, req: DeribitOrderRequest, request_id: int = 1) -> Dict[str, Any]:
        """
        Formats JSON-RPC 2.0 payload for Deribit private/buy or private/sell.
        """
        method = f"private/{req.action.lower()}"
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": {
                "instrument_name": req.instrument_name,
                "amount": req.amount_contracts,
                "type": req.order_type,
                "price": req.price_coin
            }
        }

    def process_option_order(
        self,
        req: DeribitOrderRequest,
        ticker: DeribitOptionTicker,
        available_balance_coin: float
    ) -> DeribitOptionsOrderReport:
        """
        Audits order margin requirements, converts inverse premiums to USD, and generates JSON-RPC payload.
        """
        spec = self.parse_instrument_name(req.instrument_name)

        price_usd = self.convert_inverse_premium_to_usd(req.price_coin, ticker.index_price_usd)
        total_premium_coin = round(req.price_coin * req.amount_contracts, 6)
        total_premium_usd = round(total_premium_coin * ticker.index_price_usd, 2)

        # Delta exposures
        side_mult = 1.0 if req.action.lower() == "buy" else -1.0
        delta_coin_impact = round(req.amount_contracts * ticker.delta * side_mult, 4)
        delta_usd_impact = round(delta_coin_impact * ticker.index_price_usd, 2)

        payload = self.format_json_rpc_order(req)

        # Margin / Balance check for buy orders
        is_ok = True
        if req.action.lower() == "buy" and total_premium_coin > available_balance_coin:
            is_ok = False
            logger.error(f"INSUFFICIENT BALANCE [{req.order_id}]: Required {total_premium_coin} {spec.base_currency} > Avail {available_balance_coin}")

        if is_ok:
            logger.info(f"DERIBIT ORDER DISPATCHED [{req.order_id}]: {req.action.upper()} {req.amount_contracts} x {req.instrument_name} @ {req.price_coin} {spec.base_currency} (${total_premium_usd:,} USD)")

        return DeribitOptionsOrderReport(
            order_id=req.order_id,
            instrument_name=req.instrument_name,
            action=req.action,
            amount_contracts=req.amount_contracts,
            price_coin=req.price_coin,
            price_usd_equivalent=price_usd,
            total_premium_coin=total_premium_coin,
            total_premium_usd=total_premium_usd,
            portfolio_delta_coin_impact=delta_coin_impact,
            portfolio_delta_usd_impact=delta_usd_impact,
            json_rpc_payload=payload,
            is_dispatched=is_ok
        )
