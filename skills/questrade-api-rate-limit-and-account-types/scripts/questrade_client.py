"""
questrade-api-rate-limit-and-account-types: Production-grade Questrade API client with
OAuth2 refresh token rotation, token-bucket rate limiting, and Canadian account type validation.
"""
from dataclasses import dataclass, field
import logging
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class QuestradeAPIError(Exception):
    """Raised when Questrade API returns an error or rate limit is breached."""
    pass


class AccountType(Enum):
    MARGIN = "Margin"
    TFSA = "TFSA"
    RRSP = "RRSP"
    FHSA = "FHSA"
    INDIVIDUAL = "Individual"


@dataclass
class QuestradeAuthToken:
    access_token: str
    refresh_token: str
    api_server: str
    token_type: str
    expires_at: float


@dataclass
class QuestradeAccount:
    account_number: str
    account_type: AccountType
    is_primary: bool
    status: str


class TokenBucketRateLimiter:
    """Sliding-window token-bucket rate limiter to prevent Questrade HTTP 429 errors."""

    def __init__(self, capacity: int = 30, fill_rate: float = 30.0):
        self.capacity = capacity
        self.fill_rate = fill_rate  # tokens per second
        self.tokens = float(capacity)
        self.last_update = time.time()

    def acquire(self) -> bool:
        """Acquires a token if available, else waits or returns False."""
        now = time.time()
        elapsed = now - self.last_update
        self.last_update = now
        self.tokens = min(float(self.capacity), self.tokens + elapsed * self.fill_rate)

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class QuestradeClient:
    """
    Client for Questrade's REST API handling OAuth2 single-use refresh token rotation,
    token-bucket rate limiting, and account type order restrictions.
    """

    LOGIN_URL = "https://login.questrade.com/oauth2/token"

    def __init__(
        self,
        rate_limit_capacity: int = 30,
        http_fn: Optional[Callable[[str, str, Dict[str, str], Optional[Dict[str, Any]]], Any]] = None,
    ):
        self.auth_token: Optional[QuestradeAuthToken] = None
        self.rate_limiter = TokenBucketRateLimiter(capacity=rate_limit_capacity, fill_rate=float(rate_limit_capacity))
        self._http_fn = http_fn
        self.accounts: Dict[str, QuestradeAccount] = {}

    def refresh_access_token(self, refresh_token: str) -> QuestradeAuthToken:
        """
        Exchanges a single-use refresh token for a new access token, new refresh token,
        and dynamic API server endpoint.
        """
        url = f"{self.LOGIN_URL}?grant_type=refresh_token&refresh_token={refresh_token}"

        if self._http_fn:
            status_code, response_data = self._http_fn("GET", url, {}, None)
        else:
            raise QuestradeAPIError("HTTP transport function not configured.")

        if status_code != 200 or "access_token" not in response_data:
            raise QuestradeAPIError(f"OAuth2 refresh failed (HTTP {status_code}): {response_data}")

        self.auth_token = QuestradeAuthToken(
            access_token=response_data["access_token"],
            refresh_token=response_data["refresh_token"],
            api_server=response_data["api_server"],
            token_type=response_data.get("token_type", "Bearer"),
            expires_at=time.time() + response_data.get("expires_in", 1800),
        )
        logger.info(f"Questrade OAuth2 refreshed. API Server: {self.auth_token.api_server}")
        return self.auth_token

    def fetch_accounts(self) -> List[QuestradeAccount]:
        """Queries Questrade accounts and registers their account types."""
        if not self.auth_token:
            raise QuestradeAPIError("Not authenticated. Call refresh_access_token() first.")

        if not self.rate_limiter.acquire():
            raise QuestradeAPIError("Rate limit exceeded (HTTP 429 protection). Request throttled.")

        url = f"{self.auth_token.api_server}v1/accounts"
        headers = {"Authorization": f"Bearer {self.auth_token.access_token}"}

        if self._http_fn:
            status_code, response_data = self._http_fn("GET", url, headers, None)
        else:
            raise QuestradeAPIError("HTTP transport function not configured.")

        if status_code != 200:
            raise QuestradeAPIError(f"Failed to fetch accounts (HTTP {status_code}): {response_data}")

        account_list = []
        for acc in response_data.get("accounts", []):
            try:
                acc_type = AccountType(acc.get("type", "Margin"))
            except ValueError:
                acc_type = AccountType.MARGIN

            q_acc = QuestradeAccount(
                account_number=str(acc["number"]),
                account_type=acc_type,
                is_primary=bool(acc.get("isPrimary", False)),
                status=str(acc.get("status", "Active")),
            )
            self.accounts[q_acc.account_number] = q_acc
            account_list.append(q_acc)

        return account_list

    def validate_order_for_account(
        self,
        account_number: str,
        order_action: str,  # "Buy", "Sell", "Short", "SellShort"
    ) -> bool:
        """
        Validates whether the requested order action is permitted for the given account type.
        (e.g., Short selling is forbidden in registered accounts: TFSA, RRSP, FHSA).
        """
        if account_number not in self.accounts:
            raise QuestradeAPIError(f"Account {account_number} not registered.")

        acc = self.accounts[account_number]
        registered_types = {AccountType.TFSA, AccountType.RRSP, AccountType.FHSA}

        if acc.account_type in registered_types and order_action in {"Short", "SellShort"}:
            logger.error(f"Short selling rejected: forbidden in registered account {acc.account_type.value}")
            return False

        return True
