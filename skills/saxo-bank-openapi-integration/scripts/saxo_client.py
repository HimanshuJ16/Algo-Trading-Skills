"""
saxo-bank-openapi-integration: Production-grade client for Saxo Bank OpenAPI covering
multi-asset UIC instrument resolution, order routing, and portfolio position tracking.
"""
from dataclasses import dataclass, field
import logging
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class SaxoAPIError(Exception):
    """Raised when Saxo Bank OpenAPI returns an error."""
    pass


class SaxoAssetType(Enum):
    FX_SPOT = "FxSpot"
    STOCK = "Stock"
    CONTRACT_FUTURES = "ContractFutures"
    OPTION_ROOT = "OptionRoot"
    CFD_ON_STOCK = "CfdOnStock"


class SaxoOrderType(Enum):
    MARKET = "Market"
    LIMIT = "Limit"
    STOP_IF_TRADED = "StopIfTraded"
    TRAILING_STOP = "TrailingStop"


class SaxoOrderDuration(Enum):
    DAY_ORDER = "DayOrder"
    GOOD_TILL_CANCEL = "GoodTillCancel"
    IMMEDIATE_OR_CANCEL = "ImmediateOrCancel"


@dataclass
class SaxoInstrument:
    uic: int
    symbol: str
    description: str
    asset_type: SaxoAssetType
    currency: str


@dataclass
class SaxoOrder:
    order_id: str
    account_key: str
    uic: int
    asset_type: SaxoAssetType
    buy_sell: str  # "Buy" or "Sell"
    amount: float
    order_type: SaxoOrderType
    price: Optional[float]
    status: str


@dataclass
class SaxoPosition:
    position_id: str
    uic: int
    symbol: str
    asset_type: SaxoAssetType
    amount: float
    average_open_price: float
    current_price: float
    unrealized_pnl: float


class SaxoBankOpenAPIClient:
    """
    Client for Saxo Bank OpenAPI providing multi-asset trading across Equities, FX,
    Futures, and Options.
    """

    SIM_BASE_URL = "https://gateway.saxobank.com/sim/openapi"
    LIVE_BASE_URL = "https://gateway.saxobank.com/openapi"

    def __init__(

        self,
        access_token: str,
        account_key: str,
        is_simulation: bool = True,
        http_fn: Optional[Callable[[str, str, Dict[str, str], Optional[Dict[str, Any]]], Any]] = None,
    ):
        self.access_token = access_token
        self.account_key = account_key
        self.base_url = self.SIM_BASE_URL if is_simulation else self.LIVE_BASE_URL
        self._http_fn = http_fn

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def search_instrument(self, keywords: str, asset_type: SaxoAssetType) -> List[SaxoInstrument]:
        """Queries `/ref/v1/instruments` to resolve ticker keywords to Saxo UICs."""
        url = f"{self.base_url}/ref/v1/instruments?Keywords={keywords}&AssetTypes={asset_type.value}"

        if self._http_fn:
            status_code, response_data = self._http_fn("GET", url, self._headers(), None)
        else:
            raise SaxoAPIError("HTTP transport function not configured.")

        if status_code != 200:
            raise SaxoAPIError(f"Instrument search failed (HTTP {status_code}): {response_data}")

        instruments = []
        for item in response_data.get("Data", []):
            instruments.append(SaxoInstrument(
                uic=int(item["Identifier"]),
                symbol=item.get("Symbol", keywords),
                description=item.get("Description", ""),
                asset_type=asset_type,
                currency=item.get("CurrencyCode", "USD"),
            ))
        return instruments

    def place_order(
        self,
        uic: int,
        asset_type: SaxoAssetType,
        buy_sell: str,  # "Buy" or "Sell"
        amount: float,
        order_type: SaxoOrderType = SaxoOrderType.MARKET,
        price: Optional[float] = None,
        duration: SaxoOrderDuration = SaxoOrderDuration.DAY_ORDER,
    ) -> SaxoOrder:
        """Places a multi-asset order via `/trade/v1/orders`."""
        url = f"{self.base_url}/trade/v1/orders"

        payload: Dict[str, Any] = {
            "AccountKey": self.account_key,
            "Uic": uic,
            "AssetType": asset_type.value,
            "BuySell": buy_sell,
            "Amount": amount,
            "OrderType": order_type.value,
            "OrderDuration": {"DurationType": duration.value},
        }
        if order_type == SaxoOrderType.LIMIT and price is not None:
            payload["OrderPrice"] = price

        if self._http_fn:
            status_code, response_data = self._http_fn("POST", url, self._headers(), payload)
        else:
            raise SaxoAPIError("HTTP transport function not configured.")

        if status_code not in (200, 201):
            raise SaxoAPIError(f"Order placement failed (HTTP {status_code}): {response_data}")

        order_id = str(response_data.get("OrderId", "ORD_SAXO_001"))
        return SaxoOrder(
            order_id=order_id,
            account_key=self.account_key,
            uic=uic,
            asset_type=asset_type,
            buy_sell=buy_sell,
            amount=amount,
            order_type=order_type,
            price=price,
            status=response_data.get("Status", "Placed"),
        )

    def get_positions(self) -> List[SaxoPosition]:
        """Queries active multi-asset portfolio positions via `/port/v1/positions`."""
        url = f"{self.base_url}/port/v1/positions?AccountKey={self.account_key}"

        if self._http_fn:
            status_code, response_data = self._http_fn("GET", url, self._headers(), None)
        else:
            raise SaxoAPIError("HTTP transport function not configured.")

        if status_code != 200:
            raise SaxoAPIError(f"Failed to fetch positions (HTTP {status_code}): {response_data}")

        positions = []
        for item in response_data.get("Data", []):
            pos_base = item.get("PositionBase", {})
            pos_view = item.get("PositionView", {})

            try:
                asset_enum = SaxoAssetType(pos_base.get("AssetType", "Stock"))
            except ValueError:
                asset_enum = SaxoAssetType.STOCK

            positions.append(SaxoPosition(
                position_id=str(pos_base.get("PositionId", "")),
                uic=int(pos_base.get("Uic", 0)),
                symbol=str(pos_base.get("Symbol", "UNKNOWN")),
                asset_type=asset_enum,
                amount=float(pos_base.get("Amount", 0.0)),
                average_open_price=float(pos_base.get("OpenPrice", 0.0)),
                current_price=float(pos_view.get("CurrentPrice", 0.0)),
                unrealized_pnl=float(pos_view.get("ProfitLossOnTrade", 0.0)),
            ))
        return positions
