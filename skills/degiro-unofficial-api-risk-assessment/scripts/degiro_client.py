"""
degiro-unofficial-api-risk-assessment: session lifecycle manager, pre-trade order
dry-run, and operational risk assessment for DEGIRO's reverse-engineered Web API.

ToS WARNING (read before using): DEGIRO publishes no official trading API, and
its own helpdesk states that it "does not support the use of external solutions,
such as API wrappers or custom scripts, that can interface with your DEGIRO
account" and that using third-party automation tools violates its terms of
service. Automating a DEGIRO account therefore risks account restriction or
termination regardless of how carefully this client behaves. The risk score
below models OPERATIONAL risk (lockout, stale session, cost blindness) only --
it does not and cannot reduce the contractual risk, which is not a gradient.

Endpoint shapes here follow the community `degiro-connector` library, which is
the de facto record of these undocumented endpoints. They can change without
notice; treat every response field as optional.
"""
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Transport contract: (method, url, headers, body) -> (status_code, decoded_json)
HttpFn = Callable[[str, str, Dict[str, str], Optional[Dict[str, Any]]], Tuple[int, Any]]

BASE_URL = "https://trader.degiro.nl"
LOGIN_URL = f"{BASE_URL}/login/secure/login"
LOGIN_TOTP_URL = f"{LOGIN_URL}/totp"
ORDER_CHECK_URL = f"{BASE_URL}/trading/secure/v5/checkOrder"
ORDER_CONFIRM_URL = f"{BASE_URL}/trading/secure/v5/order"

VALID_BUY_SELL = frozenset({"BUY", "SELL"})

# DEGIRO orderType integers as used by the Web API. Only the two this client
# supports are enumerated; others exist and are rejected rather than guessed.
ORDER_TYPE_LIMIT = 0
ORDER_TYPE_MARKET = 2
VALID_ORDER_TYPES = frozenset({ORDER_TYPE_LIMIT, ORDER_TYPE_MARKET})

# --- Risk-model constants -------------------------------------------------
# These are OPERATIONAL HEURISTICS chosen by this skill, not published DEGIRO
# limits. DEGIRO documents no rate limits, lockout thresholds, or session TTL,
# so none of these numbers can be cited to a source. They are named and
# constructor-overridable precisely so an operator can calibrate them against
# observed behaviour instead of inheriting an unverified default.
DEFAULT_BASE_RISK = 0.10                 # standing risk of using an unofficial API
DEFAULT_NO_SESSION_RISK = 0.80
DEFAULT_LOGIN_BURST_RISK = 0.50
DEFAULT_STALE_SESSION_RISK = 0.30
DEFAULT_LOGIN_BURST_WINDOW_S = 10.0
DEFAULT_LOGIN_BURST_THRESHOLD = 3
DEFAULT_SESSION_STALE_AFTER_S = 4 * 60 * 60  # heuristic, NOT a documented TTL


class DEGIROAPIError(Exception):
    """Raised when a DEGIRO API operation fails or is refused locally."""


class DEGIROAuthError(DEGIROAPIError):
    """Raised when authentication fails, including when 2FA is required."""


class DEGIRORiskThresholdBreached(DEGIROAPIError):
    """Raised when an order submission is refused by the local risk gate."""


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
    """
    Outcome of a `checkOrder` dry-run.

    `estimated_fee` and `total_cost` are Optional and are None when DEGIRO did
    not return the corresponding cost fields. None means "unknown", NEVER zero:
    `checkOrder` is documented by the degiro-connector model to return every
    cost field as optional, and has been observed returning only
    `confirmationId` and `responseDatetime`. Treating an absent fee as 0.0
    silently understates the cost of every trade.
    """
    is_valid: bool
    confirmation_id: Optional[str]
    estimated_fee: Optional[float]
    gross_notional: float
    total_cost: Optional[float]
    cost_fields_complete: bool = False
    error_message: Optional[str] = None


@dataclass
class OrderConfirmation:
    order_id: Optional[str]
    is_submitted: bool
    error_message: Optional[str] = None


@dataclass
class RiskEvaluation:
    risk_level: RiskLevel
    risk_score: float  # 0.0 (safe) to 1.0 (unsafe)
    reasons: List[str]


