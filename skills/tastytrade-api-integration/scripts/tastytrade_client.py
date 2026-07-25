"""
tastytrade-api-integration: Session authentication, OCC option symbol formatting,
and multi-leg complex option order placement for Tastytrade (Tastyworks) API.
"""
from dataclasses import dataclass, field
import logging
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class TastytradeAPIError(Exception):
    """Raised when Tastytrade API operation fails."""
    pass


class LegAction(Enum):
    BUY_TO_OPEN = "Buy to Open"
    SELL_TO_OPEN = "Sell to Open"
    BUY_TO_CLOSE = "Buy to Close"
    SELL_TO_CLOSE = "Sell to Close"


class PriceEffect(Enum):
    CREDIT = "Credit"
    DEBIT = "Debit"


class OrderType(Enum):
    LIMIT = "Limit"
    MARKET = "Market"


@dataclass
class OptionLeg:
    occ_symbol: str
    action: LegAction
    quantity: float
    instrument_type: str = "Equity Option"


@dataclass
class TastytradeSession:
    session_token: str
    remember_token: Optional[str]
    expires_at: float


@dataclass
class TastytradeOrder:
    order_id: str
    account_number: str
    order_type: OrderType
    legs: List[OptionLeg]
    price: float
    price_effect: PriceEffect
    status: str


class TastytradeClient:
    """
    Client for Tastytrade REST API specializing in multi-leg options order routing
    and OCC option ticker management.
    """

    CERT_BASE_URL = "https://api.cert.tastyworks.com"
    PROD_BASE_URL = "https://api.tastyworks.com"

    def __init__(
        self,
        is_production: bool = False,
        http_fn: Optional[Callable[[str, str, Dict[str, str], Optional[Dict[str, Any]]], Any]] = None,
    ):
        self.base_url = self.PROD_BASE_URL if is_production else self.CERT_BASE_URL
        self.session: Optional[TastytradeSession] = None
        self._http_fn = http_fn

    @staticmethod
    def format_occ_symbol(ticker: str, exp_date_yymmdd: str, option_type: str, strike: float) -> str:
        """
        Formats a ticker into standard 21-character OCC option symbol format.
        (e.g., AAPL, 240816, C, 200.0 -> 'AAPL  240816C00200000')
        """
        ticker_padded = ticker.upper().ljust(6)
        opt_type = option_type.upper()  # 'C' or 'P'
        strike_int = int(round(strike * 1000))
        strike_padded = f"{strike_int:08d}"
        return f"{ticker_padded}{exp_date_yymmdd}{opt_type}{strike_padded}"

    def login(self, login_email: str, password: str) -> TastytradeSession:
        """Authenticates with `/sessions` endpoint and extracts `session-token`."""
        url = f"{self.base_url}/sessions"
        payload = {"login": login_email, "password": password}

        if self._http_fn:
            status_code, res_data = self._http_fn("POST", url, {}, payload)
        else:
            raise TastytradeAPIError("HTTP transport function not configured.")

        if status_code not in (200, 201) or "data" not in res_data:
            raise TastytradeAPIError(f"Tastytrade login failed (HTTP {status_code}): {res_data}")

        token_str = res_data["data"].get("session-token")
        remember_str = res_data["data"].get("remember-token")

        if not token_str:
            raise TastytradeAPIError("session-token missing from login response.")

        self.session = TastytradeSession(
            session_token=token_str,
            remember_token=remember_str,
            expires_at=time.time() + 86400,
        )
        logger.info("Tastytrade session established successfully.")
        return self.session

    def place_complex_option_order(
        self,
        account_number: str,
        legs: List[OptionLeg],
        order_type: OrderType,
        net_price: float,
        price_effect: PriceEffect,
        time_in_force: str = "Day",
    ) -> TastytradeOrder:
        """
        Places a single or multi-leg option order (Spread, Condor, Straddle)
        via `/accounts/{account_number}/orders`.
        """
        if not self.session:
            raise TastytradeAPIError("Not authenticated. Call login() first.")

        url = f"{self.base_url}/accounts/{account_number}/orders"
        headers = {
            "Authorization": self.session.session_token,
            "Content-Type": "application/json",
        }

        leg_payloads = []
        for leg in legs:
            leg_payloads.append({
                "instrument-type": leg.instrument_type,
                "symbol": leg.occ_symbol,
                "action": leg.action.value,
                "quantity": leg.quantity,
            })

        payload: Dict[str, Any] = {
            "order-type": order_type.value,
            "time-in-force": time_in_force,
            "price": net_price,
            "price-effect": price_effect.value,
            "legs": leg_payloads,
        }

        if self._http_fn:
            status_code, res_data = self._http_fn("POST", url, headers, payload)
        else:
            raise TastytradeAPIError("HTTP transport function not configured.")

        if status_code not in (200, 201):
            raise TastytradeAPIError(f"Option order placement failed (HTTP {status_code}): {res_data}")

        order_id = str(res_data.get("data", {}).get("order", {}).get("id", "TT_ORD_1001"))
        order_status = str(res_data.get("data", {}).get("order", {}).get("status", "Received"))

        return TastytradeOrder(
            order_id=order_id,
            account_number=account_number,
            order_type=order_type,
            legs=legs,
            price=net_price,
            price_effect=price_effect,
            status=order_status,
        )
