"""
multi-exchange-feed-normalization: Production-grade multi-exchange tick normalization engine,
canonical symbol mapper, side/price/volume standardizer, and venue parser registry.
"""
from dataclasses import dataclass, field
import datetime
from enum import Enum
import logging
import time
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class NormalizationError(ValueError):
    """Raised when tick payload cannot be normalized."""
    pass


class NormalizedSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    UNKNOWN = "UNKNOWN"


@dataclass
class UnifiedTick:
    symbol: str
    venue: str
    price: float
    quantity: float
    side: NormalizedSide
    exchange_timestamp: float
    receipt_timestamp: float = field(default_factory=time.time)


class TickNormalizerRegistry:
    """
    Parses heterogeneous exchange/broker tick payloads into standard UnifiedTick objects.
    """

    def __init__(self):
        self.symbol_mappings: Dict[Tuple[str, str], str] = {}  # (venue, raw_symbol) -> canonical_symbol

    def register_symbol_mapping(self, venue: str, raw_symbol: str, canonical_symbol: str):
        self.symbol_mappings[(venue.lower(), raw_symbol.upper())] = canonical_symbol.upper()

    def get_canonical_symbol(self, venue: str, raw_symbol: str) -> str:
        key = (venue.lower(), raw_symbol.upper())
        return self.symbol_mappings.get(key, raw_symbol.upper())

    @staticmethod
    def _coerce_timestamp(ts_val: Any) -> float:
        """Coerces ISO strings, ms timestamps, or s timestamps to float epoch seconds."""
        if ts_val is None:
            return time.time()
        try:
            if isinstance(ts_val, (int, float)):
                return ts_val / 1000.0 if ts_val > 1e11 else float(ts_val)
            if isinstance(ts_val, str):
                if ts_val.isdigit():
                    v = float(ts_val)
                    return v / 1000.0 if v > 1e11 else v
                dt = datetime.datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
                return dt.timestamp()
            return time.time()
        except Exception:
            return time.time()

    def parse_binance(self, payload: Dict[str, Any]) -> UnifiedTick:
        raw_symbol = payload.get("s", "")
        price = float(payload.get("p", 0.0))
        qty = float(payload.get("q", 0.0))
        ts_raw = payload.get("T", payload.get("E", time.time()))

        # Binance: 'm' = is_buyer_maker (True -> SELL market order, False -> BUY market order)
        is_maker = payload.get("m", False)
        side = NormalizedSide.SELL if is_maker else NormalizedSide.BUY

        canonical_symbol = self.get_canonical_symbol("binance", raw_symbol)
        return UnifiedTick(
            symbol=canonical_symbol,
            venue="binance",
            price=price,
            quantity=qty,
            side=side,
            exchange_timestamp=self._coerce_timestamp(ts_raw),
        )

    def parse_coinbase(self, payload: Dict[str, Any]) -> UnifiedTick:
        raw_symbol = payload.get("product_id", payload.get("symbol", ""))
        price = float(payload.get("price", 0.0))
        qty = float(payload.get("size", payload.get("last_size", 0.0)))
        ts_raw = payload.get("time", time.time())

        side_str = payload.get("side", "").upper()
        side = NormalizedSide.BUY if side_str == "BUY" else (NormalizedSide.SELL if side_str == "SELL" else NormalizedSide.UNKNOWN)

        canonical_symbol = self.get_canonical_symbol("coinbase", raw_symbol)
        return UnifiedTick(
            symbol=canonical_symbol,
            venue="coinbase",
            price=price,
            quantity=qty,
            side=side,
            exchange_timestamp=self._coerce_timestamp(ts_raw),
        )

    def parse_zerodha(self, payload: Dict[str, Any]) -> UnifiedTick:
        raw_symbol = str(payload.get("tradingsymbol", payload.get("instrument_token", "")))
        price = float(payload.get("last_price", 0.0))
        qty = float(payload.get("last_quantity", payload.get("volume", 0.0)))
        ts_raw = payload.get("last_trade_time", time.time())

        canonical_symbol = self.get_canonical_symbol("zerodha", raw_symbol)
        return UnifiedTick(
            symbol=canonical_symbol,
            venue="zerodha",
            price=price,
            quantity=qty,
            side=NormalizedSide.UNKNOWN,  # Ticks don't carry aggressor side in Kite
            exchange_timestamp=self._coerce_timestamp(ts_raw),
        )

    def normalize(self, venue: str, raw_payload: Dict[str, Any]) -> UnifiedTick:
        v = venue.lower()
        if v == "binance":
            return self.parse_binance(raw_payload)
        elif v == "coinbase":
            return self.parse_coinbase(raw_payload)
        elif v == "zerodha":
            return self.parse_zerodha(raw_payload)
        else:
            raise NormalizationError(f"Unsupported exchange venue '{venue}'.")