class DEGIROUnofficialRiskManager:
    """
    Session lifecycle, operational risk evaluation, and the two-step
    checkOrder -> confirm order flow for DEGIRO's unofficial Web API.

    The risk gate is a local safety brake against self-inflicted lockout and
    cost-blind trading. It is not a compliance control and does not make
    automated access permitted under DEGIRO's terms.
    """

    BASE_URL = BASE_URL

    def __init__(
        self,
        max_acceptable_risk_score: float = 0.70,
        http_fn: Optional[HttpFn] = None,
        *,
        require_complete_cost_fields: bool = True,
        session_stale_after_s: float = DEFAULT_SESSION_STALE_AFTER_S,
        login_burst_window_s: float = DEFAULT_LOGIN_BURST_WINDOW_S,
        login_burst_threshold: int = DEFAULT_LOGIN_BURST_THRESHOLD,
    ):
        if not 0.0 <= max_acceptable_risk_score <= 1.0:
            raise ValueError("max_acceptable_risk_score must be within [0.0, 1.0]")
        if http_fn is None:
            raise ValueError(
                "http_fn transport is required; construct the client with an explicit "
                "transport so timeouts and TLS verification are caller-controlled."
            )
        self.session: Optional[DEGIROSession] = None
        self.max_acceptable_risk_score = max_acceptable_risk_score
        self.require_complete_cost_fields = require_complete_cost_fields
        self.session_stale_after_s = session_stale_after_s
        self.login_burst_window_s = login_burst_window_s
        self.login_burst_threshold = login_burst_threshold
        self._http_fn: HttpFn = http_fn
        self.login_attempts = 0
        self.last_login_time = 0.0
        # confirmationIds already spent. DEGIRO's confirmationId is single-use;
        # replaying one is how a retry turns into a duplicate order.
        self._consumed_confirmation_ids: set = set()

    # -- authentication ----------------------------------------------------

    def login_and_extract_session(
        self, username: str, password: str, totp_code: Optional[str] = None
    ) -> DEGIROSession:
        """
        Authenticates and extracts session parameters.

        When `totp_code` is supplied the request is routed to the TOTP login
        endpoint with a `oneTimePassword` field -- posting a TOTP code to the
        plain login endpoint silently drops it and fails on any 2FA-enabled
        account.

        Raises DEGIROAuthError if authentication fails, if DEGIRO signals that
        2FA is required, or if the response omits the account identifiers. No
        identifier is ever defaulted: a fabricated `intAccount` would address
        somebody else's account on every subsequent request.
        """
        if not isinstance(username, str) or not username:
            raise ValueError("username must be a non-empty string")
        if not isinstance(password, str) or not password:
            raise ValueError("password must be a non-empty string")

        now = time.time()
        if now - self.last_login_time < self.login_burst_window_s:
            self.login_attempts += 1
        else:
            self.login_attempts = 1
        self.last_login_time = now

        payload: Dict[str, Any] = {
            "username": username,
            "password": password,
            "isRedirect": False,
        }
        if totp_code is not None:
            url = LOGIN_TOTP_URL
            payload["oneTimePassword"] = str(totp_code)
        else:
            url = LOGIN_URL

        status, res_data = self._http_fn("POST", url, {}, payload)

        if not isinstance(res_data, dict):
            raise DEGIROAuthError(
                f"DEGIRO authentication returned a non-JSON-object body (HTTP {status})."
            )

        # DEGIRO signals a missing second factor with a status/error field rather
        # than an HTTP-level distinction; surface it as an actionable error
        # instead of a generic auth failure.
        status_text = str(res_data.get("statusText", "") or "")
        login_failed = status != 200 or "sessionId" not in res_data
        if login_failed and ("totp" in status_text.lower() or res_data.get("status") == 6):
            raise DEGIROAuthError(
                "DEGIRO requires a 2FA one-time password; re-authenticate with totp_code."
            )
        if login_failed:
            # Deliberately does not echo the response body: it may carry
            # session material or account identifiers into logs.
            raise DEGIROAuthError(
                f"DEGIRO authentication failed (HTTP {status}, statusText={status_text!r})."
            )

        session_id = res_data["sessionId"]
        if not isinstance(session_id, str) or not session_id:
            raise DEGIROAuthError("DEGIRO returned an empty sessionId.")

        int_account = res_data.get("intAccount")
        client_id = res_data.get("clientInfo", {}).get("id") if isinstance(
            res_data.get("clientInfo"), dict
        ) else None
        if int_account is None:
            raise DEGIROAuthError(
                "Login response omitted intAccount. Fetch it from /pa/secure/client before "
                "trading; this client will not guess an account number."
            )
        if client_id is None:
            raise DEGIROAuthError(
                "Login response omitted clientInfo.id. Fetch it from /pa/secure/client."
            )

        self.session = DEGIROSession(
            session_id=session_id,
            int_account=int(int_account),
            client_id=int(client_id),
            created_at=now,
            is_active=True,
        )
        logger.info("DEGIRO session established (client_id=%s).", client_id)
        return self.session

    # -- risk evaluation ---------------------------------------------------

    def evaluate_api_risk(self) -> RiskEvaluation:
        """
        Scores OPERATIONAL risk of continuing to call the unofficial endpoints.

        The weights are this skill's heuristics, not published DEGIRO limits --
        see the module constants. A low score never implies the integration is
        permitted under DEGIRO's terms.
        """
        reasons: List[str] = []
        score = DEFAULT_BASE_RISK

        if not self.session or not self.session.is_active:
            score += DEFAULT_NO_SESSION_RISK
            reasons.append("No active session established.")

        if self.login_attempts > self.login_burst_threshold:
            score += DEFAULT_LOGIN_BURST_RISK
            reasons.append(
                f"Login burst: {self.login_attempts} attempts inside "
                f"{self.login_burst_window_s:g}s windows (lockout risk)."
            )

        if self.session and (time.time() - self.session.created_at > self.session_stale_after_s):
            score += DEFAULT_STALE_SESSION_RISK
            reasons.append(
                f"Session older than {self.session_stale_after_s / 3600:.1f}h "
                "(heuristic staleness threshold, not a documented TTL)."
            )

        score = min(1.0, score)
        if score >= 0.80:
            level = RiskLevel.CRITICAL_HALT
        elif score >= 0.50:
            level = RiskLevel.HIGH
        elif score >= 0.30:
            level = RiskLevel.MEDIUM
        else:
            level = RiskLevel.LOW

        return RiskEvaluation(risk_level=level, risk_score=score, reasons=reasons)

    def _require_session(self) -> DEGIROSession:
        if self.session is None or not self.session.is_active:
            raise DEGIROAPIError("No active DEGIRO session; call login_and_extract_session first.")
        return self.session

    @staticmethod
    def _sum_cost_components(data: Dict[str, Any], key: str) -> Tuple[float, bool]:
        """
        Sums an optional list-of-dicts cost block (e.g. `transactionFees`).

        Returns (total, present). `present` is False when the key is absent, so
        an absent block is never mistaken for a zero-cost block.
        """
        block = data.get(key)
        if block is None:
            return 0.0, False
        if not isinstance(block, list):
            return 0.0, False
        total = 0.0
        for item in block:
            if isinstance(item, dict):
                amount = item.get("amount")
                if isinstance(amount, (int, float)) and not isinstance(amount, bool):
                    total += float(amount)
        return total, True

    # -- pre-trade dry run -------------------------------------------------

    def check_order_dry_run(
        self,
        product_id: int,
        buy_sell: str,
        order_type: int,
        price: float,
        quantity: int,
    ) -> PreTradeCheckResult:
        """
        Runs the `checkOrder` pre-trade validation and returns the confirmationId
        plus whatever cost information DEGIRO actually supplied.

        Cost fields are all optional in DEGIRO's response. When they are missing
        this returns `estimated_fee=None`, `total_cost=None`, and
        `cost_fields_complete=False` rather than reporting zero fees. With
        `require_complete_cost_fields=True` (default) a response lacking any
        cost data is refused, because a bot that sizes on a fabricated 0.00 fee
        underestimates the cost of every trade it places.
        """
        buy_sell = self._validate_order_params(product_id, buy_sell, order_type, price, quantity)
        session = self._require_session()

        risk = self.evaluate_api_risk()
        if risk.risk_score > self.max_acceptable_risk_score:
            return PreTradeCheckResult(
                is_valid=False,
                confirmation_id=None,
                estimated_fee=None,
                gross_notional=price * quantity,
                total_cost=None,
                cost_fields_complete=False,
                error_message=(
                    f"Order blocked by risk gate ({risk.risk_level.value}, "
                    f"score={risk.risk_score:.2f} > {self.max_acceptable_risk_score:.2f}): "
                    f"{'; '.join(risk.reasons)}"
                ),
            )

        url = (
            f"{ORDER_CHECK_URL};jsessionid={session.session_id}"
            f"?intAccount={session.int_account}&sessionId={session.session_id}"
        )
        payload = {
            "buySell": buy_sell,
            "orderType": order_type,
            "price": price,
            "productId": product_id,
            "quantity": quantity,
            "timeType": 1,
        }

        status, res_data = self._http_fn("POST", url, {}, payload)
        gross_notional = price * quantity

        if status != 200 or not isinstance(res_data, dict) or "data" not in res_data:
            return PreTradeCheckResult(
                is_valid=False,
                confirmation_id=None,
                estimated_fee=None,
                gross_notional=gross_notional,
                total_cost=None,
                cost_fields_complete=False,
                error_message=f"checkOrder failed (HTTP {status}).",
            )

        data = res_data["data"]
        if not isinstance(data, dict):
            return PreTradeCheckResult(
                is_valid=False, confirmation_id=None, estimated_fee=None,
                gross_notional=gross_notional, total_cost=None, cost_fields_complete=False,
                error_message="checkOrder returned a malformed data block.",
            )

        confirmation_id = data.get("confirmationId")
        if not isinstance(confirmation_id, str) or not confirmation_id:
            return PreTradeCheckResult(
                is_valid=False, confirmation_id=None, estimated_fee=None,
                gross_notional=gross_notional, total_cost=None, cost_fields_complete=False,
                error_message="checkOrder returned no confirmationId; the order cannot be confirmed.",
            )

        # Aggregate every cost block DEGIRO may return. `transactionFee` is a
        # scalar; fees/taxes/opposite-fees/FX surcharges arrive as lists.
        raw_fee = data.get("transactionFee")
        scalar_fee_present = isinstance(raw_fee, (int, float)) and not isinstance(raw_fee, bool)
        total_costs = float(raw_fee) if scalar_fee_present else 0.0
        any_cost_field = scalar_fee_present

        for key in (
            "transactionFees",
            "transactionTaxes",
            "transactionOppositeFees",
            "transactionAutoFxSurcharges",
            "transactionAutoFxOppositeSurcharges",
        ):
            component, present = self._sum_cost_components(data, key)
            total_costs += component
            any_cost_field = any_cost_field or present

        if not any_cost_field:
            message = (
                "checkOrder returned no cost fields (transactionFee/transactionFees/"
                "transactionTaxes absent). Fees are UNKNOWN, not zero."
            )
            if self.require_complete_cost_fields:
                logger.warning("Pre-trade check refused for product %s: %s", product_id, message)
                return PreTradeCheckResult(
                    is_valid=False,
                    confirmation_id=confirmation_id,
                    estimated_fee=None,
                    gross_notional=gross_notional,
                    total_cost=None,
                    cost_fields_complete=False,
                    error_message=message,
                )
            logger.warning("Proceeding with unknown fees for product %s: %s", product_id, message)
            return PreTradeCheckResult(
                is_valid=True,
                confirmation_id=confirmation_id,
                estimated_fee=None,
                gross_notional=gross_notional,
                total_cost=None,
                cost_fields_complete=False,
                error_message=message,
            )

        return PreTradeCheckResult(
            is_valid=True,
            confirmation_id=confirmation_id,
            estimated_fee=total_costs,
            gross_notional=gross_notional,
            total_cost=gross_notional + total_costs,
            cost_fields_complete=True,
            error_message=None,
        )

    def _validate_order_params(
        self, product_id: int, buy_sell: str, order_type: int, price: float, quantity: int
    ) -> str:
        if not isinstance(product_id, int) or isinstance(product_id, bool) or product_id <= 0:
            raise ValueError(
                "product_id must be a positive DEGIRO internal integer id; resolve it via "
                "/product_search/secure/v5/products/lookup (it is not an ISIN or ticker)."
            )
        if not isinstance(buy_sell, str):
            raise TypeError("buy_sell must be a string")
        normalized = buy_sell.strip().upper()
        if normalized not in VALID_BUY_SELL:
            raise ValueError(f"buy_sell must be one of {sorted(VALID_BUY_SELL)}")
        if not isinstance(order_type, int) or isinstance(order_type, bool):
            raise TypeError("order_type must be an int")
        if order_type not in VALID_ORDER_TYPES:
            raise ValueError(
                f"order_type {order_type} unsupported; this client handles "
                f"{ORDER_TYPE_LIMIT} (LIMIT) and {ORDER_TYPE_MARKET} (MARKET) only."
            )
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            raise TypeError("price must be a number")
        if order_type == ORDER_TYPE_LIMIT and price <= 0:
            raise ValueError("price must be positive for a LIMIT order")
        return normalized

    # -- order confirmation ------------------------------------------------

    def confirm_order(
        self,
        check_result: PreTradeCheckResult,
        product_id: int,
        buy_sell: str,
        order_type: int,
        price: float,
        quantity: int,
    ) -> OrderConfirmation:
        """
        Submits the second leg of DEGIRO's two-step order flow:
        POST /trading/secure/v5/order/{confirmationId};jsessionid=...

        NOT RETRY-SAFE, by design. A confirmationId is single-use; this client
        marks it consumed *before* dispatch, so a caller that retries after a
        timeout gets DEGIRORiskThresholdBreached instead of a duplicate order.
        If the response is lost, the order may still have reached DEGIRO --
        reconcile against order history rather than resubmitting.
        """
        buy_sell = self._validate_order_params(product_id, buy_sell, order_type, price, quantity)
        session = self._require_session()

        if not isinstance(check_result, PreTradeCheckResult):
            raise TypeError("check_result must be a PreTradeCheckResult from check_order_dry_run")
        if not check_result.is_valid or not check_result.confirmation_id:
            raise DEGIROAPIError(
                "Refusing to confirm an order whose pre-trade check did not pass: "
                f"{check_result.error_message}"
            )
        if self.require_complete_cost_fields and not check_result.cost_fields_complete:
            raise DEGIROAPIError(
                "Refusing to confirm an order with unknown fees "
                "(require_complete_cost_fields=True)."
            )

        confirmation_id = check_result.confirmation_id
        if confirmation_id in self._consumed_confirmation_ids:
            raise DEGIRORiskThresholdBreached(
                f"confirmationId {confirmation_id!r} was already dispatched. DEGIRO "
                "confirmation ids are single-use; re-run checkOrder or reconcile against "
                "order history instead of resubmitting."
            )

        risk = self.evaluate_api_risk()
        if risk.risk_score > self.max_acceptable_risk_score:
            raise DEGIRORiskThresholdBreached(
                f"Order confirmation blocked by risk gate ({risk.risk_level.value}, "
                f"score={risk.risk_score:.2f}): {'; '.join(risk.reasons)}"
            )

        # Consume before dispatch: if the transport raises or times out we must
        # not leave a replayable id behind.
        self._consumed_confirmation_ids.add(confirmation_id)

        url = (
            f"{ORDER_CONFIRM_URL}/{confirmation_id};jsessionid={session.session_id}"
            f"?intAccount={session.int_account}&sessionId={session.session_id}"
        )
        payload = {
            "buySell": buy_sell,
            "orderType": order_type,
            "price": price,
            "productId": product_id,
            "quantity": quantity,
            "timeType": 1,
        }

        status, res_data = self._http_fn("POST", url, {}, payload)

        if status != 200 or not isinstance(res_data, dict):
            return OrderConfirmation(
                order_id=None,
                is_submitted=False,
                error_message=(
                    f"Order confirmation returned HTTP {status}. The order may still have been "
                    "accepted -- reconcile against order history before any resubmission."
                ),
            )

        data = res_data.get("data") if isinstance(res_data.get("data"), dict) else {}
        order_id = data.get("orderId")
        if not isinstance(order_id, str) or not order_id:
            return OrderConfirmation(
                order_id=None,
                is_submitted=False,
                error_message=(
                    "Order confirmation succeeded at HTTP level but returned no orderId; "
                    "reconcile against order history."
                ),
            )

        logger.info("DEGIRO order submitted (orderId=%s, product=%s).", order_id, product_id)
        return OrderConfirmation(order_id=order_id, is_submitted=True, error_message=None)
