"""
robinhood-unofficial-api-integration: Authentication, order placement, and position
polling via Robinhood's unofficial/reverse-engineered API endpoints.
"""
from dataclasses import dataclass, field
import hashlib
import logging
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://api.robinhood.com"


class RobinhoodAuthError(Exception):
    """Raised when authentication fails."""
    pass


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"


@dataclass
class AuthToken:
    access_token: str
    refresh_token: str
    expires_at: float
    device_token: str

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at


@dataclass
class RobinhoodOrder:
    order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    limit_price: Optional[float]
    status: str
    created_at: float


@dataclass
class RobinhoodPosition:
    symbol: str
    quantity: float
    average_cost: float


class RobinhoodUnofficialClient:
    """
    Client for Robinhood's unofficial API. Handles device-token-based
    authentication with MFA, order placement, and position polling.

    WARNING: This uses an unofficial, reverse-engineered API. It may violate
    Robinhood's Terms of Service and could break without notice.
    """

    def __init__(
        self,
        http_fn: Optional[Any] = None,
    ):
        """
        Args:
            http_fn: Optional callable(method, url, headers, payload) -> (status, body_dict).
                     Inject for testing; in production, use requests.
        """
        self.auth_token: Optional[AuthToken] = None
        self.device_token: str = str(uuid.uuid4())
        self._http = http_fn
        self._orders: List[RobinhoodOrder] = []

    def authenticate(
        self,
        email: str,
        password: str,
        mfa_code: Optional[str] = None,
    ) -> AuthToken:
        """
        Authenticate with Robinhood using email/password + optional MFA code.
        Caches device_token to avoid repeated MFA challenges.
        """
        payload = {
            "client_id": "c82SH0WZOsabOXGP2sxqcj34FxkvfnWRZBKlBjFS",
            "grant_type": "password",
            "username": email,
            "password": password,
            "device_token": self.device_token,
            "scope": "internal",
        }
        if mfa_code:
            payload["mfa_code"] = mfa_code

        if self._http:
            status, body = self._http("POST", f"{BASE_URL}/oauth2/token/", {}, payload)
        else:
            raise RobinhoodAuthError("No HTTP transport configured.")

        if status == 400 and body.get("mfa_required"):
            raise RobinhoodAuthError(
                f"MFA_REQUIRED: Robinhood requires MFA code. "
                f"MFA type: {body.get('mfa_type', 'unknown')}"
            )

        if status != 200 or "access_token" not in body:
            raise RobinhoodAuthError(
                f"Authentication failed (HTTP {status}): {body.get('detail', 'unknown error')}"
            )

        self.auth_token = AuthToken(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token", ""),
            expires_at=time.time() + body.get("expires_in", 86400),
            device_token=self.device_token,
        )
        logger.info("Robinhood authentication successful.")
        return self.auth_token

    def _ensure_auth(self) -> Dict[str, str]:
        """Return auth headers, raising if not authenticated."""
        if self.auth_token is None:
            raise RobinhoodAuthError("Not authenticated. Call authenticate() first.")
        if self.auth_token.is_expired:
            raise RobinhoodAuthError("Token expired. Re-authenticate or refresh.")
        return {"Authorization": f"Bearer {self.auth_token.access_token}"}

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
    ) -> RobinhoodOrder:
        """Place a buy/sell order via the unofficial API."""
        headers = self._ensure_auth()

        payload = {
            "account": f"{BASE_URL}/accounts/MOCK/",
            "symbol": symbol.upper(),
            "side": side.value,
            "type": order_type.value,
            "quantity": quantity,
            "time_in_force": "gfd",
            "trigger": "immediate",
        }
        if order_type == OrderType.LIMIT and limit_price is not None:
            payload["price"] = limit_price

        if self._http:
            status, body = self._http("POST", f"{BASE_URL}/orders/", headers, payload)
        else:
            raise RobinhoodAuthError("No HTTP transport configured.")

        if status not in (200, 201):
            raise ValueError(f"Order placement failed (HTTP {status}): {body}")

        order = RobinhoodOrder(
            order_id=body.get("id", str(uuid.uuid4())[:8]),
            symbol=symbol.upper(),
            side=side.value,
            order_type=order_type.value,
            quantity=quantity,
            limit_price=limit_price,
            status=body.get("state", "queued"),
            created_at=time.time(),
        )
        self._orders.append(order)
        logger.info(f"Order placed: {side.value} {quantity} {symbol} ({order_type.value})")
        return order

    def get_positions(self) -> List[RobinhoodPosition]:
        """Poll current positions from the unofficial API."""
        headers = self._ensure_auth()

        if self._http:
            status, body = self._http("GET", f"{BASE_URL}/positions/", headers, {})
        else:
            raise RobinhoodAuthError("No HTTP transport configured.")

        if status != 200:
            raise ValueError(f"Position query failed (HTTP {status})")

        positions = []
        for result in body.get("results", []):
            qty = float(result.get("quantity", 0))
            if qty > 0:
                positions.append(RobinhoodPosition(
                    symbol=result.get("symbol", "UNKNOWN"),
                    quantity=qty,
                    average_cost=float(result.get("average_buy_price", 0)),
                ))
        return positions
