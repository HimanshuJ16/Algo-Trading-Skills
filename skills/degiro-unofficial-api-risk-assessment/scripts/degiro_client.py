"""
degiro-unofficial-api-risk-assessment: Session token lifecycle manager, pre-trade order fee dry-run,
and risk assessment engine for DEGIRO's reverse-engineered Web API.
"""
from dataclasses import dataclass, field
import logging
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class DEGIROAPIError(Exception):
    """Raised when DEGIRO API operations fail or risk threshold is breached."""
    pass


class RiskLevel(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL_HALT = "CRITICAL_HALT"


@dataclass
class DEGIROSession:
    session_id: str
    int_account: int
    client_id: int
    created_at: float
    is_active: bool = True


@dataclass
class PreTradeCheckResult:
    is_valid: bool
    confirmation_id: Optional[str]
    estimated_fee: float
    total_transaction_value: float
    error_message: Optional[str] = None


@dataclass
class RiskEvaluation:
    risk_level: RiskLevel
    risk_score: float  # 0.0 (safe) to 1.0 (unsafe)
    reasons: List[str]


class DEGIROUnofficialRiskManager:
    """
    Manages session lifecycle, risk evaluation, and pre-trade order dry-runs
    for DEGIRO's unofficial API endpoints.
    """

    BASE_URL = "https://trader.degiro.nl"

    def __init__(
        self,
        max_acceptable_risk_score: float = 0.70,
        http_fn: Optional[Callable[[str, str, Dict[str, str], Optional[Dict[str, Any]]], Any]] = None,
    ):
        self.session: Optional[DEGIROSession] = None
        self.max_acceptable_risk_score = max_acceptable_risk_score
        self._http_fn = http_fn
        self.login_attempts = 0
        self.last_login_time = 0.0

    def login_and_extract_session(
        self, username: str, password: str, totp_code: Optional[str] = None
    ) -> DEGIROSession:
        """Authenticates with DEGIRO Web API and extracts session parameters."""
        now = time.time()
        if now - self.last_login_time < 10.0:
            self.login_attempts += 1
        else:
            self.login_attempts = 1
        self.last_login_time = now

        login_url = f"{self.BASE_URL}/login/secure/login"
        payload = {"username": username, "password": password, "isRedirect": False}

        if self._http_fn:
            status, res_data = self._http_fn("POST", login_url, {}, payload)
        else:
            raise DEGIROAPIError("HTTP transport function not configured.")

        if status != 200 or "sessionId" not in res_data:
            raise DEGIROAPIError(f"DEGIRO authentication failed (HTTP {status}): {res_data}")

        session_id = res_data["sessionId"]
        int_account = int(res_data.get("intAccount", 1234567))
        client_id = int(res_data.get("clientInfo", {}).get("id", 98765))

        self.session = DEGIROSession(
            session_id=session_id,
            int_account=int_account,
            client_id=client_id,
            created_at=now,
            is_active=True,
        )
        logger.info(f"DEGIRO session established. Account: {int_account}")
        return self.session

    def evaluate_api_risk(self) -> RiskEvaluation:
        """Evaluates operational and legal risk of calling DEGIRO unofficial endpoints."""
        reasons = []
        score = 0.10  # Base risk for using unofficial API

        if not self.session or not self.session.is_active:
            score += 0.80
            reasons.append("No active session established.")

        # Rate of login attempts check
        if self.login_attempts > 3:
            score += 0.50
            reasons.append("High login attempt frequency detected (lockout risk).")

        # Session age check (sessions degrade after 4 hours)
        if self.session and (time.time() - self.session.created_at > 14400):
            score += 0.30
            reasons.append("Session age > 4 hours (token degradation risk).")

        if score >= 0.80:
            level = RiskLevel.CRITICAL_HALT
        elif score >= 0.50:
            level = RiskLevel.HIGH
        elif score >= 0.30:
            level = RiskLevel.MEDIUM
        else:
            level = RiskLevel.LOW

        return RiskEvaluation(risk_level=level, risk_score=min(1.0, score), reasons=reasons)

    def check_order_dry_run(
        self,
        product_id: int,
        buy_sell: str,  # "BUY" or "SELL"
        order_type: int,  # 0: LIMIT, 2: MARKET
        price: float,
        quantity: int,
    ) -> PreTradeCheckResult:
        """
        Executes pre-trade validation (`checkOrder` endpoint) to calculate exact fees
        and confirm order parameters prior to submission.
        """
        risk = self.evaluate_api_risk()
        if risk.risk_score > self.max_acceptable_risk_score:
            return PreTradeCheckResult(
                is_valid=False,
                confirmation_id=None,
                estimated_fee=0.0,
                total_transaction_value=0.0,
                error_message=f"Order blocked by Risk Engine ({risk.risk_level.value}): {risk.reasons}",
            )

        url = f"{self.BASE_URL}/trading/secure/v5/checkOrder;jsessionid={self.session.session_id}?intAccount={self.session.int_account}"
        payload = {
            "buySell": buy_sell,
            "orderType": order_type,
            "price": price,
            "productId": product_id,
            "quantity": quantity,
            "timeType": 1,
        }

        if self._http_fn:
            status, res_data = self._http_fn("POST", url, {}, payload)
        else:
            raise DEGIROAPIError("HTTP transport function not configured.")

        if status != 200 or "data" not in res_data:
            return PreTradeCheckResult(
                is_valid=False,
                confirmation_id=None,
                estimated_fee=0.0,
                total_transaction_value=0.0,
                error_message=f"checkOrder failed (HTTP {status}): {res_data}",
            )

        data = res_data["data"]
        confirmation_id = data.get("confirmationId")
        fee = float(data.get("transactionFee", 0.0))
        total_val = float(data.get("total", price * quantity))

        return PreTradeCheckResult(
            is_valid=True,
            confirmation_id=confirmation_id,
            estimated_fee=fee,
            total_transaction_value=total_val,
            error_message=None,
        )
